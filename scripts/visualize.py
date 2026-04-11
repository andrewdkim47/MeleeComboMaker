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
import slippi as slp

from melee.detection import actionstate_dict, attack_dict, comboscore, COMBO_WINDOW


def record_hs(slp_path: str, plotname: str, out_dir: str = "test_out") -> None:
    """
    Generate hitstun, damage, and combo score plots for a replay.

    Args:
        slp_path: Path to the .slp replay file.
        plotname: Base name for output PNG files.
        out_dir: Directory to write plots into.
    """
    game = slp.Game(slp_path)
    frames = game.frames[123:]

    p0_name = (game.metadata.players[0].netplay_name or "P1") if game.metadata.players[0] else "P1"
    p1_name = (game.metadata.players[1].netplay_name or "P2") if game.metadata.players[1] else "P2"

    p0_wind, p0_wsum = [], 0.0
    p1_wind, p1_wsum = [], 0.0
    p0_dwind, p0_dsum, p0_hp = [], 0.0, 0.0
    p1_dwind, p1_dsum, p1_hp = [], 0.0, 0.0

    p0_map, p0_dmap = [], []
    p1_map, p1_dmap = [], []
    p0_scores, p1_scores = [], []
    fcount = []

    for fc, f in enumerate(frames):
        fcount.append(fc)

        p0_ths = float(f.ports[0].leader.post.hit_stun or 0)
        p0_tdam = float(f.ports[0].leader.post.damage or 0)
        p1_ths = float(f.ports[1].leader.post.hit_stun or 0)
        p1_tdam = float(f.ports[1].leader.post.damage or 0)

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

    print(f"Saved plots to {out_dir}/{plotname}.png and {out_dir}/{plotname}_cscores.png")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Visualize combo intensity for a .slp replay")
    parser.add_argument("slp_path", help="Input .slp replay path")
    parser.add_argument("--out-dir", default="test_out", help="Output directory for PNGs")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    stem = Path(args.slp_path).stem
    record_hs(args.slp_path, stem, args.out_dir)
