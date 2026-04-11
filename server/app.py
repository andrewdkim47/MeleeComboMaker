"""
Flask API server for MeleeComboMaker.

Endpoints:
  POST /api/upload              Accept .slp file, validate, store, return fileId
  POST /api/analyze             Detect combos in a stored file, return combo list
  POST /api/render              Start async render job for one combo clip
  POST /api/highlight           Assemble multiple rendered clips into a highlight
  GET  /api/status/<jobId>      Poll job status and progress
  GET  /api/download/<clipId>   Serve a rendered .mp4 for download
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

from melee.detection import get_combo_clips, validate_slp
from melee.assembler import assemble_highlight
from melee.renderer import convert_slp_to_mp4
from melee.trimmer import find_matching_video, frame_to_seconds, resolve_ffmpeg_executable, trim_clip

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)

_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = _ROOT / "uploads"
OUTPUT_DIR = _ROOT / "comboVids"
HIGHLIGHT_DIR = _ROOT / "highlights"
FULL_VIDEO_DIR = _ROOT / "generatedVids"

for _d in (UPLOAD_DIR, OUTPUT_DIR, HIGHLIGHT_DIR, FULL_VIDEO_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# In-memory state (single-process local app)
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_files: dict[str, Path] = {}       # fileId  -> .slp path
_combos: dict[str, list] = {}      # fileId  -> combo list
_clips: dict[str, Path] = {}       # clipId  -> .mp4 path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _update_job(job_id: str, status: str, progress: int, error: str | None = None) -> None:
    with _jobs_lock:
        _jobs[job_id]["status"] = status
        _jobs[job_id]["progress"] = progress
        if error:
            _jobs[job_id]["error"] = error


def _render_worker(job_id: str, slp_path: Path, combo: dict, clip_path: Path) -> None:
    """Background thread: render full video then trim one combo clip."""
    try:
        _update_job(job_id, "rendering", 10)

        full_video = find_matching_video(FULL_VIDEO_DIR, slp_path.stem)
        if full_video is None:
            full_video = Path(convert_slp_to_mp4(str(slp_path), str(FULL_VIDEO_DIR)))

        _update_job(job_id, "rendering", 50)

        ffmpeg_cmd = resolve_ffmpeg_executable()
        start_s = frame_to_seconds(int(combo["start_frame"]), 123, 60.0)
        end_s = frame_to_seconds(int(combo["end_frame"]), 123, 60.0)
        trim_clip(ffmpeg_cmd, full_video, clip_path, start_s, end_s)

        _update_job(job_id, "done", 100)
        with _jobs_lock:
            _jobs[job_id]["clip_id"] = str(clip_path)

    except Exception as exc:
        _update_job(job_id, "error", 0, str(exc))


def _highlight_worker(job_id: str, clip_paths: list[str], output_path: Path) -> None:
    """Background thread: assemble clips into a highlight video."""
    try:
        _update_job(job_id, "assembling", 10)
        assemble_highlight(clip_paths, str(output_path))
        _update_job(job_id, "done", 100)
        with _jobs_lock:
            _jobs[job_id]["output_path"] = str(output_path)
    except Exception as exc:
        _update_job(job_id, "error", 0, str(exc))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/upload")
def handle_upload():
    """Accept a .slp file, validate it, and store it for analysis."""
    if "file" not in request.files:
        return _error("No file provided.")

    uploaded = request.files["file"]
    if not uploaded.filename:
        return _error("Empty filename.")
    if not uploaded.filename.lower().endswith(".slp"):
        return _error("Only .slp files are accepted.")

    file_id = str(uuid.uuid4())
    dest = UPLOAD_DIR / f"{file_id}.slp"
    uploaded.save(str(dest))

    try:
        validate_slp(str(dest))
    except (ValueError, FileNotFoundError) as exc:
        dest.unlink(missing_ok=True)
        return _error(str(exc))

    _files[file_id] = dest
    return jsonify({"fileId": file_id})


@app.post("/api/analyze")
def handle_analyze():
    """Detect and score all combos in a previously uploaded replay."""
    body = request.get_json(silent=True) or {}
    file_id = body.get("fileId")
    if not file_id or file_id not in _files:
        return _error("Unknown fileId.", 404)

    try:
        combos = get_combo_clips(str(_files[file_id]), max_combos=10, padding_frames=120)
    except Exception as exc:
        return _error(f"Combo detection failed: {exc}")

    tagged = [
        {
            "id": f"{file_id}_combo_{i:02d}",
            "startFrame": c["start_frame"],
            "endFrame": c["end_frame"],
            "score": round(c["score"], 2),
            "totalDamage": c["total_damage"],
            "hitCount": c["hit_count"],
            "isKill": c["is_kill"],
            "moves": c["moves"],
        }
        for i, c in enumerate(combos)
    ]

    _combos[file_id] = combos
    return jsonify({"fileId": file_id, "combos": tagged})


@app.post("/api/render")
def handle_render():
    """Start an async render job for one combo clip."""
    body = request.get_json(silent=True) or {}
    file_id = body.get("fileId")
    combo_id = body.get("comboId")

    if not file_id or file_id not in _files:
        return _error("Unknown fileId.", 404)
    if not combo_id:
        return _error("comboId is required.")

    combos = _combos.get(file_id)
    if not combos:
        return _error("No combos found — run /api/analyze first.")

    try:
        index = int(combo_id.split("_combo_")[-1])
    except (ValueError, IndexError):
        return _error("Invalid comboId format.")

    if index >= len(combos):
        return _error(f"Combo index {index} out of range ({len(combos)} available).")

    clip_id = str(uuid.uuid4())
    clip_path = OUTPUT_DIR / f"{clip_id}.mp4"
    _clips[clip_id] = clip_path

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {"status": "pending", "progress": 0, "clip_id": clip_id}

    threading.Thread(
        target=_render_worker,
        args=(job_id, _files[file_id], combos[index], clip_path),
        daemon=True,
    ).start()

    return jsonify({"jobId": job_id, "clipId": clip_id})


@app.post("/api/highlight")
def handle_highlight():
    """Assemble previously rendered clips into a single highlight video."""
    body = request.get_json(silent=True) or {}
    clip_ids = body.get("clipIds", [])

    if not clip_ids:
        return _error("clipIds must not be empty.")

    missing = [cid for cid in clip_ids if cid not in _clips]
    if missing:
        return _error(f"Unknown clipIds: {missing}", 404)

    not_rendered = [cid for cid in clip_ids if not _clips[cid].exists()]
    if not_rendered:
        return _error(f"Clips not yet rendered: {not_rendered}. Check job status.")

    highlight_id = str(uuid.uuid4())
    output_path = HIGHLIGHT_DIR / f"{highlight_id}.mp4"
    _clips[highlight_id] = output_path

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {"status": "pending", "progress": 0, "highlight_id": highlight_id}

    threading.Thread(
        target=_highlight_worker,
        args=(job_id, [str(_clips[cid]) for cid in clip_ids], output_path),
        daemon=True,
    ).start()

    return jsonify({"jobId": job_id, "highlightId": highlight_id})


@app.get("/api/status/<job_id>")
def handle_status(job_id: str):
    """Poll the status and progress of an async job."""
    with _jobs_lock:
        job = _jobs.get(job_id)

    if job is None:
        return _error("Unknown jobId.", 404)

    response: dict[str, Any] = {
        "status": job["status"],
        "progress": job["progress"],
    }
    if job["status"] == "done":
        clip_id = job.get("clip_id") or job.get("highlight_id")
        if clip_id:
            response["downloadUrl"] = f"/api/download/{clip_id}"
    if job["status"] == "error":
        response["error"] = job.get("error", "Unknown error")

    return jsonify(response)


@app.get("/api/download/<clip_id>")
def handle_download(clip_id: str):
    """Serve a rendered clip or highlight MP4 for download."""
    clip_path = _clips.get(clip_id)
    if clip_path is None:
        return _error("Unknown clipId.", 404)
    if not clip_path.exists():
        return _error("File not yet rendered.", 404)

    return send_file(
        str(clip_path),
        mimetype="video/mp4",
        as_attachment=True,
        download_name=f"combo_{clip_id[:8]}.mp4",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
