"""
AMRFinderPlus GUI — FastAPI backend.

Serves the React SPA from frontend/dist/ and provides:
  /api/projects        — list shared + personal projects (FASTQ browser)
  /api/projects/{n}/samples — list FASTQ pairs in project/download/
  /api/config          — get/set user config (DB paths)
  /api/organism-options — valid AMRFinderPlus --organism tokens (cached)
  /api/run             — start an amr_pipeline.py run
  /api/jobs            — list running/completed jobs
  /api/jobs/{id}       — job detail
  /api/jobs/{id}/log   — SSE stream of the job log
  /api/projects/{n}/samples/{s}/amr-results — per-sample result files
  /api/projects/{n}/samples/{s}/amr-table   — parsed AMRFinderPlus TSV

This backend is a sibling of vsnp_gui and kraken_id_parse_gui and shares their
project layout. All URLs are served from / (uvicorn is behind the OOD rnode
proxy — relative paths only).
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import DEFAULTS, load_config, save_config, shared_projects_root
from .jobs import JobManager
from .request_safety import install_request_safety
from .sra import (
    SRAExpansionError,
    build_download_script,
    expand_accessions_with_mapping,
    write_crosswalk_tsv,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent          # this tool's checkout, wherever it is
_BIN_DIR = _REPO_ROOT / "bin"
_CONFIG_DIR = _REPO_ROOT / "config"
_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"

# Shared project root. Resolved per call via config.shared_projects_root() —
# BDTOOLS_SHARED_PROJECTS_ROOT, then the user's setting, then the legacy server
# path — so this is correct on macOS, WSL, a lab server and any OOD site, and so
# the Settings value is actually honoured. It used to be a hard-coded literal
# here, which meant changing the setting moved nothing.

# Jobs log directory (inside repo so it survives across sessions)
_JOBS_DIR = _REPO_ROOT / "backend" / "jobs"

# Fallback list of valid AMRFinderPlus --organism tokens. The live list from
# `amrfinder -l` is DB-version dependent and authoritative; this file is only
# consulted when that command is unavailable.
_ORGANISMS_FALLBACK = _CONFIG_DIR / "amrfinder_organisms.txt"


def _resolve_app_version() -> str:
    """Version of the deployed checkout — the exact string the Diagnostic
    Tools Dashboard shows for this tool (`git describe --tags --always`,
    the same command bdtools runs). Resolved once at startup; empty when
    git or the .git dir is unavailable, in which case the frontend falls
    back to its built-in constant."""
    try:
        out = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parents[2]),
             "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


APP_VERSION = _resolve_app_version()

# ---------------------------------------------------------------------------
# App & job manager
# ---------------------------------------------------------------------------
app = FastAPI(title="AMRFinderPlus GUI")
install_request_safety(app)

job_manager = JobManager(_JOBS_DIR)


# ---------------------------------------------------------------------------
# Helpers — project listing
# ---------------------------------------------------------------------------
_SCOPE_SHARED = "shared"
_SCOPE_PERSONAL = "personal"


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime if p.is_dir() else 0
    except PermissionError:
        return 0


def _count_project_reads(download_dir: Path, step1_dir: Path) -> int:
    """Count input read files (*.fastq.gz) across download/ and step1/.

    Native projects keep reads in download/; vSNP/Roar-imported projects keep
    them in step1/<sample>/ (and may symlink them into download/). Count the
    union, deduped by resolved path, skipping *_unmapped_* (the unmapped-read
    subset vSNP3 emits — not an input read set). step1 is globbed one level deep.
    """
    seen: set = set()
    candidates = []
    if download_dir.is_dir():
        candidates += download_dir.rglob("*.fastq.gz")
    if step1_dir.is_dir():
        candidates += step1_dir.glob("*/*.fastq.gz")
    for f in candidates:
        if "_unmapped_" in f.name:
            continue
        try:
            key = f.resolve()
        except OSError:
            key = f
        seen.add(key)
    return len(seen)


def _list_projects_from_root(root: Path, scope: str) -> List[Dict]:
    if not root.is_dir():
        return []
    projects = []
    try:
        entries = sorted(root.iterdir(), key=_safe_mtime, reverse=True)
    except PermissionError:
        return []
    for p in entries:
        try:
            if not p.is_dir() or p.name.startswith("."):
                continue
        except PermissionError:
            continue
        download_dir = p / "download"
        try:
            fastq_count = _count_project_reads(download_dir, p / "step1")
        except PermissionError:
            fastq_count = -1  # signals "no access" to frontend
        amr_runs = []
        amr_dir = p / "amr"
        try:
            if amr_dir.is_dir():
                amr_runs = [d.name for d in sorted(amr_dir.iterdir()) if d.is_dir()]
        except PermissionError:
            pass
        projects.append({
            "name": p.name,
            "path": str(p),
            "scope": scope,
            "fastq_count": fastq_count,
            "amr_runs": amr_runs,
        })
    return projects


def _get_project_dir(name: str) -> Optional[Path]:
    """Find a project dir in shared then personal roots."""
    if "/" in name or name.startswith("."):
        return None
    cfg = load_config()
    for root in [shared_projects_root(), Path(cfg.get("projects_root", ""))]:
        if root is None or not str(root).strip():
            continue
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Project creation.
#
# A project created here uses the SAME on-disk skeleton vSNP/Kraken GUIs
# create, so a project made in AMRFinderPlus GUI is immediately usable in the
# siblings (and vice versa) — all tools resolve the same shared-projects root
# (see config.shared_projects_root) plus the user's own, and list whatever is
# on disk. We add the amr/ subdir up
# front so the sample browser and results endpoints have a stable layout.
# ---------------------------------------------------------------------------
_PROJECT_NAME_OK_CHARSET = re.compile(r"^[A-Za-z0-9._-]+$")


def _normalize_project_name(name: str) -> str:
    """Filesystem-safe project dir name. Mirrors the siblings' rules so a name
    accepted in one tool is accepted in the others: spaces auto-convert to
    underscores, other unsafe chars are rejected with a clear message."""
    if not isinstance(name, str):
        raise ValueError("Project name must be a string")
    cleaned = re.sub(r"\s+", "_", name.strip())
    if not cleaned:
        raise ValueError("Project name is empty")
    if cleaned.startswith("."):
        raise ValueError("Project name cannot start with '.'")
    if len(cleaned) > 100:
        raise ValueError("Project name too long (max 100 characters)")
    if not _PROJECT_NAME_OK_CHARSET.match(cleaned):
        bad = sorted(set(ch for ch in cleaned if not re.match(r"[A-Za-z0-9._-]", ch)))
        raise ValueError(
            f"Project name contains unsupported characters: {''.join(bad)!r}. "
            "Only letters, digits, _ - . are allowed (spaces become underscores)."
        )
    return cleaned


def _ensure_project_dirs(project_dir: Path) -> None:
    (project_dir / "download").mkdir(parents=True, exist_ok=True)
    (project_dir / "amr").mkdir(parents=True, exist_ok=True)
    # vSNP-compatible layout so the project is shared cleanly between tools.
    (project_dir / "step1").mkdir(parents=True, exist_ok=True)
    (project_dir / "step2" / "vcf_source").mkdir(parents=True, exist_ok=True)
    (project_dir / f"{project_dir.name}_VCFs").mkdir(parents=True, exist_ok=True)


def _create_project(name: str, scope: str) -> Path:
    """Create a project under the requested scope ('personal' or 'shared')."""
    name = _normalize_project_name(name)
    cfg = load_config()
    if scope == _SCOPE_SHARED:
        root = shared_projects_root()
        if root is None:
            raise ValueError(
                "This deployment has no shared projects root. Set one in Settings "
                "(shared_projects_root) or create the project under your own root.")
    else:
        root = Path(cfg.get("projects_root", "") or (Path.home() / "projects"))
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"Cannot create projects root {root}: {exc}")
    project_dir = root / name
    if project_dir.exists():
        raise ValueError(f"Project already exists: {name}")
    try:
        _ensure_project_dirs(project_dir)
    except PermissionError:
        raise ValueError(
            f"No permission to create a project under {root}. "
            "Shared projects require lab write access; create it as a personal "
            "project instead."
        )
    meta = {"name": name, "created_at": _now_iso(), "status": "created"}
    try:
        with open(project_dir / "project.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, sort_keys=True)
    except OSError:
        pass
    return project_dir


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


# Matches _R1/_R2 (with optional _001 etc.) or _1/_2 immediately before .fastq.gz
_READ_TAG_RE = re.compile(r'(?:_R([12])(?:_\d+)?|_([12]))\.fastq\.gz$', re.IGNORECASE)


def _strip_read_tag(filename: str):
    """Return (base, read_num) where read_num is '1', '2', or None."""
    m = _READ_TAG_RE.search(filename)
    if m:
        tag = m.group(1) or m.group(2)
        return filename[:m.start()], tag
    return filename[:-len(".fastq.gz")], None


def _list_fastq_pairs(download_dir: Path) -> List[Dict]:
    """Return samples as {sample, paired, r1, r1_name, r2, r2_name} dicts.

    Handles both Illumina (_R1/_R2) and SRA (_1/_2) naming conventions.
    Files with no read suffix are treated as single-end.
    """
    try:
        all_fq = sorted(download_dir.glob("*.fastq.gz"))
    except PermissionError:
        return []

    groups: Dict[str, Dict] = {}
    for fq in all_fq:
        base, tag = _strip_read_tag(fq.name)
        if base not in groups:
            groups[base] = {"r1": None, "r2": None, "extras": []}
        g = groups[base]
        if tag == "1":
            g["r1"] = fq
        elif tag == "2":
            g["r2"] = fq
        else:
            g["extras"].append(fq)

    pairs = []
    for base, g in groups.items():
        r1, r2 = g["r1"], g["r2"]
        if r1 or r2:
            eff_r1 = r1 or r2
            eff_r2 = r2 if r1 else None
            pairs.append({
                "sample": base,
                "paired": bool(r1 and r2),
                "r1": str(eff_r1), "r1_name": eff_r1.name,
                "r1_size": eff_r1.stat().st_size,
                "r2": str(eff_r2) if eff_r2 else None,
                "r2_name": eff_r2.name if eff_r2 else None,
                "r2_size": eff_r2.stat().st_size if eff_r2 else None,
            })
        for fq in g["extras"]:
            pairs.append({
                "sample": fq.name[:-len(".fastq.gz")],
                "paired": False,
                "r1": str(fq), "r1_name": fq.name,
                "r1_size": fq.stat().st_size,
                "r2": None, "r2_name": None,
                "r2_size": None,
            })

    return pairs


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/projects")
def api_list_projects():
    cfg = load_config()
    shared_root = shared_projects_root()
    projects = (_list_projects_from_root(shared_root, _SCOPE_SHARED)
                if shared_root is not None else [])
    personal_root = Path(cfg.get("projects_root", ""))
    if personal_root != shared_root:
        personal = _list_projects_from_root(personal_root, _SCOPE_PERSONAL)
        seen = {p["name"] for p in projects}
        projects += [p for p in personal if p["name"] not in seen]
    return JSONResponse(projects)


class ProjectCreate(BaseModel):
    name: str
    scope: Optional[str] = None   # "personal" (default) | "shared"


@app.post("/api/projects")
def api_create_project(payload: ProjectCreate):
    scope = (payload.scope or _SCOPE_PERSONAL).strip() or _SCOPE_PERSONAL
    if scope not in (_SCOPE_PERSONAL, _SCOPE_SHARED):
        raise HTTPException(400, f"Invalid scope: {scope!r}")
    try:
        project_dir = _create_project(payload.name, scope)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return JSONResponse({"name": project_dir.name, "path": str(project_dir), "scope": scope})


# ---------------------------------------------------------------------------
# Loading samples into a project — import (link), upload (drag & drop), and
# SRA download. Mirrors the siblings so a project can be populated from within
# any tool. All three land FASTQs in <project>/download/.
# ---------------------------------------------------------------------------
def _writable_project_dir(name: str) -> Path:
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    (project_dir / "download").mkdir(parents=True, exist_ok=True)
    return project_dir


@app.get("/api/projects/{name}/inputs")
def api_project_inputs(name: str):
    """List files currently in <project>/download/ (name + size + mtime)."""
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    download_dir = project_dir / "download"
    files: List[Dict] = []
    total = 0
    if download_dir.is_dir():
        for p in sorted(download_dir.iterdir()):
            if not p.is_file() or p.name.startswith("."):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            files.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
            total += st.st_size
    return JSONResponse({"files": files, "total_bytes": total, "count": len(files)})


@app.delete("/api/projects/{name}/inputs/{filename}")
def api_project_input_delete(name: str, filename: str):
    if not filename or "/" in filename or "\\" in filename or filename.startswith(".") or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    target = project_dir / "download" / filename
    if not target.is_file() and not target.is_symlink():
        raise HTTPException(404, f"File not found: {filename}")
    target.unlink()
    return JSONResponse({"deleted": filename})


@app.post("/api/projects/{name}/upload")
async def api_project_upload(name: str, files: List[UploadFile] = File(...)):
    """Save drag-and-dropped / chosen FASTQ files into <project>/download/."""
    project_dir = _writable_project_dir(name)
    download_dir = project_dir / "download"
    saved = 0
    for f in files:
        if not f.filename:
            continue
        target = download_dir / Path(f.filename).name
        async with aiofiles.open(target, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                await out.write(chunk)
        saved += 1
    return JSONResponse({"uploaded": saved})


class LinkLocalRequest(BaseModel):
    path: str


@app.post("/api/projects/{name}/link-local")
def api_project_link_local(name: str, payload: LinkLocalRequest):
    """Symlink every *.fastq.gz (or *.fasta/*.fa/*.fna assembly) under a
    server-side directory into download/.

    Lets users 'import' reads/assemblies that already live on the shared
    filesystem without copying gigabytes around.
    """
    project_dir = _writable_project_dir(name)
    src = Path((payload.path or "").strip()).expanduser()
    if not src.exists():
        raise HTTPException(400, f"Input path not found: {src}")
    download_dir = project_dir / "download"
    _accept = (".fastq.gz", ".fasta", ".fa", ".fna")
    if src.is_file():
        candidates = [src]
    else:
        candidates = sorted(
            f for f in src.iterdir()
            if f.is_file() and f.name.lower().endswith(_accept)
        )
    count = 0
    for f in candidates:
        if not f.name.lower().endswith(_accept):
            continue
        target = download_dir / f.name
        if not target.exists():
            target.symlink_to(f.resolve())
            count += 1
    return JSONResponse({"linked": count})


class SraRequest(BaseModel):
    accessions: List[str]
    folder: Optional[str] = None


@app.post("/api/projects/{name}/sra/download")
def api_project_sra_download(name: str, payload: SraRequest):
    """Resolve SRA accessions and kick off a background download into
    download/. Uses curl/ENA + (if present) fasterq-dump."""
    project_dir = _writable_project_dir(name)
    try:
        expanded, mapping = expand_accessions_with_mapping(payload.accessions, strict=True)
    except SRAExpansionError as e:
        raise HTTPException(
            502,
            f"Could not resolve SRA accessions via NCBI eutils: {e}. "
            "This is usually NCBI rate-limiting; wait ~30 s and retry.",
        )
    download_root = project_dir / "download"
    if payload.folder:
        download_root = download_root / Path(payload.folder).name
    download_root.mkdir(parents=True, exist_ok=True)
    try:
        write_crosswalk_tsv(download_root, mapping)
    except OSError as e:
        logger.warning("Failed to write sra_crosswalk.tsv: %s", e)
    script = build_download_script(download_root, expanded, allow_insecure_https=False)
    script_path = download_root / "download_sra.sh"
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o755)
    env = {"PATH": os.environ.get("PATH", "")}
    job_id = job_manager.start_job(
        name=f"sra_download — {name}",
        command=["bash", str(script_path)],
        cwd=download_root,
        env=env,
    )
    return JSONResponse({"job_id": job_id})


@app.get("/api/projects/{name}/sra-crosswalk")
def api_project_sra_crosswalk(name: str):
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    crosswalk = project_dir / "download" / "sra_crosswalk.tsv"
    if not crosswalk.is_file():
        raise HTTPException(404, "No SRA crosswalk for this project")
    return FileResponse(crosswalk, media_type="text/plain")


@app.get("/api/projects/{name}/sra/status")
def api_project_sra_status(name: str):
    """Per-accession result of the most recent SRA download (accession -> ok/fail),
    read from sra_download_status.tsv. Lets the GUI show exactly which samples
    downloaded and which failed, instead of only a count in the log."""
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    status_file = project_dir / "download" / "sra_download_status.tsv"
    accs: List[Dict[str, str]] = []
    if status_file.is_file():
        for line in status_file.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].strip():
                accs.append({"accession": parts[0].strip(), "status": parts[1].strip()})
    succeeded = sum(1 for a in accs if a["status"] == "ok")
    failed = [a["accession"] for a in accs if a["status"] != "ok"]
    return JSONResponse({
        "accessions": accs,
        "succeeded": succeeded,
        "failed": len(failed),
        "failed_accessions": failed,
        "total": len(accs),
    })


@app.get("/api/projects/{name}/samples")
def api_project_samples(name: str):
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    download_dir = project_dir / "download"
    if not download_dir.is_dir():
        return JSONResponse([])
    return JSONResponse(_list_fastq_pairs(download_dir))


# ---------------------------------------------------------------------------
# Per-sample AMR results (decoupled from a single job).
#
# Results are read straight from <project>/amr/<sample>/ on disk so any
# previously-run sample's outputs can be revisited — not just the last job.
# ---------------------------------------------------------------------------
def _collect_result_files(run_dir: Path, include_all: bool) -> List[Dict]:
    """List result files under an amr run dir, categorized + sorted."""
    files: List[Dict] = []
    if not run_dir.is_dir():
        return files
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.name.endswith(".log"):
            continue
        rel = str(p.relative_to(run_dir))
        category = _result_category(rel)
        if not include_all and category is None:
            continue
        stat = p.stat()
        files.append({
            "name": rel,
            "path": str(p),
            "label": _result_label(rel, category),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "openable": _can_open_inline(rel),
            "category": category,
        })

    def sort_key(f):
        category = f.get("category")
        if category in _CATEGORY_ORDER:
            return (_CATEGORY_ORDER[category], f["name"])
        return (50, f["name"])

    files.sort(key=sort_key)
    for f in files:
        f.pop("mtime", None)
        if include_all and f.get("category") is None:
            f["label"] = f["name"]
    return files


def _sample_run_status(run_dir: Path) -> str:
    """Status for a sample: 'running' if a live job owns its dir, else 'done'
    if the dir holds output, else 'none'."""
    run_dir_str = str(run_dir)
    for job in job_manager.list_jobs():
        if job.get("cwd") == run_dir_str and job.get("status") == "running":
            return "running"
    try:
        if run_dir.is_dir() and any(p.is_file() for p in run_dir.rglob("*")):
            return "done"
    except PermissionError:
        pass
    return "none"


@app.get("/api/projects/{name}/samples/{sample}/amr-results")
def api_sample_amr_results(name: str, sample: str, all: int = Query(0)):
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    run_dir = project_dir / "amr" / sample
    return JSONResponse({
        "project": name,
        "sample": sample,
        "present": run_dir.is_dir(),
        "status": _sample_run_status(run_dir),
        "run_dir": str(run_dir),
        "files": _collect_result_files(run_dir, bool(all)),
    })


# ---------------------------------------------------------------------------
# AMRFinderPlus TSV parsing — turn the per-sample amrfinder.tsv into a
# structured summary + rows the results table renders. Tolerant of the leading
# `name` column (--name) and the trailing `Hierarchy node` column
# (--print_node), and of header-label drift across DB versions.
# ---------------------------------------------------------------------------
def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", h.strip().lower()).strip("_")


# Map normalized header tokens -> canonical field names we emit.
_AMR_FIELD_ALIASES = {
    "name": "name",
    "protein_id": "protein_id",
    "protein_identifier": "protein_id",
    "contig_id": "contig_id",
    "contig": "contig_id",
    "start": "start",
    "stop": "stop",
    "strand": "strand",
    "element_symbol": "element_symbol",
    "gene_symbol": "element_symbol",
    "element_name": "element_name",
    "sequence_name": "element_name",
    "scope": "scope",
    "type": "type",
    "element_type": "type",
    "subtype": "subtype",
    "element_subtype": "subtype",
    "class": "class",
    "subclass": "subclass",
    "method": "method",
    "target_length": "target_length",
    "reference_sequence_length": "ref_length",
    "ref_seq_len": "ref_length",
    "coverage_of_reference": "pct_coverage",
    "coverage_of_reference_sequence": "pct_coverage",
    "identity_to_reference": "pct_identity",
    "identity_to_reference_sequence": "pct_identity",
    "alignment_length": "alignment_length",
    "closest_reference_accession": "closest_ref_accession",
    "accession_of_closest_sequence": "closest_ref_accession",
    "closest_reference_name": "closest_ref_name",
    "name_of_closest_sequence": "closest_ref_name",
    "hmm_accession": "hmm_accession",
    "hmm_id": "hmm_accession",
    "hmm_description": "hmm_description",
    "hierarchy_node": "hierarchy_node",
}


def _parse_amrfinder_tsv(tsv_path: Path) -> Dict[str, Any]:
    """Parse an amrfinder.tsv into {rows, summary, columns}. Returns
    {rows: []} if the file is empty or only a header."""
    rows: List[Dict[str, Any]] = []
    try:
        text = tsv_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"rows": [], "summary": {}, "columns": []}
    lines = [ln for ln in text.splitlines() if ln.strip() != ""]
    if not lines:
        return {"rows": [], "summary": {}, "columns": []}
    raw_header = lines[0].split("\t")
    header = [_AMR_FIELD_ALIASES.get(_norm_header(h), _norm_header(h)) for h in raw_header]
    for line in lines[1:]:
        cells = line.split("\t")
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        row = {header[i]: cells[i] for i in range(len(header))}
        rows.append(row)

    # ---- summary aggregation ----
    by_class: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    point_mutations = 0
    plus_count = 0
    for r in rows:
        cls = (r.get("class") or "").strip() or "(unclassified)"
        by_class[cls] = by_class.get(cls, 0) + 1
        typ = (r.get("type") or "").strip().upper() or "UNKNOWN"
        by_type[typ] = by_type.get(typ, 0) + 1
        subtype = (r.get("subtype") or "").strip().upper()
        method = (r.get("method") or "").strip().upper()
        if subtype == "POINT" or method.startswith("POINT"):
            point_mutations += 1
        scope = (r.get("scope") or "").strip().lower()
        if scope == "plus":
            plus_count += 1

    summary = {
        "total": len(rows),
        "by_class": by_class,
        "by_type": by_type,
        "point_mutations": point_mutations,
        "plus_count": plus_count,
    }
    return {"rows": rows, "summary": summary, "columns": header}


@app.get("/api/projects/{name}/samples/{sample}/amr-table")
def api_sample_amr_table(name: str, sample: str):
    """Parse <project>/amr/<sample>/amrfinder.tsv into a structured table plus
    the organism call and provenance (from organism_detection.json /
    run_manifest.json) so the Results pane can render everything in one fetch."""
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    run_dir = project_dir / "amr" / sample
    tsv = run_dir / "amrfinder.tsv"
    organism = {}
    provenance = {}
    det = run_dir / "organism_detection.json"
    if det.is_file():
        try:
            organism = json.loads(det.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            organism = {}
    man = run_dir / "run_manifest.json"
    if man.is_file():
        try:
            provenance = json.loads(man.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            provenance = {}
    parsed = _parse_amrfinder_tsv(tsv) if tsv.is_file() else {"rows": [], "summary": {}, "columns": []}
    return JSONResponse({
        "project": name,
        "sample": sample,
        "present": tsv.is_file(),
        "organism": organism,
        "provenance": provenance,
        "summary": parsed["summary"],
        "columns": parsed["columns"],
        "rows": parsed["rows"],
    })


# ---------------------------------------------------------------------------
# Cross-tool visibility — surface sibling tools' results for a sample (read-only).
#
# Every tool in the suite creates the same project skeleton (download/, amr/,
# kraken/, step1/, ...), so a sample analysed here may also have been run through
# Kraken ID Parse or vSNP. Those outputs live OUTSIDE this tool's run dir, so
# _collect_result_files() can never see them — they need an explicit lookup.
# ---------------------------------------------------------------------------
def _resolve_sample_subdir(base_dir: Path, sample: str) -> Optional[Path]:
    """Resolve a sample name to its subdirectory under another tool's output root.

    Tolerates the `<sample>_<suffix>` dirs some pipelines produce (vSNP appends a
    reference/date suffix), preferring an exact match."""
    exact = base_dir / sample
    if exact.is_dir():
        return exact
    try:
        candidates = sorted(
            d for d in base_dir.iterdir()
            if d.is_dir() and d.name.startswith(f"{sample}_")
        )
    except (OSError, PermissionError):
        return None
    return candidates[0] if candidates else None


@app.get("/api/projects/{name}/vsnp/samples/{sample}/files")
def api_vsnp_sample_files(name: str, sample: str):
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    step1_dir = project_dir / "step1"
    sample_dir = _resolve_sample_subdir(step1_dir, sample) if step1_dir.is_dir() else None
    files: List[Dict] = []
    sample_dir_str = ""
    if sample_dir:
        base = sample_dir.resolve()
        sample_dir_str = str(base)
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.name.startswith(".~lock"):
                continue
            try:
                rel = p.relative_to(base).as_posix()
                st = p.stat()
            except (OSError, ValueError):
                continue
            files.append({
                "name": p.name,
                "relpath": rel,
                "path": str(p),
                "size": st.st_size,
                "openable": _can_open_inline(p.name),
                "type": p.suffix.lstrip(".").lower() or "file",
            })
    return JSONResponse({
        "project": name,
        "sample": sample,
        "step1_present": bool(sample_dir),
        "step1_dir": sample_dir_str,
        "files": files,
    })


# ---------------------------------------------------------------------------
# The Results pane — every completed sample in one searchable table.
#
# Modelled on vSNP's Step 1 Results. The per-sample endpoints below answer "tell
# me about THIS sample", which is why results were only ever visible by expanding
# a row in the Projects tree; there was no way to see, sort or search everything
# that had been run. This endpoint answers "what has this project produced".
# ---------------------------------------------------------------------------
_TOOL_SUBDIR = "amr"


def _run_finished_at(run_dir: Path) -> str:
    """When this sample finished, as an ISO string ("" if unknown).

    Prefer the pipeline's own record over filesystem mtimes: a later re-read or
    an rsync can touch files long after the analysis actually ran."""
    manifest = run_dir / "run_manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for key in ("pipeline_finished_at", "finished_at_utc", "finished_at", "timestamp"):
            val = str(data.get(key) or "").strip()
            if val:
                return val
    except (OSError, ValueError, AttributeError):
        pass
    try:
        newest = max((p.stat().st_mtime for p in run_dir.rglob("*") if p.is_file()),
                     default=0)
        if newest:
            from datetime import datetime, timezone
            return datetime.fromtimestamp(newest, tz=timezone.utc).isoformat()
    except (OSError, ValueError):
        pass
    return ""


def _row_flags(run_dir: Path) -> Dict[str, Any]:
    """pass / review / fail plus the reasons.

    Two independent signals, and the run's exit status outranks the QC verdict.
    qc.json only grades the ASSEMBLY, so a sample whose amrfinder step exited
    non-zero — producing no calls at all — was still reported as "pass". A
    Results pane that says PASS for a failed run is worse than no pane."""
    level, reasons = "pass", []
    try:
        qc = json.loads((run_dir / "qc.json").read_text(encoding="utf-8"))
        verdict = str(qc.get("verdict") or "").strip().lower()
        if verdict in ("fail", "failed"):
            level = "fail"
        elif verdict in ("review", "warn", "warning"):
            level = "review"
        notes = qc.get("notes") or qc.get("reasons") or []
        if isinstance(notes, str):
            notes = [notes]
        reasons = [str(n) for n in notes if str(n).strip()]
    except (OSError, ValueError, AttributeError):
        pass
    try:
        man = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        rc = man.get("return_code")
        if rc not in (None, 0):
            level = "fail"
            # Pick the line a human would want. AMRFinderPlus prints a banner,
            # then "*** ERROR ***", then the real message, then echoes the whole
            # command line — so "last non-empty line" is the least useful choice.
            lines = [l.strip() for l in str(man.get("stderr_tail") or "").splitlines()
                     if l.strip()]
            detail = ""
            for i, l in enumerate(lines):
                if l.startswith("*** ERROR"):
                    detail = next((x for x in lines[i + 1:]
                                   if not x.startswith(("Command line:", "Running:"))), "")
                    break
            if not detail:
                detail = next((l for l in lines
                               if "WARNING" in l or "error" in l.lower()), "")
            if not detail:
                detail = next((l for l in reversed(lines)
                               if not l.startswith(("Command line:", "Running:"))), "")
            reasons.insert(0, f"AMRFinderPlus exited {rc}"
                              + (f" — {detail[:120]}" if detail else ""))
        elif not (run_dir / "amrfinder.tsv").is_file() and (run_dir / "assembly.fasta").is_file():
            level = "fail" if level == "pass" else level
            reasons.insert(0, "no amrfinder.tsv — the AMR step produced no calls")
    except (OSError, ValueError, AttributeError):
        pass
    return {"level": level, "reasons": reasons}


def _row_metrics(run_dir: Path) -> Dict[str, Any]:
    """The few numbers worth showing as columns. Missing values stay None so the
    table renders an em dash rather than inventing a zero."""
    m: Dict[str, Any] = {"organism": None, "genes": None, "point": None,
                         "plus": None, "mlst": None}
    try:
        org = json.loads((run_dir / "organism_detection.json").read_text(encoding="utf-8"))
        m["organism"] = org.get("organism_token") or org.get("dominant_species") or None
        scheme, st = org.get("mlst_scheme"), org.get("mlst_st")
        if scheme:
            m["mlst"] = f"{scheme}{f' ST{st}' if st else ''}"
    except (OSError, ValueError, AttributeError):
        pass
    tsv = run_dir / "amrfinder.tsv"
    if tsv.is_file():
        try:
            parsed = _parse_amrfinder_tsv(tsv)
            s = parsed.get("summary") or {}
            m["genes"] = s.get("total")
            m["point"] = s.get("point_mutations")
            m["plus"] = s.get("plus_count")
        except Exception:
            pass
    return m


def _results_rows(name: str, include_all: bool = False) -> List[Dict]:
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    root = project_dir / _TOOL_SUBDIR
    rows: List[Dict] = []
    if not root.is_dir():
        return rows
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir())
    except (OSError, PermissionError):
        return rows
    for d in entries:
        rows.append({
            "sample": d.name,
            "status": _sample_run_status(d),
            "run_date": _run_finished_at(d),
            "run_dir": str(d),
            "flags": _row_flags(d),
            "metrics": _row_metrics(d),
            "files": _collect_result_files(d, include_all),
            "cross_tool": _cross_tool_entries(name, project_dir, d.name),
        })
    rows.sort(key=lambda r: (r["run_date"] or "", r["sample"]), reverse=True)
    return rows


def _cross_tool_entries(project: str, project_dir: Path, sample: str) -> List[Dict]:
    """Sibling tools' outputs for the same sample.

    Every tool builds the same project skeleton, so a sample analysed here may
    also have a Kraken or vSNP run. Those live outside this tool's run dir, which
    is exactly why they were invisible in the Results pane."""
    out: List[Dict] = []
    if _kraken_krona_path(project_dir, sample) is not None:
        out.append({
            "tool": "kraken", "kind": "krona", "label": "📊 Krona",
            "href": f"./api/projects/{project}/kraken/samples/{sample}/krona",
        })
    step1 = project_dir / "step1"
    if step1.is_dir() and _resolve_sample_subdir(step1, sample) is not None:
        out.append({
            "tool": "vsnp", "kind": "step1", "label": "🧬 vSNP Step 1",
            "href": f"./api/projects/{project}/vsnp/samples/{sample}/files",
        })
    return out


def _filter_rows(rows: List[Dict], start: str, end: str, q: str) -> List[Dict]:
    """Apply the pane's filters server-side too, so an export matches the view."""
    ql = (q or "").strip().lower()
    out = []
    for r in rows:
        if ql and ql not in r["sample"].lower():
            continue
        day = (r.get("run_date") or "")[:10]
        if start and (not day or day < start):
            continue
        if end and (not day or day > end):
            continue
        out.append(r)
    return out


@app.get("/api/projects/{name}/results")
def api_project_results(name: str, all: int = Query(0)):
    return JSONResponse({
        "project": name,
        "tool": _TOOL_SUBDIR,
        "rows": _results_rows(name, include_all=bool(all)),
    })


_EXPORT_COLUMNS = [
    ("sample", "Sample"), ("status", "Status"), ("run_date", "Run date"),
    ("qc", "QC"), ("qc_reasons", "QC notes"), ("organism", "Organism"),
    ("mlst", "MLST"), ("genes", "AMR genes"), ("point", "Point mutations"),
    ("plus", "Plus elements"), ("run_dir", "Run directory"),
]


def _export_records(name, start, end, q):
    rows = _filter_rows(_results_rows(name), start, end, q)
    for r in rows:
        m = r.get("metrics") or {}
        yield {
            "sample": r["sample"], "status": r["status"],
            "run_date": (r.get("run_date") or "")[:19],
            "qc": (r.get("flags") or {}).get("level", ""),
            "qc_reasons": "; ".join((r.get("flags") or {}).get("reasons", [])),
            "organism": m.get("organism") or "", "mlst": m.get("mlst") or "",
            "genes": m.get("genes"), "point": m.get("point"), "plus": m.get("plus"),
            "run_dir": r.get("run_dir", ""),
        }


@app.get("/api/projects/{name}/results.csv")
def api_project_results_csv(name: str, start: str = Query(""), end: str = Query(""),
                            q: str = Query("")):
    import csv
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=[k for k, _ in _EXPORT_COLUMNS], extrasaction="ignore")
    w.writerow({k: label for k, label in _EXPORT_COLUMNS})
    for rec in _export_records(name, start, end, q):
        w.writerow(rec)
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}_amr_results.csv"'})


@app.get("/api/projects/{name}/results.xlsx")
def api_project_results_xlsx(name: str, start: str = Query(""), end: str = Query(""),
                             q: str = Query("")):
    try:
        from openpyxl import Workbook
    except ImportError:
        raise HTTPException(501, "Excel export needs openpyxl in this tool's environment.")
    import io
    wb = Workbook()
    ws = wb.active
    ws.title = "AMR results"
    ws.append([label for _, label in _EXPORT_COLUMNS])
    for rec in _export_records(name, start, end, q):
        ws.append([rec.get(k) for k, _ in _EXPORT_COLUMNS])
    for i, (_k, label) in enumerate(_EXPORT_COLUMNS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(12, len(label) + 2)
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}_amr_results.xlsx"'})


def _kraken_krona_path(project_dir: Path, sample: str) -> Optional[Path]:
    """Newest Krona HTML chart for `sample`, produced by the Kraken ID Parse GUI.

    Kraken writes it under <project>/kraken/<sample>/kraken/<sample>_<ts>_krona.html
    — note the nested `kraken/` — so an rglob is what actually finds it. Re-runs
    leave several; the newest is the one worth showing."""
    kraken_root = project_dir / "kraken"
    if not kraken_root.is_dir():
        return None
    sample_dir = _resolve_sample_subdir(kraken_root, sample)
    if sample_dir is None:
        return None
    try:
        charts = [p for p in sample_dir.rglob("*_krona.html") if p.is_file()]
        charts += [p for p in sample_dir.rglob("krona.html") if p.is_file()]
    except (OSError, PermissionError):
        return None
    if not charts:
        return None
    return max(charts, key=lambda p: p.stat().st_mtime)


@app.get("/api/projects/{name}/kraken/samples")
def api_kraken_samples(name: str):
    """Which samples in this project have Kraken output (and a Krona chart)."""
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    kraken_root = project_dir / "kraken"
    samples: List[Dict] = []
    if kraken_root.is_dir():
        try:
            for d in sorted(p for p in kraken_root.iterdir() if p.is_dir()):
                samples.append({
                    "sample": d.name,
                    "has_krona": _kraken_krona_path(project_dir, d.name) is not None,
                })
        except (OSError, PermissionError):
            pass
    return JSONResponse({"project": name, "samples": samples})


@app.get("/api/projects/{name}/kraken/samples/{sample}/krona")
def api_kraken_sample_krona(name: str, sample: str):
    """Serve the sample's Krona chart inline so it opens as an interactive page."""
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    krona = _kraken_krona_path(project_dir, sample)
    if krona is None:
        raise HTTPException(404, f"No Krona report for {sample}. Run Kraken on it first.")
    return FileResponse(krona, media_type="text/html")


@app.get("/api/projects/{name}/file")
def api_project_file(name: str, path: str = Query(...), inline: int = 0):
    """Serve a file from anywhere inside a project dir (cross-tool downloads)."""
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    root = project_dir.resolve()
    target = Path(path).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(403, "Path outside project directory")
    if not target.is_file():
        raise HTTPException(404, f"File not found: {path}")
    media_type = _media_type_for(target.name)
    want_inline = bool(inline) and _can_open_inline(target.name)
    disposition = "inline" if want_inline else "attachment"
    headers = {"Content-Disposition": f'{disposition}; filename="{target.name}"'}
    return FileResponse(target, media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# Organism options — valid AMRFinderPlus --organism tokens.
#
# The authoritative list comes from `amrfinder -l`, which is DB-version
# dependent. We cache it (in-process) and fall back to the shipped
# config/amrfinder_organisms.txt if the binary or DB is unavailable.
# ---------------------------------------------------------------------------
_ORG_CACHE: Dict[str, Any] = {"organisms": None, "db_version": None, "source": None, "ts": 0.0}
_ORG_CACHE_TTL = 600  # seconds


def _read_fallback_organisms() -> List[str]:
    try:
        text = _ORGANISMS_FALLBACK.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def _query_amrfinder_organisms() -> Dict[str, Any]:
    """Query `amrfinder -l` for the valid --organism tokens + DB version.

    Returns {organisms, db_version, source}. Falls back to the shipped list
    when amrfinder is not on PATH or fails.
    """
    organisms: List[str] = []
    db_version: Optional[str] = None
    source = "fallback"
    try:
        proc = subprocess.run(
            ["amrfinder", "-l"],
            capture_output=True, text=True, timeout=60,
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        for line in out.splitlines():
            low = line.lower()
            if "database version" in low:
                m = re.search(r"version[:\s]+([0-9][\w.\-]*)", line, re.IGNORECASE)
                if m and db_version is None:
                    db_version = m.group(1)
            if "available --organism" in low or "--organism options" in low or "valid options" in low:
                tail = line.split(":", 1)[-1]
                organisms = [t.strip() for t in re.split(r"[,\s]+", tail) if t.strip()]
        # Some versions print one organism per line; grab plausible tokens too.
        if not organisms:
            for line in out.splitlines():
                s = line.strip()
                if re.fullmatch(r"[A-Z][a-z]+(?:_[a-z]+)*", s):
                    organisms.append(s)
        if organisms:
            source = "amrfinder"
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
        logger.info("amrfinder -l unavailable (%s); using fallback organism list", exc)

    if not organisms:
        organisms = _read_fallback_organisms()
        source = "fallback"
    # de-dupe, preserve order
    seen = set()
    uniq = []
    for o in organisms:
        if o not in seen:
            seen.add(o)
            uniq.append(o)
    return {"organisms": uniq, "db_version": db_version, "source": source}


@app.get("/api/organism-options")
def api_organism_options(refresh: int = Query(0)):
    now = time.time()
    if (not refresh) and _ORG_CACHE["organisms"] is not None and (now - _ORG_CACHE["ts"] < _ORG_CACHE_TTL):
        return JSONResponse({
            "organisms": _ORG_CACHE["organisms"],
            "db_version": _ORG_CACHE["db_version"],
            "source": _ORG_CACHE["source"],
        })
    result = _query_amrfinder_organisms()
    _ORG_CACHE.update({
        "organisms": result["organisms"],
        "db_version": result["db_version"],
        "source": result["source"],
        "ts": now,
    })
    return JSONResponse(result)


def _kraken_gui_config() -> Dict[str, Any]:
    """The Kraken ID Parse GUI's own user config.

    That tool owns Kraken2 database configuration for the suite: its Settings
    panel adds and removes databases and remembers them in `saved_kraken_dbs`.
    Reading it here means a database the user has already set up once is offered
    in this tool too, instead of being re-typed as an absolute path — which is
    how a path from one site ends up hard-coded somewhere it does not exist.
    The vSNP GUI reads the same file the same way.

    Mirrors that tool's config-dir logic: XDG_CONFIG_HOME, else
    ~/.config/kraken_id_parse_gui. Absent or unreadable is normal (the sibling
    may not be installed), and yields no options rather than an error.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else (Path.home() / ".config")
    try:
        data = json.loads((base / "kraken_id_parse_gui" / "config.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _is_kraken_db(path: str) -> bool:
    """A Kraken2 DB is a directory holding hash.k2d — the check the run itself
    would fail on, made before the dropdown offers the entry."""
    try:
        return (Path(path) / "hash.k2d").is_file()
    except OSError:
        return False


@app.get("/api/kraken-dbs")
def api_kraken_dbs():
    """Kraken2 databases to offer for organism detection.

    This tool's own setting first, then whatever the Kraken ID Parse GUI has
    remembered, then this machine's conventional location. Entries that are not
    actually databases are reported but flagged, so a stale path shows as
    unusable rather than silently failing mid-run.
    """
    cfg = load_config()
    kgui = _kraken_gui_config()
    current = str(cfg.get("kraken_db") or "").strip()
    candidates = [
        current,
        str(kgui.get("kraken_db") or "").strip(),
        *[str(p).strip() for p in (kgui.get("saved_kraken_dbs") or [])],
        str(DEFAULTS.get("kraken_db") or "").strip(),
    ]
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for p in candidates:
        if not p:
            continue
        try:
            key = str(Path(p).resolve())
        except OSError:
            key = p
        if key in seen:
            continue
        seen.add(key)
        out.append({"path": p, "name": Path(p).name, "usable": _is_kraken_db(p)})
    return JSONResponse({"databases": out, "current": current})


@app.get("/api/config")
def api_get_config():
    cfg = load_config()
    # Deployed checkout's version (git describe) — what the dashboard shows.
    cfg["app_version"] = APP_VERSION
    return JSONResponse(cfg)


class ConfigPayload(BaseModel):
    kraken_db: Optional[str] = None
    amrfinder_db: Optional[str] = None
    projects_root: Optional[str] = None
    shared_projects_root: Optional[str] = None
    saved_project_roots: Optional[List[str]] = None


@app.post("/api/config")
def api_save_config(payload: ConfigPayload):
    cfg = load_config()
    updates = payload.model_dump(exclude_none=True)
    cfg.update(updates)
    roots = cfg.get("saved_project_roots") or []
    if isinstance(roots, list):
        seen, cleaned = set(), []
        for r in roots:
            r = (r or "").strip()
            if r and r not in seen:
                seen.add(r)
                cleaned.append(r)
        cfg["saved_project_roots"] = cleaned
    save_config(cfg)
    return JSONResponse({"ok": True})


@app.get("/api/browse-dirs")
def api_browse_dirs(path: str = ""):
    """List sub-directories of `path` for the project-root folder picker."""
    try:
        p = (Path(path).expanduser() if path.strip() else Path.home()).resolve()
    except (OSError, RuntimeError):
        raise HTTPException(400, "Invalid path")
    if not p.is_dir():
        raise HTTPException(400, f"Not a directory: {p}")
    entries: List[Dict[str, str]] = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    entries.append({"name": child.name, "path": str(child)})
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(403, f"Permission denied: {p}")
    parent = str(p.parent) if p.parent != p else None
    return JSONResponse({"path": str(p), "parent": parent, "entries": entries})


class RunPayload(BaseModel):
    project: str
    r1: str                       # absolute path to R1 FASTQ or an assembly FASTA
    r2: Optional[str] = None
    assembly: Optional[str] = None   # provide an assembly FASTA directly (skip assembly)
    force_organism: Optional[str] = None
    use_plus: bool = True
    run_kraken: bool = True
    run_mlst: bool = True
    threads: Optional[int] = None
    ident_min: Optional[float] = None
    coverage_min: Optional[float] = None
    kraken_db: Optional[str] = None
    amrfinder_db: Optional[str] = None


@app.post("/api/run")
def api_run(payload: RunPayload):
    cfg = load_config()
    kraken_db = payload.kraken_db or cfg.get("kraken_db", "")
    amrfinder_db = payload.amrfinder_db or cfg.get("amrfinder_db", "")

    project_dir = _get_project_dir(payload.project)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {payload.project}")

    # The "primary" input file is r1 — either FASTQ reads or an assembly FASTA.
    primary = Path(payload.r1)
    if not primary.exists():
        raise HTTPException(400, f"Input file not found: {payload.r1}")

    # Derive sample name — strip _R1/_R2 or _1/_2 read tags; for an assembly
    # FASTA, drop the fasta suffix.
    if primary.name.lower().endswith((".fasta", ".fa", ".fna")):
        sample_name = re.sub(r"\.(fasta|fa|fna)$", "", primary.name, flags=re.IGNORECASE)
    else:
        sample_name, _ = _strip_read_tag(primary.name)

    run_dir = project_dir / "amr" / sample_name

    # A space ANYWHERE in the run path (e.g. a projects-root named "ESBL EC-sheep")
    # silently breaks the third-party tools: shovill, AMRFinderPlus/StxTyper and the
    # CGE finders build internal shell/BLAST commands that don't quote paths, so the
    # path splits at the space and every step fails with a useless error. Project
    # NAMES are already space-sanitised at creation; this guards the projects-root /
    # input paths the user controls directly. Fail fast with an actionable message.
    for label, p in (("input file", str(primary)), ("project path", str(run_dir))):
        if " " in p:
            raise HTTPException(
                400,
                f"The {label} contains a space ({p!r}), which breaks the assembly "
                "and gene-finder tools (AMRFinderPlus, PlasmidFinder, etc.). Rename "
                "the project folder / projects root to remove spaces (use _ or -).",
            )

    # Refuse to start a second pipeline in the same output directory (race).
    for existing in job_manager.list_jobs():
        if existing.get("status") == "running" and existing.get("cwd") == str(run_dir):
            raise HTTPException(
                409,
                f"A run is already in progress for {sample_name} "
                f"(job {existing['id'][:8]}). Wait for it to finish before re-running.",
            )

    run_dir.mkdir(parents=True, exist_ok=True)

    script = _BIN_DIR / "amr_pipeline.py"
    command = [sys.executable, "-u", str(script),
               "--sample", sample_name,
               "--outdir", str(run_dir)]

    # Reads vs. assembly input.
    if payload.assembly:
        asm = Path(payload.assembly)
        if not asm.exists():
            raise HTTPException(400, f"Assembly FASTA not found: {payload.assembly}")
        command.extend(["--assembly", str(asm)])
    elif primary.name.lower().endswith((".fasta", ".fa", ".fna")):
        command.extend(["--assembly", str(primary)])
    else:
        command.extend(["-r1", str(primary)])
        if payload.r2:
            r2 = Path(payload.r2)
            if not r2.exists():
                raise HTTPException(400, f"R2 file not found: {payload.r2}")
            command.extend(["-r2", str(r2)])

    if payload.force_organism:
        command.extend(["--force-organism", payload.force_organism.strip()])
    if payload.use_plus:
        command.append("--plus")
    if not payload.run_kraken:
        command.append("--no-kraken")
    if not payload.run_mlst:
        command.append("--no-mlst")
    if kraken_db:
        command.extend(["--kraken-db", kraken_db])
    if amrfinder_db:
        command.extend(["--amrfinder-db", amrfinder_db])
    if payload.threads:
        command.extend(["--threads", str(int(payload.threads))])
    if payload.ident_min is not None:
        command.extend(["--ident-min", str(payload.ident_min)])
    if payload.coverage_min is not None:
        command.extend(["--coverage-min", str(payload.coverage_min)])

    env = {
        "PYTHONPATH": str(_BIN_DIR),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }

    org_label = payload.force_organism or "auto-detect organism"
    job_name = f"{payload.project}/{sample_name} — AMRFinderPlus ({org_label})"
    job_id = job_manager.start_job(name=job_name, command=command, cwd=run_dir, env=env)
    return JSONResponse({"job_id": job_id, "run_dir": str(run_dir)})


@app.get("/api/jobs")
def api_list_jobs():
    return JSONResponse(job_manager.list_jobs())


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str):
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return JSONResponse(job)


@app.get("/api/jobs/{job_id}/log")
async def api_job_log(job_id: str, request: Request):
    """SSE stream of the job's log file. Tails from beginning, closes when job finishes."""
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")

    log_path = Path(job["log_path"])
    _ansi_re = re.compile(r'\x1b\[[0-9;]*[mGKHFABCDJsur]')

    async def event_stream():
        position = 0
        while True:
            if await request.is_disconnected():
                break
            current_job = job_manager.get_job(job_id)
            if log_path.exists():
                async with aiofiles.open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    await f.seek(position)
                    chunk = await f.read(4096)
                    if chunk:
                        lines = chunk.splitlines(keepends=True)
                        for line in lines:
                            clean = _ansi_re.sub("", line.rstrip())
                            if clean:
                                yield f"data: {clean}\n\n"
                        position += len(chunk.encode("utf-8"))
            if current_job and current_job["status"] in ("succeeded", "failed"):
                yield "data: [DONE]\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# File extensions a browser can render in a tab (open inline); everything else
# is sent as a download. Maps extension -> MIME type.
_INLINE_MEDIA = {
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".json": "application/json",
    ".tsv": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".csv": "text/plain",
}
_DOWNLOAD_MEDIA = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".vcf": "text/plain",
    ".fasta": "text/plain",
    ".fa": "text/plain",
    ".fna": "text/plain",
    ".gz": "application/gzip",
}


def _can_open_inline(name: str) -> bool:
    return Path(name).suffix.lower() in _INLINE_MEDIA


def _media_type_for(name: str) -> str:
    ext = Path(name).suffix.lower()
    return _INLINE_MEDIA.get(ext) or _DOWNLOAD_MEDIA.get(ext) or "application/octet-stream"


def _result_category(rel: str) -> Optional[str]:
    """Return the primary-results category for a relative run output path.

    The run dir keeps intermediates for audit; the GUI surfaces the small set
    users normally open or download.
    """
    path = Path(rel)
    name = path.name
    parts = path.parts

    if any(part.startswith(".") for part in parts):
        return None
    if name.endswith(".fastq.gz"):
        return None

    if name == "report.pdf":
        return "report_pdf"
    if name == "report.html":
        return "report_html"
    if name.endswith("_stats.xlsx"):
        return "stats_xlsx"
    if name == "fastq_qc.json":
        return "fastq_qc"
    if name == "amrfinder.tsv":
        return "amrfinder_tsv"
    if name == "mutation_all.tsv":
        return "mutation_all"
    if name == "organism_detection.json":
        return "organism_detection"
    if name == "qc.json":
        return "qc"
    if name == "run_manifest.json":
        return "run_manifest"
    if name in ("mlst_result.json", "mlst.tsv"):
        return "mlst"
    if name == "assembly.fasta" or name.endswith("_assembly.fasta"):
        return "assembly_fasta"
    if name.endswith("_krona.html") or name == "krona.html":
        return "krona"
    if name.endswith("_kraken_report.txt") or name == "kraken_report.txt":
        return "kraken_report"
    if name == "pipeline.log":
        return "log"
    return None


_CATEGORY_ORDER = {
    "report_pdf": 0,
    "report_html": 1,
    "stats_xlsx": 1,
    "amrfinder_tsv": 2,
    "mutation_all": 3,
    "organism_detection": 4,
    "qc": 5,
    "fastq_qc": 6,
    "mlst": 7,
    "assembly_fasta": 8,
    "krona": 9,
    "kraken_report": 10,
    "run_manifest": 11,
    "log": 99,
}


def _result_label(rel: str, category: Optional[str]) -> str:
    return {
        "report_pdf": "Report (PDF)",
        "report_html": "Report (HTML)",
        "stats_xlsx": "Statistics workbook (Excel)",
        "amrfinder_tsv": "AMRFinderPlus results (TSV)",
        "mutation_all": "All assessed mutations (TSV)",
        "organism_detection": "Organism detection (JSON)",
        "qc": "Assembly QC (JSON)",
        "fastq_qc": "Input read QC (JSON)",
        "mlst": "MLST result",
        "assembly_fasta": "Assembly FASTA",
        "krona": "Krona taxonomy report",
        "kraken_report": "Kraken2 report",
        "run_manifest": "Run manifest / provenance (JSON)",
        "log": "Pipeline log",
    }.get(category, rel)


@app.get("/api/jobs/{job_id}/results")
def api_job_results(job_id: str, all: int = Query(0)):
    """List output files in the job's run directory, plus the pipeline log."""
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")

    files = []
    cwd = job.get("cwd")
    if cwd and Path(cwd).is_dir():
        run_dir = Path(cwd)
        for p in sorted(run_dir.rglob("*")):
            if p.is_file() and not p.name.endswith(".log"):
                rel = str(p.relative_to(run_dir))
                category = _result_category(rel)
                if not all and category is None:
                    continue
                files.append({
                    "name": rel,
                    "label": _result_label(rel, category),
                    "size": p.stat().st_size,
                    "mtime": p.stat().st_mtime,
                    "openable": _can_open_inline(rel),
                    "category": category,
                })

    log_path = Path(job.get("log_path", ""))
    if log_path.is_file():
        files.append({
            "name": "pipeline_log.txt",
            "label": "Pipeline log",
            "size": log_path.stat().st_size,
            "mtime": log_path.stat().st_mtime,
            "openable": True,
            "category": "log",
            "is_log": True,
        })

    def sort_key(f):
        if f.get("is_log"):
            return (_CATEGORY_ORDER["log"], f["name"])
        category = f.get("category")
        if category in _CATEGORY_ORDER:
            return (_CATEGORY_ORDER[category], f["name"])
        return (50, f["name"])

    files.sort(key=sort_key)
    for file in files:
        file.pop("mtime", None)
        if all and file.get("category") is None:
            file["label"] = file["name"]
    return JSONResponse(files)


@app.get("/api/jobs/{job_id}/file")
def api_job_file(job_id: str, path: str = Query(...), inline: int = 0):
    """Serve a single result file. `inline=1` renders in the browser."""
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")

    if path == "pipeline_log.txt":
        target = Path(job.get("log_path", ""))
        display_name = f"{job_id[:8]}_pipeline_log.txt"
    else:
        cwd = job.get("cwd")
        if not cwd:
            raise HTTPException(404, "No run directory for job")
        run_dir = Path(cwd).resolve()
        target = (run_dir / path).resolve()
        if run_dir != target and run_dir not in target.parents:
            raise HTTPException(403, "Path outside run directory")
        display_name = target.name

    if not target.is_file():
        raise HTTPException(404, f"File not found: {path}")

    media_type = _media_type_for(target.name)
    want_inline = bool(inline) and _can_open_inline(target.name)
    disposition = "inline" if want_inline else "attachment"
    headers = {"Content-Disposition": f'{disposition}; filename="{display_name}"'}
    return FileResponse(target, media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# Static frontend — must be last (catches everything not matched above)
# ---------------------------------------------------------------------------
if _FRONTEND_DIST.is_dir():
    _INDEX_HTML = _FRONTEND_DIST / "index.html"

    @app.get("/")
    def index():
        return FileResponse(
            _INDEX_HTML,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="static")
else:
    @app.get("/")
    def root():
        return JSONResponse(
            {"error": "Frontend not built. Run: cd frontend && npm run build"},
            status_code=503,
        )
