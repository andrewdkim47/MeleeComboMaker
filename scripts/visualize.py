"""
Developer tool: visualize combo intensity graphs for a .slp replay.

Generates hitstun/damage maps and combo score plots saved to the
specified output directory.

Usage:
    python scripts/visualize.py <slp_path> [--out-dir <dir>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib.pyplot as plt
import numpy as np
from peppi_py import read_slippi

from melee.detection import (
    comboscore,
    COMBO_WINDOW,
    STARTUP_FRAMES,
    DAMAGE_STATE_MIN,
    DAMAGE_STATE_MAX,
    HITSTUN_STATE_WEIGHT,
    _to_numpy,
)


def record_hs(slp_path: str, plotname: str, out_dir: str = "test_out") -> None:
    """
    Generate hitstun, damage, and combo score plots for a replay.

    Args:
        slp_path: Path to the .slp replay file.
        plotname: Base name for output PNG files.
        out_dir: Directory to write plots into.
    """
    game = read_slippi(slp_path)
    total_frames = len(game.frames.id)

    metadata = game.metadata or {}
    players = metadata.get("players", {})
    p0_name = players.get("0", {}).get("names", {}).get("netplay") or "P1"
    p1_name = players.get("1", {}).get("names", {}).get("netplay") or "P2"

    p0_post = game.frames.ports[0].leader.post
    p1_post = game.frames.ports[1].leader.post

    def _hitstun_array(post, length: int) -> np.ndarray:
        """Build a hitstun signal array, falling back to state detection."""
        if post.hitlag is not None:
            return _to_numpy(post.hitlag, length)
        state = _to_numpy(post.state, length)
        return np.where(
            (state >= DAMAGE_STATE_MIN) & (state <= DAMAGE_STATE_MAX),
            HITSTUN_STATE_WEIGHT,
            0.0,
        ).astype(np.float32)

    p0_hs_arr = _hitstun_array(p0_post, total_frames)
    p1_hs_arr = _hitstun_array(p1_post, total_frames)
    p0_dam_arr = _to_numpy(p0_post.percent, total_frames)
    p1_dam_arr = _to_numpy(p1_post.percent, total_frames)

    p0_wind, p0_wsum = [], 0.0
    p1_wind, p1_wsum = [], 0.0
    p0_dwind, p0_dsum, p0_hp = [], 0.0, 0.0
    p1_dwind, p1_dsum, p1_hp = [], 0.0, 0.0

    p0_map, p0_dmap = [], []
    p1_map, p1_dmap = [], []
    p0_scores, p1_scores = [], []
    fcount = []

    for fc in range(STARTUP_FRAMES, total_frames):
        fcount.append(fc - STARTUP_FRAMES)

        p0_ths = float(p0_hs_arr[fc])
        p0_tdam = float(p0_dam_arr[fc])
        p1_ths = float(p1_hs_arr[fc])
        p1_tdam = float(p1_dam_arr[fc])

        p0_dwind.append(max(0.0, p0_tdam - p0_hp))
        p0_dsum += p0_dwind[-1]
        p0_wind.append(p0_ths if p0_ths > 0.1 else 0.0)
        p0_wsum += p0_wind[-1]

        p1_dwind.append(max(0.0, p1_tdam - p1_hp))
        p1_dsum += p1_dwind[-1]
        p1_wind.append(p1_ths if p1_ths > 0.1 else 0.0)
        p1_wsum += p1_wind[-1]

        if len(p0_wind) > COMBO_WINDOW:
            p0_wsum -= p0_wind.pop(0)
            p0_dsum -= p0_dwind.pop(0)
        if len(p1_wind) > COMBO_WINDOW:
            p1_wsum -= p1_wind.pop(0)
            p1_dsum -= p1_dwind.pop(0)

        p0_map.append(p0_wsum)
        p0_dmap.append(p0_dsum)
        p1_map.append(p1_wsum)
        p1_dmap.append(p1_dsum)

        p0_scores.append(comboscore(p1_dsum, p1_wsum))
        p1_scores.append(comboscore(p0_dsum, p0_wsum))

        p0_hp = p0_tdam
        p1_hp = p1_tdam

    Path(out_dir).mkdir(parents=True, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(2)
    ax1.plot(fcount, p0_map, label=p0_name)
    ax1.plot(fcount, p1_map, label=p1_name, color="red")
    ax1.set_title("hitstun map")
    ax1.legend()
    ax2.plot(fcount, p0_dmap, label=p0_name)
    ax2.plot(fcount, p1_dmap, label=p1_name, color="red")
    ax2.set_title("damage map")
    ax2.legend()
    fig.savefig(f"{out_dir}/{plotname}.png")
    plt.close(fig)

    scorefig, ax = plt.subplots()
    ax.plot(fcount, p0_scores, label=p0_name)
    ax.plot(fcount, p1_scores, label=p1_name)
    ax.legend()
    scorefig.savefig(f"{out_dir}/{plotname}_cscores.png")
    plt.close(scorefig)

    print(f"Saved: {out_dir}/{plotname}.png and {out_dir}/{plotname}_cscores.png")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visualize combo intensity for a .slp replay"
    )
    parser.add_argument("slp_path", help="Input .slp replay path")
    parser.add_argument("--out-dir", default="test_out", help="Output directory for PNGs")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    stem = Path(args.slp_path).stem
    record_hs(args.slp_path, stem, args.out_dir)
