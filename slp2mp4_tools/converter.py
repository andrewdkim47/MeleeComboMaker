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
    # slp2mp4 can prepend source directory names (e.g. "test_files falconpunch.mp4"),
    # so we search for a best matching file instead of assuming exact basename.
    candidates = sorted(output_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None

    stem_lower = input_stem.lower()
    for candidate in candidates:
        if stem_lower in candidate.stem.lower():
            return candidate
    return candidates[0]


def convert_slp_to_mp4(slp_path: str, output_path: str | None = None) -> str:
    """
    Convert a single .slp file to .mp4 using the `slp2mp4` CLI tool.

    Args:
        slp_path: Path to an input replay file ending in `.slp`.
        output_path: Optional output mp4 path or directory. If omitted, writes
            `<input_name>.mp4` next to the input replay.

    Returns:
        The absolute output `.mp4` path.

    Raises:
        FileNotFoundError: If input file does not exist.
        ValueError: If input file is not an `.slp` file.
        ConversionError: If `slp2mp4` is not installed or conversion fails.
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
        # Fallback to the legacy local script that already exists in this repository.
        legacy_script = Path(__file__).resolve().parents[1] / "slp2mp4" / "slp-to-mp4.py"
        if not legacy_script.exists():
            raise ConversionError(
                "slp2mp4 CLI was not found and no local fallback script exists.\n"
                "Install dependencies with `pip install -r requirements.txt`.\n"
                "Note: current upstream `slp2mp4` requires Python >= 3.11."
            )
        result = subprocess.run(
            [sys.executable, str(legacy_script), str(input_path), str(final_output)],
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        raise ConversionError(
            "slp2mp4 failed.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    if not final_output.exists():
        guessed_output = _find_generated_output(final_output.parent, input_path.stem)
        if guessed_output is not None:
            return str(guessed_output)
        raise ConversionError(
            "Conversion command succeeded but output was not found.\n"
            f"expected: {final_output}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    return str(final_output)
