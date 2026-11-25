"""INF-07: CI refuses secrets files that are not SOPS-encrypted."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("check_secrets", Path(__file__).parents[1] / "scripts" / "check_secrets.py")
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)  # type: ignore[union-attr]

ENCRYPTED = """apiVersion: v1
kind: Secret
metadata:
    name: hrscreen-web
stringData:
    FLASK_SECRET_KEY: ENC[AES256_GCM,data:abc=,iv:x=,tag:y=,type:str]
    DATA_KEY: ENC[AES256_GCM,data:def=,iv:x=,tag:y=,type:str]
sops:
    age:
        - recipient: age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq
    encrypted_regex: ^(data|stringData)$
    version: 3.13.3
"""


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_encrypted_file_passes(tmp_path):
    assert check.problems(_write(tmp_path, "secrets.enc.yaml", ENCRYPTED)) == []
    assert check.main([str(tmp_path / "secrets.enc.yaml"), str(tmp_path / "missing.enc.yaml")]) == 0


def test_one_plain_value_fails(tmp_path):
    text = ENCRYPTED.replace("ENC[AES256_GCM,data:def=,iv:x=,tag:y=,type:str]", "c2VjcmV0")
    found = check.problems(_write(tmp_path, "secrets.enc.yaml", text))
    assert found == [f"{tmp_path / 'secrets.enc.yaml'}:7: stringData.DATA_KEY is not encrypted"]


def test_plain_working_copy_and_missing_metadata_fail(tmp_path, capsys):
    plain = _write(tmp_path, "secrets.yaml", "stringData:\n  KEY: value\n")
    no_meta = _write(tmp_path, "other.enc.yaml", ENCRYPTED.split("sops:")[0])
    assert "plain secrets file" in check.problems(plain)[0]
    assert "no SOPS metadata" in check.problems(no_meta)[0]
    assert check.main([str(plain), str(no_meta)]) == 1
    assert "2 problem(s)" in capsys.readouterr().out


def test_data_section_is_checked_too(tmp_path):
    text = ENCRYPTED.replace("stringData:", "data:")
    assert check.problems(_write(tmp_path, "s.enc.yaml", text)) == []
    bad = text.replace("ENC[AES256_GCM,data:abc=,iv:x=,tag:y=,type:str]", '"plain"')
    assert "data.FLASK_SECRET_KEY is not encrypted" in check.problems(_write(tmp_path, "b.enc.yaml", bad))[0]
