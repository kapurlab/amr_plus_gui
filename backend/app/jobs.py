import json
import logging
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Sequence

logger = logging.getLogger(__name__)

# A substring that must appear in /proc/<pid>/cmdline for a PID to be accepted as
# one of *our* pipeline processes. Guards against PID reuse (e.g. a recycled or
# kernel PID) being mistaken for a live job after a uvicorn restart.
_PIPELINE_MARKER = "amr_plus"


def _pid_is_pipeline(pid: int) -> bool:
    """True only if `pid` is alive AND its command line looks like our pipeline.

    Reading /proc/<pid>/cmdline both proves liveness and confirms identity, so a
    reused PID (or an unrelated/kernel PID) is never mistaken for a running job.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmdline = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return False
    return _PIPELINE_MARKER in cmdline


class JobManager:
    def __init__(self, jobs_dir: Path):
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        # This dir is shared across all lab users (it lives inside the shared
        # tool install, not under any one $HOME), so every member must be able
        # to write job state here. Make it group-writable + setgid so files
        # created by one user stay group-accessible to the rest. Best-effort:
        # a user who doesn't own the dir can't chmod it, which is fine — the
        # owner/admin sets it once and the bit sticks for everyone after.
        try:
            os.chmod(self.jobs_dir, 0o2775)
        except OSError:
            pass
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict] = {}
        self._restore_jobs()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _state_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _pid_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.pid"

    def _exit_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.exit"

    def _read_exit_code(self, job_id: str) -> Optional[int]:
        """Return the exit code the pipeline shell recorded, or None if absent."""
        try:
            text = self._exit_path(job_id).read_text().strip()
            return int(text)
        except (OSError, ValueError):
            return None

    def _write_state(self, job: Dict) -> None:
        try:
            self._state_path(job["id"]).write_text(json.dumps(job))
        except OSError:
            logger.exception("Failed to persist state for job %s", job.get("id"))

    # ------------------------------------------------------------------
    # Restore after a uvicorn restart
    # ------------------------------------------------------------------
    def _restore_jobs(self) -> None:
        """Re-load persisted jobs and re-attach to any pipeline still running.

        Completed jobs are loaded back into memory too, so the log/reconnect
        endpoints keep working after a uvicorn restart. Jobs marked running are
        only trusted if their PID is alive AND still looks like our pipeline.
        """
        for state_file in sorted(self.jobs_dir.glob("*.json")):
            try:
                job = json.loads(state_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            job_id = job.get("id")
            if not job_id:
                continue

            if job.get("status") != "running":
                with self._lock:
                    self._jobs[job_id] = job
                continue

            pid_path = self._pid_path(job_id)
            pid = None
            if pid_path.exists():
                try:
                    pid = int(pid_path.read_text().strip())
                except (ValueError, OSError):
                    pid = None

            if pid is not None and _pid_is_pipeline(pid):
                logger.info("Restored running job %s (pid %s)", job_id, pid)
                with self._lock:
                    self._jobs[job_id] = job
                threading.Thread(
                    target=self._watch_pid,
                    args=(job_id, pid, Path(job["log_path"])),
                    daemon=True,
                ).start()
            else:
                # Process is gone — finalize the job from whatever it left behind.
                pid_path.unlink(missing_ok=True)
                self._finalize(job_id, Path(job["log_path"]), in_memory_job=job)

    # ------------------------------------------------------------------
    # Completion handling (shared by _run, _watch_pid, _restore_jobs)
    # ------------------------------------------------------------------
    def _finalize(
        self,
        job_id: str,
        log_path: Path,
        exit_code: Optional[int] = None,
        in_memory_job: Optional[Dict] = None,
    ) -> None:
        """Mark a job finished, deriving success from the recorded exit code.

        Falls back to the log's completion marker when no exit file exists
        (e.g. the originating uvicorn died before it could be written).
        """
        if exit_code is None:
            exit_code = self._read_exit_code(job_id)

        if exit_code is None:
            # No exit file: trust the log marker the pipeline writes on clean exit.
            try:
                has_marker = "# finished_at_utc:" in log_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                has_marker = False
            status = "succeeded" if has_marker else "failed"
            exit_code = 0 if status == "succeeded" else -1
        else:
            status = "succeeded" if exit_code == 0 else "failed"

        # Ensure the log carries a completion marker so the SSE stream emits
        # [DONE] even for jobs whose original writer (uvicorn) had died.
        try:
            if "# finished_at_utc:" not in log_path.read_text(
                encoding="utf-8", errors="replace"
            ):
                with open(log_path, "a", encoding="utf-8") as log:
                    log.write(
                        f"\n# finished_at_utc: {datetime.now(timezone.utc).isoformat()}\n"
                    )
                    log.write(f"# exit_code: {exit_code}\n")
        except OSError:
            pass

        finished_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            job = self._jobs.get(job_id) or in_memory_job
            if job is not None:
                job["status"] = status
                job["exit_code"] = exit_code
                if not job.get("finished_at"):
                    job["finished_at"] = finished_at
                self._jobs[job_id] = job
                self._write_state(job)

        self._pid_path(job_id).unlink(missing_ok=True)
        self._exit_path(job_id).unlink(missing_ok=True)

    def _watch_pid(self, job_id: str, pid: int, log_path: Path) -> None:
        """Poll a detached pipeline PID until it exits, then finalize the job."""
        while _pid_is_pipeline(pid):
            time.sleep(2)
        self._finalize(job_id, log_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start_job(
        self,
        name: str,
        command: Sequence[str],
        cwd: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        log_path = self.jobs_dir / f"{job_id}.log"
        started_at = datetime.now(timezone.utc)
        command_display = shlex.join([str(part) for part in command])
        job = {
            "id": job_id,
            "name": name,
            "command": command_display,
            "argv": [str(part) for part in command],
            "cwd": str(cwd) if cwd else None,
            "status": "running",
            "exit_code": None,
            "log_path": str(log_path),
            "started_at": started_at.isoformat(),
            "finished_at": None,
            "duration_seconds": None,
        }
        self._write_state(job)
        with self._lock:
            self._jobs[job_id] = job
        threading.Thread(
            target=self._run,
            args=(job_id, command, cwd, env, log_path),
            daemon=True,
        ).start()
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict]:
        with self._lock:
            if job_id in self._jobs:
                return dict(self._jobs[job_id])
        # Fall back to the on-disk state (e.g. a job from a previous process
        # that was not yet loaded). Keeps /log and /results working.
        try:
            job = json.loads(self._state_path(job_id).read_text())
        except (OSError, json.JSONDecodeError):
            return None
        with self._lock:
            self._jobs[job_id] = job
        return dict(job)

    def list_jobs(self) -> list:
        with self._lock:
            return [dict(j) for j in self._jobs.values()]

    # ------------------------------------------------------------------
    # Run a job in this process
    # ------------------------------------------------------------------
    def _run(
        self,
        job_id: str,
        command: Sequence[str],
        cwd: Optional[Path],
        env: Optional[Dict[str, str]],
        log_path: Path,
    ) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path = self._pid_path(job_id)
        exit_path = self._exit_path(job_id)
        exit_path.unlink(missing_ok=True)

        command_argv = [str(part) for part in command]
        command_display = shlex.join(command_argv)
        # Provenance: record the exact command into <cwd>/.provenance/<tool>_run_cmd.txt
        # so the sample/output folder plainly shows what was run on the command line to
        # produce these results. Best-effort, append-per-run — never breaks a job.
        try:
            if cwd:
                _tool = Path(__file__).resolve().parents[2].name
                _prov = Path(cwd) / ".provenance"
                _prov.mkdir(parents=True, exist_ok=True)
                _name = (self._jobs.get(job_id) or {}).get("name", "")
                _ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
                _entry = [
                    "# " + "=" * 64,
                    f"# {_tool} - run command",
                    f"# run at:      {_ts}",
                    (f"# job:         {_name}" if _name else "# job:"),
                    f"# working dir: {cwd}",
                    "# " + "-" * 64,
                    "# Command executed (copy/paste to reproduce):",
                    command_display,
                    "",
                ]
                with open(_prov / f"{_tool}_run_cmd.txt", "a", encoding="utf-8") as _fh:
                    _fh.write("\n".join(_entry) + "\n")
        except Exception:
            pass
        wrapper_code = (
            "import subprocess, sys\n"
            "exit_path = sys.argv[1]\n"
            "argv = sys.argv[2:]\n"
            "exit_code = subprocess.run(argv).returncode\n"
            "with open(exit_path, 'w', encoding='utf-8') as fh:\n"
            "    fh.write(str(exit_code))\n"
            "sys.exit(exit_code)\n"
        )
        wrapped_argv = [sys.executable, "-c", wrapper_code, str(exit_path), *command_argv]

        with open(log_path, "w", encoding="utf-8") as log:
            started_at = datetime.now(timezone.utc)
            log.write(f"# started_at_utc: {started_at.isoformat()}\n")
            log.write(f"$ {command_display}\n\n")
            log.flush()
            try:
                process = subprocess.Popen(
                    wrapped_argv,
                    cwd=str(cwd) if cwd else None,
                    env={**os.environ, **(env or {})},
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    text=True,
                    start_new_session=True,  # own process group/session: survives
                    stdin=subprocess.DEVNULL,  # tmux SIGHUP on OOD session teardown
                )
            except OSError as exc:
                log.write(f"\nERROR: failed to launch pipeline: {exc}\n")
                log.flush()
                self._finalize(job_id, log_path, exit_code=-1)
                return

            pid_path.write_text(str(process.pid))
            exit_code = process.wait()
            finished_at = datetime.now(timezone.utc)
            duration = (finished_at - started_at).total_seconds()
            log.write(f"\n# finished_at_utc: {finished_at.isoformat()}\n")
            log.write(f"# duration_seconds: {duration:.2f}\n")
            log.flush()

        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["duration_seconds"] = duration
                job["finished_at"] = finished_at.isoformat()
        self._finalize(job_id, log_path, exit_code=exit_code)
