#!/usr/bin/env python
"""
amr_pipeline.py — orchestrator for the AMRFinderPlus GUI.

Pipeline (per sample):
  1. Organism detection — Kraken2 on the reads -> report, parsed by
     detect_organism.py (conservative dominance + complex collapse).
  2. Assembly — shovill (preferred) or spades.py --isolate -> assembly.fasta.
     Skipped when an assembly FASTA is provided.
  3. Assembly QC — seqkit stats -> qc.json (verdict pass/review vs. expected
     genome size). Never blocks; warns in the log.
  4. MLST corroboration (optional) — sibling mlst_gui runner or `mlst` binary
     -> scheme/ST -> token, compared with Kraken's call.
  5. AMRFinderPlus — run_amrfinder.py on the assembly with the resolved/forced
     --organism, --plus, --mutation_all, --print_node, etc.
  6. Provenance — run_manifest.json (written by run_amrfinder) + the
     organism_detection.json and qc.json this orchestrator writes.

Output dir is <project>/amr/<sample>/ (passed via --outdir). Every artifact
lands there with a stable name so the backend's result endpoints find it.

Usage:
  amr_pipeline.py --sample S --outdir DIR (-r1 R1 [-r2 R2] | --assembly A.fasta)
      [--force-organism TOKEN] [--plus] [--no-kraken] [--no-mlst]
      [--kraken-db DB] [--amrfinder-db DB] [--threads N]
      [--ident-min -1] [--coverage-min 0.5]
"""

import argparse
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
_CONFIG_DIR = _REPO_ROOT / "config"

import detect_organism  # local import (PYTHONPATH includes bin/)
import run_amrfinder

_MLST_GUI = Path("/srv/kapurlab/tools/mlst_gui")
_GENOME_SIZES = _CONFIG_DIR / "genome_sizes.yaml"


def log(msg: str) -> None:
    print(msg, flush=True)


def step(title: str) -> None:
    log("")
    log(f"### {title}")


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _run(cmd: List[str], cwd: Optional[Path] = None, env: Optional[dict] = None) -> int:
    log(f"$ {' '.join(str(c) for c in cmd)}")
    try:
        proc = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env)
        return proc.returncode
    except FileNotFoundError:
        log(f"ERROR: command not found: {cmd[0]}")
        return 127


# ---------------------------------------------------------------------------
# Step 1 — Kraken2
# ---------------------------------------------------------------------------
def run_kraken(r1: Path, r2: Optional[Path], outdir: Path, kraken_db: str, threads: int) -> Optional[Path]:
    """Run Kraken2 -> kraken_report.txt. Returns the report path or None."""
    if not _have("kraken2"):
        log("WARNING: kraken2 not on PATH — skipping organism detection by reads.")
        return None
    if not kraken_db:
        log("WARNING: no Kraken2 DB configured — skipping organism detection.")
        return None
    report = outdir / "kraken_report.txt"
    output = outdir / "kraken_output.txt"
    cmd = ["kraken2", "--db", kraken_db, "--threads", str(threads),
           "--report", str(report), "--output", str(output)]
    if r2:
        cmd += ["--paired", str(r1), str(r2)]
    else:
        cmd += [str(r1)]
    rc = _run(cmd)
    if rc != 0 or not report.is_file():
        log(f"WARNING: kraken2 failed (rc={rc}); continuing without read-based detection.")
        return None
    return report


# ---------------------------------------------------------------------------
# Step 2 — Assembly
# ---------------------------------------------------------------------------
def assemble(r1: Path, r2: Optional[Path], outdir: Path, threads: int) -> Optional[Path]:
    """Assemble reads -> assembly.fasta (shovill preferred, then spades)."""
    target = outdir / "assembly.fasta"
    work = outdir / "_assembly"
    if _have("shovill"):
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        cmd = ["shovill", "--outdir", str(work), "--R1", str(r1), "--cpus", str(threads), "--force"]
        if r2:
            cmd += ["--R2", str(r2)]
        else:
            # shovill needs paired reads; fall back to spades for single-end.
            log("shovill requires paired reads; single-end input -> using SPAdes.")
            return _spades(r1, r2, outdir, threads, target)
        rc = _run(cmd)
        contigs = work / "contigs.fa"
        if rc == 0 and contigs.is_file():
            shutil.copyfile(contigs, target)
            return target
        log(f"WARNING: shovill failed (rc={rc}); trying SPAdes.")
    return _spades(r1, r2, outdir, threads, target)


