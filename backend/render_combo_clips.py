import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from jaeyooncode.combos import get_combo_clips
from slp2mp4_tools import convert_slp_to_mp4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render combo highlights: .slp -> full mp4 -> combo clip mp4 files"
    )
    parser.add_argument("slp_path", help="Input .slp path")
    parser.add_argument(
        "-o",
        "--output-dir",
        default="comboVids",
        help="Directory for combo clip outputs",
    )
    parser.add_argument(
        "--max-combos",
        type=int,
        default=5,
        help="Maximum number of combos to export",
    )
    parser.add_argument(
        "--padding-frames",
        type=int,
        default=120,
        help="Extra frames before combo start (also used in detector call)",
    )
    parser.add_argument(
        "--startup-offset-frames",
        type=int,
        default=123,
        help="Startup frames removed by py-slippi before indexing",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=60.0,
        help="Frame rate used for frame->time conversion",
    )
    parser.add_argument(
        "--full-video-path",
        default=None,
        help="Existing full-match mp4 path to skip replay rendering",
    )
    parser.add_argument(
        "--full-video-output-dir",
        default="generatedVids",
        help="Where to render the full match mp4 when --full-video-path is not given",
    )
    return parser.parse_args()


def _ffmpeg_from_slp2mp4_config() -> str | None:
    config_path = Path.home() / ".slp2mp4.toml"
    if not config_path.exists():
        return None

    content = config_path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r'^\s*ffmpeg\s*=\s*"([^"]+)"', content, flags=re.MULTILINE)
    if match is None:
        return None

    ffmpeg_path = Path(match.group(1)).expanduser()
    return str(ffmpeg_path) if ffmpeg_path.exists() else None


def resolve_ffmpeg_executable() -> str:
    command = shutil.which("ffmpeg")
    if command:
        return command

    configured = _ffmpeg_from_slp2mp4_config()
    if configured:
        return configured

    raise RuntimeError(
        "Could not find ffmpeg executable. Either add ffmpeg to PATH or set "
        "`paths.ffmpeg` in ~/.slp2mp4.toml."
    )


def normalize_name_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return "".join(ch for ch in normalized if ch.isalnum())


def find_matching_video(output_dir: Path, slp_stem: str) -> Path | None:
    candidates = sorted(output_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None

    target = normalize_name_token(slp_stem)
    for candidate in candidates:
        if target in normalize_name_token(candidate.stem):
            return candidate
    return candidates[0]


def frame_to_seconds(frame: int, startup_offset_frames: int, fps: float) -> float:
    return (frame + startup_offset_frames) / fps


def trim_clip(ffmpeg_cmd: str, full_video: Path, output_clip: Path, start_s: float, end_s: float) -> None:
    output_clip.parent.mkdir(parents=True, exist_ok=True)

    fast_copy_command = [
        ffmpeg_cmd,
        "-y",
        "-ss",
        f"{start_s:.3f}",
        "-to",
        f"{end_s:.3f}",
        "-i",
        str(full_video),
        "-c",
        "copy",
        str(output_clip),
    ]
    fast_result = subprocess.run(fast_copy_command, capture_output=True, text=True, check=False)
    if fast_result.returncode == 0 and output_clip.exists():
        return

    # Fallback for files where copy trim cannot cut at non-keyframes.
    precise_encode_command = [
        ffmpeg_cmd,
        "-y",
        "-ss",
        f"{start_s:.3f}",
        "-to",
        f"{end_s:.3f}",
        "-i",
        str(full_video),
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output_clip),
    ]
    precise_result = subprocess.run(precise_encode_command, capture_output=True, text=True, check=False)
    if precise_result.returncode != 0:
        raise RuntimeError(
            "Failed to trim combo clip.\n"
            f"copy stderr:\n{fast_result.stderr}\n"
            f"encode stderr:\n{precise_result.stderr}"
        )


def render_combo_clips(
    slp_path: str,
    output_dir: str,
    max_combos: int,
    padding_frames: int,
    startup_offset_frames: int,
    fps: float,
    full_video_path: str | None,
    full_video_output_dir: str,
) -> Dict[str, object]:
    slp_file = Path(slp_path).expanduser().resolve()
    if not slp_file.exists():
        raise FileNotFoundError(f"Input replay not found: {slp_file}")

    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if full_video_path:
        full_video = Path(full_video_path).expanduser().resolve()
        if not full_video.exists():
            guessed = find_matching_video(full_video.parent, slp_file.stem)
            if guessed is not None:
                full_video = guessed
    else:
        full_video_dir = Path(full_video_output_dir).expanduser().resolve()
        guessed = find_matching_video(full_video_dir, slp_file.stem)
        if guessed is not None:
            full_video = guessed
        else:
            full_video = Path(convert_slp_to_mp4(str(slp_file), full_video_output_dir)).resolve()
    if not full_video.exists():
        raise FileNotFoundError(f"Full video file not found: {full_video}")

    clips = get_combo_clips(
        str(slp_file),
        max_combos=max_combos,
        padding_frames=padding_frames,
    )
    ffmpeg_cmd = resolve_ffmpeg_executable()

    created_files: List[str] = []
    metadata: List[Dict[str, object]] = []

    for index, clip in enumerate(clips, start=1):
        start_frame = int(clip["start_frame"])
        end_frame = int(clip["end_frame"])
        start_s = frame_to_seconds(start_frame, startup_offset_frames, fps)
        end_s = frame_to_seconds(end_frame, startup_offset_frames, fps)
        if end_s <= start_s:
            continue

        clip_name = f"{slp_file.stem}_combo_{index:02d}.mp4"
        clip_path = out_dir / clip_name
        trim_clip(ffmpeg_cmd, full_video, clip_path, start_s, end_s)

        created_files.append(str(clip_path))
        metadata.append(
            {
                "index": index,
                "output": str(clip_path),
                "start_frame": start_frame,
                "end_frame": end_frame,
                "start_seconds": start_s,
                "end_seconds": end_s,
                "score": clip["score"],
                "total_damage": clip["total_damage"],
                "hit_count": clip["hit_count"],
                "is_kill": clip["is_kill"],
                "moves": clip["moves"],
            }
        )

    metadata_path = out_dir / f"{slp_file.stem}_combo_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return {
        "slp_file": str(slp_file),
        "full_video": str(full_video),
        "clip_count": len(created_files),
        "clips": created_files,
        "metadata_path": str(metadata_path),
    }


def main() -> None:
    args = parse_args()
    result = render_combo_clips(
        slp_path=args.slp_path,
        output_dir=args.output_dir,
        max_combos=args.max_combos,
        padding_frames=args.padding_frames,
        startup_offset_frames=args.startup_offset_frames,
        fps=args.fps,
        full_video_path=args.full_video_path,
        full_video_output_dir=args.full_video_output_dir,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
