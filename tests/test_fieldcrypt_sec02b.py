"""SEC-02b: personal data encrypted at rest (resume text, file name, profile, demographics,
uploaded file, 2FA secret), key handling and rotation."""
from __future__ import annotations

import io
import os
import subprocess

import pytest

from app import create_app
from app.config import ConfigError, load_data_keyring, load_settings
from app.services import fieldcrypt, storage

from .conftest import ON_POSTGRES, TEST_DATA_KEY, TEST_DATABASE_URL, login, make_user
from .helpers import create_session, resume_text

KEY_B = fieldcrypt.generate_key()
EMAIL = "aigerim@example.kz"  # tests.fakes.smart_handler is not involved: contacts come from pii.py


@pytest.fixture(autouse=True)
def _test_keyring():
    """Tests without an app still need the process keyring (create_app sets it otherwise)."""
    fieldcrypt.configure(fieldcrypt.Keyring.from_values(TEST_DATA_KEY))


def _resume_with_contacts(i: int = 1) -> bytes:
    return resume_text(i) + f"\nEmail: {EMAIL}\nТелефон: +7 701 555 12 34".encode()


# The cipher ------------------------------------------------------------------------------------

def test_round_trip_and_format():
    sealed = fieldcrypt.encrypt("Айгерим Сапарова", "resumes.text")
    assert sealed.startswith(f"enc1:{fieldcrypt.keyring().current_id}:") and "Айгерим" not in sealed
    assert fieldcrypt.decrypt(sealed, "resumes.text") == "Айгерим Сапарова"
    assert fieldcrypt.encrypt("same", "resumes.text") != fieldcrypt.encrypt("same", "resumes.text")  # fresh nonce
    assert fieldcrypt.encrypt(sealed, "resumes.text") == sealed  # never sealed twice
    for empty in (None, ""):
        assert fieldcrypt.encrypt(empty, "resumes.text") == empty
    assert fieldcrypt.decrypt("plain text from before SEC-02b", "resumes.text") == "plain text from before SEC-02b"
    blob = fieldcrypt.encrypt_bytes(b"%PDF-1.7 ...", "resumes.raw_file")
    assert blob.startswith(b"ENC1") and b"%PDF" not in blob
    assert fieldcrypt.decrypt_bytes(blob, "resumes.raw_file") == b"%PDF-1.7 ..."
    assert fieldcrypt.decrypt_bytes(b"%PDF old upload", "resumes.raw_file") == b"%PDF old upload"


def test_a_value_is_bound_to_its_column_and_key():
    sealed = fieldcrypt.encrypt("female", "demographics.gender")
    with pytest.raises(fieldcrypt.DataKeyError, match="cannot decrypt demographics.age"):
        fieldcrypt.decrypt(sealed, "demographics.age")  # copied into another column: refused
    with pytest.raises(fieldcrypt.DataKeyError, match="cannot decrypt"):
        fieldcrypt.decrypt_bytes(fieldcrypt.encrypt_bytes(b"x", "resumes.raw_file"), "resumes.text")
    stranger = fieldcrypt.Keyring.from_values(KEY_B)
    previous = fieldcrypt.keyring()
    fieldcrypt.configure(stranger)
    try:
        with pytest.raises(fieldcrypt.DataKeyError, match=r"data key \w{8} is not configured"):
            fieldcrypt.decrypt(sealed, "demographics.gender")
    finally:
        fieldcrypt.configure(previous)


def test_bad_keys_are_configuration_errors(monkeypatch):
    for env, problem in (({"DATA_KEY": "not-base64!"}, "DATA_KEY is not valid base64"),
                         ({"DATA_KEY": "c2hvcnQ="}, "DATA_KEY must be 32 bytes"),
                         ({"DATA_KEY": "", "DATA_KEY_OLD": KEY_B}, "DATA_KEY_OLD is set without DATA_KEY"),
                         ({"DATA_KEY": KEY_B, "DATA_KEY_OLD": f"{TEST_DATA_KEY},c2hvcnQ="}, "DATA_KEY_OLD must be 32 bytes")):
        with pytest.raises(ConfigError, match=problem):
            load_settings(env)
    settings = load_settings({"DATA_KEY": KEY_B})
    assert dict(settings.report())["DATA_KEY"] == "set" and KEY_B not in str(settings.report())


