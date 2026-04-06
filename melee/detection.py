"""
Combo detection engine.

Parses .slp replay files frame-by-frame to detect, score, and return
the best combo windows as (start_frame, end_frame) ranges.
"""

import json
from collections import deque
from enum import IntEnum
from pathlib import Path
from typing import Any

import slippi as slp

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STARTUP_FRAMES = 123       # frames skipped at game start (ready, go! sequence)
COMBO_WINDOW = 240         # rolling window size (4 s at 60 fps) for detection
COMBO_THRESHOLD = 100      # minimum rolling hitstun sum to consider a combo active
PREPOST = 120              # 2-second buffer added before/after a detected combo
MAX_MATCH_FRAMES = 28_800  # 8 min × 60 s × 60 fps — matches longer than this are rejected

_DATA_DIR = Path(__file__).resolve().parent / "data"


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

    active_players = [p for p in game.metadata.players if p is not None]
    if len(active_players) != 2:
        raise ValueError(
            f"Only 1v1 matches are supported. "
            f"Found {len(active_players)} active player(s)."
        )

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
        A non-negative float score.
    """
    if dsum <= 0 or hsum <= 0:
        return 0.0

    A, B = 1.0, 0.4   # hitstun weight / exponent
    C, D = 1.0, 1.25  # damage weight / exponent
    return A * (hsum ** B) * C * (dsum ** D)


# ---------------------------------------------------------------------------
# Internal detection
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
    Detect combo windows where the player on port `comboer` attacks port `victim`.

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

        # Damage delta — ignore resets when victim respawns after death
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

        victim_prev_damage = 0.0 if stock_lost else curr_damage
        if curr_stocks is not None:
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

    Combos are detected for both player directions and ranked by score so
    the most highlight-worthy clips always come first regardless of which
    port the attacking player is on.

    Args:
        slp_path: Path to the .slp replay file.
        max_combos: Maximum number of combos to return.
        padding_frames: Extra frames buffered before combo start and after end.

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

    occupied = [i for i, p in enumerate(game.metadata.players) if p is not None]
    port_a, port_b = occupied[0], occupied[1]

    all_combos: list = []
    for comboer, victim in [(port_a, port_b), (port_b, port_a)]:
        all_combos.extend(
            _detect_combos_for_ports(frames, comboer, victim, adict, sdict, padding_frames)
        )

    all_combos = [c for c in all_combos if c["hit_count"] > 0]
    all_combos.sort(key=lambda c: c["score"], reverse=True)
    return all_combos[:max_combos]
