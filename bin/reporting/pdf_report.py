"""
AMRFinderPlus PDF report (reportlab + matplotlib).

Pure-Python PDF — no headless browser — so it renders reliably on any OOD host.
matplotlib figures are best-effort: if matplotlib is unavailable the report is
still produced, just without the charts.

Layout: title + organism banner, a plain-language analysis summary, input-file
quality, organism identification, assembly QC, the AMR findings table (with a
by-class figure), and a methods/provenance page with the standards referenced
and a genotype-vs-phenotype disclaimer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Theme (matches the GUI's App.css palette)
TEAL = colors.HexColor("#4C8C8A")
TERRA = colors.HexColor("#C88F7A")
INK = colors.HexColor("#1F2A2E")
MUTED = colors.HexColor("#6E7B82")
PANEL = colors.HexColor("#F1EDE6")
BORDER = colors.HexColor("#E3DED6")
DANGER = colors.HexColor("#C46A6A")
SUCCESS = colors.HexColor("#6BAA75")
WARN = colors.HexColor("#D8B26E")


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("H1", parent=ss["Title"], textColor=INK, fontSize=20, spaceAfter=2))
    ss.add(ParagraphStyle("Sub", parent=ss["Normal"], textColor=MUTED, fontSize=10, spaceAfter=10))
    ss.add(ParagraphStyle("H2", parent=ss["Heading2"], textColor=TEAL, fontSize=13,
                          spaceBefore=12, spaceAfter=4))
    ss.add(ParagraphStyle("Body", parent=ss["Normal"], textColor=INK, fontSize=9.5,
                          leading=13, alignment=TA_LEFT, spaceAfter=4))
    ss.add(ParagraphStyle("Small", parent=ss["Normal"], textColor=MUTED, fontSize=8, leading=10))
    ss.add(ParagraphStyle("Cell", parent=ss["Normal"], textColor=INK, fontSize=8.5, leading=11))
    return ss


def _kv_table(rows: List[Tuple[str, str]], ss, col0=2.4 * inch, col1=4.4 * inch) -> Table:
    data = [[Paragraph(f"<b>{k}</b>", ss["Cell"]), Paragraph(str(v), ss["Cell"])] for k, v in rows]
    t = Table(data, colWidths=[col0, col1])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDER),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#FBFAF8")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _banner(text: str, fill, ss) -> Table:
    t = Table([[Paragraph(f'<font color="white"><b>{text}</b></font>', ss["Body"])]],
              colWidths=[6.9 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), fill),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return t


# ---------------------------------------------------------------------------
# Figures (best-effort)
# ---------------------------------------------------------------------------
def _bar_by_class(summary: Dict[str, Any], outpath: Path) -> bool:
    by_class = {k: v for k, v in (summary.get("by_class") or {}).items() if k and k != "—"}
    if not by_class:
        return False
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        labels = list(by_class.keys())
        vals = [by_class[k] for k in labels]
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        labels = [labels[i] for i in order]
        vals = [vals[i] for i in order]
        fig, ax = plt.subplots(figsize=(6.6, max(1.4, 0.34 * len(labels) + 0.6)))
        ax.barh(labels, vals, color="#4C8C8A")
        ax.set_xlabel("elements reported")
        ax.set_title("AMR/Plus elements by drug class", color="#1F2A2E", fontsize=11)
        for i, v in enumerate(vals):
            ax.text(v, i, f" {v}", va="center", fontsize=8, color="#1F2A2E")
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(outpath, dpi=150)
        plt.close(fig)
        return True
    except Exception:
        return False


def _scatter_cov_ident(rows: List[Dict[str, str]], outpath: Path) -> bool:
    pts = []
    for r in rows:
        try:
            pts.append((float(r["coverage"]), float(r["identity"])))
        except (KeyError, ValueError, TypeError):
            continue
    if not pts:
        return False
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        xs, ys = zip(*pts)
        fig, ax = plt.subplots(figsize=(3.2, 2.6))
        ax.scatter(xs, ys, color="#C88F7A", edgecolor="#1F2A2E", linewidth=0.4, s=28, alpha=0.85)
        ax.set_xlabel("% coverage", fontsize=8)
        ax.set_ylabel("% identity", fontsize=8)
        ax.set_title("Hit coverage vs identity", fontsize=9, color="#1F2A2E")
        ax.tick_params(labelsize=7)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(outpath, dpi=150)
        plt.close(fig)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def write_pdf(ctx: Dict[str, Any], path: Path, outdir: Path) -> None:
    ss = _styles()
    sample = ctx["sample"]
    det = ctx["detection"]
    qc = ctx["qc"]
    fq = ctx.get("fastq_qc") or {}
    man = ctx["manifest"]
    rows = ctx["amr_rows"]
    summ = ctx["amr_summary"]
    opts = man.get("options", {}) or {}
    vers = man.get("versions", {}) or {}
    vers_extra = man.get("versions_extra", {}) or {}

    assets = outdir / "_report_assets"
    assets.mkdir(exist_ok=True)

    story: List[Any] = []
    story.append(Paragraph("AMRFinderPlus Antimicrobial Resistance Report", ss["H1"]))
    org = det.get("organism_token") or "not assigned"
    story.append(Paragraph(
        f"Sample <b>{sample}</b> &nbsp;·&nbsp; {ctx['date']} &nbsp;·&nbsp; "
        f"AMRFinderPlus {vers.get('amrfinder', '?')} / DB {vers.get('amrfinder_db', det.get('db_version','?'))}",
        ss["Sub"]))

    # Organism banner + contamination flag
    conf = (det.get("confidence") or "none").lower()
    bfill = {"high": SUCCESS, "medium": WARN, "low": WARN}.get(conf, MUTED)
    story.append(_banner(
        f"Organism for AMR analysis: {org}  (source: {det.get('organism_source','?')}, "
        f"confidence: {conf})", bfill, ss))
    if det.get("contamination_flag"):
        story.append(Spacer(1, 4))
        story.append(_banner(
            "⚠ Possible contamination / mixed isolate — AMRFinderPlus was run WITHOUT a "
            "species (-O), so point-mutation screening and species curation were not applied.",
            DANGER, ss))
    story.append(Spacer(1, 8))

    # --- Analysis summary (plain language) ---
    story.append(Paragraph("Analysis summary", ss["H2"]))
    n = summ.get("total", 0)
    classes = summ.get("classes") or []
    summary_txt = (
        f"This isolate was identified as <b>{det.get('dominant_species','an unknown organism')}</b> "
        f"({det.get('dominant_pct','?')}% of classified reads"
        + (f", MLST scheme <b>{det.get('mlst_scheme')}</b> ST <b>{det.get('mlst_st')}</b>" if det.get("mlst_st") else "")
        + f"). AMRFinderPlus reported <b>{n}</b> element(s)"
        + (f": {summ.get('amr_genes',0)} acquired AMR gene(s), {summ.get('point_mutations',0)} point mutation(s)"
           f", {summ.get('stress',0)} stress/biocide/metal and {summ.get('virulence',0)} virulence element(s)." if n else ".")
    )
    if n and classes:
        summary_txt += f" Drug classes represented: <b>{', '.join(classes)}</b>."
    if not n:
        summary_txt += (" No acquired resistance genes or known resistance-conferring point mutations "
                        "were detected above the reporting thresholds. This is a genotypic result and "
                        "does not by itself establish phenotypic susceptibility.")
    story.append(Paragraph(summary_txt, ss["Body"]))

    # --- Input file quality ---
    story.append(Paragraph("Input file quality", ss["H2"]))
    files = fq.get("files") or {}
    if files:
        story.append(Paragraph(
            "Per-FASTQ read statistics from <i>seqkit stats</i>. Q20/Q30 are the percentage of "
            "bases at or above those Phred quality scores; higher is better.", ss["Body"]))
        hdr = ["File", "Reads", "Bases (bp)", "Avg len", "GC%", "Q20%", "Q30%", "Avg Q"]
        data = [hdr]
        for tag in ("R1", "R2"):
            s = files.get(tag)
            if not s:
                continue
            data.append([
                tag, _i(s.get("num_seqs")), _i(s.get("sum_len")), _i(s.get("avg_len")),
                _f(s.get("gc_pct")), _f(s.get("q20_pct")), _f(s.get("q30_pct")),
                str(s.get("avg_qual", "—")),
            ])
        story.append(_grid(data, ss, [0.7, 0.9, 1.2, 0.8, 0.7, 0.7, 0.7, 0.7]))
    else:
        story.append(Paragraph("Input was an assembly FASTA; no raw-read quality metrics available.", ss["Body"]))

    # --- Organism identification ---
    story.append(Paragraph("Organism identification", ss["H2"]))
    org_rows = [
        ("Dominant species (Kraken2)", f"{det.get('dominant_species','—')} ({det.get('dominant_pct','—')}%)"),
        ("Runner-up species", f"{det.get('runner_up_species','—')} ({det.get('runner_up_pct','—')}%)"),
        ("MLST", f"scheme {det.get('mlst_scheme') or '—'}, ST {det.get('mlst_st') or '—'}"),
    ]
    sero = ctx.get("serotype") or {}
    if sero.get("serotype"):
        org_rows.append(("Serotype (SerotypeFinder)", sero["serotype"]))
    org_rows.append(("Resolved --organism", f"{org}  (source {det.get('organism_source','—')}, confidence {conf})"))
    story.append(_kv_table(org_rows, ss))
    notes = det.get("notes") or []
    if notes:
        story.append(Spacer(1, 3))
        for ntxt in notes:
            story.append(Paragraph(f"• {ntxt}", ss["Small"]))

    # --- Assembly QC ---
    story.append(Paragraph("Assembly quality", ss["H2"]))
    m = (qc or {}).get("metrics", {}) or {}
    verdict = (qc.get("verdict") or "—").upper()
    story.append(_kv_table([
        ("Contigs", _i(m.get("num_seqs"))),
        ("Assembly length (bp)", _i(m.get("total_length"))),
        ("N50", _i(m.get("n50"))),
        ("GC (%)", _f(m.get("gc_pct"))),
        ("Largest contig (bp)", _i(m.get("max_len"))),
        ("Expected genome size (bp)", _i(qc.get("expected_genome_size")) if qc.get("expected_genome_size") else "—"),
        ("QC verdict", verdict),
    ], ss))
    for nt in (qc.get("notes") or []):
        story.append(Paragraph(f"• {nt}", ss["Small"]))

    # --- AMR findings ---
    story.append(Paragraph("Antimicrobial resistance findings", ss["H2"]))
    fig1 = assets / "amr_by_class.png"
    if _bar_by_class(summ, fig1):
        story.append(Image(str(fig1), width=6.4 * inch, height=_img_h(fig1, 6.4)))
    if rows:
        story.append(Paragraph(
            "Each row is an element reported by AMRFinderPlus. <b>Method</b> EXACT/ALLELE/POINT are "
            "high-confidence; PARTIAL* / INTERNAL_STOP warrant review. %Cov/%Id are coverage of and "
            "identity to the closest reference.", ss["Body"]))
        hdr = ["Element", "Type", "Subtype", "Class", "Subclass", "Method", "%Cov", "%Id"]
        data = [hdr]
        for r in rows[:60]:
            data.append([
                r.get("element", ""), r.get("type", ""), r.get("subtype", ""),
                r.get("class", ""), r.get("subclass", ""), r.get("method", ""),
                r.get("coverage", ""), r.get("identity", ""),
            ])
        story.append(_grid(data, ss, [1.2, 0.7, 0.7, 1.0, 1.0, 0.9, 0.5, 0.5], small=True))
        if len(rows) > 60:
            story.append(Paragraph(f"… {len(rows) - 60} more rows in amrfinder.tsv.", ss["Small"]))
    else:
        story.append(_banner("No acquired AMR genes or known resistance point mutations detected "
                             "above reporting thresholds.", TEAL, ss))

    # --- Plasmid replicons (PlasmidFinder) — only when the step ran. An empty
    # result is shown explicitly ("none detected") rather than omitted, because
    # a confirmed absence is itself a surveillance result. ---
    if ctx.get("plasmid_ran"):
        story.append(Paragraph("Plasmid replicons", ss["H2"]))
        plasmids = ctx.get("plasmids") or []
        if plasmids:
            story.append(Paragraph(
                "Plasmid replicon types identified by PlasmidFinder (BLAST vs. the CGE replicon "
                "database) on the assembly. %Id is identity to the closest reference; replicons "
                "co-located with AMR contigs suggest plasmid-borne resistance.", ss["Body"]))
            hdr = ["Replicon", "Database", "%Id", "Contig", "Accession"]
            data = [hdr]
            for p in plasmids[:40]:
                data.append([
                    p.get("replicon", ""), p.get("database", ""), p.get("identity", ""),
                    p.get("contig", ""), p.get("accession", ""),
                ])
            story.append(_grid(data, ss, [1.6, 1.4, 0.6, 1.3, 1.1], small=True))
            if len(plasmids) > 40:
                story.append(Paragraph(f"… {len(plasmids) - 40} more in plasmidfinder.tsv.", ss["Small"]))
        else:
            story.append(_banner("No plasmid replicons detected above thresholds.", TEAL, ss))

    # --- Virulence genes (VirulenceFinder) — species-gated; shown when it ran. ---
    if ctx.get("virulence_ran"):
        story.append(Paragraph("Virulence genes", ss["H2"]))
        vir = ctx.get("virulence_genes") or []
        if vir:
            story.append(Paragraph(
                "Virulence genes identified by VirulenceFinder against the species-specific CGE "
                "database. %Id is identity to the closest reference.", ss["Body"]))
            hdr = ["Gene", "Database", "%Id", "Contig"]
            data = [hdr]
            for v in vir[:60]:
                data.append([v.get("gene", ""), v.get("database", ""),
                             v.get("identity", ""), v.get("contig", "")])
            story.append(_grid(data, ss, [1.6, 1.8, 0.6, 1.4], small=True))
            if len(vir) > 60:
                story.append(Paragraph(f"… {len(vir) - 60} more in virulencefinder.tsv.", ss["Small"]))
        else:
            story.append(_banner("No virulence genes detected above thresholds.", TEAL, ss))

    # --- Methods & provenance ---
    story.append(Paragraph("Methods &amp; provenance", ss["H2"]))
    iso = ", ".join(r.get("standard", "") for r in (man.get("iso_references") or []) if r.get("standard"))
    # CGE finders provenance: "<Tool> <version> (DB <commit>)" for each that ran.
    _cgev = ctx.get("cge_versions") or {}

    def _cge_label(key, label):
        v = _cgev.get(key)
        if not v:
            return None
        s = f"{label} {v.get('version')}".rstrip() if v.get("version") else label
        return f"{s} (DB {v['db']})" if v.get("db") else s
    cge_parts = [_cge_label(k, lbl) for k, lbl in (
        ("plasmidfinder", "PlasmidFinder"),
        ("serotypefinder", "SerotypeFinder"),
        ("virulencefinder", "VirulenceFinder"),
    )]
    cge_parts = [p for p in cge_parts if p]
    if not cge_parts:  # fallback when versions weren't captured (older runs)
        cge_parts = [n for n, on in (
            ("PlasmidFinder", ctx.get("plasmid_ran")),
            ("SerotypeFinder", bool((ctx.get("serotype") or {}).get("serotype"))),
            ("VirulenceFinder", ctx.get("virulence_ran")),
        ) if on]
    cge_line = ", ".join(cge_parts) or "none run"
    story.append(_kv_table([
        ("AMRFinderPlus", f"{vers.get('amrfinder','—')} (DB {vers.get('amrfinder_db','—')})"),
        ("Kraken2 / MLST", f"{vers_extra.get('kraken2','—')} / {vers_extra.get('mlst','—')}"),
        ("Assembler", "shovill " + (vers_extra.get("shovill") or "") if vers_extra.get("shovill")
         else ("SPAdes " + (vers_extra.get("spades") or "") if vers_extra.get("spades") else "—")),
        ("Thresholds", f"ident_min={opts.get('ident_min','—')} (−1 = curated per-gene), "
                       f"coverage_min={opts.get('coverage_min','—')}"),
        ("--plus", "yes" if opts.get("plus") else "no"),
        ("CGE finders", cge_line),
        ("Standards referenced", iso or "—"),
    ], ss))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Disclaimer: results are genotypic predictions from a draft assembly and curated reference "
        "databases. They support, but do not replace, phenotypic antimicrobial susceptibility testing "
        "(reference method ISO 20776-1). Interpret categorical susceptibility against current "
        "EUCAST/CLSI breakpoints. Negative findings are documented in mutation_all.tsv.", ss["Small"]))

    doc = SimpleDocTemplate(
        str(path), pagesize=letter,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        title=f"AMRFinderPlus report — {sample}", author="amr_plus_gui",
    )
    doc.build(story)


# ---- small helpers ----
def _i(v):
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "—" if v in (None, "") else str(v)


def _f(v):
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "—" if v in (None, "") else str(v)


def _img_h(path: Path, width_in: float) -> float:
    """Preserve aspect ratio for an embedded PNG given a target width (inches)."""
    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as im:
            w, h = im.size
        return width_in * (h / w) * inch
    except Exception:
        return 2.0 * inch


def _grid(data, ss, col_in, small=False):
    style = ss["Small"] if small else ss["Cell"]
    body = [[Paragraph(str(c), style) for c in row] for row in data]
    t = Table(body, colWidths=[c * inch for c in col_in], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F5F2")]),
        ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t
