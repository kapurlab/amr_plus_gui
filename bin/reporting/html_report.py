"""
AMRFinderPlus GUI — HTML report (+ optional WeasyPrint PDF).

Builds a single self-contained `report.html` — a comprehensive, professional
gathering of the run's results: input read QC, assembly QC, organism
identification (Kraken + MLST scheme/ST/alleles), the full AMRFinderPlus table,
an AMR summary, and methods/provenance. All CSS is inline so the file opens
anywhere. `html_to_pdf()` renders the same HTML to `report.pdf` via WeasyPrint
when it is installed; callers fall back to the reportlab PDF otherwise.

The palette matches the suite's other reports (teal/terra/ink) for a unified
look across tools.
"""

from __future__ import annotations

import csv
import html
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TEAL = "#4C8C8A"
TERRA = "#C88F7A"
INK = "#1F2A2E"
MUTED = "#6E7B82"
BORDER = "#E3DED6"
DANGER = "#C46A6A"
SUCCESS = "#6BAA75"
WARN = "#D8B26E"

_VERDICT_COLOR = {"PASS": SUCCESS, "REVIEW": WARN, "FAIL": DANGER}


def _e(v: Any) -> str:
    return html.escape("—" if v in (None, "") else str(v))


def load_mlst(outdir: Path) -> Dict[str, Any]:
    """Best-effort MLST detail: scheme, ST and per-locus alleles.

    Prefers mlst.tsv (`FILE  SCHEME  ST  locus(allele) …`); falls back to
    mlst_result.json's scheme/st. Returns {} when no MLST ran."""
    out: Dict[str, Any] = {}
    tsv = outdir / "mlst.tsv"
    if tsv.is_file():
        try:
            line = tsv.read_text(encoding="utf-8", errors="replace").splitlines()
            if line:
                cols = line[0].split("\t")
                if len(cols) >= 3:
                    out["scheme"] = cols[1]
                    out["st"] = cols[2]
                    out["alleles"] = [c for c in cols[3:] if c.strip()]
        except OSError:
            pass
    if not out:
        import json
        try:
            j = json.loads((outdir / "mlst_result.json").read_text(encoding="utf-8"))
            out["scheme"] = j.get("scheme") or j.get("organism_token")
            out["st"] = j.get("st") or j.get("ST") or j.get("sequence_type")
            if j.get("alleles"):
                out["alleles"] = j["alleles"]
        except (OSError, ValueError):
            pass
    return out


def _fmt_int(v: Any) -> str:
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return _e(v)


def _fmt_pct(v: Any, dp: int = 2) -> str:
    try:
        return f"{float(v):.{dp}f}%"
    except (TypeError, ValueError):
        return _e(v)


def _kv_rows(rows: List[Tuple[str, Any]]) -> str:
    cells = "".join(
        f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in rows
    )
    return f'<table class="kv">{cells}</table>'


