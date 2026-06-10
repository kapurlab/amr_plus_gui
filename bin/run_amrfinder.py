#!/usr/bin/env python
"""
run_amrfinder.py — run NCBI AMRFinderPlus on an assembly, capturing EVERY
option used and writing ISO-aware provenance.

AMRFinderPlus identifies acquired AMR genes, point mutations, and (with --plus)
virulence + stress/biocide/metal/acid-resistance content. Resistance findings
must be defensible, so this wrapper:
  - records every command-line option, threshold and tool/DB version used,
  - keeps --mutation_all so "position assessed, no resistance mutation" is
    documented (negative findings are reportable),
  - honors TMPDIR,
  - surfaces StxTyper (auto-run for Escherichia with -n --plus).

ISO / quality standards referenced in the provenance (for traceability):
  ISO 20776-1/-2 (reference broth microdilution; phenotypic AST comparator),
  ISO 15189:2022 (medical lab quality: traceability, validation, version
  control, reporting), ISO/IEC 17025 (testing-lab competence),
  EUCAST/CLSI breakpoints (interpretive standard if categorical S/I/R shown).

Run standalone:
  python run_amrfinder.py --assembly assembly.fasta --outdir DIR --name SAMPLE \
      [--organism Escherichia] [--plus] [--threads 8]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ISO_REFERENCES = [
    {"standard": "ISO 20776-1", "scope": "Reference broth microdilution (phenotypic AST comparator)"},
    {"standard": "ISO 20776-2", "scope": "Evaluation of AST device performance vs. reference"},
    {"standard": "ISO 15189:2022", "scope": "Medical lab quality & competence (traceability, validation, version control, reporting)"},
    {"standard": "ISO/IEC 17025", "scope": "Testing-lab competence (surveillance / veterinary)"},
    {"standard": "EUCAST/CLSI breakpoints", "scope": "Interpretive standard if categorical S/I/R is reported"},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tool_version(cmd: List[str]) -> Optional[str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        return out.splitlines()[0].strip() if out else None
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None


def _amrfinder_db_version() -> Optional[str]:
    """Parse the AMRFinderPlus database version from `amrfinder -V` / `-l`."""
    for args in (["amrfinder", "-V"], ["amrfinder", "-l"]):
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            for line in out.splitlines():
                if "database version" in line.lower():
                    m = re.search(r"version[:\s]+([0-9][\w.\-]*)", line, re.IGNORECASE)
                    if m:
                        return m.group(1)
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            continue
    return None


def build_amrfinder_command(
    assembly: Path,
    outdir: Path,
    name: str,
    organism: Optional[str],
    use_plus: bool,
    threads: int,
    ident_min: float,
    coverage_min: float,
    amrfinder_db: Optional[str] = None,
) -> List[str]:
    """Assemble the full amrfinder argv. Every option here is mirrored into the
    manifest by run()."""
    cmd = [
        "amrfinder",
        "-n", str(assembly),            # nucleotide input (assembly)
        "--name", name,                 # leading `name` column
        "-o", str(outdir / "amrfinder.tsv"),
        "--mutation_all", str(outdir / "mutation_all.tsv"),  # negative findings
        "--print_node",                 # adds Hierarchy node column
        "--threads", str(threads),
        "--coverage_min", str(coverage_min),
        "--ident_min", str(ident_min),  # -1 = curated per-gene default
    ]
    if organism:
        cmd += ["-O", organism]
    if use_plus:
        cmd += ["--plus"]
    # Only pin -d when the path is a *real* AMRFinderPlus DB. A configured-but-
    # missing path (e.g. the informative default before the shared DB is
    # installed) would make amrfinder abort with "No valid AMRFinder database".
    # When invalid/empty we omit -d and let amrfinder resolve its own DB via
    # $CONDA_PREFIX/share/amrfinderplus/data/latest (the OOD launcher sets it).
    if amrfinder_db and _is_valid_amrfinder_db(amrfinder_db):
        cmd += ["-d", amrfinder_db]
    elif amrfinder_db:
        print(f"WARNING: configured amrfinder_db is not a valid DB ({amrfinder_db}); "
              "omitting -d and using the env's bundled DB instead.", flush=True)
    return cmd


def _is_valid_amrfinder_db(path: str) -> bool:
    """True if `path` (a dir or symlink to one) holds an AMRFinderPlus DB.

    A built DB dir carries a version stamp plus the indexed reference files;
    checking for the version file + an AMRProt index is enough to distinguish a
    real DB from a missing/empty path."""
    try:
        d = Path(path).resolve()
    except OSError:
        return False
    if not d.is_dir():
        return False
    has_version = (d / "version.txt").is_file()
    has_refs = any(d.glob("AMRProt*")) or any(d.glob("AMR_CDS*"))
    return has_version and has_refs


def run(
    assembly: Path,
    outdir: Path,
    name: str,
    organism: Optional[str] = None,
    use_plus: bool = True,
    threads: int = 4,
    ident_min: float = -1.0,
    coverage_min: float = 0.5,
    amrfinder_db: Optional[str] = None,
    organism_detection: Optional[Dict[str, Any]] = None,
    qc: Optional[Dict[str, Any]] = None,
    extra_provenance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run AMRFinderPlus and write amrfinder.tsv, mutation_all.tsv, and
    run_manifest.json. Returns the manifest dict."""
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = build_amrfinder_command(
        assembly, outdir, name, organism, use_plus, threads, ident_min, coverage_min, amrfinder_db
    )

    env = dict(os.environ)
    env.setdefault("TMPDIR", "/tmp")

    print(f"$ {' '.join(cmd)}", flush=True)
    started = _now()
    rc = 0
    stderr_tail = ""
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        rc = proc.returncode
        if proc.stdout:
            print(proc.stdout, flush=True)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr, flush=True)
            stderr_tail = "\n".join(proc.stderr.splitlines()[-20:])
    except FileNotFoundError:
        rc = 127
        stderr_tail = "amrfinder not found on PATH"
        print("ERROR: amrfinder not found on PATH", file=sys.stderr, flush=True)
    finished = _now()

    stxtyper_active = bool(use_plus and organism and organism.lower().startswith("escherichia"))

    manifest: Dict[str, Any] = {
        "tool": "AMRFinderPlus",
        "sample": name,
        "assembly": str(assembly),
        "command": cmd,
        "started_at": started,
        "finished_at": finished,
        "return_code": rc,
        "options": {
            "organism": organism,
            "plus": bool(use_plus),
            "threads": threads,
            "ident_min": ident_min,
            "coverage_min": coverage_min,
            "mutation_all": True,
            "print_node": True,
            "amrfinder_db": amrfinder_db,
            "input_mode": "nucleotide (-n)",
        },
        "outputs": {
            "amrfinder_tsv": str(outdir / "amrfinder.tsv"),
            "mutation_all_tsv": str(outdir / "mutation_all.tsv"),
        },
        "versions": {
            "amrfinder": _tool_version(["amrfinder", "--version"]) or _tool_version(["amrfinder", "-V"]),
            "amrfinder_db": _amrfinder_db_version(),
        },
        "organism_source": (organism_detection or {}).get("organism_source"),
        "organism_confidence": (organism_detection or {}).get("confidence"),
        "contamination_flag": (organism_detection or {}).get("contamination_flag"),
        "stxtyper_active": stxtyper_active,
        "qc": qc,
        "thresholds_note": (
            "ident_min=-1 uses AMRFinderPlus's curated per-gene identity cutoffs; "
            "coverage_min is the minimum fraction of the reference covered. "
            "Method ALLELE/EXACT/POINT = high-confidence; PARTIAL*/INTERNAL_STOP = review."
        ),
        "reportable_metrics_per_call": [
            "% Coverage of reference", "% Identity to reference", "Method",
            "Alignment length",
        ],
        "iso_references": ISO_REFERENCES,
        "tmpdir": env.get("TMPDIR"),
        "stderr_tail": stderr_tail,
    }
    if extra_provenance:
        manifest.update(extra_provenance)

    (outdir / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if rc != 0:
        print(f"WARNING: amrfinder exited with code {rc}", flush=True)
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run AMRFinderPlus with full provenance.")
    ap.add_argument("--assembly", type=Path, required=True)
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--organism", default=None)
    ap.add_argument("--plus", action="store_true", default=False)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--ident-min", type=float, default=-1.0)
    ap.add_argument("--coverage-min", type=float, default=0.5)
    ap.add_argument("--amrfinder-db", default=None)
    args = ap.parse_args(argv)

    manifest = run(
        args.assembly, args.outdir, args.name,
        organism=args.organism, use_plus=args.plus, threads=args.threads,
        ident_min=args.ident_min, coverage_min=args.coverage_min,
        amrfinder_db=args.amrfinder_db,
    )
    return 0 if manifest.get("return_code", 1) == 0 else manifest.get("return_code", 1)


if __name__ == "__main__":
    sys.exit(main())