def test_where_the_key_comes_from(tmp_path):
    with pytest.raises(ConfigError, match="DATA_KEY .* is required in production"):
        load_data_keyring({}, str(tmp_path), production=True)
    assert not (tmp_path / ".data_key").exists()  # production never invents a key
    ring = load_data_keyring({}, str(tmp_path), production=False)  # development: created once
    assert (tmp_path / ".data_key").read_text().strip() and load_data_keyring({}, str(tmp_path), True).current == ring.current
    explicit = load_data_keyring({"DATA_KEY": KEY_B, "DATA_KEY_OLD": f" {TEST_DATA_KEY} ,"}, str(tmp_path), True)
    assert explicit.current_id == fieldcrypt.key_id(fieldcrypt.parse_key(KEY_B)) and len(explicit.old) == 1


def test_data_key_command(tmp_path, monkeypatch, capsys):
    from app import data_key

    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)  # never the developer's .env
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATA_KEY", "")
    assert data_key.main(["status"]) == 1 and "No data key yet" in capsys.readouterr().out
    assert data_key.main(["init"]) == 0
    created = (tmp_path / ".data_key").read_text().strip()
    out = capsys.readouterr().out
    assert "Created" in out and created not in out  # the key itself is never printed
    assert data_key.main(["init"]) == 0 and "already exists" in capsys.readouterr().out
    assert (tmp_path / ".data_key").read_text().strip() == created  # never replaced
    assert data_key.main(["status"]) == 0
    assert fieldcrypt.key_id(fieldcrypt.parse_key(created)) in capsys.readouterr().out
    assert data_key.main(["generate"]) == 0 and len(fieldcrypt.parse_key(capsys.readouterr().out.strip())) == 32
    assert data_key.main(["bogus"]) == 2


# Definition of done: the stored data is unreadable, the app works as before -------------------

def _analysed(app, client, csrf):
    session_id = create_session(client, csrf)
    files = [("Сапарова_Айгерим_CV.txt", _resume_with_contacts(1))]
    data = {"files": [(io.BytesIO(content), name) for name, content in files]}
    client.post(f"/sessions/{session_id}/resumes", data=data, headers={"X-CSRF-Token": csrf},
                content_type="multipart/form-data")
    with app.app_context():
        vacancy_id = storage.list_vacancies(storage.get_db(), session_id)[0]["id"]
    return session_id, vacancy_id


SECRETS = ("Айгерим", "Сапарова", EMAIL, "701 555", "Python-разработчик", "female")


def test_stored_columns_hold_no_personal_data(app, client, csrf, fake_gemini):
    _analysed(app, client, csrf)
    with app.app_context():
        db = storage.get_db()
        row = db.execute("SELECT text, filename, profile_json FROM resumes").fetchone()
        demo = db.execute("SELECT gender, age FROM demographics").fetchone()
        stored = " ".join(str(v) for v in (*row, *demo))
    assert all(str(v).startswith("enc1:") for v in (*row, *demo))
    for secret in SECRETS:  # (the age is sealed too: every value starts with enc1:)
        assert secret not in stored, secret


def test_ranking_session_page_and_exports_work_as_before(app, client, csrf, fake_gemini):
    session_id, vacancy_id = _analysed(app, client, csrf)
    page = client.get(f"/sessions/{session_id}/vacancies/{vacancy_id}").get_data(as_text=True)
    assert "Айгерим Сапарова" in page and EMAIL in page
    listing = client.get(f"/sessions/{session_id}/status").get_json()
    assert listing["resumes"][0]["filename"] == "Сапарова_Айгерим_CV.txt"
    csv_text = client.get(f"/sessions/{session_id}/vacancies/{vacancy_id}/export.csv").get_data().decode("utf-8-sig")
    assert "Айгерим Сапарова" in csv_text and EMAIL in csv_text
    assert client.get(f"/sessions/{session_id}/vacancies/{vacancy_id}/export.xlsx").status_code == 200
    assert client.get(f"/sessions/{session_id}/vacancies/{vacancy_id}?q=сапарова").status_code == 200
    found = client.get(f"/sessions/{session_id}/vacancies/{vacancy_id}?q=сапарова").get_data(as_text=True)
    assert "Айгерим Сапарова" in found  # search by name still works (in the app, not in SQL)