def _amr_table(rows: List[Dict[str, str]]) -> str:
    if not rows:
        return '<p class="muted">No resistance / virulence elements reported.</p>'
    head = ("Element", "Name", "Type", "Subtype", "Class", "Subclass",
            "Method", "% Cov", "% Ident", "Contig")
    keys = ("element", "name", "type", "subtype", "class", "subclass",
            "method", "coverage", "identity", "contig")
    thead = "".join(f"<th>{_e(h)}</th>" for h in head)
    body = []
    for r in rows:
        tds = "".join(f"<td>{_e(r.get(k))}</td>" for k in keys)
        body.append(f"<tr>{tds}</tr>")
    return (f'<table class="grid"><thead><tr>{thead}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def _hits_table(hits: List[Dict[str, Any]]) -> str:
    head = ("Gene", "% Ident", "% Cov", "Contig", "Accession", "Product", "Note")
    thead = "".join(f"<th>{_e(h)}</th>" for h in head)
    body = []
    # Confident hits first, then by identity, so the sure calls lead the table.
    ordered = sorted(hits, key=lambda h: (bool(h.get("low_identity")), -(h.get("identity") or 0)))
    for h in ordered:
        cells = (h.get("gene"), _fmt_pct(h.get("identity")), _fmt_pct(h.get("coverage")),
                 h.get("contig"), h.get("accession"), h.get("product"),
                 (f"low identity, weak second match beside {h.get('beside')}" if h.get("beside")
                  else "low identity" if h.get("low_identity") else ""))
        body.append("<tr>" + "".join(f"<td>{_e(c) if c != '' else ''}</td>" for c in cells) + "</tr>")
    return (f'<table class="grid"><thead><tr>{thead}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def _typing_sections(typing: Dict[str, Any]) -> List[str]:
    """Serovar, plasmid replicon and virulence sections for the steps that ran."""
    from . import _AGREEMENT_TEXT, cgmlst_match_text, gene_names, replicon_confident_cutoff

    parts: List[str] = []
    serovar = typing.get("serovar") or {}
    if serovar:
        sistr = serovar.get("sistr") or {}
        seqsero2 = serovar.get("seqsero2") or {}
        agreement = serovar.get("agreement")
        parts.append("<h2>Serovar and lineage</h2>")
        if agreement == "disagree":
            parts.append('<div class="banner danger">SISTR and SeqSero2 give different serovars. '
                         'Review both calls before reporting a serovar.</div>')
        parts.append(_kv_rows([
            ("Serovar (SISTR)", sistr.get("serovar")),
            ("Serovar (SeqSero2)", seqsero2.get("serovar")),
            ("Agreement", _AGREEMENT_TEXT.get(agreement, agreement)),
            ("Antigenic profile (SeqSero2)", seqsero2.get("antigenic_profile")),
            ("Antigenic formula (SISTR)", sistr.get("antigenic_formula")),
            ("Serogroup (SISTR)", sistr.get("serogroup")),
            ("cgMLST alleles matching the nearest genome (SISTR)", cgmlst_match_text(sistr) if sistr else None),
            ("Nearest genome by cgMLST (SISTR)", sistr.get("cgmlst_genome_match")),
            ("SISTR QC", sistr.get("qc_status")),
            ("SeqSero2 note", seqsero2.get("note")),
        ]))
        parts.append('<p class="small">SISTR uses cgMLST together with the antigen genes, so its '
                     'serovar call and its cgMLST result are not independent of each other. '
                     'SeqSero2 is the independent second method.</p>')
    if "plasmid_replicons" in typing:
        hits = typing.get("plasmid_replicons") or []
        parts.append("<h2>Plasmid replicons</h2>")
        if hits:
            parts.append(f'<div class="scroll">{_hits_table(hits)}</div>')
            if any(h.get("low_identity") for h in hits):
                cut = replicon_confident_cutoff(typing)
                parts.append(f'<p class="small">Hits marked low identity match a reference replicon '
                             f'at under {cut}%. One can be a real but divergent replicon, such as a '
                             f'small Col plasmid, so check its contig before reporting it. A hit '
                             f'noted as a weak second match lies within 1 kb of a confident hit of '
                             f'the same family and is left out of the summary.</p>')
        else:
            parts.append('<p class="muted">No plasmid replicons detected.</p>')
    if "virulence_genes" in typing:
        hits = typing.get("virulence_genes") or []
        names = gene_names(hits)
        parts.append("<h2>Virulence genes (VFDB)</h2>")
        if names:
            chips = "".join(f"<span>{_e(n)}</span>" for n in names)
            parts.append(f'<p>{len(names)} gene(s) detected. Identity, coverage and position for '
                         f'each hit are in <code>abricate_vfdb.tsv</code>.</p>'
                         f'<div class="chips">{chips}</div>')
        else:
            parts.append('<p class="muted">No virulence genes detected.</p>')
    if parts:
        parts.append('<p class="small">Replicon and virulence results are sequence matches to '
                     'reference databases. A match shows the gene is present in the assembly; it '
                     'does not show that it is expressed.</p>')
    return parts


_CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
       color: %(ink)s; margin: 0; padding: 24px; font-size: 13px; line-height: 1.45; }
h1 { font-size: 22px; margin: 0 0 2px; }
h2 { color: %(teal)s; font-size: 15px; margin: 20px 0 6px; border-bottom: 2px solid %(border)s; padding-bottom: 3px; }
.sub { color: %(muted)s; margin: 0 0 12px; }
.banner { background: %(teal)s; color: #fff; padding: 10px 14px; border-radius: 6px; font-weight: 600; margin: 6px 0; }
.banner.danger { background: %(danger)s; }
.pill { display: inline-block; padding: 1px 8px; border-radius: 10px; color: #fff; font-size: 11px; font-weight: 700; }
table { border-collapse: collapse; width: 100%%; margin: 4px 0 8px; }
table.kv th { text-align: left; width: 34%%; background: #FBFAF8; }
table.kv th, table.kv td { border-bottom: 1px solid %(border)s; padding: 4px 8px; vertical-align: top; }
table.grid { font-size: 11.5px; }
table.grid th { background: %(teal)s; color: #fff; text-align: left; padding: 5px 6px; }
table.grid td { border-bottom: 1px solid %(border)s; padding: 4px 6px; }
table.grid tbody tr:nth-child(even) { background: #F6F5F2; }
.muted { color: %(muted)s; }
.small { color: %(muted)s; font-size: 11px; }
.scroll { overflow-x: auto; }
.chips span { display: inline-block; background: #EEF3F2; border: 1px solid %(border)s;
              border-radius: 10px; padding: 1px 9px; margin: 2px 3px 0 0; font-size: 11px; }
""" % {"ink": INK, "teal": TEAL, "muted": MUTED, "border": BORDER, "danger": DANGER}


def write_html(ctx: Dict[str, Any], path: Path, outdir: Path) -> None:
    sample = ctx["sample"]
    fq = ctx.get("fastq_qc") or {}
    qc = ctx.get("qc") or {}
    det = ctx.get("detection") or {}
    man = ctx.get("manifest") or {}
    opts = man.get("options", {}) or {}
    vers = man.get("versions", {}) or {}
    vers_extra = man.get("versions_extra", {}) or {}
    rows = ctx.get("amr_rows") or []
    summ = ctx.get("amr_summary") or {}
    mlst = ctx.get("mlst") or load_mlst(outdir)
    typing = ctx.get("typing") or {}
    checkm2 = typing.get("checkm2") or {}
    m = (qc or {}).get("metrics", {}) or {}

    verdict = (qc.get("verdict") or "—").upper()
    vcolor = _VERDICT_COLOR.get(verdict, MUTED)
    contamination = det.get("contamination_flag")

    parts: List[str] = []
    parts.append(f"<h1>AMRFinderPlus resistance report</h1>")
    parts.append(f'<p class="sub">Sample <b>{_e(sample)}</b> · {_e(ctx.get("date"))} · '
                 f'{_e(man.get("tool", "AMRFinderPlus"))}</p>')

    # Headline banner
    org = det.get("dominant_species") or det.get("organism_token") or "organism unknown"
    st = mlst.get("st") or det.get("mlst_st") or "—"
    scheme = mlst.get("scheme") or det.get("mlst_scheme") or "—"
    # The serovar joins the headline only when both tools gave the same call.
    serovar_call = (typing.get("serovar") or {})
    serovar_txt = (f' · serovar {_e(serovar_call.get("serovar"))}'
                   if serovar_call.get("agreement") == "agree" and serovar_call.get("serovar") else "")
    parts.append(
        f'<div class="banner">Organism: {_e(org)}{serovar_txt} · MLST {_e(scheme)} ST {_e(st)} · '
        f'{_fmt_int(summ.get("total"))} element(s) · '
        f'assembly QC <span class="pill" style="background:{vcolor}">{_e(verdict)}</span></div>'
    )
    if contamination:
        parts.append('<div class="banner danger">⚠ Possible contamination flagged '
                     '(a second species is present at notable abundance) — interpret with care.</div>')

    # Organism identification (Kraken + MLST)
    parts.append("<h2>Organism identification</h2>")
    parts.append(_kv_rows([
        ("Dominant species (Kraken)", det.get("dominant_species")),
        ("Dominant species (%)", _fmt_pct(det.get("dominant_pct"))),
        ("Runner-up species", det.get("runner_up_species")),
        ("Runner-up species (%)", _fmt_pct(det.get("runner_up_pct"))),
        ("Contamination flagged", "yes" if contamination else "no"),
        ("MLST scheme", scheme),
        ("MLST sequence type (ST)", st),
        ("Resolved --organism", det.get("organism_token") or "(none)"),
        ("Organism source", det.get("organism_source")),
        ("Organism confidence", det.get("confidence")),
    ]))
    alleles = mlst.get("alleles") or []
    if alleles:
        chips = "".join(f"<span>{_e(a)}</span>" for a in alleles)
        parts.append(f'<div class="chips"><b class="small">MLST alleles:</b> {chips}</div>')

    # Input read QC
    files = (fq or {}).get("files", {})
    if files:
        parts.append("<h2>Input read quality</h2>")
        head = ("File", "Reads", "Bases (bp)", "Avg len", "GC%", "Q20%", "Q30%", "Avg Q")
        thr = "".join(f"<th>{h}</th>" for h in head)
        body = []
        for tag in ("R1", "R2"):
            s = files.get(tag)
            if not s:
                continue
            body.append("<tr>" + "".join(f"<td>{c}</td>" for c in [
                tag, _fmt_int(s.get("num_seqs")), _fmt_int(s.get("sum_len")),
                _fmt_int(s.get("avg_len")), _fmt_pct(s.get("gc_pct")),
                _fmt_pct(s.get("q20_pct")), _fmt_pct(s.get("q30_pct")),
                _e(s.get("avg_qual")),
            ]) + "</tr>")
        parts.append(f'<div class="scroll"><table class="grid"><thead><tr>{thr}</tr></thead>'
                     f'<tbody>{"".join(body)}</tbody></table></div>')

    # Assembly QC
    parts.append("<h2>Assembly quality</h2>")
    parts.append(_kv_rows([
        ("Contigs", _fmt_int(m.get("num_seqs"))),
        ("Assembly length (bp)", _fmt_int(m.get("total_length"))),
        ("N50", _fmt_int(m.get("n50"))),
        ("Assembly GC (%)", _fmt_pct(m.get("gc_pct"))),
        ("Largest contig (bp)", _fmt_int(m.get("max_len"))),
        ("Expected genome size (bp)", _fmt_int(qc.get("expected_genome_size")) if qc.get("expected_genome_size") else "—"),
        ("Assembly QC verdict", verdict),
    ] + ([
        ("CheckM2 completeness (%)", _fmt_pct(checkm2.get("completeness"))),
        ("CheckM2 contamination (%)", _fmt_pct(checkm2.get("contamination"))),
    ] if checkm2 else [])))

    # Serovar, plasmid replicons, virulence (only the steps that ran)
    parts.extend(_typing_sections(typing))

    # AMR summary
    parts.append("<h2>Resistance summary</h2>")
    parts.append(_kv_rows([
        ("AMRFinderPlus DB version", vers.get("amrfinder_db") or det.get("db_version")),
        ("--plus used", "yes" if opts.get("plus") else "no"),
        ("Total elements reported", _fmt_int(summ.get("total"))),
        ("AMR genes", _fmt_int(summ.get("amr_genes"))),
        ("Point mutations", _fmt_int(summ.get("point_mutations"))),
        ("Stress/biocide/metal genes", _fmt_int(summ.get("stress"))),
        ("Virulence genes", _fmt_int(summ.get("virulence"))),
    ]))
    classes = summ.get("classes") or []
    if classes:
        chips = "".join(f"<span>{_e(c)}</span>" for c in classes)
        parts.append(f'<div class="chips"><b class="small">Drug classes:</b> {chips}</div>')

    # Full AMR table
    parts.append("<h2>AMRFinderPlus results</h2>")
    parts.append(f'<div class="scroll">{_amr_table(rows)}</div>')

    # Methods / provenance
    parts.append("<h2>Methods &amp; provenance</h2>")
    assembler = "shovill" if vers_extra.get("shovill") else ("SPAdes" if vers_extra.get("spades") else "—")
    parts.append(_kv_rows([
        ("AMRFinderPlus version", vers.get("amrfinder")),
        ("ident_min / coverage_min", f'{opts.get("ident_min", "—")} / {opts.get("coverage_min", "—")}'),
        ("kraken2 version", vers_extra.get("kraken2")),
        ("mlst version", vers_extra.get("mlst")),
        ("Assembler", assembler),
    ] + _typing_method_rows(typing) + [
        ("Standards referenced",
         ", ".join(r.get("standard") for r in (man.get("iso_references") or []) if r.get("standard")) or "—"),
    ]))
    parts.append('<p class="small">Disclaimer: AMRFinderPlus reports resistance <i>genotype</i> '
                 '(genes and point mutations present); it does not measure expressed phenotypic '
                 'resistance. Partial hits (coverage &lt; 100%) and INTERNAL_STOP/PARTIAL calls warrant '
                 'review. Clinical interpretation combines this with susceptibility testing and context.</p>')

    doc = (f'<!doctype html><html><head><meta charset="utf-8">'
           f'<title>AMR report — {_e(sample)}</title><style>{_CSS}</style></head>'
           f'<body>{"".join(parts)}</body></html>')
    Path(path).write_text(doc, encoding="utf-8")


def _typing_method_rows(typing: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """Tool and database versions for the typing steps that ran."""
    versions = (typing or {}).get("versions") or {}
    thresholds = (typing or {}).get("thresholds") or {}
    rows: List[Tuple[str, Any]] = []
    for label, key in (("SISTR version", "sistr"), ("SeqSero2 version", "seqsero2"),
                       ("CheckM2 version", "checkm2"), ("abricate version", "abricate")):
        if versions.get(key):
            rows.append((label, versions[key]))
    databases = versions.get("abricate_databases") or {}
    if databases:
        rows.append(("abricate databases",
                     ", ".join(f"{name} ({date})" for name, date in sorted(databases.items()))))
    for database, cut in sorted(thresholds.items()):
        text = f'identity ≥ {cut.get("min_identity")}%, coverage ≥ {cut.get("min_coverage")}%'
        if cut.get("confident_identity"):
            text += f'; marked low identity below {cut.get("confident_identity")}%'
        rows.append((f"abricate {database} thresholds", text))
    return rows


def html_to_pdf(html_path: Path, pdf_path: Path) -> bool:
    """Render report.html to report.pdf via WeasyPrint. Returns True on success,
    False if WeasyPrint (or its native deps) is unavailable — callers keep the
    reportlab PDF in that case."""
    try:
        from weasyprint import HTML
    except Exception:  # noqa: BLE001 — import may fail on missing native libs
        return False
    try:
        HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        return True
    except Exception:  # noqa: BLE001
        return False
