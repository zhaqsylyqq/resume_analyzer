"""Concurrency edge cases of the background job runner."""
from app.services import jobs, storage
from app.services.gemini import GeminiService
from app.services.schemas import CandidateProfile

from .fakes import FakeClient, smart_handler
from .helpers import VACANCY_TEXT, create_session, resume_text, status, upload


def test_resume_queued_while_job_is_finishing_is_not_lost(app, client, csrf, fake_gemini, monkeypatch):
    session_id = create_session(client, csrf)
    original = storage.claim_pending
    injected = {"done": False}

    def claim_then_race(db, sid, limit=1000):
        ids = original(db, sid, limit)
        if not ids and not injected["done"]:
            injected["done"] = True  # an upload lands right after the queue looked empty
            storage.add_resume(db, sid, "late.txt", "late-hash", resume_text(9).decode(), storage.PENDING)
        return ids

    monkeypatch.setattr(storage, "claim_pending", claim_then_race)
    upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    progress = status(client, session_id)["progress"]
    assert progress["done"] == 2 and progress["pending"] == 0
    assert not jobs.is_running(session_id)


def test_daily_quota_marks_resumes_with_a_clear_reason(app, client, csrf):
    from .fakes import daily_quota_error

    app.extensions["gemini"] = GeminiService(FakeClient(handler=lambda call: daily_quota_error()), "m", "fb",
                                             sleep=lambda s: None)
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1)), ("b.txt", resume_text(2))])
    resumes = status(client, session_id)["resumes"]
    assert [r["status"] for r in resumes] == ["analysis_failed"] * 2
    assert all("дневной лимит" in r["message"] for r in resumes)
    assert len(app.extensions["gemini"].client.models.calls) == 2  # second resume failed without calling


def test_vacancy_added_during_scoring_is_scored_for_that_resume(app, client, csrf):
    session_id = create_session(client, csrf)
    base = smart_handler()
    added = {"done": False}

    def handler(call):
        if call["config"].response_schema is not CandidateProfile and not added["done"]:
            added["done"] = True  # HR adds a vacancy while this resume is being scored
            db = storage.connect(app.config["DATABASE"])
            storage.add_vacancy(db, session_id, "Data Engineer", VACANCY_TEXT + " Airflow")
            db.close()
        return base(call)

    app.extensions["gemini"] = GeminiService(FakeClient(handler=handler), "m", sleep=lambda s: None)
    upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    with app.app_context():
        assert storage.get_db().execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 2


# QW-05: analysis interrupted by a restart continues on its own ------------------------------

def test_restart_resumes_pending_analysis(app, fake_gemini):
    from app import create_app

    with app.app_context():
        db = storage.get_db()
        sid = storage.create_session(db, "До перезапуска", "ru", [("v", VACANCY_TEXT)])
        storage.add_resume(db, sid, "a.txt", "h-a", resume_text(1).decode(), storage.PENDING)
        rid = storage.add_resume(db, sid, "b.txt", "h-b", resume_text(2).decode(), storage.PENDING)
        db.execute("UPDATE resumes SET status = ? WHERE id = ?", (storage.PROCESSING, rid))  # cut off mid-analysis
        db.commit()

    restarted = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": app.config["DATABASE"], "SYNC_JOBS": True,
                            "LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "k", "GEMINI_MODEL": "m"})
    restarted.extensions["gemini"] = fake_gemini
    assert jobs.resume_pending(restarted) == [sid]
    with restarted.app_context():
        assert storage.progress(storage.get_db(), sid)["done"] == 2


def test_resume_pending_without_ai_key_keeps_resumes_waiting(app):
    app.config.update(LLM_PROVIDER="gemini", GEMINI_API_KEY="")
    app.extensions.pop("gemini", None)
    with app.app_context():
        db = storage.get_db()
        sid = storage.create_session(db, "Без ключа", "ru", [("v", VACANCY_TEXT)])
        storage.add_resume(db, sid, "a.txt", "h-a", resume_text(1).decode(), storage.PENDING)
    assert jobs.resume_pending(app) == []
    with app.app_context():
        assert storage.progress(storage.get_db(), sid)["pending"] == 1


def test_resume_pending_with_empty_queue_does_nothing(app):
    assert jobs.resume_pending(app) == []


def test_server_entry_point_resumes_the_queue(app, monkeypatch):
    import app as package

    called = []
    monkeypatch.setattr(package, "create_app", lambda: app)  # never touch the real instance/app.db
    monkeypatch.setattr(jobs, "resume_pending", lambda a: called.append(a) or [])
    assert package.create_server_app() is app
    assert called == [app]
