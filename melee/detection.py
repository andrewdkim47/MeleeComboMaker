"""
Combo detection engine.

Parses .slp replay files frame-by-frame to detect, score, and return
the best combo windows as (start_frame, end_frame) ranges.

Uses peppi-py (Rust-backed) for replay parsing. peppi-py uses a
struct-of-arrays layout — all frame data for a field is stored as a
single columnar array rather than per-frame objects. We convert these
to numpy arrays upfront for efficient iteration.

Detection uses a two-pass strategy:
1. State-machine detector (primary): strict hit-count and gap-based rules
   that closely match the viewer's intuition of what a "combo" is.
2. Rolling-window detector (fallback): used when the state machine finds
   no combos, e.g. for very old replays or unusual match formats.
"""

from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
from peppi_py import read_slippi

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STARTUP_FRAMES = 123       # frames skipped at game start (ready, go! sequence)
MAX_MATCH_FRAMES = 28_800  # 8 min × 60 fps — matches longer than this are rejected

# Damage action-state range (DAMAGE_HI_1 … DAMAGE_FLY_ROLL in actionstates.txt).
# Used as hitstun signal when the hitlag field is absent in older replay files.
DAMAGE_STATE_MIN = 75
DAMAGE_STATE_MAX = 91
HITSTUN_STATE_WEIGHT = 5.0  # per-frame weight when using state fallback

# ---------------------------------------------------------------------------
# State-machine detector constants
# ---------------------------------------------------------------------------

MIN_COMBO_HITS = 5                   # discrete hits required (opening + 4 more)
MAX_HIT_GAP_FRAMES = 300             # 5 s max gap between hits (normal)
MAX_HIT_GAP_OFFSTAGE_FRAMES = 600    # 10 s max gap (victim offstage / recovering)
MAX_ATTACKER_LARGE_HITS = 2          # attacker may absorb at most this many large hits
MULTIHIT_WINDOW = 10                 # frames — damage within this window = same hit
CLIP_PRE_FRAMES = 210                # 3.5 s before first hit
CLIP_POST_FRAMES = 150               # 2.5 s after last hit (onstage)
CLIP_POST_OFFSTAGE_FRAMES = 300      # 5 s after last hit (victim offstage/recovering)
CLIP_POST_KILL_FRAMES = 180          # 3 s after kill (extra time for death animation)

# Tournament-legal stage edge X half-widths (stage_id → units from centre).
# A victim whose |x| exceeds this value is considered offstage.
STAGE_EDGES: dict[int, float] = {
    2:  63.35,    # Fountain of Dreams
    8:  55.91,    # Yoshi's Story
    18: 87.75,    # Pokémon Stadium
    28: 77.27,    # Dreamland 64
    31: 68.4,     # Battlefield
    32: 85.56,    # Final Destination
}
DEFAULT_STAGE_EDGE = 70.0  # fallback for unknown stages

# ---------------------------------------------------------------------------
# Rolling-window detector constants (fallback only)
# ---------------------------------------------------------------------------

COMBO_WINDOW = 240     # rolling window size (4 s at 60 fps)
COMBO_THRESHOLD = 100  # minimum rolling hitstun sum to consider a combo active
PREPOST = 120          # 2-second buffer added before/after a detected combo

_DATA_DIR = Path(__file__).resolve().parent / "data"


# ---------------------------------------------------------------------------
# Reference-data loaders
# ---------------------------------------------------------------------------

def actionstate_dict() -> dict:
    """Return a mapping of numeric action-state IDs to human-readable names."""
    result = {}
    with open(_DATA_DIR / "actionstates.txt", "r") as f:
        for count, line in enumerate(f):
            result[count] = line.rstrip()
    return result


def attack_dict() -> dict:
    """Return a mapping of numeric attack IDs to human-readable attack names."""
    result = {}
    with open(_DATA_DIR / "attacks.txt", "r") as f:
        for count, line in enumerate(f, start=1):
            result[count] = line.rstrip()
    return result


