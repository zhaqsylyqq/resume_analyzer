"""Vacancies: add one later (F6), ranking + CSV/Excel/PDF export (F2, US7), bias check (B1), diversity (B2)."""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from ..i18n import get_locale, translate
from ..services import bias, diversity, export as exporter, jobs, scoring, storage
from ..services.gemini import GeminiError, get_gemini
from .screening import start_analysis
from .sessions import MAX_VACANCIES, MAX_VACANCY_TEXT, load_session_or_404, validate_vacancy

bp = Blueprint("vacancies", __name__)


def _load(session_id: int, vacancy_id: int):
    session = load_session_or_404(session_id)
    vacancy = storage.get_vacancy(storage.get_db(), session_id, vacancy_id)
    if vacancy is None:
        abort(404)
    return session, vacancy


def _gemini_or_none():
    try:
        return get_gemini()
    except GeminiError:
        return None


@bp.post("/sessions/<int:session_id>/vacancies")
def create(session_id: int):
    load_session_or_404(session_id)
    db = storage.get_db()
    title = request.form.get("title", "").strip()
    text = request.form.get("text", "").strip()

    if len(storage.list_vacancies(db, session_id)) >= MAX_VACANCIES:
        flash("form.error.too_many_vacancies", "danger")
    elif error := validate_vacancy(title, text):
        flash(error, "danger")
    else:
        vacancy_id = storage.add_vacancy(db, session_id, title, text)
        storage.save_bias_report(db, vacancy_id, bias.check(text))
        if storage.requeue_unscored(db, session_id):
            start_analysis(session_id)
        flash("vacancy.added", "success")
    return redirect(url_for("sessions.show", session_id=session_id))


@bp.get("/sessions/<int:session_id>/vacancies/<int:vacancy_id>")
def show(session_id: int, vacancy_id: int):
    session, vacancy = _load(session_id, vacancy_id)
    db = storage.get_db()
    storage.audit(db, "ranking_open", g.user, session_id, f"vacancy={vacancy_id}")
    top = scoring.parse_top(request.args.get("top", str(scoring.TOP_CHOICES[1])))
    candidates = scoring.ranking(db, vacancy_id)
    shortlist = scoring.top_n(candidates, top)
    progress = storage.progress(db, session_id)

    bias_report = json.loads(vacancy["bias_json"]) if vacancy["bias_json"] else None
    if bias_report is None or bias_report.get("text_hash") != bias.text_hash(vacancy["text"]):
        bias_report = bias.check(vacancy["text"])
        storage.save_bias_report(db, vacancy_id, bias_report)

    return render_template(
        "vacancies/show.html",
        session=session,
        vacancy=vacancy,
        vacancies=storage.list_vacancies(db, session_id),
        candidates=shortlist,
        total=len(candidates),
        stats=scoring.summary_stats(candidates),
        top=top,
        top_choices=scoring.TOP_CHOICES,
        in_progress=jobs.is_running(session_id) or progress[storage.PENDING] > 0,
        bias_report=bias.localize(bias_report, translate),
        highlighted=bias.highlight(vacancy["text"], bias_report["findings"]),
        diversity=diversity.report(db, [c.resume_id for c in candidates], [c.resume_id for c in shortlist]),
    )


@bp.post("/sessions/<int:session_id>/vacancies/<int:vacancy_id>/bias")
def recheck_bias(session_id: int, vacancy_id: int):
    session, vacancy = _load(session_id, vacancy_id)
    service = _gemini_or_none()
    report = bias.check(vacancy["text"], session["language"], service)
    storage.save_bias_report(storage.get_db(), vacancy_id, report)
    flash("bias.ai_done" if report["ai_checked"] else "bias.ai_unavailable", "success" if report["ai_checked"] else "warning")
    top = request.form.get("top", "")
    return redirect(url_for("vacancies.show", session_id=session_id, vacancy_id=vacancy_id, top=top or None) + "#bias")


