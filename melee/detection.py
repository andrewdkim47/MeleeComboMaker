"""
Combo detection engine.

Parses .slp replay files frame-by-frame to detect, score, and return
the best combo windows as (start_frame, end_frame) ranges.

Uses peppi-py (Rust-backed) for replay parsing. peppi-py uses a
struct-of-arrays layout — all frame data for a field is stored as a
single columnar array rather than per-frame objects. We convert these
to numpy arrays upfront for efficient iteration.
"""

from collections import deque
from enum import IntEnum
from pathlib import Path
from typing import Any, Optional

import numpy as np
from peppi_py import read_slippi

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STARTUP_FRAMES = 123       # frames skipped at game start (ready, go! sequence)
COMBO_WINDOW = 240         # rolling window size (4 s at 60 fps) for detection
COMBO_THRESHOLD = 100      # minimum rolling hitstun sum to consider a combo active
PREPOST = 120              # 2-second buffer added before/after a detected combo
MAX_MATCH_FRAMES = 28_800  # 8 min × 60 s × 60 fps — matches longer than this are rejected

# Damage action-state range (DAMAGE_HI_1 … DAMAGE_FLY_ROLL in actionstates.txt).
# Used as a hitstun signal when the hitlag field is absent in older replay files.
DAMAGE_STATE_MIN = 75
DAMAGE_STATE_MAX = 91
# Per-frame weight applied to each damage-state frame so that a single
# 20-frame hit roughly meets COMBO_THRESHOLD (20 × 5 = 100).
HITSTUN_STATE_WEIGHT = 5.0

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
# peppi-py helpers
# ---------------------------------------------------------------------------

def _to_numpy(arr, length: int, default: float = 0.0) -> np.ndarray:
    """
    Convert a peppi-py PyArrow array to a numpy array.

    Returns an array of `default` values if the field is absent (None),
    which happens for optional fields not present in older replay files.
    """
    if arr is None:
        return np.full(length, default, dtype=np.float32)
    return arr.to_numpy(zero_copy_only=False).astype(np.float32)


def _occupied_ports(game: Any) -> list[int]:
    """Return port indices for active players in this game.

    peppi-py stores only occupied ports in the ports tuple, so the tuple
    length equals the number of active players (2 for a 1v1 match).
    """
    return list(range(len(game.frames.ports)))


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

    # Duration: peppi-py metadata is a raw dict; fall back to frame count
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
# Internal detection
# ---------------------------------------------------------------------------

def _detect_combos_for_ports(
    game: Any,
    comboer: int,
    victim: int,
    adict: dict,
    sdict: dict,
    padding_frames: int,
) -> list:
    """
    Detect combo windows where the player on port `comboer` attacks port `victim`.

    Extracts all needed frame data as numpy arrays upfront, then iterates
    over frames starting after STARTUP_FRAMES.

    Returns:
        List of combo dicts with keys:
        start_frame, end_frame, score, total_damage, hit_count, is_kill,
        moves, combo_count.
    """
    frames = game.frames
    total_frames = len(frames.id)

    victim_post = frames.ports[victim].leader.post
    comboer_post = frames.ports[comboer].leader.post

    # Extract columnar arrays to numpy upfront.
    # Prefer the `hitlag` field (Slippi post-frame byte 0x6 — "hitstun remaining
    # frames"), added in replay format v3.5. For older replays where it is absent
    # (None), fall back to action-state detection: assign HITSTUN_STATE_WEIGHT to
    # every frame the victim is in a DAMAGE_* state (states 75-91).
    if victim_post.hitlag is not None:
        v_hitstun = _to_numpy(victim_post.hitlag, total_frames)
    else:
        v_state = _to_numpy(victim_post.state, total_frames)
        v_hitstun = np.where(
            (v_state >= DAMAGE_STATE_MIN) & (v_state <= DAMAGE_STATE_MAX),
            HITSTUN_STATE_WEIGHT,
            0.0,
        ).astype(np.float32)
    v_damage = _to_numpy(victim_post.percent, total_frames)
    v_stocks = _to_numpy(victim_post.stocks, total_frames)
    v_airborne = _to_numpy(victim_post.airborne, total_frames)

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
    is_kill = False

    victim_prev_damage = float(v_damage[STARTUP_FRAMES])
    victim_prev_stocks = float(v_stocks[STARTUP_FRAMES])

    combos: list = []

    for fcount in range(STARTUP_FRAMES, total_frames):
        hs = float(v_hitstun[fcount])
        curr_damage = float(v_damage[fcount])
        curr_stocks = float(v_stocks[fcount])

        # Update rolling hitstun window
        window.append(hs)
        window_hs += hs
        if len(window) > COMBO_WINDOW:
            window_hs -= window.popleft()

        # Damage delta — ignore resets when victim respawns after death
        damage_delta = max(0.0, curr_damage - victim_prev_damage)

        # Detect stock loss (kill)
        stock_lost = curr_stocks < victim_prev_stocks

        if in_combo:
            combo_hsum += hs

            if damage_delta > 0:
                combo_damage += damage_delta
                combo_hits += 1
                lal = int(c_last_attack[fcount])
                if lal > 0:
                    try:
                        name = adict[lal] if lal < 30 else sdict[lal]
                        if name and name not in combo_moves:
                            combo_moves.append(name)
                    except (KeyError, TypeError):
                        pass

            if stock_lost:
                is_kill = True

            end_of_match = fcount == total_frames - 1
            if window_hs < COMBO_THRESHOLD or stock_lost or end_of_match:
                # Use relative frame index (startup frames removed) for
                # consistency with how render_clips.py converts to timestamps
                rel = fcount - STARTUP_FRAMES
                start_frame = max(0, (combo_start - STARTUP_FRAMES) - padding_frames)
                end_frame = min(rel + padding_frames, total_frames - STARTUP_FRAMES - 1)
                combos.append({
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "score": comboscore(combo_damage, combo_hsum),
                    "total_damage": round(combo_damage, 1),
                    "hit_count": combo_hits,
                    "is_kill": is_kill,
                    "moves": list(combo_moves),
                    "combo_count": int(c_combo_count[fcount]),
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

    Combos are detected for both player directions and ranked by score so
    the most highlight-worthy clips always come first regardless of which
    port the attacking player is on.

    Args:
        slp_path: Path to the .slp replay file.
        max_combos: Maximum number of combos to return.
        padding_frames: Extra frames buffered before combo start and after end.

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

    all_combos: list = []
    for comboer, victim in [(port_a, port_b), (port_b, port_a)]:
        all_combos.extend(
            _detect_combos_for_ports(game, comboer, victim, adict, sdict, padding_frames)
        )

    all_combos = [c for c in all_combos if c["hit_count"] > 0]
    all_combos.sort(key=lambda c: c["score"], reverse=True)
    return all_combos[:max_combos]
