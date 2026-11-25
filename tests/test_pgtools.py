"""pg_dump / pg_restore lookup: tools of the server's major version first (CI fix 2026-11-24)."""
from __future__ import annotations

import os
import stat

import pytest

from app.services import pgtools

from .conftest import ON_POSTGRES, TEST_DATABASE_URL

EXE = ".exe" if os.name == "nt" else ""


def _fake_tool(folder, name="pg_restore"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + EXE)
    path.write_text("")
    return path


@pytest.fixture()
def no_system_tools(monkeypatch, tmp_path):
    monkeypatch.delenv("PG_BIN_DIR", raising=False)
    monkeypatch.setattr(pgtools.shutil, "which", lambda name: None)
    monkeypatch.setattr(pgtools, "WINDOWS_DEFAULT", tmp_path / "not-installed")
    monkeypatch.setattr(pgtools, "VERSIONED_DIRS", (str(tmp_path / "pg{major}"),))


@pytest.mark.parametrize(("output", "major"), [
    ("pg_restore (PostgreSQL) 18.1 (Ubuntu 18.1-1.pgdg24.04+1)", 18),
    ("pg_dump (PostgreSQL) 16.15", 16),
    ("pg_restore.exe (PostgreSQL) 16.4", 16),
    ("pg_dump (PostgreSQL) 9.6.24", 9),
    ("something else", None),
])
def test_parse_major(output, major):
    assert pgtools.parse_major(output) == major


def test_the_server_version_folder_comes_first(no_system_tools, tmp_path, monkeypatch):
    sixteen = _fake_tool(tmp_path / "pg16")
    _fake_tool(tmp_path / "pg18")
    newest = _fake_tool(tmp_path / "on-path")
    monkeypatch.setattr(pgtools.shutil, "which", lambda name: str(newest))
    assert pgtools.tool("pg_restore", 16) == str(sixteen)
    assert pgtools.tool("pg_restore") == str(newest)  # version unknown: PATH as before


def test_pg_bin_dir_wins(no_system_tools, tmp_path, monkeypatch):
    _fake_tool(tmp_path / "pg16")
    chosen = _fake_tool(tmp_path / "chosen")
    monkeypatch.setenv("PG_BIN_DIR", str(tmp_path / "chosen"))
    assert pgtools.tool("pg_restore", 16) == str(chosen)


def test_missing_tools_are_named(no_system_tools):
    with pytest.raises(pgtools.PgToolError, match="pg_dump not found"):
        pgtools.tool("pg_dump", 16)


def test_a_newer_pg_restore_is_refused_with_a_clear_message(monkeypatch, tmp_path):
    # pg_restore 17+ sends `SET transaction_timeout`, which PostgreSQL 16 does not know.
    monkeypatch.setattr(pgtools, "server_major", lambda url: 16)
    monkeypatch.setattr(pgtools, "tool", lambda name, major=None: "pg_restore")
    monkeypatch.setattr(pgtools, "client_major", lambda path: 18)
    with pytest.raises(pgtools.PgToolError, match="pg_restore 18 cannot restore into PostgreSQL 16"):
        pgtools.verify(tmp_path / "copy.dump", ["users"], "postgresql://scratch")


@pytest.mark.skipif(os.name == "nt", reason="the fake tool is a shell script")
def test_client_major_runs_the_tool(tmp_path):
    script = tmp_path / "pg_restore"
    script.write_text("#!/bin/sh\necho 'pg_restore (PostgreSQL) 16.15'\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    assert pgtools.client_major(str(script)) == 16


@pytest.mark.skipif(not ON_POSTGRES, reason="needs TEST_DATABASE_URL (PostgreSQL)")
def test_server_major_of_the_test_database():
    assert pgtools.server_major(TEST_DATABASE_URL) >= 13