# ---------------------------------------------------------------------------
# peppi-py helpers
# ---------------------------------------------------------------------------

def _to_numpy(arr: Any, length: int, default: float = 0.0) -> np.ndarray:
    """
    Convert a peppi-py PyArrow array to a numpy array.

    Returns an array of `default` values if the field is absent (None),
    which happens for optional fields not present in older replay files.
    """
    if arr is None:
        return np.full(length, default, dtype=np.float32)
    return arr.to_numpy(zero_copy_only=False).astype(np.float32)


def _player_name(game: Any, port_index: int) -> str:
    """
    Return the netplay display name for a player, falling back to 'P1'/'P2'.

    port_index is the 0-based index into game.frames.ports (not the Melee
    port number), matching the indices returned by _occupied_ports.
    """
    try:
        players = (game.metadata or {}).get("players", {})
        name = players.get(str(port_index), {}).get("names", {}).get("netplay") or ""
    except Exception:
        name = ""
    # Sanitise for use in file names: keep alphanumerics, dashes, underscores
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")
    return safe or f"P{port_index + 1}"


def _occupied_ports(game: Any) -> list[int]:
    """Return port indices for active players in this game.

    peppi-py stores only occupied ports in the ports tuple, so the tuple
    length equals the number of active players (2 for a 1v1 match).
    """
    return list(range(len(game.frames.ports)))


def _hitstun_array(post: Any, total_frames: int) -> np.ndarray:
    """
    Build a per-frame hitstun signal array.

    Prefers the hitlag field (Slippi v3.5+). Falls back to DAMAGE_* action
    state detection for older replays where hitlag is absent.
    """
    if post.hitlag is not None:
        return _to_numpy(post.hitlag, total_frames)
    state = _to_numpy(post.state, total_frames)
    return np.where(
        (state >= DAMAGE_STATE_MIN) & (state <= DAMAGE_STATE_MAX),
        HITSTUN_STATE_WEIGHT,
        0.0,
    ).astype(np.float32)


