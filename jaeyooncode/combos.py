import slippi as slp
import json
import os
from collections import deque
from pathlib import Path
from enum import IntEnum
from typing import Any

SLP_PATH = 'test_files/'

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STARTUP_FRAMES = 123       # frames skipped at game start (ready, go! sequence)
COMBO_WINDOW = 240         # rolling window size (4 s at 60 fps) for detection
COMBO_THRESHOLD = 100      # minimum rolling hitstun sum to consider a combo active
PREPOST = 120              # 2-second buffer added before/after a detected combo
MAX_MATCH_FRAMES = 28_800  # 8 min * 60 s * 60 fps — matches longer than this are rejected

# Legacy aliases kept for any code that still references the old names
HST = COMBO_THRESHOLD
WINDOW = COMBO_WINDOW


# ---------------------------------------------------------------------------
# Game state enum
# ---------------------------------------------------------------------------

class State(IntEnum):
    NEUTRAL = 0
    COMBO = 1
    EDGEGUARD = 2
    DEAD = 3


# ---------------------------------------------------------------------------
# Reference-data loaders
# ---------------------------------------------------------------------------

def actionstate_dict() -> dict:
    """Return a mapping of numeric action-state IDs to human-readable names."""
    temp = {}
    count = 0
    actions_path = Path(__file__).resolve().parent / "actionstates.txt"
    with open(actions_path, 'r') as f:
        for line in f:
            temp[count] = line.rstrip()
            count += 1
    return temp


def attack_dict() -> dict:
    """Return a mapping of numeric attack IDs to human-readable attack names."""
    temp = {}
    count = 1
    attacks_path = Path(__file__).resolve().parent / "attacks.txt"
    with open(attacks_path, 'r') as f:
        for line in f:
            temp[count] = line.rstrip()
            count += 1
    return temp


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_slp(slp_path: str) -> Any:
    """
    Validate a .slp file meets pipeline requirements.

    Checks:
    - File exists and has .slp extension
    - Exactly 2 players (1v1 only)
    - Match duration does not exceed 8 minutes

    Args:
        slp_path: Path to the .slp replay file.

    Returns:
        The parsed slippi.Game object if valid.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If validation fails.
    """
    path = Path(slp_path)
    if not path.exists():
        raise FileNotFoundError(f"Replay file not found: {path}")
    if path.suffix.lower() != ".slp":
        raise ValueError(f"Expected a .slp file, got: {path.suffix!r}")

    try:
        game = slp.Game(slp_path)
    except Exception as exc:
        raise ValueError(f"Could not parse replay file: {exc}") from exc

    # 1v1 only
    active_players = [p for p in game.metadata.players if p is not None]
    if len(active_players) != 2:
        raise ValueError(
            f"Only 1v1 matches are supported. Found {len(active_players)} active player(s)."
        )

    # Max 8-minute match
    duration = game.metadata.duration
    if duration is not None and duration > MAX_MATCH_FRAMES:
        raise ValueError(
            f"Match duration ({duration} frames) exceeds the 8-minute limit "
            f"({MAX_MATCH_FRAMES} frames)."
        )

    return game


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def comboscore(dsum: float, hsum: float) -> float:
    """
    Score a combo window based on damage dealt and hitstun accumulated.

    Higher scores indicate more impressive, highlight-worthy combos.
    Damage is weighted more heavily than raw hitstun duration.

    Args:
        dsum: Total damage dealt during the combo window.
        hsum: Total hitstun frames accumulated during the combo window.

    Returns:
        A non-negative float score. Returns 0.0 if inputs are non-positive.
    """
    if dsum <= 0 or hsum <= 0:
        return 0.0

    A, B = 1.0, 0.4    # hitstun weight / exponent
    C, D = 1.0, 1.25   # damage weight / exponent
    return A * (hsum ** B) * C * (dsum ** D)


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def _detect_combos_for_ports(
    frames: list,
    comboer: int,
    victim: int,
    adict: dict,
    sdict: dict,
    padding_frames: int,
) -> list:
    """
    Detect combo windows where player on port `comboer` is attacking port `victim`.

    Args:
        frames: Frame list with the startup frames already removed.
        comboer: Port index of the attacking player.
        victim: Port index of the defending player.
        adict: Attack ID → name lookup.
        sdict: Action-state ID → name lookup.
        padding_frames: Buffer frames added before combo start and after combo end.

    Returns:
        List of combo dicts with keys:
        start_frame, end_frame, score, total_damage, hit_count, is_kill, moves.
    """
    window: deque = deque()
    window_hs = 0.0

    in_combo = False
    combo_start = 0
    combo_damage = 0.0
    combo_hsum = 0.0
    combo_hits = 0
    combo_moves: list = []
    is_kill = False

    first_post = frames[0].ports[victim].leader.post
    victim_prev_damage = float(first_post.damage or 0)
    victim_prev_stocks = first_post.stocks

    combos: list = []
    total_frames = len(frames)

    for fcount, f in enumerate(frames):
        victim_post = f.ports[victim].leader.post
        comboer_post = f.ports[comboer].leader.post

        hs = float(victim_post.hit_stun or 0)
        curr_damage = float(victim_post.damage or 0)
        curr_stocks = victim_post.stocks

        # Update rolling hitstun window
        window.append(hs)
        window_hs += hs
        if len(window) > COMBO_WINDOW:
            window_hs -= window.popleft()

        # Damage this frame — ignore resets when victim respawns after death
        damage_delta = max(0.0, curr_damage - victim_prev_damage)

        # Detect stock loss (kill)
        stock_lost = (
            curr_stocks is not None
            and victim_prev_stocks is not None
            and curr_stocks < victim_prev_stocks
        )

        if in_combo:
            combo_hsum += hs

            if damage_delta > 0:
                combo_damage += damage_delta
                combo_hits += 1
                lal = comboer_post.last_attack_landed
                if lal is not None:
                    try:
                        name = adict[lal] if int(lal) < 30 else sdict[int(lal)]
                        if name and name not in combo_moves:
                            combo_moves.append(name)
                    except (KeyError, TypeError):
                        pass

            if stock_lost:
                is_kill = True

            # End combo when rolling sum drops below threshold, opponent dies,
            # or we have reached the last frame of the match
            end_of_match = fcount == total_frames - 1
            if window_hs < COMBO_THRESHOLD or stock_lost or end_of_match:
                start_frame = max(0, combo_start - padding_frames)
                end_frame = min(fcount + padding_frames, total_frames - 1)
                combos.append({
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "score": comboscore(combo_damage, combo_hsum),
                    "total_damage": round(combo_damage, 1),
                    "hit_count": combo_hits,
                    "is_kill": is_kill,
                    "moves": list(combo_moves),
                })
                in_combo = False
                combo_damage = 0.0
                combo_hsum = 0.0
                combo_hits = 0
                combo_moves = []
                is_kill = False

        else:
            if window_hs >= COMBO_THRESHOLD:
                in_combo = True
                combo_start = fcount

        # Reset damage baseline to 0 when victim respawns after dying
        victim_prev_damage = 0.0 if stock_lost else curr_damage
        if curr_stocks is not None:
            victim_prev_stocks = curr_stocks

    return combos


