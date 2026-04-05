"""
Flask API server for MeleeComboMaker.

Endpoints:
  POST /api/upload          — Accept .slp file, validate, store, return fileId
  POST /api/analyze         — Detect combos in a stored file, return combo list
  POST /api/render          — Start async render job, return jobId
  GET  /api/status/<jobId>  — Poll render job status / progress
  GET  /api/download/<clipId> — Serve a rendered clip or highlight .mp4
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Path setup — ensure project root is importable
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from jaeyooncode.combos import get_combo_clips, validate_slp
from pipeline.assembler import assemble_highlight
from slp2mp4_tools import convert_slp_to_mp4

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)

UPLOAD_DIR = PROJECT_ROOT / "uploads"
OUTPUT_DIR = PROJECT_ROOT / "comboVids"
HIGHLIGHT_DIR = PROJECT_ROOT / "highlights"
FULL_VIDEO_DIR = PROJECT_ROOT / "generatedVids"

for _d in (UPLOAD_DIR, OUTPUT_DIR, HIGHLIGHT_DIR, FULL_VIDEO_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# In-memory job registry: jobId -> {status, progress, output_path, error}
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()

# In-memory file registry: fileId -> Path
_files: dict[str, Path] = {}

# In-memory combo registry: fileId -> list[combo dicts]
_combos: dict[str, list[dict]] = {}

# In-memory clip registry: clipId -> Path
_clips: dict[str, Path] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _run_render_job(job_id: str, slp_path: Path, combo: dict, clip_path: Path) -> None:
    """Worker function executed in a background thread for one clip render."""
    def update(status: str, progress: int, error: str | None = None) -> None:
        with _jobs_lock:
            _jobs[job_id]["status"] = status
            _jobs[job_id]["progress"] = progress
            if error:
                _jobs[job_id]["error"] = error

    try:
        update("rendering", 10)

        # Render or find the full match video
        full_video = _find_or_render_full_video(slp_path)
        update("rendering", 50)

        # Trim the clip using FFmpeg
        from backend.render_combo_clips import frame_to_seconds, resolve_ffmpeg_executable, trim_clip
        ffmpeg_cmd = resolve_ffmpeg_executable()
        fps = 60.0
        startup = 123
        start_s = frame_to_seconds(int(combo["start_frame"]), startup, fps)
        end_s = frame_to_seconds(int(combo["end_frame"]), startup, fps)

        trim_clip(ffmpeg_cmd, full_video, clip_path, start_s, end_s)
        update("done", 100)

        with _jobs_lock:
            _jobs[job_id]["output_path"] = str(clip_path)

    except Exception as exc:
        update("error", 0, str(exc))


def _find_or_render_full_video(slp_path: Path) -> Path:
    """Return an existing full-match MP4 or render it via Dolphin."""
    candidates = sorted(FULL_VIDEO_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for c in candidates:
        if slp_path.stem.lower() in c.stem.lower():
            return c
    rendered = convert_slp_to_mp4(str(slp_path), str(FULL_VIDEO_DIR))
    return Path(rendered)


def _run_highlight_job(job_id: str, clip_paths: list[str], output_path: Path) -> None:
    """Worker function to assemble a multi-clip highlight video."""
    def update(status: str, progress: int, error: str | None = None) -> None:
        with _jobs_lock:
            _jobs[job_id]["status"] = status
            _jobs[job_id]["progress"] = progress
            if error:
                _jobs[job_id]["error"] = error

    try:
        update("assembling", 10)
        assemble_highlight(clip_paths, str(output_path))
        update("done", 100)
        with _jobs_lock:
            _jobs[job_id]["output_path"] = str(output_path)
    except Exception as exc:
        update("error", 0, str(exc))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/upload")
def handle_upload():
    """
    Accept a .slp file upload, validate it, store it, and return a fileId.

    Form data:
        file: The .slp replay file.

    Returns:
        {"fileId": "<uuid>"}
    """
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
    """
    Detect and score all combos in a previously uploaded replay.

    JSON body:
        {"fileId": "<uuid>"}

    Returns:
        {"fileId": "<uuid>", "combos": [...]}
    """
    body = request.get_json(silent=True) or {}
    file_id = body.get("fileId")
    if not file_id or file_id not in _files:
        return _error("Unknown fileId.", 404)

    slp_path = _files[file_id]

    try:
        combos = get_combo_clips(str(slp_path), max_combos=10, padding_frames=120)
    except Exception as exc:
        return _error(f"Combo detection failed: {exc}")

    # Attach stable IDs to each combo for downstream use
    tagged = []
    for i, c in enumerate(combos):
        tagged.append({
            "id": f"{file_id}_combo_{i:02d}",
            "startFrame": c["start_frame"],
            "endFrame": c["end_frame"],
            "score": round(c["score"], 2),
            "totalDamage": c["total_damage"],
            "hitCount": c["hit_count"],
            "isKill": c["is_kill"],
            "moves": c["moves"],
        })

    _combos[file_id] = combos
    return jsonify({"fileId": file_id, "combos": tagged})


@app.post("/api/render")
def handle_render():
    """
    Start an async render job for one combo clip.

    JSON body:
        {"comboId": "<fileId>_combo_<nn>", "fileId": "<uuid>"}

    Returns:
        {"jobId": "<uuid>"}
    """
    body = request.get_json(silent=True) or {}
    file_id = body.get("fileId")
    combo_id = body.get("comboId")

    if not file_id or file_id not in _files:
        return _error("Unknown fileId.", 404)
    if not combo_id:
        return _error("comboId is required.")

    combos = _combos.get(file_id)
    if not combos:
        return _error("No combos found for fileId — run /api/analyze first.")

    # comboId format: "<fileId>_combo_<index>"
    try:
        index = int(combo_id.split("_combo_")[-1])
    except (ValueError, IndexError):
        return _error("Invalid comboId format.")

    if index >= len(combos):
        return _error(f"Combo index {index} out of range ({len(combos)} combos).")

    combo = combos[index]
    clip_id = str(uuid.uuid4())
    clip_path = OUTPUT_DIR / f"{clip_id}.mp4"

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {"status": "pending", "progress": 0, "clip_id": clip_id}

    _clips[clip_id] = clip_path

    thread = threading.Thread(
        target=_run_render_job,
        args=(job_id, _files[file_id], combo, clip_path),
        daemon=True,
    )
    thread.start()

    return jsonify({"jobId": job_id, "clipId": clip_id})


@app.post("/api/highlight")
def handle_highlight():
    """
    Assemble a multi-clip highlight video from previously rendered clips.

    JSON body:
        {"clipIds": ["<clipId>", ...], "musicPath": "<optional path>"}

    Returns:
        {"jobId": "<uuid>", "highlightId": "<uuid>"}
    """
    body = request.get_json(silent=True) or {}
    clip_ids = body.get("clipIds", [])
    music_path = body.get("musicPath")

    if not clip_ids:
        return _error("clipIds must not be empty.")

    missing = [cid for cid in clip_ids if cid not in _clips]
    if missing:
        return _error(f"Unknown clipIds: {missing}", 404)

    clip_paths = [str(_clips[cid]) for cid in clip_ids]
    not_rendered = [p for p in clip_paths if not Path(p).exists()]
    if not_rendered:
        return _error(f"Clips not yet rendered: {not_rendered}. Check job status first.")

    highlight_id = str(uuid.uuid4())
    output_path = HIGHLIGHT_DIR / f"{highlight_id}.mp4"

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {"status": "pending", "progress": 0, "highlight_id": highlight_id}

    _clips[highlight_id] = output_path

    thread = threading.Thread(
        target=_run_highlight_job,
        args=(job_id, clip_paths, output_path),
        daemon=True,
    )
    thread.start()

    return jsonify({"jobId": job_id, "highlightId": highlight_id})


@app.get("/api/status/<job_id>")
def handle_status(job_id: str):
    """
    Poll the status and progress of an async job.

    Returns:
        {"status": "pending"|"rendering"|"assembling"|"done"|"error",
         "progress": 0-100,
         "downloadUrl": "/api/download/<clipId>"}   # only when done
    """
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