def _spades(r1: Path, r2: Optional[Path], outdir: Path, threads: int, target: Path) -> Optional[Path]:
    if not _have("spades.py"):
        log("ERROR: neither shovill nor spades.py available — cannot assemble.")
        return None
    work = outdir / "_spades"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    cmd = ["spades.py", "--isolate", "-o", str(work), "-t", str(threads)]
    if r2:
        cmd += ["-1", str(r1), "-2", str(r2)]
    else:
        cmd += ["-s", str(r1)]
    rc = _run(cmd)
    contigs = work / "contigs.fasta"
    if rc == 0 and contigs.is_file():
        shutil.copyfile(contigs, target)
        return target
    log(f"ERROR: SPAdes failed (rc={rc}).")
    return None


# ---------------------------------------------------------------------------
# Step 3 — Assembly QC (seqkit stats)
# ---------------------------------------------------------------------------
def assembly_qc(assembly: Path, outdir: Path, dominant_species: Optional[str]) -> Dict[str, Any]:
    """seqkit stats -> qc.json with a pass/review verdict vs. expected size."""
    qc: Dict[str, Any] = {"assembly": str(assembly), "verdict": "review", "metrics": {}, "notes": []}
    if _have("seqkit"):
        try:
            proc = subprocess.run(
                ["seqkit", "stats", "-T", "-a", str(assembly)],
                capture_output=True, text=True, timeout=300,
            )
            lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
            if len(lines) >= 2:
                hdr = lines[0].split("\t")
                vals = lines[1].split("\t")
                row = dict(zip(hdr, vals))
                def num(k):
                    try:
                        return float(row.get(k, "").replace(",", ""))
                    except (ValueError, AttributeError):
                        return None
                qc["metrics"] = {
                    "num_seqs": num("num_seqs"),
                    "total_length": num("sum_len"),
                    "n50": num("N50"),
                    "gc_pct": num("GC(%)"),
                    "min_len": num("min_len"),
                    "max_len": num("max_len"),
                }
        except (subprocess.SubprocessError, OSError) as exc:
            qc["notes"].append(f"seqkit stats failed: {exc}")
    else:
        qc["notes"].append("seqkit not on PATH — assembly QC metrics unavailable.")

    # Compare to expected genome size.
    expected, tol = _expected_genome_size(dominant_species)
    total = (qc["metrics"] or {}).get("total_length")
    if expected and total:
        lo, hi = expected * (1 - tol), expected * (1 + tol)
        within = lo <= total <= hi
        qc["expected_genome_size"] = expected
        qc["size_tolerance"] = tol
        qc["verdict"] = "pass" if within else "review"
        if not within:
            qc["notes"].append(
                f"Assembly total length {int(total):,} bp is outside ±{int(tol*100)}% "
                f"of expected {expected:,} bp for {dominant_species}."
            )
    elif total:
        qc["verdict"] = "pass"
        qc["notes"].append("No expected genome size for this species; size check skipped.")

    (outdir / "qc.json").write_text(json.dumps(qc, indent=2) + "\n", encoding="utf-8")
    return qc


def _expected_genome_size(species: Optional[str]):
    if not species:
        return None, 0.20
    data = detect_organism._load_yaml(_GENOME_SIZES)
    tol = ((data.get("defaults") or {}).get("tolerance")) or 0.20
    sizes = data.get("species", {}) or {}
    if species in sizes:
        return sizes[species], tol
    # genus + species exact already tried; try binomial reduction
    binom = detect_organism._species_binomial(species)
    if binom in sizes:
        return sizes[binom], tol
    return None, tol


# ---------------------------------------------------------------------------
# Input-file QC (seqkit stats on the raw reads) — the "quality stats of the
# input files" surfaced in the report and stats workbook.
# ---------------------------------------------------------------------------
def fastq_qc(r1: Optional[Path], r2: Optional[Path], outdir: Path) -> Dict[str, Any]:
    """Run `seqkit stats -a` on the input reads -> fastq_qc.json.

    -a adds Q20(%)/Q30(%)/AvgQual/GC(%) alongside counts/lengths/N50, giving the
    read-quality metrics a defensible report needs."""
    qc: Dict[str, Any] = {"files": {}, "notes": []}
    inputs = [("R1", r1), ("R2", r2)]
    if not _have("seqkit"):
        qc["notes"].append("seqkit not on PATH — input read QC unavailable.")
        (outdir / "fastq_qc.json").write_text(json.dumps(qc, indent=2) + "\n", encoding="utf-8")
        return qc
    for tag, path in inputs:
        if not path:
            continue
        try:
            proc = subprocess.run(
                ["seqkit", "stats", "-T", "-a", str(path)],
                capture_output=True, text=True, timeout=600,
            )
            lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
            if len(lines) >= 2:
                row = dict(zip(lines[0].split("\t"), lines[1].split("\t")))

                def num(k):
                    try:
                        return float(str(row.get(k, "")).replace(",", ""))
                    except (ValueError, AttributeError):
                        return None

                qc["files"][tag] = {
                    "file": Path(path).name,
                    "num_seqs": num("num_seqs"),
                    "sum_len": num("sum_len"),
                    "min_len": num("min_len"),
                    "avg_len": num("avg_len"),
                    "max_len": num("max_len"),
                    "n50": num("N50"),
                    "gc_pct": num("GC(%)"),
                    "q20_pct": num("Q20(%)"),
                    "q30_pct": num("Q30(%)"),
                    "avg_qual": row.get("AvgQual", "").strip() or None,
                }
        except (subprocess.SubprocessError, OSError) as exc:
            qc["notes"].append(f"seqkit stats failed for {tag}: {exc}")
    (outdir / "fastq_qc.json").write_text(json.dumps(qc, indent=2) + "\n", encoding="utf-8")
    return qc