def _record_move(
    combo_moves: list,
    adict: dict,
    sdict: dict,
    last_attack: float,
) -> None:
    """Append a move name to combo_moves if not already present."""
    lal = int(last_attack)
    if lal <= 0:
        return
    try:
        name = adict[lal] if lal < 30 else sdict[lal]
        if name and name not in combo_moves:
            combo_moves.append(name)
    except (KeyError, TypeError):
        pass


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
        The parsed peppi_py Game object if valid.

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
        game = read_slippi(slp_path)
    except Exception as exc:
        raise ValueError(f"Could not parse replay file: {exc}") from exc

    active_ports = _occupied_ports(game)
    if len(active_ports) != 2:
        raise ValueError(
            f"Only 1v1 matches are supported. "
            f"Found {len(active_ports)} active player(s)."
        )

    metadata = game.metadata or {}
    duration = metadata.get("lastFrame") or len(game.frames.id)
    if duration > MAX_MATCH_FRAMES:
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
        A non-negative float score.
    """
    if dsum <= 0 or hsum <= 0:
        return 0.0

    A, B = 1.0, 0.4   # hitstun weight / exponent
    C, D = 1.0, 1.25  # damage weight / exponent
    return A * (hsum ** B) * C * (dsum ** D)


# ---------------------------------------------------------------------------
# State-machine detector (primary)
# ---------------------------------------------------------------------------

def _detect_combos_statemachine(
    game: Any,
    comboer: int,
    victim: int,
    adict: dict,
    sdict: dict,
    stage_edge: float,
    padding_frames: int,
) -> list:
    """
    Detect combos using an explicit state machine.

    A qualifying combo requires:
    - >= MIN_COMBO_HITS discrete hits on the victim
    - Gap between consecutive hits <= MAX_HIT_GAP_FRAMES (normal) or
      MAX_HIT_GAP_OFFSTAGE_FRAMES (victim offstage/recovering)
    - Attacker absorbs at most MAX_ATTACKER_LARGE_HITS distinct hits

    Multi-hit moves (e.g. Fox dair) count as one discrete hit by suppressing
    repeated damage events within MULTIHIT_WINDOW frames.

    Returns:
        List of combo dicts with keys: start_frame, end_frame, score,
        total_damage, hit_count, is_kill, moves, combo_count.
    """
    frames = game.frames
    total_frames = len(frames.id)

    victim_post = frames.ports[victim].leader.post
    comboer_post = frames.ports[comboer].leader.post

    v_damage     = _to_numpy(victim_post.percent, total_frames)
    v_stocks     = _to_numpy(victim_post.stocks, total_frames)
    v_position_x = _to_numpy(victim_post.position.x, total_frames)
    v_hitstun    = _hitstun_array(victim_post, total_frames)

    c_damage      = _to_numpy(comboer_post.percent, total_frames)
    c_last_attack = _to_numpy(comboer_post.last_attack_landed, total_frames)
    c_combo_count = _to_numpy(comboer_post.combo_count, total_frames)

    # State constants
    NEUTRAL, TRACKING, CONFIRMED = 0, 1, 2
    state = NEUTRAL

    # Combo accumulators
    hit_count = 0
    large_hits_on_attacker = 0
    first_hit_frame = 0
    last_hit_frame = 0
    frames_since_last_hit = 0
    combo_damage = 0.0
    combo_hsum = 0.0
    combo_moves: list = []
    is_kill = False

    # Multi-hit suppression timers (run continuously, independent of state)
    frames_since_victim_damage = MULTIHIT_WINDOW + 1
    frames_since_attacker_damage = MULTIHIT_WINDOW + 1

    victim_prev_damage = float(v_damage[STARTUP_FRAMES])
    victim_prev_stocks = float(v_stocks[STARTUP_FRAMES])
    comboer_prev_damage = float(c_damage[STARTUP_FRAMES])

    combos: list = []

    for fcount in range(STARTUP_FRAMES, total_frames):
        hs = float(v_hitstun[fcount])
        curr_victim_damage = float(v_damage[fcount])
        curr_victim_stocks = float(v_stocks[fcount])
        curr_comboer_damage = float(c_damage[fcount])

        victim_damage_delta = max(0.0, curr_victim_damage - victim_prev_damage)
        comboer_damage_delta = max(0.0, curr_comboer_damage - comboer_prev_damage)
        stock_lost = curr_victim_stocks < victim_prev_stocks

        is_offstage = abs(float(v_position_x[fcount])) > stage_edge
        max_gap = MAX_HIT_GAP_OFFSTAGE_FRAMES if is_offstage else MAX_HIT_GAP_FRAMES

        # --- Discrete hit detection (multi-hit suppression) ---
        new_hit_on_victim = False
        if victim_damage_delta > 0:
            if frames_since_victim_damage > MULTIHIT_WINDOW:
                new_hit_on_victim = True
            frames_since_victim_damage = 0
        else:
            frames_since_victim_damage += 1

        new_hit_on_attacker = False
        if comboer_damage_delta > 0:
            if frames_since_attacker_damage > MULTIHIT_WINDOW:
                new_hit_on_attacker = True
            frames_since_attacker_damage = 0
        else:
            frames_since_attacker_damage += 1

        # --- Advance in-progress combo counters ---
        if state in (TRACKING, CONFIRMED):
            frames_since_last_hit += 1
            combo_hsum += hs

            if new_hit_on_attacker:
                large_hits_on_attacker += 1

        # --- Determine whether the current combo should end ---
        if state in (TRACKING, CONFIRMED):
            gap_exceeded = frames_since_last_hit > max_gap
            attacker_broken = large_hits_on_attacker > MAX_ATTACKER_LARGE_HITS
            end_of_match = fcount == total_frames - 1

            should_end = stock_lost or gap_exceeded or attacker_broken or end_of_match
            should_emit = state == CONFIRMED and not attacker_broken

            if should_end:
                if should_emit:
                    rel_first = first_hit_frame - STARTUP_FRAMES
                    start = max(0, rel_first - CLIP_PRE_FRAMES)
                    if stock_lost:
                        rel_end = fcount - STARTUP_FRAMES
                        end = min(
                            total_frames - STARTUP_FRAMES - 1,
                            rel_end + CLIP_POST_KILL_FRAMES,
                        )
                    else:
                        rel_last = last_hit_frame - STARTUP_FRAMES
                        victim_offstage = (
                            abs(float(v_position_x[last_hit_frame])) > stage_edge
                        )
                        post = CLIP_POST_OFFSTAGE_FRAMES if victim_offstage else CLIP_POST_FRAMES
                        end = min(
                            total_frames - STARTUP_FRAMES - 1,
                            rel_last + post,
                        )
                    combos.append({
                        "start_frame": start,
                        "end_frame": end,
                        "score": comboscore(combo_damage, combo_hsum),
                        "total_damage": round(combo_damage, 1),
                        "hit_count": hit_count,
                        "is_kill": stock_lost,
                        "moves": list(combo_moves),
                        "combo_count": int(c_combo_count[fcount]),
                        "comboer_port": comboer,
                    })

                # Reset combo state
                state = NEUTRAL
                hit_count = 0
                large_hits_on_attacker = 0
                frames_since_last_hit = 0
                combo_damage = 0.0
                combo_hsum = 0.0
                combo_moves = []
                is_kill = False

        # --- Start new combo or accumulate hits ---
        # Don't start a new combo on the same frame the victim lost a stock —
        # the killing blow would otherwise immediately seed a new TRACKING window.
        if state == NEUTRAL and not stock_lost:
            if new_hit_on_victim:
                state = TRACKING
                hit_count = 1
                first_hit_frame = fcount
                last_hit_frame = fcount
                frames_since_last_hit = 0
                combo_damage = victim_damage_delta
                combo_hsum = hs
                combo_moves = []
                large_hits_on_attacker = 0
                _record_move(combo_moves, adict, sdict, c_last_attack[fcount])

        elif state in (TRACKING, CONFIRMED):
            if new_hit_on_victim:
                hit_count += 1
                last_hit_frame = fcount
                frames_since_last_hit = 0
                combo_damage += victim_damage_delta
                _record_move(combo_moves, adict, sdict, c_last_attack[fcount])
                if hit_count >= MIN_COMBO_HITS:
                    state = CONFIRMED

        # --- Update previous-frame values ---
        victim_prev_damage = 0.0 if stock_lost else curr_victim_damage
        if stock_lost:
            victim_prev_stocks = curr_victim_stocks
        comboer_prev_damage = curr_comboer_damage

    return combos


# ---------------------------------------------------------------------------
# Rolling-window detector (fallback)
# ---------------------------------------------------------------------------

def _detect_combos_rolling_window(
    game: Any,
    comboer: int,
    victim: int,
    adict: dict,
    sdict: dict,
    padding_frames: int,
) -> list:
    """
    Detect combo windows using a rolling hitstun sum (fallback detector).

    Used when the state-machine detector finds no combos. Triggers whenever
    the rolling hitstun sum over COMBO_WINDOW frames exceeds COMBO_THRESHOLD.

    Returns:
        List of combo dicts with the same keys as the state-machine detector.
    """
    frames = game.frames
    total_frames = len(frames.id)

    victim_post = frames.ports[victim].leader.post
    comboer_post = frames.ports[comboer].leader.post

    v_hitstun = _hitstun_array(victim_post, total_frames)
    v_damage  = _to_numpy(victim_post.percent, total_frames)
    v_stocks  = _to_numpy(victim_post.stocks, total_frames)

    c_last_attack = _to_numpy(comboer_post.last_attack_landed, total_frames)
    c_combo_count = _to_numpy(comboer_post.combo_count, total_frames)

    window: deque = deque()
    window_hs = 0.0

    in_combo = False
    combo_start = 0
    combo_damage = 0.0
    combo_hsum = 0.0
    combo_hits = 0
    combo_moves: list = []

    victim_prev_damage = float(v_damage[STARTUP_FRAMES])
    victim_prev_stocks = float(v_stocks[STARTUP_FRAMES])

    combos: list = []

    for fcount in range(STARTUP_FRAMES, total_frames):
        hs = float(v_hitstun[fcount])
        curr_damage = float(v_damage[fcount])
        curr_stocks = float(v_stocks[fcount])

        window.append(hs)
        window_hs += hs
        if len(window) > COMBO_WINDOW:
            window_hs -= window.popleft()

        damage_delta = max(0.0, curr_damage - victim_prev_damage)
        stock_lost = curr_stocks < victim_prev_stocks

        if in_combo:
            combo_hsum += hs

            if damage_delta > 0:
                combo_damage += damage_delta
                combo_hits += 1
                _record_move(combo_moves, adict, sdict, c_last_attack[fcount])

            end_of_match = fcount == total_frames - 1
            if window_hs < COMBO_THRESHOLD or stock_lost or end_of_match:
                rel = fcount - STARTUP_FRAMES
                start_frame = max(0, (combo_start - STARTUP_FRAMES) - padding_frames)
                end_frame = min(rel + padding_frames, total_frames - STARTUP_FRAMES - 1)
                combos.append({
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "score": comboscore(combo_damage, combo_hsum),
                    "total_damage": round(combo_damage, 1),
                    "hit_count": combo_hits,
                    "is_kill": stock_lost,
                    "moves": list(combo_moves),
                    "combo_count": int(c_combo_count[fcount]),
                })
                in_combo = False
                combo_damage = 0.0
                combo_hsum = 0.0
                combo_hits = 0
                combo_moves = []
        else:
            if window_hs >= COMBO_THRESHOLD:
                in_combo = True
                combo_start = fcount

        victim_prev_damage = 0.0 if stock_lost else curr_damage
        if stock_lost:
            victim_prev_stocks = curr_stocks

    return combos


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_combo_clips(
    slp_path: str,
    max_combos: int = 5,
    padding_frames: int = PREPOST,
) -> list:
    """
    Detect, score, and return the best combo windows from a .slp replay.

    Runs the state-machine detector first. Falls back to the rolling-window
    detector if no combos are found (e.g. older replay formats).

    Combos are detected for both player directions and ranked by score so
    the most highlight-worthy clips always come first.

    Args:
        slp_path: Path to the .slp replay file.
        max_combos: Maximum number of combos to return.
        padding_frames: Extra frames buffered in the rolling-window fallback.

    Returns:
        List of combo dicts sorted by score descending, each with keys:
        start_frame, end_frame, score, total_damage, hit_count, is_kill,
        moves, combo_count.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If validation fails (not 1v1, too long, bad file, etc.).
    """
    game = validate_slp(slp_path)
    adict = attack_dict()
    sdict = actionstate_dict()

    occupied = _occupied_ports(game)
    port_a, port_b = occupied[0], occupied[1]
    stage_edge = STAGE_EDGES.get(game.start.stage, DEFAULT_STAGE_EDGE)

    all_combos: list = []
    for comboer, victim in [(port_a, port_b), (port_b, port_a)]:
        all_combos.extend(
            _detect_combos_statemachine(
                game, comboer, victim, adict, sdict, stage_edge, padding_frames
            )
        )

    # Fall back to rolling-window if state machine found nothing
    if not all_combos:
        for comboer, victim in [(port_a, port_b), (port_b, port_a)]:
            all_combos.extend(
                _detect_combos_rolling_window(
                    game, comboer, victim, adict, sdict, padding_frames
                )
            )

    all_combos = [c for c in all_combos if c["hit_count"] > 0]
    all_combos.sort(key=lambda c: c["score"], reverse=True)

    for combo in all_combos:
        combo["comboer_name"] = _player_name(game, combo["comboer_port"])

    return all_combos[:max_combos]
