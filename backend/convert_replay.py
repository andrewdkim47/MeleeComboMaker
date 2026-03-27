import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from slp2mp4_tools import convert_slp_to_mp4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert .slp replay to .mp4")
    parser.add_argument("slp_path", help="Path to input .slp file")
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output .mp4 path or output directory",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = convert_slp_to_mp4(args.slp_path, args.output)
    print(f"Created video: {output}")


if __name__ == "__main__":
    main()