@pytest.mark.skipif(ON_POSTGRES, reason="the SQLite file itself")
def test_the_sqlite_file_has_no_personal_data(app, client, csrf, fake_gemini):
    _analysed(app, client, csrf)
    with app.app_context():
        storage.get_db().commit()
    raw = open(app.config["DATABASE"], "rb").read()  # noqa: PTH123 - whole file, as a thief would
    for secret in (EMAIL, "Сапарова_Айгерим", "701 555 12 34"):
        assert secret.encode("utf-8") not in raw, secret


@pytest.mark.skipif(not ON_POSTGRES, reason="needs TEST_DATABASE_URL (PostgreSQL)")
def test_a_postgresql_dump_has_no_personal_data(app, client, csrf, fake_gemini):
    from app.services import pgtools

    try:
        pg_dump = pgtools.tool("pg_dump", pgtools.server_major(TEST_DATABASE_URL))
    except pgtools.PgToolError:
        pytest.skip("pg_dump is not installed")
    _analysed(app, client, csrf)
    conninfo, env = pgtools.split(TEST_DATABASE_URL)
    dump = subprocess.run([pg_dump, "--data-only", "--table=resumes", "--table=demographics", "--table=users",  # noqa: S603
                           f"--dbname={conninfo}"], env=env, capture_output=True, text=True, check=True).stdout
    assert "enc1:" in dump
    for secret in (EMAIL, "Сапарова", "701 555"):
        assert secret not in dump, secret


def test_uploaded_file_waits_encrypted(app, client, csrf, fake_gemini):
    app.config.update(WORKER_MODE="external", SYNC_JOBS=False)  # APP-05: the worker parses later
    session_id = create_session(client, csrf)
    data = {"files": [(io.BytesIO(_resume_with_contacts(2)), "cv.txt")]}
    client.post(f"/sessions/{session_id}/resumes", data=data, headers={"X-CSRF-Token": csrf},
                content_type="multipart/form-data")
    with app.app_context():
        db = storage.get_db()
        stored = bytes(db.execute("SELECT raw_file FROM resumes").fetchone()[0])
        assert stored.startswith(b"ENC1") and EMAIL.encode() not in stored
        resume = storage.list_resumes(db, session_id)[0]
        assert storage.raw_file(storage.get_resume(db, resume["id"])) == _resume_with_contacts(2)


# Existing plaintext and key rotation ------------------------------------------------------------

def _plaintext_resume(app):
    """A row as written before SEC-02b."""
    owner = make_user(app, "legacy")
    with app.app_context():
        db = storage.get_db()
        session_id = storage.create_session(db, "Старая сессия", "ru", [("Python", "Python-разработчик, Django" * 3)], owner)
        with db:
            resume_id = storage._rowid(db.execute(
                "INSERT INTO resumes (session_id, filename, sha256, text, status, created_at, profile_json) "
                "VALUES (?, ?, ?, ?, 'done', ?, ?) RETURNING id",
                (session_id, "Иванов.pdf", "abc", "Иванов Иван, ivanov@example.kz", storage.now(),
                 '{"full_name": "Иванов Иван", "contacts": {"email": "ivanov@example.kz"}}')).fetchone())
            db.execute("INSERT INTO demographics (resume_id, gender, age) VALUES (?, 'male', ?)", (resume_id, "41"))
            db.execute("UPDATE users SET totp_secret = 'JBSWY3DPEHPK3PXP' WHERE id = ?", (owner,))
    return resume_id, owner


def _raw(app, resume_id, owner):
    with app.app_context():
        db = storage.get_db()
        values = [*db.execute("SELECT text, filename, profile_json FROM resumes WHERE id = ?", (resume_id,)).fetchone(),
                  *db.execute("SELECT gender, age FROM demographics WHERE resume_id = ?", (resume_id,)).fetchone(),
                  db.execute("SELECT totp_secret FROM users WHERE id = ?", (owner,)).fetchone()[0]]
    return [str(v) for v in values]


