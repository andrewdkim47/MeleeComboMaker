"""
CLI: render combo clips from a .slp replay file.

Usage:
    python scripts/render_clips.py <slp_path> [options]

Example:
    python scripts/render_clips.py tests/replays/vs_falcon.slp -o comboVids --max-combos 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from melee.detection import get_combo_clips
from melee.renderer import convert_slp_to_mp4
from melee.trimmer import find_matching_video, frame_to_seconds, resolve_ffmpeg_executable, trim_clip


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render combo highlights from a .slp replay"
    )
    parser.add_argument("slp_path", help="Input .slp replay path")
    parser.add_argument("-o", "--output-dir", default="comboVids",
                        help="Directory for combo clip outputs (default: comboVids)")
    parser.add_argument("--max-combos", type=int, default=5,
                        help="Maximum combos to export (default: 5)")
    parser.add_argument("--padding-frames", type=int, default=120,
                        help="Buffer frames before/after each combo (default: 120)")
    parser.add_argument("--full-video-path", default=None,
                        help="Use an existing full-match mp4 to skip re-rendering")
    parser.add_argument("--full-video-output-dir", default="generatedVids",
                        help="Where to write the full match render (default: generatedVids)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    slp_file = Path(args.slp_path).expanduser().resolve()
    if not slp_file.exists():
        print(f"Error: file not found: {slp_file}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve full-match video
    full_video_dir = Path(args.full_video_output_dir).expanduser().resolve()
    if args.full_video_path:
        full_video = Path(args.full_video_path).expanduser().resolve()
    else:
        full_video = find_matching_video(full_video_dir, slp_file.stem)
        if full_video is None:
            print("Rendering full match via Dolphin (this may take a few minutes)...")
            full_video = Path(convert_slp_to_mp4(str(slp_file), str(full_video_dir)))

    if not full_video.exists():
        print(f"Error: full video not found: {full_video}", file=sys.stderr)
        sys.exit(1)

    # Detect combos
    print("Detecting combos...")
    clips = get_combo_clips(
        str(slp_file),
        max_combos=args.max_combos,
        padding_frames=args.padding_frames,
    )
    print(f"Found {len(clips)} combo(s).")

    ffmpeg_cmd = resolve_ffmpeg_executable()
    created_files = []
    metadata = []

    for index, clip in enumerate(clips, start=1):
        start_s = frame_to_seconds(int(clip["start_frame"]), 123, 60.0)
        end_s = frame_to_seconds(int(clip["end_frame"]), 123, 60.0)
        if end_s <= start_s:
            continue

        clip_path = out_dir / f"{slp_file.stem}_combo_{index:02d}.mp4"
        print(f"  Trimming combo {index}: {start_s:.1f}s – {end_s:.1f}s "
              f"(dmg={clip['total_damage']}, hits={clip['hit_count']}, "
              f"kill={clip['is_kill']})...")
        trim_clip(ffmpeg_cmd, full_video, clip_path, start_s, end_s)

        created_files.append(str(clip_path))
        metadata.append({
            "index": index,
            "output": str(clip_path),
            "start_frame": clip["start_frame"],
            "end_frame": clip["end_frame"],
            "start_seconds": start_s,
            "end_seconds": end_s,
            "score": clip["score"],
            "total_damage": clip["total_damage"],
            "hit_count": clip["hit_count"],
            "is_kill": clip["is_kill"],
            "moves": clip["moves"],
        })

    metadata_path = out_dir / f"{slp_file.stem}_combo_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    result = {
        "slp_file": str(slp_file),
        "full_video": str(full_video),
        "clip_count": len(created_files),
        "clips": created_files,
        "metadata_path": str(metadata_path),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