def get_combo_clips(
    slp_path: str,
    max_combos: int = 5,
    padding_frames: int = PREPOST,
) -> list:
    """
    Detect, score, and return the best combo windows from a .slp replay.

    Combos are detected for both player directions and ranked by score so
    the most highlight-worthy clips always come first regardless of which
    port the attacking player is on.

    Args:
        slp_path: Path to the .slp replay file.
        max_combos: Maximum number of combos to return.
        padding_frames: Extra frames added before combo start and after end.

    Returns:
        List of combo dicts sorted by score descending, each with keys:
        start_frame, end_frame, score, total_damage, hit_count, is_kill, moves.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If validation fails (not 1v1, too long, bad file, etc.).
    """
    game = validate_slp(slp_path)
    frames = game.frames[STARTUP_FRAMES:]
    adict = attack_dict()
    sdict = actionstate_dict()

    # Find which ports are occupied (handles non-standard port assignments)
    occupied = [i for i, p in enumerate(game.metadata.players) if p is not None]
    port_a, port_b = occupied[0], occupied[1]

    all_combos: list = []
    for comboer, victim in [(port_a, port_b), (port_b, port_a)]:
        all_combos.extend(
            _detect_combos_for_ports(frames, comboer, victim, adict, sdict, padding_frames)
        )

    # Drop empty windows where no damage was actually dealt
    all_combos = [c for c in all_combos if c["hit_count"] > 0]
    all_combos.sort(key=lambda c: c["score"], reverse=True)
    return all_combos[:max_combos]


# ---------------------------------------------------------------------------
# Legacy functions (kept for backwards compatibility)
# ---------------------------------------------------------------------------

