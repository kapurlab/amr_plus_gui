"""The metadata step of an SRA download really runs.

The generated download script ends in an explicit `exit`, so a step appended
after it never runs. These tests run the real generated script, with stand-in
downloaders that fail at once, and check that the step placed by
`after_download` ran and that it could not change the script's exit code.

Run with:  pytest tests/
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.sra import build_download_script  # noqa: E402


def _run(script: str, folder: Path) -> subprocess.CompletedProcess:
    path = folder / "download_sra.sh"
    path.write_text(script, encoding="utf-8")
    fake = folder / "fakebin"
    fake.mkdir()
    for tool, rc in (("curl", 1), ("wget", 1), ("fasterq-dump", 1), ("prefetch", 1),
                     ("enaDataGet", 1), ("sleep", 0)):
        (fake / tool).write_text(f"#!/bin/sh\nexit {rc}\n")
        (fake / tool).chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ.get('PATH', '')}"}
    return subprocess.run(["bash", str(path)], cwd=folder, capture_output=True, text=True,
                          timeout=180, env=env)


def test_the_step_runs_before_the_scripts_exit_and_cannot_change_its_code(tmp_path):
    script = build_download_script(tmp_path, ["SRR0000001"], False,
                                   after_download="touch ran_after\nfalse || true\n")
    assert script.index("touch ran_after") < script.rindex("exit 0")
    proc = _run(script, tmp_path)
    assert (tmp_path / "ran_after").is_file(), proc.stdout[-400:] + proc.stderr[-400:]
    # Every downloader failed, so the download itself failed, and still says so.
    assert proc.returncode == 1


def test_text_appended_after_the_script_would_not_run(tmp_path):
    # The reason the step is a parameter and not an append.
    script = build_download_script(tmp_path, ["SRR0000001"], False) + "\ntouch appended\n"
    _run(script, tmp_path)
    assert not (tmp_path / "appended").exists()


def test_without_a_step_the_script_is_unchanged(tmp_path):
    plain = build_download_script(tmp_path, ["SRR0000001"], False)
    with_step = build_download_script(tmp_path, ["SRR0000001"], False, after_download="true\n")
    assert plain.count("exit 0") == with_step.count("exit 0")
    assert with_step.replace("true\n\nif [", "\nif [") == plain


def test_the_backend_builds_a_quoted_step_and_none_for_no_runs(tmp_path):
    pytest.importorskip("fastapi")
    from app import main as backend_main

    project = tmp_path / "my project"
    project.mkdir()
    step = backend_main._sample_metadata_step(project, ["SRR1234567", "ERR7654321"])
    assert "sample_metadata.py" in step and "fetch --source sra" in step
    assert f"'{project}'" in step and "SRR1234567 ERR7654321" in step
    assert step.rstrip().endswith("|| true")
    assert backend_main._sample_metadata_step(project, []) == ""
    # In a real shell, for a sample that is not a run: exits 0, writes nothing.
    proc = subprocess.run(["bash", "-c", backend_main._sample_metadata_step(project, ["farm12"])],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0 and not (project / "sample_metadata.tsv").exists()
