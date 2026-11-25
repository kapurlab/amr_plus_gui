#!/usr/bin/env python
"""
cohort_report.py: one report for every isolate in a project.

Reads what the project folder already holds and computes nothing new about a
single isolate:

  <project>/amr/<sample>/      this tool's per-isolate results (organism,
                               MLST, AMRFinderPlus, typing.json, assembly QC)
  a metadata sheet             display names and grouping columns (optional)
  <project>/step2/<run>/...    the newest vSNP Step 2 tree and SNP alignment
                               (optional)

and writes, into <project>/cohort_report/:

  cohort_report.html / .pdf    the report
  cohort_table.tsv / .xlsx     one row per isolate
  cohort_checks.json           the consistency checks and what they found

The report is for the people reading the result. It opens with a summary in
words and a list of the things that change how a call should be read, then the
tables and figures. Every count in the text is computed from the per-isolate
table, and the checks say so when the layers do not line up (an isolate with
no typing result, a tree tip with no isolate, a metadata row with no data).

Usage:
  cohort_report.py --project DIR [--metadata FILE] [--group-by COLUMN]
      [--label-column COLUMN] [--step2-run ID] [--outgroup TIP]
      [--title TEXT] [--no-pdf]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import cohort_figures as fig  # noqa: E402
import cohort_tree  # noqa: E402
import reporting  # noqa: E402
from reporting.html_report import _CSS, _e, html_to_pdf  # noqa: E402

# Matrix block colours: resistance, plasmid replicons, virulence.
C_RESISTANCE = "#C96A7B"
C_REPLICON = "#4B4F9C"
C_REPLICON_LOW = "#B9BBE0"
C_VIRULENCE = "#3FA796"

# The tip vSNP adds for the reference sequence.
VSNP_REFERENCE_TIP = "root"
# Metadata columns tried, in order, as the grouping column when none is named.
GROUP_COLUMN_GUESSES = ("state", "group", "region", "province", "country", "source")


# ---------------------------------------------------------------------------
# Per-isolate data
# ---------------------------------------------------------------------------
@dataclass
class Isolate:
    sample: str
    label: str = ""
    meta: Dict[str, str] = field(default_factory=dict)
    organism: str = ""
    st: str = ""
    serovar_sistr: str = ""
    serovar_seqsero2: str = ""
    serovar_agreement: str = ""
    cgmlst_match: str = ""
    contigs: Optional[float] = None
    length: Optional[float] = None
    n50: Optional[float] = None
    completeness: Optional[float] = None
    contamination: Optional[float] = None
    qc_verdict: str = ""
    contamination_flag: bool = False
    acquired: List[str] = field(default_factory=list)
    point: List[str] = field(default_factory=list)
    replicons: List[str] = field(default_factory=list)
    replicons_low: List[str] = field(default_factory=list)
    virulence: List[str] = field(default_factory=list)
    has_typing: bool = False
    has_amr: bool = False
    sistr_qc: str = ""
    versions: Dict[str, str] = field(default_factory=dict)
    in_tree: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def lineage(self) -> str:
        return self.st

    @property
    def serovar(self) -> str:
        if self.serovar_agreement == "agree":
            return self.serovar_sistr
        if self.serovar_sistr and self.serovar_seqsero2:
            return f"{self.serovar_sistr} / {self.serovar_seqsero2}"
        return self.serovar_sistr or self.serovar_seqsero2

    @property
    def resistance(self) -> List[str]:
        return self.acquired + self.point


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _unique(names: Sequence[str]) -> List[str]:
    seen: List[str] = []
    for n in names:
        if n and n not in seen:
            seen.append(n)
    return seen


def load_isolate(run_dir: Path) -> Isolate:
    """Read one <project>/amr/<sample>/ folder."""
    iso = Isolate(sample=run_dir.name)
    det = _load_json(run_dir / "organism_detection.json")
    qc = _load_json(run_dir / "qc.json")
    typing = _load_json(run_dir / "typing.json")
    manifest = _load_json(run_dir / "run_manifest.json")

    iso.organism = det.get("dominant_species") or det.get("organism_token") or ""
    st = str(det.get("mlst_st") or "").strip()
    iso.st = "" if st in ("", "-", "None") else st
    iso.contamination_flag = bool(det.get("contamination_flag"))
    metrics = qc.get("metrics") or {}
    iso.contigs, iso.length, iso.n50 = metrics.get("num_seqs"), metrics.get("total_length"), metrics.get("n50")
    iso.qc_verdict = str(qc.get("verdict") or "")

    tsv = run_dir / "amrfinder.tsv"
    iso.has_amr = tsv.is_file()
    acquired, point = [], []
    for row in reporting.load_amrfinder_tsv(tsv):
        if (row.get("type") or "").upper() != "AMR":
            continue
        is_point = "POINT" in ((row.get("subtype") or "") + " " + (row.get("method") or "")).upper()
        (point if is_point else acquired).append(row.get("element") or "")
    iso.acquired, iso.point = sorted(_unique(acquired)), sorted(_unique(point))

    iso.has_typing = bool(typing)
    serovar = typing.get("serovar") or {}
    sistr = serovar.get("sistr") or {}
    iso.serovar_sistr = sistr.get("serovar") or ""
    iso.serovar_seqsero2 = (serovar.get("seqsero2") or {}).get("serovar") or ""
    iso.serovar_agreement = serovar.get("agreement") or ""
    iso.sistr_qc = sistr.get("qc_status") or ""
    match = reporting.cgmlst_match_text(sistr) if sistr else ""
    iso.cgmlst_match = "" if match == "—" else match
    hits = typing.get("plasmid_replicons")
    iso.replicons = sorted(reporting.gene_names(hits, low_identity=False))
    iso.replicons_low = sorted(reporting.gene_names(hits, low_identity=True))
    iso.virulence = sorted(reporting.gene_names(typing.get("virulence_genes")))
    checkm2 = typing.get("checkm2") or {}
    iso.completeness, iso.contamination = checkm2.get("completeness"), checkm2.get("contamination")

    versions = dict((manifest.get("versions") or {}))
    versions.update({k: v for k, v in (manifest.get("versions_extra") or {}).items() if v})
    for key, value in (typing.get("versions") or {}).items():
        if isinstance(value, dict):
            for name, date in value.items():
                versions[f"abricate {name} database"] = date
        elif value:
            versions[key] = value
    iso.versions = {k: str(v) for k, v in versions.items() if v}
    return iso


def load_isolates(project: Path) -> List[Isolate]:
    root = project / "amr"
    if not root.is_dir():
        return []
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        if (d / "organism_detection.json").is_file() or (d / "amrfinder.tsv").is_file():
            out.append(load_isolate(d))
    return out


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
def read_table(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """Read a .tsv, .csv or .xlsx sheet with a header row."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
        rows = [["" if c is None else str(c).strip() for c in r] for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(r)]
        if not rows:
            return [], []
        header = rows[0]
        return header, [dict(zip(header, r)) for r in rows[1:]]
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    delimiter = "\t" if "\t" in text.splitlines()[0] else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader]
    return [h.strip() for h in (reader.fieldnames or [])], rows


