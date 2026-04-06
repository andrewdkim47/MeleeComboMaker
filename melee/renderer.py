"""
Replay rendering wrapper.

Converts .slp replay files to .mp4 via the slp2mp4 CLI (Dolphin + FFmpeg).
Treat the underlying slp2mp4 tooling as a black box configured via
slp2mp4/config_windows.json.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


class ConversionError(RuntimeError):
    """Raised when slp2mp4 conversion fails."""


def _resolve_output_path(input_path: Path, output_path: Path | None) -> Path:
    if output_path is None:
        return input_path.with_suffix(".mp4")
    if output_path.suffix.lower() == ".mp4":
        return output_path
    output_path.mkdir(parents=True, exist_ok=True)
    return output_path / f"{input_path.stem}.mp4"


def _find_generated_output(output_dir: Path, input_stem: str) -> Path | None:
    """Search for the best-matching MP4 in output_dir by name similarity."""
    candidates = sorted(
        output_dir.glob("*.mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    stem_lower = input_stem.lower()
    for candidate in candidates:
        if stem_lower in candidate.stem.lower():
            return candidate
    return None


def convert_slp_to_mp4(slp_path: str, output_path: str | None = None) -> str:
    """
    Convert a single .slp replay file to .mp4.

    Tries the installed `slp2mp4` CLI first, then falls back to the local
    slp2mp4/slp-to-mp4.py script in this repository.

    Args:
        slp_path: Path to the input replay file.
        output_path: Output .mp4 path or directory. Defaults to input dir.

    Returns:
        Absolute path of the output .mp4 file.

    Raises:
        FileNotFoundError: If the input file does not exist.
        ValueError: If the input is not a .slp file.
        ConversionError: If slp2mp4 is not installed or conversion fails.
    """
    input_path = Path(slp_path).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input replay not found: {input_path}")
    if input_path.suffix.lower() != ".slp":
        raise ValueError(f"Expected a .slp file, got: {input_path.suffix}")

    output_arg = Path(output_path).expanduser().resolve() if output_path else None
    final_output = _resolve_output_path(input_path, output_arg)
    final_output.parent.mkdir(parents=True, exist_ok=True)

    command = shutil.which("slp2mp4")
    if command is not None:
        result = subprocess.run(
            [command, "-o", str(final_output.parent), "single", str(input_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        legacy_script = (
            Path(__file__).resolve().parents[1] / "slp2mp4" / "slp-to-mp4.py"
        )
        if not legacy_script.exists():
            raise ConversionError(
                "slp2mp4 CLI was not found and no local fallback script exists.\n"
                "Install dependencies with `pip install -r requirements.txt`."
            )
        result = subprocess.run(
            [sys.executable, str(legacy_script), str(input_path), str(final_output)],
            capture_output=True,
            text=True,
            check=False,
        )

    if result.returncode != 0:
        raise ConversionError(
            f"slp2mp4 failed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    if not final_output.exists():
        guessed = _find_generated_output(final_output.parent, input_path.stem)
        if guessed is not None:
            return str(guessed)
        raise ConversionError(
            "Conversion succeeded but output file was not found.\n"
            f"Expected: {final_output}\nstdout:\n{result.stdout}"
        )

    return str(final_output)
