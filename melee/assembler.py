"""
Final video assembly.

Concatenates combo clips into a single highlight video and optionally
overlays background music.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def assemble_highlight(
    clip_paths: list[str],
    output_path: str,
    music_path: Optional[str] = None,
    fps: int = 60,
    music_volume: float = 0.3,
) -> str:
    """
    Concatenate combo clips into a single highlight video.

    Args:
        clip_paths: Ordered list of .mp4 clip file paths to concatenate.
        output_path: Destination path for the assembled highlight video.
        music_path: Optional background audio file. Trimmed to video duration
            and mixed in at `music_volume` level.
        fps: Output frame rate (default 60 to match Melee's native rate).
        music_volume: Volume multiplier for background music (0.0–1.0).

    Returns:
        Absolute path of the assembled output video.

    Raises:
        ValueError: If clip_paths is empty or any clip does not exist.
        RuntimeError: If video assembly fails.
    """
    from moviepy.editor import AudioFileClip, VideoFileClip, concatenate_videoclips

    if not clip_paths:
        raise ValueError("clip_paths must not be empty.")

    missing = [p for p in clip_paths if not Path(p).exists()]
    if missing:
        raise ValueError(f"Clip files not found: {missing}")

    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if output.exists():
        output.unlink()

    clips = [VideoFileClip(p) for p in clip_paths]
    try:
        final = concatenate_videoclips(clips, method="compose")

        if music_path:
            audio_path = Path(music_path)
            if not audio_path.exists():
                raise ValueError(f"Music file not found: {audio_path}")
            music = AudioFileClip(str(audio_path)).volumex(music_volume)
            music = music.subclip(0, min(music.duration, final.duration))
            final = final.set_audio(music)

        final.write_videofile(str(output), fps=fps, remove_temp=True)
    finally:
        for clip in clips:
            clip.close()

    return str(output)
