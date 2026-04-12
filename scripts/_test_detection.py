"""Temporary smoke test for combo detection."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from melee.detection import get_combo_clips

replays = [
    "tests/replays/vs_falcon.slp",
    "tests/replays/Game_20260404T012751.slp",
]

for slp in replays:
    print(slp)
    combos = get_combo_clips(slp, max_combos=5)
    print(f"  Found {len(combos)} combo(s)")
    for i, c in enumerate(combos):
        print(
            f"  [{i}] comboer={c['comboer_name']}"
            f"  dmg={c['total_damage']}"
            f"  hits={c['hit_count']}"
            f"  kill={c['is_kill']}"
        )
    print()
