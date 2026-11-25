"""Tests for sending a cohort tree to iTOL. Nothing here touches the network:
the request is built and the answer is read against stand-ins.

Run with:  pytest tests/
"""

from __future__ import annotations

import io
import json
import ssl
import subprocess
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))
sys.path.insert(0, str(REPO / "backend"))

import itol_upload  # noqa: E402

KEY = "test-key-not-a-real-one"


def _report(tmp_path: Path, popup: bool = True) -> Path:
    project = tmp_path / "proj"
    out = project / "cohort_report"
    out.mkdir(parents=True)
    with zipfile.ZipFile(out / "itol_batch.zip", "w") as zf:
        zf.writestr("proj.tree", "(A:1,B:1);\n")
        zf.writestr("colorstrip_01_state.txt", "DATASET_COLORSTRIP\nSEPARATOR TAB\nDATA\nA\t#D98C9A\tPR\n")
        if popup:
            zf.writestr("popup.txt", "POPUP_INFO\nSEPARATOR TAB\nDATA\nA\tA\t<table></table>\n")
    (out / "cohort_checks.json").write_text(json.dumps({"generated": "2026-11-25 05:27"}))
    return project


def test_multipart_body_carries_the_fields_and_the_file():
    body, content_type = itol_upload.encode_multipart(
        {"APIkey": KEY, "projectName": "Salmonella\r\nX-Injected: 1"}, "zipFile", "itol batch.zip", b"PK\x03\x04data")
    boundary = content_type.split("boundary=")[1]
    assert content_type.startswith("multipart/form-data; boundary=")
    text = body.decode("latin-1")
    assert text.count("--" + boundary) == 4 and text.rstrip().endswith("--" + boundary + "--")
    assert 'name="APIkey"\r\n\r\n' + KEY in text
    assert 'name="zipFile"; filename="itol_batch.zip"' in text and "PK\x03\x04data" in text
    assert "Salmonella X-Injected: 1" in text and "\r\nX-Injected" not in text   # a value cannot add a header


def test_success_gives_the_tree_id_and_keeps_warnings():
    outcome = itol_upload.parse_response("Warning: dataset popup.txt skipped\nSUCCESS: 1234567890123\n")
    assert outcome.ok and outcome.tree_id == "1234567890123"
    assert outcome.url == "https://itol.embl.de/tree/1234567890123"
    assert outcome.warnings == ["Warning: dataset popup.txt skipped"]


@pytest.mark.parametrize("text", ["ERR: Invalid API key", "", "<html><body><h1>502 Bad Gateway</h1></body></html>",
                                  "The upload was a SUCCESS story"])
def test_anything_else_is_a_failure_with_its_reason(text):
    outcome = itol_upload.parse_response(text)
    assert not outcome.ok and not outcome.tree_id and outcome.message
    assert "<" not in outcome.message


def test_the_popup_can_be_left_out(tmp_path):
    report = _report(tmp_path) / "cohort_report"
    full, names = itol_upload.read_batch(report)
    assert "popup.txt" in names and full == (report / "itol_batch.zip").read_bytes()
    slim, names = itol_upload.read_batch(report, include_popup=False)
    assert names == ["proj.tree", "colorstrip_01_state.txt"]
    assert zipfile.ZipFile(io.BytesIO(slim)).namelist() == names


def test_a_zip_without_exactly_one_tree_is_refused(tmp_path):
    out = tmp_path / "cohort_report"
    out.mkdir()
    with pytest.raises(ValueError, match="no itol_batch.zip"):
        itol_upload.read_batch(out)
    with zipfile.ZipFile(out / "itol_batch.zip", "w") as zf:
        zf.writestr("a.tree", "(A,B);")
        zf.writestr("b.tree", "(A,B);")
    with pytest.raises(ValueError, match="exactly one"):
        itol_upload.read_batch(out)


def test_send_report_posts_once_and_records_where_it_went(tmp_path):
    project = _report(tmp_path)
    calls = []

    def fake_post(url, body, content_type):
        calls.append((url, body, content_type))
        return "SUCCESS: 987654321\n"

    outcome = itol_upload.send_report(project, KEY, "Salmonella", http_post=fake_post)
    assert outcome.ok and len(calls) == 1
    url, body, _ = calls[0]
    assert url == "https://itol.embl.de/batch_uploader.cgi"
    text = body.decode("latin-1")
    assert 'name="projectName"\r\n\r\nSalmonella' in text and 'name="treeName"\r\n\r\nproj' in text
    assert "Cohort report built 2026-11-25 05:27" in text
    record_path = project / "cohort_report" / "itol_upload.json"
    record = json.loads(record_path.read_text())
    assert record["tree_id"] == "987654321" and record["itol_project"] == "Salmonella"
    assert record["files"] == ["proj.tree", "colorstrip_01_state.txt", "popup.txt"]
    assert KEY not in record_path.read_text()                # the key is never written down


