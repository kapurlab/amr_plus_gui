"""
AMRFinderPlus GUI — report builder.

Produces two deliverables from a completed per-sample run directory:

  <sample>_<date>_stats.xlsx
      A single labeled column of statistics (column A = label, column B =
      value), modelled on the vSNP3 stats workbook so the two tools read the
      same way. Input-file QC, assembly QC, organism ID and AMR summary in one
      flat, labeled list.

  report.pdf
      A human-readable PDF: input file quality, analysis summary (with a couple
      of figures when matplotlib is available), and the main AMR results in a
      well-described, easy-to-understand layout, plus a methods/provenance page.

Both are best-effort: a missing artifact or a missing optional dependency
(reportlab / matplotlib) degrades gracefully and is reported in the log rather
than failing the pipeline.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Artifact loading
# ---------------------------------------------------------------------------
def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# AMRFinderPlus column header aliases (the leading `name` and trailing
# `Hierarchy node` columns and small header-wording changes across DB versions).
_COL = {
    "element": ("Element symbol", "Gene symbol"),
    "name": ("Element name", "Sequence name"),
    "scope": ("Scope",),
    "type": ("Type", "Element type"),
    "subtype": ("Subtype", "Element subtype"),
    "cls": ("Class",),
    "subcls": ("Subclass",),
    "method": ("Method",),
    "cov": ("% Coverage of reference", "% Coverage of reference sequence"),
    "ident": ("% Identity to reference", "% Identity to reference sequence"),
    "contig": ("Contig id", "Sequence name"),
}


def _pick(row: Dict[str, str], keys: Tuple[str, ...]) -> str:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return ""


def load_amrfinder_tsv(path: Path) -> List[Dict[str, str]]:
    """Read amrfinder.tsv into a list of normalized dict rows."""
    if not path.is_file():
        return []
    rows: List[Dict[str, str]] = []
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for raw in reader:
            rows.append(
                {
                    "element": _pick(raw, _COL["element"]),
                    "name": _pick(raw, _COL["name"]),
                    "scope": _pick(raw, _COL["scope"]),
                    "type": _pick(raw, _COL["type"]),
                    "subtype": _pick(raw, _COL["subtype"]),
                    "class": _pick(raw, _COL["cls"]),
                    "subclass": _pick(raw, _COL["subcls"]),
                    "method": _pick(raw, _COL["method"]),
                    "coverage": _pick(raw, _COL["cov"]),
                    "identity": _pick(raw, _COL["ident"]),
                    "contig": _pick(raw, _COL["contig"]),
                }
            )
    return rows


def summarize_amr(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """Counts and groupings for the summary + figures."""
    by_type: Dict[str, int] = {}
    by_class: Dict[str, int] = {}
    point = 0
    for r in rows:
        t = (r.get("type") or "—").upper()
        by_type[t] = by_type.get(t, 0) + 1
        cls = r.get("class") or "—"
        by_class[cls] = by_class.get(cls, 0) + 1
        if (r.get("method") or "").upper() == "POINT" or (r.get("subtype") or "").upper() == "POINT":
            point += 1
    return {
        "total": len(rows),
        "by_type": by_type,
        "by_class": by_class,
        "amr_genes": by_type.get("AMR", 0),
        "stress": by_type.get("STRESS", 0),
        "virulence": by_type.get("VIRULENCE", 0),
        "point_mutations": point,
        "classes": sorted(c for c in by_class if c and c != "—"),
    }


def _fmt_int(v: Any) -> str:
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "—" if v in (None, "") else str(v)


def _fmt_pct(v: Any, dp: int = 2) -> str:
    try:
        return f"{float(v):.{dp}f}%"
    except (TypeError, ValueError):
        return "—" if v in (None, "") else str(v)


def _fastq_label(stats: Dict[str, Any], tag: str, items: List[Tuple[str, str]]) -> None:
    """Append vSNP3-style R1/R2 read-quality rows for one FASTQ file."""
    if not stats:
        return
    items.append((f"FASTQ_{tag}", stats.get("file", "—")))
    items.append((f"{tag} Read Count", _fmt_int(stats.get("num_seqs"))))
    items.append((f"{tag} Length Sum (bp)", _fmt_int(stats.get("sum_len"))))
    items.append((f"{tag} Min Length", _fmt_int(stats.get("min_len"))))
    items.append((f"{tag} Avg Length", _fmt_int(stats.get("avg_len"))))
    items.append((f"{tag} GC (%)", _fmt_pct(stats.get("gc_pct"))))
    items.append((f"{tag} Q20 (%)", _fmt_pct(stats.get("q20_pct"))))
    items.append((f"{tag} Q30 (%)", _fmt_pct(stats.get("q30_pct"))))
    items.append((f"{tag} Read Quality Ave", stats.get("avg_qual", "—")))


# ---------------------------------------------------------------------------
# Build the ordered, labeled stats list (one metric per row)
# ---------------------------------------------------------------------------
def build_stats_items(
    sample: str,
    date_stamp: str,
    fastq_qc: Dict[str, Any],
    qc: Dict[str, Any],
    detection: Dict[str, Any],
    manifest: Dict[str, Any],
    amr_summary: Dict[str, Any],
) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    opts = manifest.get("options", {}) or {}
    vers = manifest.get("versions", {}) or {}
    vers_extra = manifest.get("versions_extra", {}) or {}

    # — Sample —
    items.append(("sample", sample))
    items.append(("date", date_stamp))
    items.append(("Pipeline", manifest.get("tool", "AMRFinderPlus")))

    # — Input file quality (vSNP3-style) —
    files = (fastq_qc or {}).get("files", {})
    if files:
        _fastq_label(files.get("R1", {}), "R1", items)
        if files.get("R2"):
            _fastq_label(files.get("R2", {}), "R2", items)
    else:
        items.append(("Input", "assembly FASTA (no reads — read QC skipped)"))

    # — Organism identification —
    items.append(("Dominant species (Kraken)", detection.get("dominant_species", "—")))
    items.append(("Dominant species (%)", _fmt_pct(detection.get("dominant_pct"))))
    items.append(("Runner-up species", detection.get("runner_up_species", "—")))
    items.append(("Runner-up species (%)", _fmt_pct(detection.get("runner_up_pct"))))
    items.append(("Contamination flagged", "yes" if detection.get("contamination_flag") else "no"))
    items.append(("MLST scheme", detection.get("mlst_scheme") or "—"))
    items.append(("MLST ST", detection.get("mlst_st") or "—"))
    items.append(("Resolved --organism", detection.get("organism_token") or "(none)"))
    items.append(("Organism source", detection.get("organism_source", "—")))
    items.append(("Organism confidence", detection.get("confidence", "—")))

    # — Assembly QC —
    m = (qc or {}).get("metrics", {}) or {}
    items.append(("Contigs", _fmt_int(m.get("num_seqs"))))
    items.append(("Assembly Length (bp)", _fmt_int(m.get("total_length"))))
    items.append(("N50", _fmt_int(m.get("n50"))))
    items.append(("Assembly GC (%)", _fmt_pct(m.get("gc_pct"))))
    items.append(("Largest contig (bp)", _fmt_int(m.get("max_len"))))
    if qc.get("expected_genome_size"):
        items.append(("Expected genome size (bp)", _fmt_int(qc.get("expected_genome_size"))))
    items.append(("Assembly QC verdict", (qc.get("verdict") or "—").upper()))

    # — AMR results —
    items.append(("AMRFinderPlus DB version", vers.get("amrfinder_db") or detection.get("db_version") or "—"))
    items.append(("--plus used", "yes" if opts.get("plus") else "no"))
    items.append(("Total elements reported", _fmt_int(amr_summary.get("total"))))
    items.append(("AMR genes", _fmt_int(amr_summary.get("amr_genes"))))
    items.append(("Point mutations", _fmt_int(amr_summary.get("point_mutations"))))
    items.append(("Stress/biocide/metal genes", _fmt_int(amr_summary.get("stress"))))
    items.append(("Virulence genes", _fmt_int(amr_summary.get("virulence"))))
    classes = amr_summary.get("classes") or []
    items.append(("Drug classes detected", ", ".join(classes) if classes else "none"))

    # — Methods / provenance —
    items.append(("ident_min", str(opts.get("ident_min", "—"))))
    items.append(("coverage_min", str(opts.get("coverage_min", "—"))))
    items.append(("amrfinder version", vers.get("amrfinder", "—")))
    items.append(("kraken2 version", vers_extra.get("kraken2") or "—"))
    items.append(("mlst version", vers_extra.get("mlst") or "—"))
    assembler = "shovill" if vers_extra.get("shovill") else ("SPAdes" if vers_extra.get("spades") else "—")
    items.append(("Assembler", assembler))
    iso = [r.get("standard") for r in (manifest.get("iso_references") or []) if r.get("standard")]
    items.append(("Standards referenced", ", ".join(iso) if iso else "—"))
    return items


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def load_plasmidfinder_tsv(path: Path) -> List[Dict[str, str]]:
    """Parse PlasmidFinder's results_tab.tsv (copied to plasmidfinder.tsv) into
    replicon rows for the report. Returns [] when the file is absent (the step
    didn't run) — the report distinguishes that from 'ran, none found' via
    plasmidfinder.json's presence, checked by the caller."""
    rows: List[Dict[str, str]] = []
    if not Path(path).is_file():
        return rows
    import csv as _csv
    with open(path, encoding="utf-8") as fh:
        for r in _csv.DictReader(fh, delimiter="\t"):
            contig = (r.get("Contig") or "").split()[0] if r.get("Contig") else ""
            rows.append({
                "replicon": (r.get("Plasmid") or "").strip(),
                "identity": (r.get("Identity") or "").strip(),
                "contig": contig,
                "accession": (r.get("Accession number") or "").strip(),
                "database": (r.get("Database") or "").strip(),
            })
    return rows


def load_virulencefinder_tsv(path: Path) -> List[Dict[str, str]]:
    """Parse VirulenceFinder's results_tab.tsv into virulence-gene rows."""
    rows: List[Dict[str, str]] = []
    if not Path(path).is_file():
        return rows
    import csv as _csv
    with open(path, encoding="utf-8") as fh:
        for r in _csv.DictReader(fh, delimiter="\t"):
            contig = (r.get("Contig") or "").split()[0] if r.get("Contig") else ""
            rows.append({
                "gene": (r.get("Virulence factor") or r.get("Gene") or "").strip(),
                "identity": (r.get("Identity") or "").strip(),
                "contig": contig,
                "database": (r.get("Database") or "").strip(),
            })
    return rows


def build(outdir: Path, sample: str, log=print) -> Dict[str, Optional[str]]:
    """Build stats.xlsx + report.pdf for a finished run dir. Returns the paths
    (or None for any artifact that couldn't be produced). Never raises."""
    outdir = Path(outdir)
    result: Dict[str, Optional[str]] = {"stats_xlsx": None, "report_pdf": None}

    fastq_qc = _load_json(outdir / "fastq_qc.json")
    qc = _load_json(outdir / "qc.json")
    detection = _load_json(outdir / "organism_detection.json")
    manifest = _load_json(outdir / "run_manifest.json")
    amr_rows = load_amrfinder_tsv(outdir / "amrfinder.tsv")
    amr_summary = summarize_amr(amr_rows)
    # CGE finders: each "ran" iff its primary artifact exists (distinguishes
    # 'ran, none found' from 'did not run' for the report's negatives).
    plasmid_ran = (outdir / "plasmidfinder.json").is_file()
    plasmids = load_plasmidfinder_tsv(outdir / "plasmidfinder.tsv")
    serotype = _load_json(outdir / "serotype.json")
    virulence_ran = (outdir / "virulencefinder.tsv").is_file()
    virulence_genes = load_virulencefinder_tsv(outdir / "virulencefinder.tsv")

    date_stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    items = build_stats_items(sample, date_stamp, fastq_qc, qc, detection, manifest, amr_summary)

    # --- stats workbook (single labeled column) ---
    try:
        from .stats_excel import write_stats_xlsx
        xlsx_path = outdir / f"{sample}_{date_stamp}_stats.xlsx"
        write_stats_xlsx(items, xlsx_path, sample)
        result["stats_xlsx"] = str(xlsx_path)
        log(f"  wrote {xlsx_path.name}")
    except Exception as exc:  # noqa: BLE001 — soft-fail, report it
        log(f"  WARNING: stats workbook not written: {exc}")

    # --- PDF report ---
    try:
        from .pdf_report import write_pdf
        pdf_path = outdir / "report.pdf"
        ctx = {
            "sample": sample,
            "date": date_stamp,
            "fastq_qc": fastq_qc,
            "qc": qc,
            "detection": detection,
            "manifest": manifest,
            "amr_rows": amr_rows,
            "amr_summary": amr_summary,
            "stats_items": items,
            "plasmid_ran": plasmid_ran,
            "plasmids": plasmids,
            "serotype": serotype,
            "virulence_ran": virulence_ran,
            "virulence_genes": virulence_genes,
        }
        write_pdf(ctx, pdf_path, outdir)
        result["report_pdf"] = str(pdf_path)
        log(f"  wrote {pdf_path.name}")
    except Exception as exc:  # noqa: BLE001
        log(f"  WARNING: PDF report not written ({exc}). Is reportlab installed?")

    return result


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Build AMR stats.xlsx + report.pdf for a run dir.")
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--sample", required=True)
    args = ap.parse_args()
    out = build(args.outdir, args.sample)
    print(json.dumps(out, indent=2))