def _restart(app, **env):
    """The same database, a new app (as after a restart), with the given key settings."""
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        return create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": app.config["DATABASE"], "GEMINI_API_KEY": "k"})
    finally:
        for k, v in old.items():
            os.environ[k] = v if v is not None else ""
        fieldcrypt.configure(fieldcrypt.Keyring.from_values(os.environ["DATA_KEY"]))


def test_start_up_encrypts_old_plaintext_once(app):
    resume_id, owner = _plaintext_resume(app)
    assert _raw(app, resume_id, owner)[0].startswith("Иванов")
    restarted = _restart(app)
    values = _raw(restarted, resume_id, owner)
    assert all(v.startswith(f"enc1:{fieldcrypt.key_id(fieldcrypt.parse_key(TEST_DATA_KEY))}:") for v in values)
    with restarted.app_context():
        db = storage.get_db()
        resume = storage.get_resume(db, resume_id)
        assert resume["text"] == "Иванов Иван, ivanov@example.kz" and resume["filename"] == "Иванов.pdf"
        assert storage.load_profile(resume).full_name == "Иванов Иван"
        assert storage.totp_secret(storage.get_user(db, owner)) == "JBSWY3DPEHPK3PXP"
        assert all(v == 0 for v in storage.seal_all(db).values())  # nothing left: idempotent


def test_rotation_to_a_new_key(app):
    resume_id, owner = _plaintext_resume(app)
    _restart(app)  # sealed with the test key
    kid_b = fieldcrypt.key_id(fieldcrypt.parse_key(KEY_B))
    with pytest.raises(fieldcrypt.DataKeyError, match="is not configured"):
        _restart(app, DATA_KEY=KEY_B)  # the new key alone cannot read the data: refuses to start
    rotated = _restart(app, DATA_KEY=KEY_B, DATA_KEY_OLD=TEST_DATA_KEY)
    assert all(v.startswith(f"enc1:{kid_b}:") for v in _raw(rotated, resume_id, owner))
    fieldcrypt.configure(fieldcrypt.Keyring.from_values(KEY_B))  # from now on the old key is not needed
    with rotated.app_context():
        assert storage.get_resume(storage.get_db(), resume_id)["filename"] == "Иванов.pdf"


def test_data_key_rotate_command(app):
    resume_id, owner = _plaintext_resume(app)
    result = app.test_cli_runner().invoke(args=["data-key-rotate"])
    assert result.exit_code == 0 and "resumes.text=1" in result.output and "users.totp_secret=1" in result.output
    again = app.test_cli_runner().invoke(args=["data-key-rotate"])
    assert again.exit_code == 0 and "nothing to change" in again.output


def test_2fa_keeps_working_and_sessions_survive_the_encryption(app, client):
    from .test_mfa import _enable_2fa, _second_step, totp

    secret, _ = _enable_2fa(app, client)
    browser = app.test_client()
    login(browser, "tester")
    assert _second_step(browser, totp.code_at(secret, totp.current_step() + 1)).status_code == 302  # setup used the current step
    assert browser.get("/").status_code == 200
    restarted = _restart(app)
    with restarted.app_context():
        assert storage.totp_secret(storage.get_user_by_name(storage.get_db(), "tester")) == secret


def test_rollback_to_plaintext(app, client, csrf, fake_gemini):
    _analysed(app, client, csrf)
    runner = app.test_cli_runner()
    assert runner.invoke(args=["data-key-rotate", "--to-plaintext"], input="n\n").exit_code != 0  # asks first
    result = runner.invoke(args=["data-key-rotate", "--to-plaintext"], input="y\n")
    assert result.exit_code == 0 and "decrypted" in result.output and "resumes.text=1" in result.output
    with app.app_context():
        db = storage.get_db()
        text, filename = db.execute("SELECT text, filename FROM resumes").fetchone()
        assert EMAIL in text and filename == "Сапарова_Айгерим_CV.txt"
        assert str(db.execute("SELECT age FROM demographics").fetchone()[0]) == "27"
        assert all(v == 0 for v in storage.seal_all(db, plaintext=True).values())
        storage.seal_all(db)  # and back
        assert db.execute("SELECT text FROM resumes").fetchone()[0].startswith("enc1:")