def find_metadata(project: Path) -> Optional[Path]:
    for name in ("sample_crosswalk.tsv", "metadata.tsv", "metadata.csv", "metadata.xlsx"):
        for folder in (project, project / "download"):
            if (folder / name).is_file():
                return folder / name
    return None


def best_column(header: List[str], rows: List[Dict[str, str]], wanted: set) -> Tuple[Optional[str], int]:
    """The column whose values match the most names in `wanted`."""
    best, hits = None, 0
    for col in header:
        n = len({r.get(col, "") for r in rows} & wanted)
        if n > hits:
            best, hits = col, n
    return best, hits


# ---------------------------------------------------------------------------
# vSNP Step 2
# ---------------------------------------------------------------------------
@dataclass
class Step2:
    run_id: str
    group: str
    tree_path: Path
    alignment_path: Optional[Path]


def find_step2(project: Path, run_id: Optional[str] = None) -> Optional[Step2]:
    """The newest Step 2 run that holds a tree; its All group when it has one."""
    step2 = project / "step2"
    if not step2.is_dir():
        return None
    runs = sorted((d for d in step2.iterdir() if d.is_dir() and d.name != "vcf_database"), reverse=True)
    if run_id:
        runs = [d for d in runs if d.name == run_id]
    for run in runs:
        groups = sorted((g for g in run.iterdir() if g.is_dir() and not g.name.startswith((".", "_"))),
                        key=lambda g: (not g.name.endswith("-All"), g.name))
        for group in groups:
            trees = sorted(t for t in group.glob("*.tre") if not t.name.endswith("_labeled.tre"))
            if trees:
                fastas = sorted(group.glob("*.fasta"))
                return Step2(run.name, group.name, trees[-1], fastas[-1] if fastas else None)
    return None


def read_fasta(path: Path) -> Dict[str, str]:
    seqs: Dict[str, List[str]] = {}
    name = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(">"):
            name = line[1:].split()[0]
            seqs[name] = []
        elif name is not None:
            seqs[name].append(line.strip())
    return {k: "".join(v).upper() for k, v in seqs.items()}


def snp_distance(a: str, b: str) -> int:
    """Positions where both sequences have a plain base and the bases differ.
    A position with N, a gap or an ambiguity code in either is not counted."""
    return sum(1 for x, y in zip(a, b) if x != y and x in "ACGT" and y in "ACGT")


