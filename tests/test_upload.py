import io

from app.services import storage
from app.services.gemini import GeminiService

from .fakes import FakeClient, make_docx, make_pdf, smart_handler
from .helpers import VACANCY_TEXT, create_session, resume_text, upload


def test_create_session_validates_input(client, csrf):
    response = client.post("/sessions", data={"csrf_token": csrf, "title": "", "vacancy_title": ["X"], "vacancy_text": ["short"]})
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "Укажите название" in html
    assert "от 30 до 20 000 символов" in html


def test_create_session_with_several_vacancies(app, client, csrf):
    session_id = create_session(client, csrf, vacancies=[("Python", VACANCY_TEXT), ("Data", VACANCY_TEXT + " SQL")])
    with app.app_context():
        assert len(storage.list_vacancies(storage.get_db(), session_id)) == 2
    assert client.get(f"/sessions/{session_id}").status_code == 200


def test_batch_of_20_mixed_files_with_bad_ones(app, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    files = []
    for i in range(20):
        kind = i % 3
        if kind == 0:
            files.append((f"cv{i}.txt", resume_text(i)))
        elif kind == 1:
            files.append((f"cv{i}.docx", make_docx([resume_text(i).decode()])))
        else:
            files.append((f"cv{i}.pdf", make_pdf([f"Candidate {i}", "Python developer, Django, PostgreSQL, Docker"])))
    files += [("scan.pdf", make_pdf([])), ("broken.docx", b"PK\x03\x04 nope"), ("virus.exe", b"MZ"), ("cv0.txt", resume_text(0))]

    response = upload(client, csrf, session_id, files)
    assert response.status_code == 201
    body = response.get_json()
    assert len(body["accepted"]) == 20
    assert {r["error"] for r in body["rejected"]} == {"no_text_layer", "corrupt", "unsupported_type", "duplicate"}
    assert all(r["message"] for r in body["rejected"])

    status = client.get(f"/sessions/{session_id}/status").get_json()
    assert status["progress"]["done"] == 20
    assert status["progress"]["parse_failed"] == 3
    assert status["running"] is False
    with app.app_context():
        db = storage.get_db()
        assert db.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 20


def test_reupload_same_file_is_duplicate(client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    body = upload(client, csrf, session_id, [("renamed.txt", resume_text(1))]).get_json()
    assert body["accepted"] == [] and body["rejected"][0]["error"] == "duplicate"


def test_too_many_files(app, client, csrf, fake_gemini):
    app.config["MAX_FILES_PER_BATCH"] = 2
    session_id = create_session(client, csrf)
    response = upload(client, csrf, session_id, [(f"{i}.txt", resume_text(i)) for i in range(3)])
    assert response.status_code == 400 and response.get_json()["error"] == "too_many_files"


def test_upload_requires_csrf(client, csrf):
    session_id = create_session(client, csrf)
    response = client.post(f"/sessions/{session_id}/resumes", data={"files": [(io.BytesIO(b"x"), "a.txt")]},
                           content_type="multipart/form-data")
    assert response.status_code == 400


def test_upload_to_missing_session_is_404(client, csrf):
    assert upload(client, csrf, 999, [("a.txt", resume_text(1))]).status_code == 404


def test_analysis_failure_is_recorded_and_batch_continues(app, client, csrf):
    calls = {"n": 0}

    def flaky(call):
        calls["n"] += 1
        return "garbage" if "Кандидат 1." in call["contents"] else smart_handler()(call)

    app.extensions["gemini"] = GeminiService(FakeClient(handler=flaky), "m", sleep=lambda s: None)
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1)), ("b.txt", resume_text(2))])
    status = client.get(f"/sessions/{session_id}/status").get_json()
    assert status["progress"]["done"] == 1 and status["progress"]["analysis_failed"] == 1
    failed = next(r for r in status["resumes"] if r["status"] == "analysis_failed")
    assert failed["message"]

    app.extensions["gemini"] = GeminiService(FakeClient(handler=smart_handler()), "m", sleep=lambda s: None)
    assert client.post(f"/sessions/{session_id}/analyze", headers={"X-CSRF-Token": csrf}).get_json()["queued"] == 1
    assert client.get(f"/sessions/{session_id}/status").get_json()["progress"]["done"] == 2


def test_missing_api_key_leaves_resumes_pending(app, client, csrf):
    app.config["GEMINI_API_KEY"] = ""
    session_id = create_session(client, csrf)
    body = upload(client, csrf, session_id, [("a.txt", resume_text(1))]).get_json()
    assert body["started"] is False
    assert client.get(f"/sessions/{session_id}/status").get_json()["progress"]["pending"] == 1


def test_demographics_are_stored_separately_from_profile(app, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    with app.app_context():
        db = storage.get_db()
        profile_json = db.execute("SELECT profile_json FROM resumes").fetchone()[0]
        assert "demographics" not in profile_json and "female" not in profile_json
        assert tuple(db.execute("SELECT gender, age FROM demographics").fetchone()) == ("female", 27)