def test_nothing_is_posted_without_a_key_or_a_project(tmp_path):
    project = _report(tmp_path)

    def never(*_a):
        raise AssertionError("posted")

    assert "API key" in itol_upload.send_report(project, "", "Salmonella", http_post=never).message
    assert "project" in itol_upload.send_report(project, KEY, " ", http_post=never).message
    assert not (project / "cohort_report" / "itol_upload.json").exists()


def test_a_failed_upload_leaves_no_record_and_says_why(tmp_path):
    project = _report(tmp_path)
    outcome = itol_upload.send_report(project, KEY, "Nope", http_post=lambda *_a: "ERR: Project not found")
    assert not outcome.ok and "Project not found" in outcome.message
    assert not (project / "cohort_report" / "itol_upload.json").exists()


def test_an_unverified_certificate_stops_the_upload(tmp_path):
    project = _report(tmp_path)

    def expired(*_a):
        error = ssl.SSLCertVerificationError(1, "certificate verify failed")
        error.verify_message = "certificate has expired"
        raise urllib.error.URLError(error)

    outcome = itol_upload.send_report(project, KEY, "Salmonella", http_post=expired)
    assert not outcome.ok and "certificate has expired" in outcome.message and "nothing was sent" in outcome.message


def test_the_real_request_verifies_certificates():
    # The default opener is used as it is: no context that skips verification.
    source = (REPO / "bin" / "itol_upload.py").read_text()
    assert "CERT_NONE" not in source and "_create_unverified_context" not in source
    assert "check_hostname = False" not in source


def test_cli_reads_the_key_from_the_environment_only(tmp_path):
    project = _report(tmp_path)
    proc = subprocess.run([sys.executable, str(REPO / "bin" / "itol_upload.py"), "--project", str(project),
                           "--itol-project", "Salmonella"], capture_output=True, text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin"})
    assert proc.returncode == 1
    assert "ITOL_API_KEY is not set" in json.loads(proc.stdout)["message"]
    help_text = subprocess.run([sys.executable, str(REPO / "bin" / "itol_upload.py"), "--help"],
                               capture_output=True, text=True, timeout=60).stdout
    assert "--api-key" not in help_text and "--key" not in help_text


def test_key_lookup_prefers_the_environment_then_the_file(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "ITOL_KEY_PATH", tmp_path / "itol_api_key")
    monkeypatch.delenv("ITOL_API_KEY", raising=False)
    assert config.itol_api_key() == ""
    (tmp_path / "itol_api_key").write_text("# my iTOL key\n  from-file  \n")
    assert config.itol_api_key() == "from-file"
    monkeypatch.setenv("ITOL_API_KEY", "from-env")
    assert config.itol_api_key() == "from-env"


def test_the_route_passes_the_key_by_environment_and_never_returns_it(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from app import config, main as backend
    project = _report(tmp_path)
    monkeypatch.setattr(backend, "_get_project_dir", lambda _name: project)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.delenv("ITOL_API_KEY", raising=False)
    monkeypatch.setattr(backend, "itol_api_key", lambda: "")
    with pytest.raises(HTTPException) as refused:
        backend.api_cohort_itol_upload("proj", backend.ItolUpload(itol_project="Salmonella"))
    assert refused.value.status_code == 400 and "API key" in refused.value.detail

    monkeypatch.setattr(backend, "itol_api_key", lambda: KEY)
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"], seen["env"] = command, kwargs["env"]
        reply = {"ok": True, "tree_id": "42", "url": "https://itol.embl.de/tree/42", "warnings": [],
                 "files": ["proj.tree"]}
        return subprocess.CompletedProcess(command, 0, json.dumps(reply) + "\n", "")

    monkeypatch.setattr(backend.subprocess, "run", fake_run)
    response = backend.api_cohort_itol_upload(
        "proj", backend.ItolUpload(itol_project="Salmonella", include_popup=False))
    assert KEY not in " ".join(seen["command"]) and seen["env"]["ITOL_API_KEY"] == KEY
    assert "--no-popup" in seen["command"] and "--itol-project=Salmonella" in seen["command"]
    body = response.body.decode()
    assert KEY not in body and json.loads(body)["url"] == "https://itol.embl.de/tree/42"
    assert json.loads((tmp_path / "config.json").read_text())["itol_project"] == "Salmonella"

    def refused_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, json.dumps({"ok": False, "message": "ERR: no"}) + "\n", "")

    monkeypatch.setattr(backend.subprocess, "run", refused_run)
    with pytest.raises(HTTPException) as failed:
        backend.api_cohort_itol_upload("proj", backend.ItolUpload(itol_project="Salmonella"))
    assert failed.value.status_code == 502 and "ERR: no" in failed.value.detail