def distance_matrix(seqs: Dict[str, str], names: List[str]) -> List[List[Optional[int]]]:
    n = len(names)
    m: List[List[Optional[int]]] = [[None] * n for _ in range(n)]
    for i in range(n):
        m[i][i] = 0
        for j in range(i + 1, n):
            if names[i] in seqs and names[j] in seqs:
                m[i][j] = m[j][i] = snp_distance(seqs[names[i]], seqs[names[j]])
    return m


# ---------------------------------------------------------------------------
# Small helpers for the text
# ---------------------------------------------------------------------------
def _num(v: Optional[float], digits: int = 0) -> str:
    if v is None:
        return "—"
    return f"{v:,.{digits}f}"


def _range_row(label: str, values: List[Optional[float]], digits: int = 0) -> Tuple[str, str, str, str, int]:
    vals = [v for v in values if v is not None]
    if not vals:
        return (label, "—", "—", "—", 0)
    return (label, _num(min(vals), digits), _num(statistics.median(vals), digits), _num(max(vals), digits), len(vals))


def lineage_text(key: str) -> str:
    return f"ST{key}" if key else "no ST"


def counts_text(counter: Counter, name=lambda k: k) -> str:
    return ", ".join(f"{name(k)} n={n}" for k, n in counter.most_common())


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def build(project: Path, metadata: Optional[Path] = None, group_by: Optional[str] = None,
          label_column: Optional[str] = None, step2_run: Optional[str] = None,
          outgroup: Optional[str] = VSNP_REFERENCE_TIP, title: Optional[str] = None,
          make_pdf: bool = True, log=print) -> Dict[str, Any]:
    project = Path(project).expanduser().resolve()
    outdir = project / "cohort_report"
    problems: List[str] = []
    info: List[str] = []

    isolates = load_isolates(project)
    if not isolates:
        raise SystemExit(f"No per-isolate results under {project / 'amr'}. Run the samples first.")
    samples = {i.sample for i in isolates}
    proj = _load_json(project / "project.json")

    # ---- the tree, so the metadata's label column can be matched to its tips ----
    step2 = find_step2(project, step2_run)
    root = tips = None
    rooting = ""
    seqs: Dict[str, str] = {}
    if step2 is None:
        info.append("No vSNP Step 2 tree was found in this project, so the report has no "
                    "phylogeny or distance section.")
    else:
        try:
            root, tips, rooting = cohort_tree.prepared(
                step2.tree_path.read_text(encoding="utf-8", errors="replace"), outgroup)
        except (OSError, cohort_tree.NewickError) as exc:
            problems.append(f"The Step 2 tree {step2.tree_path.name} could not be read: {exc}")
            root = tips = None
        if step2.alignment_path is not None:
            seqs = read_fasta(step2.alignment_path)
    tip_names = [t.name for t in tips] if tips else []

    # ---- metadata: sample column, label column, grouping column ----
    metadata = metadata or find_metadata(project)
    header: List[str] = []
    rows: List[Dict[str, str]] = []
    sample_col = None
    if metadata is not None:
        try:
            header, rows = read_table(Path(metadata))
        except Exception as exc:  # noqa: BLE001: a bad sheet is reported, not fatal
            problems.append(f"The metadata sheet {Path(metadata).name} could not be read: {exc}")
        sample_col, matched = best_column(header, rows, samples)
        if rows and not sample_col:
            problems.append(f"No column of {Path(metadata).name} holds this project's sample names, "
                            "so the metadata was not used.")
            rows = []
    by_sample = {r[sample_col]: r for r in rows} if sample_col else {}
    if sample_col:
        extra = sorted(set(by_sample) - samples)
        missing = sorted(samples - set(by_sample))
        if extra:
            problems.append(f"{_plural(len(extra), 'metadata row')} with no results in this project: "
                            + ", ".join(extra[:12]) + (" …" if len(extra) > 12 else ""))
        if missing:
            problems.append(f"{_plural(len(missing), 'isolate')} with no metadata row: "
                            + ", ".join(missing[:12]) + (" …" if len(missing) > 12 else ""))
    if label_column and label_column not in header:
        problems.append(f"The label column '{label_column}' is not in the metadata sheet.")
        label_column = None
    if not label_column and rows and tip_names:
        guess, hits = best_column(header, rows, set(tip_names))
        if guess and hits > len(samples & set(tip_names)):
            label_column = guess
    if group_by and group_by not in header:
        problems.append(f"The grouping column '{group_by}' is not in the metadata sheet.")
        group_by = None
    if not group_by:
        lower = {h.lower(): h for h in header}
        group_by = next((lower[g] for g in GROUP_COLUMN_GUESSES if g in lower), None)
    meta_cols = [h for h in header if h not in (sample_col, label_column)]

    for iso in isolates:
        iso.meta = by_sample.get(iso.sample, {})
        iso.label = (iso.meta.get(label_column, "") if label_column else "") or iso.sample
    dup = [k for k, n in Counter(i.label for i in isolates).items() if n > 1]
    if dup:
        problems.append("Two or more isolates share a display name: " + ", ".join(sorted(dup)[:10]))
    by_label = {i.label: i for i in isolates}

    # ---- line the tree up with the isolates ----
    reference_name = proj.get("reference") or "reference"
    if tips:
        for t in tips:
            if t.name in by_label:
                by_label[t.name].in_tree = True
        strangers = [t.name for t in tips if t.name not in by_label and t.name != outgroup]
        absent = sorted(i.label for i in isolates if not i.in_tree)
        if strangers:
            problems.append(f"{_plural(len(strangers), 'tree tip')} with no isolate in this project: "
                            + ", ".join(strangers[:12]) + (" …" if len(strangers) > 12 else ""))
        if absent:
            problems.append(f"{_plural(len(absent), 'isolate')} not in the Step 2 tree: "
                            + ", ".join(absent[:12]) + (" …" if len(absent) > 12 else ""))

    # ---- notes that change how a call is read ----
    untyped = [i.label for i in isolates if not i.has_typing]
    if untyped:
        problems.append(f"{_plural(len(untyped), 'isolate')} with no typing result (the typing tools "
                        "were not installed or the run predates them): " + ", ".join(untyped[:12])
                        + (" …" if len(untyped) > 12 else ""))
    for iso in isolates:
        if not iso.has_amr:
            iso.notes.append("no AMRFinderPlus result")
        if iso.serovar_agreement in ("disagree", "consistent"):
            iso.notes.append(f"serovar: SISTR {iso.serovar_sistr or '—'}, SeqSero2 {iso.serovar_seqsero2 or '—'}")
        elif iso.serovar_agreement == "single":
            iso.notes.append("serovar from one tool only")
        if iso.sistr_qc and iso.sistr_qc != "PASS":
            iso.notes.append(f"SISTR QC {iso.sistr_qc}")
        if iso.qc_verdict and iso.qc_verdict.lower() != "pass":
            iso.notes.append(f"assembly QC {iso.qc_verdict}")
        if iso.contamination_flag:
            iso.notes.append("second species in the reads")
        if iso.contamination is not None and iso.contamination >= 5:
            iso.notes.append(f"CheckM2 contamination {iso.contamination:.1f}%")
        if iso.completeness is not None and iso.completeness < 95:
            iso.notes.append(f"CheckM2 completeness {iso.completeness:.1f}%")
        if iso.replicons_low:
            iso.notes.append("low-identity replicon: " + ", ".join(iso.replicons_low))
        if tips and not iso.in_tree:
            iso.notes.append("not in the tree")

    # ---- what is in every isolate, and what varies ----
    n = len(isolates)
    typed = [i for i in isolates if i.has_typing]

    def everywhere(getter, pool) -> List[str]:
        if not pool:
            return []
        c = Counter(x for i in pool for x in getter(i))
        return sorted(k for k, v in c.items() if v == len(pool))

    res_all = everywhere(lambda i: i.resistance, [i for i in isolates if i.has_amr])
    vir_all = everywhere(lambda i: i.virulence, typed)

    def variable(getter, drop: List[str]) -> List[str]:
        c = Counter(x for i in isolates for x in getter(i) if x not in drop)
        return [k for k, _ in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]

    res_cols = variable(lambda i: i.point, res_all) + variable(lambda i: i.acquired, res_all)
    rep_cols = variable(lambda i: i.replicons + i.replicons_low, [])
    vir_cols = variable(lambda i: i.virulence, vir_all)
    acquired_all = [g for g in res_all if any(g in i.acquired for i in isolates)]
    with_acquired = [i for i in isolates if any(g not in acquired_all for g in i.acquired)]

    lineages = Counter(i.lineage for i in isolates)
    palette = fig.lineage_palette([k for k, _ in lineages.most_common()])
    lineage_label = {k: lineage_text(k) for k in palette}

    # ---- distances, in tree order ----
    ordered = [by_label[t.name] for t in tips if t.name in by_label] if tips else []
    matrix: List[List[Optional[int]]] = []
    pair_values: List[int] = []
    if ordered and seqs:
        names = [i.label for i in ordered]
        matrix = distance_matrix(seqs, names)
        size = len(names)
        symmetric = all(matrix[a][b] == matrix[b][a] for a in range(size) for b in range(size))
        diagonal = all(matrix[a][a] == 0 for a in range(size))
        if not (symmetric and diagonal):
            problems.append("The distance matrix is not symmetric with a zero diagonal. "
                            "The distance section should not be used.")
        pair_values = [matrix[a][b] for a in range(size) for b in range(a + 1, size) if matrix[a][b] is not None]
        not_aligned = [nm for nm in names if nm not in seqs]
        if not_aligned:
            problems.append(f"{_plural(len(not_aligned), 'isolate')} in the tree but not in the SNP "
                            "alignment: " + ", ".join(not_aligned[:12]))
    alignment_columns = len(next(iter(seqs.values()))) if seqs else 0

    # ---- figures ----
    figures: Dict[str, str] = {}
    if group_by:
        grouped: Dict[str, Dict[str, int]] = {}
        for iso in isolates:
            g = iso.meta.get(group_by, "") or "not given"
            grouped.setdefault(g, {})
            grouped[g][iso.lineage] = grouped[g].get(iso.lineage, 0) + 1
        figures["bars"] = fig.stacked_bars(grouped, palette, lineage_label,
                                           f"Lineage by {group_by}: {n} isolates, seven-gene MLST")
    if tips:
        def block(title, color, cols, getter, has_data, low_getter=None, low_color=None):
            values: Dict[str, Dict[str, str]] = {}
            for c in cols:
                values[c] = {}
                for iso in isolates:
                    if c in getter(iso):
                        values[c][iso.label] = "full"
                    elif low_getter is not None and c in low_getter(iso):
                        values[c][iso.label] = "low"
            return fig.ColumnGroup(title, color, cols, values, low_color,
                                   known={i.label for i in isolates if has_data(i)})

        blocks = [
            block("Resistance", C_RESISTANCE, res_cols, lambda i: i.resistance, lambda i: i.has_amr),
            block("Plasmid replicons", C_REPLICON, rep_cols, lambda i: i.replicons,
                  lambda i: i.has_typing, lambda i: i.replicons_low, C_REPLICON_LOW),
            block("Virulence, variable", C_VIRULENCE, vir_cols, lambda i: i.virulence,
                  lambda i: i.has_typing),
        ]
        tip_text = {i.label: i.label for i in isolates}
        figures["tree"] = fig.tree_matrix(
            root, tips, tip_text, {i.label: i.lineage for i in isolates}, palette, lineage_label,
            [b for b in blocks if b.columns],
            "Phylogeny, lineage and gene content", "substitutions per SNP site")
    if matrix:
        figures["heat"] = fig.heatmap(
            [i.label for i in ordered], matrix, [palette.get(i.lineage, fig.NO_LINEAGE) for i in ordered],
            "Pairwise SNP distances, in tree order", "SNPs (square-root colour scale)")

    # ---- the per-isolate table ----
    def shown(values: List[str], drop: List[str]) -> str:
        kept = [v for v in values if v not in drop]
        return ", ".join(kept) if kept else "–"

    table_header = (["Isolate", "Sample"] + meta_cols
                    + ["Lineage", "Serovar", "Contigs", "N50", "Length", "CheckM2 compl. / contam.",
                       "cgMLST match", "Resistance determinants", "Plasmid replicons",
                       "Low-identity replicons", "Virulence genes", "Notes"])
    order_for_table = ordered + [i for i in isolates if i not in ordered]
    table_rows = []
    for iso in order_for_table:
        table_rows.append(
            [iso.label, iso.sample] + [iso.meta.get(c, "") for c in meta_cols]
            + [lineage_text(iso.lineage), iso.serovar or "—", _num(iso.contigs), _num(iso.n50),
               _num(iso.length),
               (f"{iso.completeness:.2f} / {iso.contamination:.2f}"
                if iso.completeness is not None and iso.contamination is not None else "—"),
               iso.cgmlst_match or "—",
               shown(iso.resistance, res_all) if iso.has_amr else "—",
               (", ".join(iso.replicons) or "–") if iso.has_typing else "—",
               ", ".join(iso.replicons_low),
               str(len(iso.virulence)) if iso.has_typing else "—",
               "; ".join(iso.notes)])

    # ---- the summary, computed from the isolates ----
    organisms = Counter(i.organism or "organism unknown" for i in isolates)
    serovars = Counter(i.serovar for i in typed if i.serovar)
    summary: List[str] = []
    summary.append(f"{_plural(n, 'isolate')}: {counts_text(organisms)}.")
    if serovars:
        agree = sum(1 for i in typed if i.serovar_agreement == "agree")
        summary.append(f"Serovar: {counts_text(serovars)}. SISTR and SeqSero2 agree on {agree} of "
                       f"{len(typed)} typed isolates.")
    summary.append(f"Seven-gene MLST: {counts_text(lineages, lineage_text)}.")
    if any(i.has_amr for i in isolates):
        with_amr = sum(1 for i in isolates if i.has_amr)
        summary.append(f"{len(with_acquired)} of {with_amr} isolates carry at least one acquired "
                       "resistance gene"
                       + (f" beyond those found in every isolate ({', '.join(acquired_all)})" if acquired_all else "")
                       + ".")
        points = Counter(p for i in isolates for p in i.point)
        if points:
            summary.append("Resistance point mutations: "
                           + ", ".join(f"{k} in {v}" for k, v in points.most_common(6)) + ".")
    if pair_values:
        summary.append(f"Across {len(pair_values):,} pairs of isolates the SNP distance runs from "
                       f"{min(pair_values):,} to {max(pair_values):,}, median {statistics.median(pair_values):,.0f}.")
    noted = sum(1 for i in isolates if i.notes)
    summary.append(f"{_plural(noted, 'isolate')} {'carries' if noted == 1 else 'carry'} a note that "
                   "changes how a call should be read.")

    # ---- HTML ----
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = title or f"{proj.get('name') or project.name}: cohort report"
    parts: List[str] = [f"<h1>{_e(title)}</h1>",
                        f'<p class="sub">{_e(_plural(n, "isolate"))} · project <b>{_e(project.name)}</b> · '
                        f'generated {_e(stamp)}</p>']
    headline = [_plural(n, "isolate")]
    if serovars:
        headline.append("serovar " + ", ".join(k for k, _ in serovars.most_common(3)))
    headline.append(_plural(len(lineages), "MLST lineage"))
    if any(i.has_amr for i in isolates):
        headline.append(f"{len(with_acquired)} with acquired resistance genes")
    if problems:
        headline.append(_plural(len(problems), "check") + " to read")
    parts.append('<div class="banner">' + _e(" · ".join(headline)) + "</div>")
    parts.append("<h2>Summary</h2><ul>" + "".join(f"<li>{_e(s)}</li>" for s in summary) + "</ul>")
    if problems:
        parts.append('<div class="banner danger">Check before using this report</div><ul>'
                     + "".join(f"<li>{_e(p)}</li>" for p in problems) + "</ul>")
    for line in info:
        parts.append(f'<p class="muted">{_e(line)}</p>')
    noted_rows = [(i.label, "; ".join(i.notes)) for i in order_for_table if i.notes]
    if noted_rows:
        parts.append("<h2>Notes on individual isolates</h2>")
        parts.append('<table class="kv">' + "".join(
            f"<tr><th>{_e(a)}</th><td>{_e(b)}</td></tr>" for a, b in noted_rows) + "</table>")

    parts.append("<h2>Sequence quality</h2>")
    qrows = [_range_row("Assembly length (bp)", [i.length for i in isolates]),
             _range_row("Contigs", [i.contigs for i in isolates]),
             _range_row("N50 (bp)", [i.n50 for i in isolates]),
             _range_row("CheckM2 completeness (%)", [i.completeness for i in isolates], 2),
             _range_row("CheckM2 contamination (%)", [i.contamination for i in isolates], 2)]
    parts.append('<table class="grid"><thead><tr><th>Metric</th><th>Min</th><th>Median</th><th>Max</th>'
                 "<th>Isolates</th></tr></thead><tbody>"
                 + "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in qrows)
                 + "</tbody></table>")

    parts.append("<h2>Serovar and lineage</h2>")
    if serovars:
        parts.append(f"<p>Serovar: {_e(counts_text(serovars))}. Serovar is called by SISTR and by "
                     "SeqSero2. SISTR uses cgMLST with the antigen genes, so its serovar and its "
                     "cgMLST result are one method, and SeqSero2 is the independent second.</p>")
    parts.append(f"<p>Seven-gene MLST: {_e(counts_text(lineages, lineage_text))}.</p>")
    if figures.get("bars"):
        parts.append(f'<div class="scroll">{figures["bars"]}</div>')

    if figures.get("tree"):
        parts.append('<h2 class="newpage">Phylogeny and gene content</h2>')
        parts.append(f'<div class="scroll figure">{figures["tree"]}</div>')
        rooted = ("rooted with the reference as outgroup, which is not drawn" if rooting == "outgroup"
                  else "rooted at the midpoint of the longest tip-to-tip path")
        parts.append(
            f'<p class="small">Tree from vSNP Step 2 run {_e(step2.run_id)}, group {_e(step2.group)}, '
            f'reference {_e(reference_name)}, {rooted}. '
            + f"{alignment_columns:,} SNP positions. Columns show what varies: determinants and "
              "virulence genes found in every isolate are left out and listed below. A paler "
              "replicon square is a low-identity match. An isolate with no squares in a block has "
              "no result for it.</p>")
        if res_all:
            parts.append(f'<p class="small">Resistance determinants in every isolate: {_e(", ".join(res_all))}.</p>')
        if vir_all:
            parts.append(f'<p class="small">{len(vir_all)} virulence genes are in every typed isolate '
                         "and are not drawn.</p>")
    if figures.get("heat"):
        parts.append("<h2>Pairwise distances</h2>")
        parts.append(f"<p>Across {len(pair_values):,} pairs: minimum {min(pair_values):,}, median "
                     f"{statistics.median(pair_values):,.0f}, maximum {max(pair_values):,} SNPs.</p>")
        parts.append(f'<div class="scroll figure">{figures["heat"]}</div>')
        parts.append('<p class="small">These are reference-mapped SNPs from the vSNP Step 2 alignment, '
                     "after its position filters. A position where either isolate has no call or a "
                     "mixed call is not counted for that pair. They are not comparable with distances "
                     "from a core-gene alignment.</p>")

    if any(i.has_amr for i in isolates):
        parts.append("<h2>Antimicrobial resistance</h2>")
        det = Counter(x for i in isolates for x in i.resistance if x not in res_all)
        if det:
            keys = [k for k, _ in lineages.most_common()]
            head = "".join(f"<th>{_e(lineage_text(k))} (n={lineages[k]})</th>" for k in keys)
            body = []
            for name, total in sorted(det.items(), key=lambda kv: (-kv[1], kv[0])):
                cells = "".join(
                    f"<td>{sum(1 for i in isolates if i.lineage == k and name in i.resistance) or ''}</td>"
                    for k in keys)
                body.append(f"<tr><td>{_e(name)}</td><td>{total}</td>{cells}</tr>")
            parts.append('<div class="scroll"><table class="grid"><thead><tr><th>Determinant</th>'
                         f"<th>Isolates</th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>")
        else:
            parts.append('<p class="muted">No resistance determinant varies between isolates.</p>')
        parts.append('<p class="small">Results are genotypic. A gene or mutation that is present does '
                     "not by itself establish resistance, and no susceptibility testing is reported "
                     "here.</p>")

    if typed:
        parts.append("<h2>Plasmid replicons and virulence</h2>")
        rep = Counter(x for i in typed for x in i.replicons)
        low = Counter(x for i in typed for x in i.replicons_low)
        parts.append("<p>Replicons: " + (_e(", ".join(f"{k} in {v}" for k, v in rep.most_common())) or "none")
                     + ("; at low identity: " + _e(", ".join(f"{k} in {v}" for k, v in low.most_common())) if low else "")
                     + f", of {len(typed)} typed isolates.</p>")
        vcount = Counter(x for i in typed for x in i.virulence)
        if vcount:
            parts.append(f"<p>{len(vcount)} VFDB virulence genes detected, {len(vir_all)} in every typed "
                         "isolate. Variable: "
                         + (_e(", ".join(f"{k} in {vcount[k]}" for k in vir_cols)) or "none") + ".</p>")

    def html_table(columns: List[int]) -> str:
        thead = "".join(f"<th>{_e(table_header[c])}</th>" for c in columns)
        body = "".join("<tr>" + "".join(f"<td>{_e(r[c]) if r[c] != '' else ''}</td>" for c in columns) + "</tr>"
                       for r in table_rows)
        return (f'<div class="scroll"><table class="grid wide"><thead><tr>{thead}</tr></thead>'
                f"<tbody>{body}</tbody></table></div>")

    # Two tables, so each fits a page: who the isolates are, then what was found.
    n_meta = 2 + len(meta_cols)
    parts.append("<h2>Per isolate results</h2>")
    parts.append(html_table([0] + list(range(n_meta, len(table_header)))))
    if meta_cols:
        parts.append("<h2>Isolates and metadata</h2>")
        parts.append(html_table(list(range(n_meta))))
        parts.append(f'<p class="small">Metadata as given in {_e(Path(metadata).name)}. Rows are in '
                     "tree order where the isolate is in the tree.</p>")
    if res_all:
        parts.append(f'<p class="small">The resistance column leaves out what is in every isolate: '
                     f'{_e(", ".join(res_all))}.</p>')

    parts.append("<h2>Methods and versions</h2>")
    version_rows: Dict[str, Counter] = {}
    for iso in isolates:
        for k, v in iso.versions.items():
            version_rows.setdefault(k, Counter())[v] += 1
    mrows = [("Per-isolate pipeline", "reads to assembly (shovill or SPAdes), MLST, AMRFinderPlus, then "
              "SISTR, SeqSero2, abricate (PlasmidFinder, VFDB) and CheckM2 on the same assembly")]
    for k in sorted(version_rows):
        c = version_rows[k]
        mrows.append((k, "; ".join(f"{v} ({m} isolates)" if len(c) > 1 else v for v, m in c.most_common())))
    if step2 is not None:
        mrows.append(("Phylogeny and distances", f"vSNP Step 2 run {step2.run_id}, group {step2.group}, "
                      f"reference {reference_name}; tree file {step2.tree_path.name}"))
    if metadata is not None:
        mrows.append(("Metadata sheet", f"{Path(metadata).name}; sample column {sample_col or '—'}, "
                      f"display names from {label_column or 'the sample names'}, grouped by {group_by or '—'}"))
    parts.append('<table class="kv">' + "".join(f"<tr><th>{_e(a)}</th><td>{_e(b)}</td></tr>" for a, b in mrows) + "</table>")
    parts.append('<p class="small">Every figure in this report is read from the per-isolate result '
                 "folders and the Step 2 run named above. cohort_checks.json beside this file lists the "
                 "consistency checks and what they found.</p>")

    # Landscape pages, figures scaled to the page, and smaller type in the two
    # wide tables, so nothing runs off the edge of the PDF.
    css = (_CSS + "\n@page{size:A4 landscape;margin:12mm}\n"
           "ul{margin:4px 0 8px 18px;padding:0} li{margin:2px 0}\n"
           "svg{max-width:100%;height:auto}\n"
           # In print a figure is kept whole and scaled to fit one page, and the
           # tree starts its own page so its heading stays with it.
           "@media print{svg{max-height:158mm;width:auto} .figure{break-inside:avoid} "
           "h2.newpage{break-before:page}}\n"
           "table.wide{font-size:9.5px} table.wide th,table.wide td{padding:3px 5px}\n"
           "h2{break-after:avoid} tr{break-inside:avoid}\n")
    doc = (f'<!doctype html><html><head><meta charset="utf-8"><title>{_e(title)}</title>'
           f"<style>{css}</style></head><body>{''.join(parts)}</body></html>")

    # ---- write ----
    outdir.mkdir(parents=True, exist_ok=True)
    html_path = outdir / "cohort_report.html"
    html_path.write_text(doc, encoding="utf-8")
    log(f"wrote {html_path}")
    with open(outdir / "cohort_table.tsv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(table_header)
        writer.writerows(table_rows)
    try:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Isolates"
        ws.append(table_header)
        for r in table_rows:
            ws.append(r)
        ws.freeze_panes = "B2"
        wb.save(outdir / "cohort_table.xlsx")
    except Exception as exc:  # noqa: BLE001: the TSV is always written
        log(f"NOTE: cohort_table.xlsx not written ({exc}); the .tsv holds the same table.")
    pdf_ok = False
    if make_pdf:
        pdf_ok = html_to_pdf(html_path, outdir / "cohort_report.pdf")
        log("wrote cohort_report.pdf" if pdf_ok else "NOTE: WeasyPrint is not available; no PDF written.")

    result = {
        "project": str(project), "generated": stamp, "isolates": n, "typed": len(typed),
        "in_tree": sum(1 for i in isolates if i.in_tree), "step2_run": step2.run_id if step2 else None,
        "metadata": str(metadata) if metadata else None, "label_column": label_column,
        "group_by": group_by, "rooting": rooting, "alignment_columns": alignment_columns,
        "pairs": len(pair_values), "problems": problems, "info": info, "summary": summary,
        "with_acquired_resistance": len(with_acquired), "pdf": pdf_ok,
        "files": sorted(p.name for p in outdir.iterdir() if p.is_file()),
    }
    (outdir / "cohort_checks.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the cohort report for a project.")
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--metadata", type=Path, default=None,
                    help="metadata sheet (.tsv, .csv, .xlsx); default: sample_crosswalk.tsv or "
                         "metadata.* in the project or its download folder")
    ap.add_argument("--group-by", default=None, help="metadata column for the lineage chart")
    ap.add_argument("--label-column", default=None,
                    help="metadata column holding display names; default: the column matching the tree's tips")
    ap.add_argument("--step2-run", default=None, help="Step 2 run folder name; default: the newest with a tree")
    ap.add_argument("--outgroup", default=VSNP_REFERENCE_TIP,
                    help="tree tip to root on; default: vSNP's reference tip, else midpoint rooting")
    ap.add_argument("--title", default=None)
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args(argv)
    result = build(args.project, args.metadata, args.group_by, args.label_column, args.step2_run,
                   args.outgroup, args.title, make_pdf=not args.no_pdf)
    for line in result["summary"]:
        print("  " + line)
    for line in result["problems"]:
        print("  CHECK: " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
