"""F1 batch upload, F6 append-only analysis, and re-running failed resumes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request

from ..auth import current_settings, is_admin

from ..i18n import translate
from ..services import jobs, storage
from ..services.gemini import GeminiError
from ..services.parsing import parse_file
from .sessions import load_session_or_404

bp = Blueprint("screening", __name__, url_prefix="/sessions")


def start_analysis(session_id: int) -> bool:
    try:
        jobs.start(session_id)
    except GeminiError:
        current_app.logger.error("Gemini is not configured; analysis not started")
        return False
    return True


def remaining_today() -> int | None:
    """S2: resumes the signed-in recruiter may still add in the last 24 h; None = unlimited."""
    limit = current_settings().daily_limit
    if is_admin() or limit <= 0:
        return None
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    return max(0, limit - storage.resumes_added_since(storage.get_db(), g.user["id"], since))


def remaining_label(remaining: int | None) -> str | None:
    if remaining is None:
        return None
    return translate("limit.remaining", n=remaining, limit=current_settings().daily_limit)


@bp.post("/<int:session_id>/resumes")
def upload(session_id: int):
    load_session_or_404(session_id)
    cfg = current_app.config
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        return jsonify(error="no_files", message=translate("upload.error.no_files")), 400
    if len(files) > cfg["MAX_FILES_PER_BATCH"]:
        return jsonify(
            error="too_many_files", message=translate("upload.error.too_many", limit=cfg["MAX_FILES_PER_BATCH"])
        ), 400

    db = storage.get_db()
    accepted, rejected = [], []
    remaining = remaining_today()
    for upload_file in files:
        data = upload_file.stream.read(cfg["MAX_FILE_BYTES"] + 1)
        parsed = parse_file(upload_file.filename, data, max_bytes=cfg["MAX_FILE_BYTES"], max_chars=cfg["MAX_RESUME_CHARS"])
        if storage.resume_exists(db, session_id, parsed.sha256):
            rejected.append({"filename": parsed.filename, "error": "duplicate", "message": translate("parse_error.duplicate")})
            continue
        if parsed.ok and remaining is not None:
            if remaining <= 0:  # S2: protects the prepaid AI balance
                rejected.append({"filename": parsed.filename, "error": "daily_limit",
                                 "message": translate("limit.reached", limit=current_settings().daily_limit)})
                continue
            remaining -= 1
        status = storage.PENDING if parsed.ok else storage.PARSE_FAILED
        resume_id = storage.add_resume(db, session_id, parsed.filename, parsed.sha256, parsed.text, status, parsed.error)
        if resume_id is None:  # same file twice in one batch
            rejected.append({"filename": parsed.filename, "error": "duplicate", "message": translate("parse_error.duplicate")})
        elif parsed.ok:
            accepted.append({"id": resume_id, "filename": parsed.filename})
        else:
            rejected.append({"id": resume_id, "filename": parsed.filename, "error": parsed.error,
                             "message": translate(f"parse_error.{parsed.error}")})

    started = start_analysis(session_id) if accepted else False
    return jsonify(accepted=accepted, rejected=rejected, started=started,
                   remaining_label=remaining_label(remaining_today())), 201 if accepted else 200


@bp.post("/<int:session_id>/analyze")
def analyze(session_id: int):
    """Re-queue failed resumes and resumes missing a score, then run the job."""
    load_session_or_404(session_id)
    db = storage.get_db()
    queued = storage.requeue_failed(db, session_id) + storage.requeue_unscored(db, session_id)
    pending = storage.progress(db, session_id)[storage.PENDING]
    started = start_analysis(session_id) if pending else False
    return jsonify(queued=queued, started=started)
