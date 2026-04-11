"""
Clip trimming utilities.

Handles FFmpeg-based trimming of combo windows from a full-match video,
plus helpers for resolving the FFmpeg executable and matching video files.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from pathlib import Path


def resolve_ffmpeg_executable() -> str:
    """
    Return the path to the ffmpeg executable.

    Checks PATH first, then falls back to the path configured in
    ~/.slp2mp4.toml.

    Raises:
        RuntimeError: If ffmpeg cannot be found.
    """
    command = shutil.which("ffmpeg")
    if command:
        return command

    configured = _ffmpeg_from_slp2mp4_config()
    if configured:
        return configured

    raise RuntimeError(
        "Could not find ffmpeg. Add it to PATH or set `paths.ffmpeg` "
        "in ~/.slp2mp4.toml."
    )


def frame_to_seconds(frame: int, startup_offset_frames: int, fps: float) -> float:
    """Convert a frame index (with startup frames removed) to wall-clock seconds."""
    return (frame + startup_offset_frames) / fps


def find_matching_video(output_dir: Path, slp_stem: str) -> Path | None:
    """
    Search output_dir for an MP4 whose name matches slp_stem.

    Returns None (rather than a fallback guess) if no name match is found,
    to prevent accidentally using a cached video from a different replay.
    """
    candidates = sorted(
        output_dir.glob("*.mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    target = _normalize(slp_stem)
    for candidate in candidates:
        if target in _normalize(candidate.stem):
            return candidate
    return None


def trim_clip(
    ffmpeg_cmd: str,
    full_video: Path,
    output_clip: Path,
    start_s: float,
    end_s: float,
) -> None:
    """
    Trim a time range from full_video and write it to output_clip.

    Attempts a fast stream-copy first. Falls back to a full re-encode if
    the copy cannot cut at non-keyframe boundaries.

    Args:
        ffmpeg_cmd: Path or name of the ffmpeg executable.
        full_video: Source video file.
        output_clip: Destination clip file.
        start_s: Start time in seconds.
        end_s: End time in seconds.

    Raises:
        RuntimeError: If both trim strategies fail.
    """
    output_clip.parent.mkdir(parents=True, exist_ok=True)

    fast_cmd = [
        ffmpeg_cmd, "-y",
        "-ss", f"{start_s:.3f}",
        "-to", f"{end_s:.3f}",
        "-i", str(full_video),
        "-c", "copy",
        str(output_clip),
    ]
    fast = subprocess.run(fast_cmd, capture_output=True, text=True, check=False)
    if fast.returncode == 0 and output_clip.exists():
        return

    encode_cmd = [
        ffmpeg_cmd, "-y",
        "-ss", f"{start_s:.3f}",
        "-to", f"{end_s:.3f}",
        "-i", str(full_video),
        "-c:v", "libx264",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(output_clip),
    ]
    encode = subprocess.run(encode_cmd, capture_output=True, text=True, check=False)
    if encode.returncode != 0:
        raise RuntimeError(
            "Failed to trim clip.\n"
            f"copy stderr:\n{fast.stderr}\n"
            f"encode stderr:\n{encode.stderr}"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return "".join(ch for ch in normalized if ch.isalnum())


def _ffmpeg_from_slp2mp4_config() -> str | None:
    from pathlib import Path
    config_path = Path.home() / ".slp2mp4.toml"
    if not config_path.exists():
        return None
    content = config_path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r'^\s*ffmpeg\s*=\s*"([^"]+)"', content, flags=re.MULTILINE)
    if match is None:
        return None
    ffmpeg_path = Path(match.group(1)).expanduser()
    return str(ffmpeg_path) if ffmpeg_path.exists() else None
