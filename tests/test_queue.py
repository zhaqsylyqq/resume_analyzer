"""Q1 — fair round-robin queue between sessions and the time-remaining estimate."""
import re
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.services import jobs, storage
from app.services.gemini import GeminiService
from app.services.schemas import CandidateProfile

from .fakes import FakeClient, smart_handler
from .helpers import VACANCY_TEXT, create_session, resume_text, status, upload


@pytest.fixture(autouse=True)
def clean_scheduler():
    """The scheduler is process-wide; every test starts and ends with an empty one."""
    jobs._rotation.clear()
    jobs._batches.clear()
    yield
    jobs._rotation.clear()
    jobs._batches.clear()


def recording_service(order):
    base = smart_handler()

    def handler(call):
        if call["config"].response_schema is CandidateProfile:
            order.append(re.search(r"CV (\w+)-\d+", call["contents"]).group(1))
        return base(call)

    return GeminiService(FakeClient(handler=handler), "m", sleep=lambda s: None)


def queue(app, title, n, label):
    with app.app_context():
        db = storage.get_db()
        sid = storage.create_session(db, title, "ru", [("v", VACANCY_TEXT)])
        for i in range(n):
            storage.add_resume(db, sid, f"{label}{i}.txt", f"{label}-{i}", f"CV {label}-{i} Python Django", storage.PENDING)
    return sid


def test_sessions_take_turns_resume_by_resume(app):
    order = []
    service = recording_service(order)
    big = queue(app, "Большая", 6, "A")
    small = queue(app, "Маленькая", 3, "B")
    jobs._rotation.extend([big, small])
    jobs._worker(app, service)
    assert order == ["A", "B", "A", "B", "A", "B", "A", "A", "A"]
    with app.app_context():
        db = storage.get_db()
        assert storage.progress(db, big)["done"] == 6 and storage.progress(db, small)["done"] == 3
    assert not jobs.is_running(big) and not jobs.is_running(small)


def test_a_session_started_later_gets_every_other_turn(app):
    order = []
    service = recording_service(order)
    big = queue(app, "Большая", 5, "A")
    jobs._rotation.append(big)
    db = storage.connect(app.config["DATABASE"])
    with app.app_context():
        assert jobs._work_once(app, service, db) and jobs._work_once(app, service, db)  # A, A
        late = queue(app, "Поздняя", 2, "B")
        jobs._rotation.append(late)
        while jobs._work_once(app, service, db):
            pass
    db.close()
    assert order == ["A", "A", "A", "B", "A", "B", "A"]


def test_threaded_workers_finish_every_session(app, client, csrf):
    app.config["SYNC_JOBS"] = False
    app.config["GEMINI_CONCURRENCY"] = 2
    app.extensions["gemini"] = GeminiService(FakeClient(handler=smart_handler()), "m", sleep=lambda s: None)
    sessions = [create_session(client, csrf, title=f"S{k}") for k in range(3)]
    for k, sid in enumerate(sessions):
        response = upload(client, csrf, sid, [(f"cv{k}-{i}.txt", resume_text(10 * k + i)) for i in range(4)])
        assert len(response.get_json()["accepted"]) == 4
    deadline = time.time() + 20
    while any(jobs.is_running(sid) for sid in sessions) and time.time() < deadline:
        time.sleep(0.05)
    assert [status(client, sid)["progress"]["done"] for sid in sessions] == [4, 4, 4]
    assert jobs._workers == 0 or not jobs._rotation  # pool shrinks when idle


# ETA ------------------------------------------------------------------------------------

def finished(db, sid, n, seconds):
    end = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(n):
        rid = storage.add_resume(db, sid, f"done{i}.txt", f"d-{sid}-{i}", "t", storage.DONE)
        db.execute("UPDATE resumes SET started_at = ?, analyzed_at = ? WHERE id = ?",
                   ((end - timedelta(seconds=seconds)).isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), rid))
    db.commit()


def test_eta_counts_turns_of_other_sessions(app):
    with app.app_context():
        db = storage.get_db()
        a = storage.create_session(db, "a", "ru", [("v", VACANCY_TEXT)])
        b = storage.create_session(db, "b", "ru", [("v", VACANCY_TEXT)])
        finished(db, a, 4, 6)  # 6 s per resume
        for i in range(10):
            storage.add_resume(db, a, f"a{i}", f"a{i}", "t", storage.PENDING)
        for i in range(2):
            storage.add_resume(db, b, f"b{i}", f"b{i}", "t", storage.PENDING)
        # b: 2 own + min(10, 2) of a = 4 turns × 6 s / 2 workers = 12 s
        assert jobs.eta_seconds(db, b, workers=2) == 12
        # a: 10 own + 2 of b = 12 turns × 6 s / 2 = 36 s
        assert jobs.eta_seconds(db, a, workers=2) == 36


def test_eta_unknown_without_history_or_work(app):
    with app.app_context():
        db = storage.get_db()
        a = storage.create_session(db, "a", "ru", [("v", VACANCY_TEXT)])
        assert jobs.eta_seconds(db, a, workers=2) is None  # nothing queued
        storage.add_resume(db, a, "x", "x", "t", storage.PENDING)
        assert jobs.eta_seconds(db, a, workers=2) is None  # no finished resume to learn from


@pytest.mark.parametrize("lang,seconds,expected", [
    ("ru", 30, "осталось меньше минуты"), ("ru", 61, "осталось ≈ 2 мин"), ("ru", None, "оцениваем оставшееся время…"),
    ("kk", 30, "бір минуттан аз қалды"), ("kk", 125, "шамамен 3 мин қалды"),
])
def test_status_endpoint_returns_localized_eta(app, client, csrf, monkeypatch, lang, seconds, expected):
    sid = create_session(client, csrf)
    jobs._batches[sid] = jobs._Batch()  # pretend the analysis runs
    monkeypatch.setattr(jobs, "eta_seconds", lambda db, session_id, workers: seconds)
    client.get(f"/lang/{lang}")
    data = status(client, sid)
    assert data["running"] and data["eta_seconds"] == seconds and data["eta_label"] == expected
    assert expected in client.get(f"/sessions/{sid}").get_data(as_text=True)


def test_idle_session_has_no_eta(client, csrf):
    sid = create_session(client, csrf)
    data = status(client, sid)
    assert data["eta_seconds"] is None and data["eta_label"] is None


def test_claim_records_start_time_and_average_uses_it(app):
    with app.app_context():
        db = storage.get_db()
        sid = storage.create_session(db, "s", "ru", [("v", VACANCY_TEXT)])
        rid = storage.add_resume(db, sid, "a", "a", "t", storage.PENDING)
        assert storage.claim_pending(db, sid, 1) == [rid]
        assert storage.get_resume(db, rid)["started_at"] is not None
        assert storage.average_analysis_seconds(db) is None
        finished(db, sid, 3, 9)
        assert storage.average_analysis_seconds(db) == pytest.approx(9.0)


def test_concurrent_claims_never_hand_out_a_resume_twice(app):
    import threading

    with app.app_context():
        db = storage.get_db()
        sid = storage.create_session(db, "s", "ru", [("v", VACANCY_TEXT)])
        for i in range(200):
            storage.add_resume(db, sid, f"{i}.txt", f"c-{i}", "t", storage.PENDING)
    claimed, errors = [], []

    def worker():
        conn = storage.connect(app.config["DATABASE"])
        try:
            while True:
                ids = storage.claim_pending(conn, sid, 1)
                if not ids:
                    return
                claimed.extend(ids)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(claimed) == 200 and len(set(claimed)) == 200