def find_combos(slp_path: str) -> None:
    """
    Parse a .slp file and write a hitmap JSON to ref/.

    Legacy function — use get_combo_clips() for new code.
    """
    game = slp.Game(slp_path)
    frames = game.frames[STARTUP_FRAMES:]
    sdict = actionstate_dict()
    adict = attack_dict()

    p1_prev_state = frames[0].ports[0].leader.post.state
    p2_prev_state = frames[0].ports[1].leader.post.state
    p1_hs = 0.5
    p2_hs = 0.5
    p1_dam = 0
    p2_dam = 0
    p1_lal = None
    p2_lal = None

    comboer = 0
    victim = 1
    window_hs = 0
    state = State.NEUTRAL
    combo_counter = 0

    hitmap = {}
    combomap = {}

    fcount = 0
    for f in frames:
        if f.ports[victim].leader.post.damage > p2_dam:
            p1_lal = f.ports[comboer].leader.post.last_attack_landed
            if p1_lal < 30:
                hitmap[fcount] = (adict[p1_lal], f.ports[victim].leader.post.damage - p2_dam)
            else:
                hitmap[fcount] = (sdict[p1_lal], f.ports[victim].leader.post.damage - p2_dam)

        p2_dam = f.ports[victim].leader.post.damage
        p2_hs = f.ports[victim].leader.post.hit_stun

        if fcount > COMBO_WINDOW:
            window_hs -= 1
        if f.ports[1].leader.post.hit_stun >= 1:
            window_hs += 1
        if window_hs >= COMBO_THRESHOLD and state != State.COMBO:
            state = State.COMBO
            combomap[fcount] = "combo started"

        fcount += 1

    with open('ref/' + slp_path[-9:-4] + '_hitmap.json', 'w') as outfile:
        json.dump(hitmap, outfile, indent=4)


def record_moves(slp_path: str, filename: str) -> None:
    """Record all character action states and attacks from a replay to JSON files."""
    game = slp.Game(slp_path)
    frames = game.frames[STARTUP_FRAMES:]
    sdict = actionstate_dict()
    adict = attack_dict()

    md = game.metadata
    falco_states = {}
    fox_states = {}
    moves = {}

    falco_states["duration"] = md.duration
    fox_states["duration"] = md.duration

    count = 0
    for f in frames:
        falstate = f.ports[0].leader.post.state
        foxstate = f.ports[1].leader.post.state
        last_move = f.ports[0].leader.post.last_attack_landed

        if falstate not in falco_states.keys():
            falco_states[sdict[falstate]] = count
        if foxstate not in fox_states.keys():
            fox_states[sdict[foxstate]] = count

        if last_move is not None and last_move not in moves.keys():
            if int(last_move) > 30:
                moves[sdict[last_move]] = count
            else:
                moves[adict[last_move]] = count

        count += 1

    falco_states = {k: v for k, v in sorted(falco_states.items(), key=lambda item: item[1])}
    fox_states = {k: v for k, v in sorted(fox_states.items(), key=lambda item: item[1])}
    moves = {k: v for k, v in sorted(moves.items(), key=lambda item: item[1])}

    with open('ref/' + filename + '.json', 'w') as outfile:
        json.dump(falco_states, outfile, indent=4)
    with open('ref/fox_' + filename + '.json', 'w') as outfile:
        json.dump(fox_states, outfile, indent=4)
    with open('ref/attacks_' + filename + '.json', 'w') as outfile:
        json.dump(moves, outfile, indent=4)


def frame_to_sec(fnum: int) -> str:
    """Convert a frame index (startup frames removed) to an in-game clock string."""
    last_dig = [9, 8, 7, 4, 3, 1]
    passed = fnum // 60
    rem = fnum % 60
    final = 0

    mins = 7 - (passed // 60)
    secs = 60 - (passed % 60)
    if rem:
        secs -= 1
    ms = 9 - (rem // 6)
    if rem % 6:
        final = rem % 6

    return '0{}:{}.{}{}.... {} frames in'.format(mins, secs, ms, last_dig[final], fnum)


def test() -> None:
    """Quick utility to inspect hitstun values in a replay."""
    fp = SLP_PATH + 'run_2.slp'
    game = slp.Game(fp)
    frames = game.frames[STARTUP_FRAMES:]
    for f in frames:
        print(f.ports[1].leader.post.hit_stun)
    print("done")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    falcon = SLP_PATH + 'vs_falcon.slp'
    run1 = SLP_PATH + 'run_1.slp'
    run2 = SLP_PATH + 'run_2.slp'
    run3 = SLP_PATH + 'run_3.slp'
    run4 = SLP_PATH + 'run_4.slp'

    print(frame_to_sec(2500))


if __name__ == '__main__':
    main()