# ---------------------------------------------------------------------------
# Step 4 — MLST corroboration
# ---------------------------------------------------------------------------
def run_mlst(assembly: Path, outdir: Path, sample: str) -> Optional[Path]:
    """Run MLST and return the path to a JSON result with an `organism_token`.

    Prefers the sibling mlst_gui runner (bin/mlst_pipeline.py), which writes
    DIR/mlst_result.json with an `organism_token` field. That tool is being
    built in parallel, so its absence is tolerated. Falls back to the `mlst`
    binary, whose TSV we convert into a minimal JSON.
    """
    out_json = outdir / "mlst_result.json"
    runner = _MLST_GUI / "bin" / "mlst_pipeline.py"
    if runner.is_file():
        rc = _run([sys.executable, str(runner), "--assembly", str(assembly),
                   "--outdir", str(outdir), "--label", sample])
        if rc == 0 and out_json.is_file():
            return out_json
        log(f"WARNING: mlst_gui runner did not produce mlst_result.json (rc={rc}).")
    else:
        log(f"NOTE: sibling mlst runner not found at {runner} (built in parallel).")

    if _have("mlst"):
        tsv = outdir / "mlst.tsv"
        rc = _run(["mlst", str(assembly)])  # mlst prints to stdout
        try:
            proc = subprocess.run(["mlst", str(assembly)], capture_output=True, text=True, timeout=600)
            tsv.write_text(proc.stdout or "", encoding="utf-8")
            # mlst TSV: FILE  SCHEME  ST  allele:n ...
            first = (proc.stdout or "").splitlines()[0].split("\t") if proc.stdout else []
            scheme = first[1] if len(first) > 1 else None
            st = first[2] if len(first) > 2 else None
            result = {"scheme": scheme, "st": st, "organism_token": scheme}
            out_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return out_json
        except (subprocess.SubprocessError, OSError, IndexError) as exc:
            log(f"WARNING: mlst binary failed: {exc}")
    else:
        log("NOTE: `mlst` binary not on PATH — skipping MLST corroboration.")
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    try:
        multiprocessing.set_start_method("spawn", True)
    except RuntimeError:
        pass

    ap = argparse.ArgumentParser(description="AMRFinderPlus pipeline orchestrator.")
    ap.add_argument("--sample", required=True)
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("-r1", "--r1", dest="r1", type=Path, default=None)
    ap.add_argument("-r2", "--r2", dest="r2", type=Path, default=None)
    ap.add_argument("--assembly", type=Path, default=None)
    ap.add_argument("--force-organism", default=None)
    ap.add_argument("--plus", action="store_true", default=False)
    ap.add_argument("--no-kraken", action="store_true", default=False)
    ap.add_argument("--no-mlst", action="store_true", default=False)
    ap.add_argument("--kraken-db", default=os.environ.get("KRAKEN_DB", ""))
    ap.add_argument("--amrfinder-db", default=None)
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    ap.add_argument("--ident-min", type=float, default=-1.0)
    ap.add_argument("--coverage-min", type=float, default=0.5)
    args = ap.parse_args(argv)

    outdir: Path = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    log("=" * 70)
    log(f"AMRFinderPlus pipeline — sample: {args.sample}")
    log(f"  outdir:  {outdir}")
    log(f"  threads: {args.threads}  plus: {args.plus}")
    log("=" * 70)

    if not args.assembly and not args.r1:
        log("ERROR: provide either --assembly or -r1 (reads).")
        return 2

    # ---- Input file QC (raw reads) ----
    if args.r1:
        step("Input file QC (seqkit stats on reads)")
        fq = fastq_qc(args.r1, args.r2, outdir)
        for tag, s in (fq.get("files") or {}).items():
            log(f"  {tag}: {s.get('num_seqs')} reads, Q30 {s.get('q30_pct')}%, GC {s.get('gc_pct')}%")

    # ---- Step 1: Kraken2 organism detection ----
    kraken_report: Optional[Path] = None
    if not args.no_kraken and args.r1 and not args.assembly:
        step("Step 1: Organism detection (Kraken2)")
        kraken_report = run_kraken(args.r1, args.r2, outdir, args.kraken_db, args.threads)
    elif args.assembly:
        log("Input is an assembly FASTA — skipping read-based Kraken2 detection.")
    else:
        log("Kraken2 detection disabled (--no-kraken).")

    # ---- Step 2: Assembly ----
    if args.assembly:
        assembly = args.assembly
        log(f"Using provided assembly: {assembly}")
    else:
        step("Step 2: Assembly (shovill / SPAdes)")
        assembly = assemble(args.r1, args.r2, outdir, args.threads)
        if assembly is None:
            log("ERROR: assembly failed — cannot run AMRFinderPlus.")
            return 1

    # ---- Step 4 (run before detection reconcile so MLST can corroborate) ----
    mlst_json: Optional[Path] = None
    if not args.no_mlst:
        step("Step 3: MLST corroboration")
        mlst_json = run_mlst(assembly, outdir, args.sample)

    # ---- Organism resolution (Kraken + MLST + force) ----
    step("Resolving organism (conservative, corroborated)")
    detection = detect_organism.resolve(
        kraken_report=kraken_report,
        mlst_json=mlst_json,
        force_organism=args.force_organism,
    )
    detection["used_plus"] = bool(args.plus)
    (outdir / "organism_detection.json").write_text(
        json.dumps(detection, indent=2) + "\n", encoding="utf-8"
    )
    for note in detection.get("notes", []):
        log(f"  - {note}")
    organism = detection.get("organism_token")
    log(f"Resolved --organism: {organism or '(none — running without -O)'} "
        f"[source={detection.get('organism_source')}, "
        f"confidence={detection.get('confidence')}]")
    if detection.get("contamination_flag"):
        log("  ⚠ Contamination/mixture flagged — AMRFinderPlus will run WITHOUT -O.")

    # ---- Step 3: Assembly QC ----
    step("Step 4: Assembly QC (seqkit stats)")
    qc = assembly_qc(assembly, outdir, detection.get("dominant_species"))
    log(f"  QC verdict: {qc.get('verdict')}  metrics: {qc.get('metrics')}")

    # ---- Step 5: AMRFinderPlus ----
    step("Step 5: AMRFinderPlus")
    manifest = run_amrfinder.run(
        assembly=assembly,
        outdir=outdir,
        name=args.sample,
        organism=organism,
        use_plus=bool(args.plus),
        threads=args.threads,
        ident_min=args.ident_min,
        coverage_min=args.coverage_min,
        amrfinder_db=args.amrfinder_db,
        organism_detection=detection,
        qc=qc,
        extra_provenance={
            "pipeline_started_at": started,
            "pipeline_finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kraken_db": args.kraken_db or None,
            "kraken_report": str(kraken_report) if kraken_report else None,
            "mlst_result": str(mlst_json) if mlst_json else None,
            "versions_extra": {
                "kraken2": run_amrfinder._tool_version(["kraken2", "--version"]),
                "mlst": run_amrfinder._tool_version(["mlst", "--version"]),
                "shovill": run_amrfinder._tool_version(["shovill", "--version"]),
                "spades": run_amrfinder._tool_version(["spades.py", "--version"]),
                "seqkit": run_amrfinder._tool_version(["seqkit", "version"]),
            },
        },
    )

    rc = manifest.get("return_code", 1)
    if manifest.get("stxtyper_active"):
        log("StxTyper ran (Escherichia + --plus): Stx subtypes are in the AMRFinderPlus output.")

    # ---- Step 6: Report (stats workbook + PDF) ----
    step("Step 6: Building report (stats.xlsx + report.pdf)")
    try:
        import reporting  # bin/ is on PYTHONPATH
        reporting.build(outdir, args.sample, log=log)
    except Exception as exc:  # noqa: BLE001 — never fail the run over the report
        log(f"  WARNING: report generation failed: {exc}")

    step("Pipeline completed")
    log(f"AMRFinderPlus return code: {rc}")
    log(f"Outputs in: {outdir}")
    return 0 if rc == 0 else rc


if __name__ == "__main__":
    sys.exit(main())