@bp.post("/vacancies/bias-check")
def bias_check():
    """Check draft vacancy text from the new-session form (JSON in, JSON out)."""
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()[:MAX_VACANCY_TEXT]
    language = payload.get("language") if payload.get("language") in current_app.config["LANGUAGES"] else get_locale()
    if len(text) < 10:
        return jsonify(error="too_short", message=translate("form.error.vacancy_text")), 400
    report = bias.check(text, language, _gemini_or_none() if payload.get("ai", True) else None)
    return jsonify(bias.localize(report, translate))


MIMETYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def _filename(*parts: str) -> str:
    return "_".join(re.sub(r"[^\w\-]+", "-", p, flags=re.UNICODE).strip("-")[:60] for p in parts if p)


def _download(data: bytes, stem: str, ext: str, session_id: int, vacancy_id: int | None = None) -> Response:
    """File response with a UTF-8 filename (RFC 5987) and an ASCII fallback; audited (A1)."""
    scope = f"vacancy={vacancy_id}" if vacancy_id else "all vacancies"
    storage.audit(storage.get_db(), "export", g.user, session_id, f"{ext} {scope} top={request.args.get('top') or '-'}")
    name = f"{stem}.{ext}"
    ascii_stem = re.sub(r"[-_]{2,}", "_", stem.encode("ascii", "ignore").decode()).strip("_-")
    ascii_name = f"{ascii_stem or 'ranking'}.{ext}"
    response = Response(data, mimetype=MIMETYPES[ext])
    response.headers["Content-Disposition"] = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    response.headers["Cache-Control"] = "no-store"
    return response


def _shortlist(session_id: int, vacancy_id: int):
    session, vacancy = _load(session_id, vacancy_id)
    top = scoring.parse_top(request.args.get("top"))
    ranked = scoring.ranking(storage.get_db(), vacancy_id)
    stem = _filename(session["title"], vacancy["title"], f"top{top}" if top else "all")
    return session, vacancy, top, ranked, scoring.top_n(ranked, top), stem


@bp.get("/sessions/<int:session_id>/vacancies/<int:vacancy_id>/export.csv")
def export(session_id: int, vacancy_id: int):
    *_, shortlist, stem = _shortlist(session_id, vacancy_id)
    return _download(scoring.to_csv(shortlist, translate), stem, "csv", session_id, vacancy_id)


@bp.get("/sessions/<int:session_id>/vacancies/<int:vacancy_id>/export.xlsx")
def export_xlsx(session_id: int, vacancy_id: int):
    session, vacancy, _top, ranked, shortlist, stem = _shortlist(session_id, vacancy_id)
    sheet = exporter.VacancySheet(vacancy["title"], shortlist, len(ranked))
    return _download(exporter.to_xlsx(session["title"], [sheet], translate), stem, "xlsx", session_id, vacancy_id)


@bp.get("/sessions/<int:session_id>/vacancies/<int:vacancy_id>/export.pdf")
def export_pdf(session_id: int, vacancy_id: int):
    session, vacancy, top, ranked, shortlist, stem = _shortlist(session_id, vacancy_id)
    data = exporter.to_pdf(session["title"], vacancy["title"], shortlist, len(ranked), top, translate)
    return _download(data, stem, "pdf", session_id, vacancy_id)


@bp.get("/sessions/<int:session_id>/export.xlsx")
def export_session_xlsx(session_id: int):
    """Whole session: one sheet per vacancy with its Top-N (default Top-10, ties kept)."""
    session = load_session_or_404(session_id)
    db = storage.get_db()
    top = scoring.parse_top(request.args.get("top", str(scoring.TOP_CHOICES[1])))
    sheets = []
    for vacancy in storage.list_vacancies(db, session_id):
        ranked = scoring.ranking(db, vacancy["id"])
        sheets.append(exporter.VacancySheet(vacancy["title"], scoring.top_n(ranked, top), len(ranked)))
    stem = _filename(session["title"], f"top{top}" if top else "all")
    return _download(exporter.to_xlsx(session["title"], sheets, translate), stem, "xlsx", session_id)
