"""Tests for bin/typing_steps.py and the report rows built from typing.json.

The fixtures in tests/fixtures are real tool output for one Salmonella Dublin
isolate (SRA run SRR37707748), so the parsers are checked against the formats
the installed tool versions write.

Run with:  pytest tests/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(REPO / "bin"))

import reporting  # noqa: E402
import typing_steps  # noqa: E402


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Parsers, against real output
# ---------------------------------------------------------------------------
def test_sistr_json_is_reduced_to_the_reported_fields():
    result = typing_steps.parse_sistr_json(_fixture("sistr.json"))
    assert result["serovar"] == "Dublin"
    assert result["serovar_antigen"] == "Kiel|Dublin"
    assert result["serovar_cgmlst"] == "Dublin"
    assert result["serogroup"] == "D1"
    assert result["h1"] == "g,p"
    assert result["cgmlst_st"] == 2789633150
    assert result["cgmlst_matching_alleles"] == 312
    assert result["qc_status"] == "PASS"


def test_seqsero2_tsv_is_read_by_column_name():
    result = typing_steps.parse_seqsero2_tsv(_fixture("seqsero2.tsv"))
    assert result["serovar"] == "Dublin"
    assert result["antigenic_profile"] == "9:g,p:-"
    assert result["o_antigen"] == "9"
    assert result["h1"] == "g,p"
    assert result["h2"] == "-"
    assert result["note"] is None


def test_seqsero2_empty_output_gives_no_call():
    assert typing_steps.parse_seqsero2_tsv("") == {}


def test_abricate_plasmidfinder_hits():
    hits = typing_steps.parse_abricate_tab(_fixture("abricate_plasmidfinder.tsv"))
    screen = typing_steps.ABRICATE_DATABASES["plasmidfinder"]
    assert {h["gene"] for h in hits} == {"IncFII(S)", "IncX1"}
    assert {h["allele"] for h in hits} == {"IncFII(S)_1", "IncX1_1"}
    for hit in hits:
        assert hit["identity"] >= screen.min_identity
        assert hit["coverage"] >= screen.min_coverage
        assert hit["contig"]


@pytest.mark.parametrize(
    "allele, replicon",
    [("IncFII(S)_1", "IncFII(S)"), ("IncX1_1", "IncX1"), ("IncFIB(AP001918)_1", "IncFIB(AP001918)"),
     ("Col440I_1", "Col440I"), ("Col156", "Col156"), ("_1", "_1")],
)
def test_replicon_name_drops_only_the_variant_number(allele, replicon):
    assert typing_steps.replicon_name(allele) == replicon


def test_virulence_gene_names_are_kept_as_written():
    hits = typing_steps.parse_abricate_tab(_fixture("abricate_vfdb.tsv"))
    genes = {h["gene"] for h in hits}
    assert {"spvB", "spvC", "spvD"} <= genes
    assert all(h["gene"] == h["allele"] for h in hits)


def test_abricate_header_only_is_a_real_negative():
    header = _fixture("abricate_plasmidfinder.tsv").splitlines()[0] + "\n"
    assert typing_steps.parse_abricate_tab(header) == []
    assert typing_steps.parse_abricate_tab("") == []


def test_checkm2_report_row():
    result = typing_steps.parse_checkm2_tsv(_fixture("checkm2.tsv"))
    assert 99.0 <= result["completeness"] <= 100.0
    assert 0.0 <= result["contamination"] < 5.0
    assert result["model"]


# ---------------------------------------------------------------------------
# Serovar agreement
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sistr, seqsero2, agreement, serovar",
    [
        ("Dublin", "Dublin", "agree", "Dublin"),
        ("Dublin", "dublin", "agree", "Dublin"),
        ("Dublin", "Enteritidis", "disagree", None),
        ("Kiel|Dublin", "Dublin", "consistent", None),
        ("Dublin", "Dublin|Rostock", "consistent", None),
        ("Dublin", None, "single", "Dublin"),
        (None, "Dublin", "single", "Dublin"),
        (None, None, "none", None),
        ("Dublin", "-", "single", "Dublin"),
    ],
)
def test_serovar_agreement(sistr, seqsero2, agreement, serovar):
    result = typing_steps.serovar_agreement({"serovar": sistr}, {"serovar": seqsero2})
    assert result == {"agreement": agreement, "serovar": serovar}


# ---------------------------------------------------------------------------
# Gating and tool lookup
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "detection, expected",
    [
        ({"dominant_species": "Salmonella enterica"}, True),
        ({"organism_token": "Salmonella"}, True),
        ({"dominant_species": "Escherichia coli", "organism_token": "Escherichia"}, False),
        ({}, False),
    ],
)
def test_is_salmonella(detection, expected):
    assert typing_steps.is_salmonella(detection) is expected


def test_find_tool_accepts_per_tool_and_shared_layouts(tmp_path):
    per_tool = tmp_path / "per" / "sistr" / "bin"
    per_tool.mkdir(parents=True)
    (per_tool / "sistr").write_text("")
    found = typing_steps.find_tool(str(tmp_path / "per"), "sistr", "sistr")
    assert found is not None and found.env_dir == tmp_path / "per" / "sistr"

    shared = tmp_path / "shared" / "env" / "bin"
    shared.mkdir(parents=True)
    (shared / "abricate").write_text("")
    found = typing_steps.find_tool(str(tmp_path / "shared"), "abricate", "abricate")
    assert found is not None and found.env_dir == tmp_path / "shared" / "env"

    assert typing_steps.find_tool(str(tmp_path / "per"), "abricate", "abricate") is None
    assert typing_steps.find_tool("", "sistr", "sistr") is None


def test_tool_environment_puts_its_own_bin_first(tmp_path):
    tool = typing_steps.Tool(exe=tmp_path / "bin" / "sistr", env_dir=tmp_path)
    assert tool.environ()["PATH"].split(":")[0] == str(tmp_path / "bin")


# ---------------------------------------------------------------------------
# run_all without any tools installed
# ---------------------------------------------------------------------------
def test_run_all_with_nothing_installed_skips_every_step(tmp_path):
    logged = []
    summary = typing_steps.run_all(
        assembly=tmp_path / "assembly.fasta", outdir=tmp_path,
        detection={"dominant_species": "Salmonella enterica"},
        typing_root="", checkm2_db="", threads=1, log=logged.append,
    )
    assert summary["salmonella"] is True
    assert set(summary["steps"].values()) == {"skipped"}
    assert len(logged) == 1 and "not installed" in logged[0]
    assert "serovar" not in summary and "plasmid_replicons" not in summary
    assert json.loads((tmp_path / "typing.json").read_text()) == summary


def test_run_all_marks_salmonella_steps_not_applicable_for_other_organisms(tmp_path):
    # A typing root that exists but holds no tools.
    summary = typing_steps.run_all(
        assembly=tmp_path / "assembly.fasta", outdir=tmp_path,
        detection={"dominant_species": "Escherichia coli"},
        typing_root=str(tmp_path), checkm2_db="", threads=1, log=lambda _msg: None,
    )
    assert summary["steps"]["sistr"] == "not applicable"
    assert summary["steps"]["vfdb"] == "not applicable"
    assert summary["steps"]["checkm2"] == "skipped"


# ---------------------------------------------------------------------------
# Report rows
# ---------------------------------------------------------------------------
def _typing_summary() -> dict:
    sistr = typing_steps.parse_sistr_json(_fixture("sistr.json"))
    seqsero2 = typing_steps.parse_seqsero2_tsv(_fixture("seqsero2.tsv"))
    return {
        "serovar": {"sistr": sistr, "seqsero2": seqsero2,
                    **typing_steps.serovar_agreement(sistr, seqsero2)},
        "plasmid_replicons": typing_steps.parse_abricate_tab(_fixture("abricate_plasmidfinder.tsv")),
        "virulence_genes": [],
        "checkm2": typing_steps.parse_checkm2_tsv(_fixture("checkm2.tsv")),
    }


def test_stats_rows_report_what_ran():
    rows = dict(reporting.typing_stats_items(_typing_summary()))
    assert rows["Serovar (SISTR)"] == "Dublin"
    assert rows["Serovar (SeqSero2)"] == "Dublin"
    assert rows["Serovar agreement"] == "SISTR and SeqSero2 agree"
    assert rows["Plasmid replicons"] == "IncX1, IncFII(S)"
    assert rows["Virulence genes (VFDB)"] == "0"
    assert rows["CheckM2 completeness (%)"].endswith("%")


def test_stats_rows_are_empty_when_no_typing_ran():
    assert reporting.typing_stats_items({}) == []
    # A step that did not run adds no row, so it cannot read as a negative.
    rows = dict(reporting.typing_stats_items({"checkm2": {"completeness": 99.9, "contamination": 0.1}}))
    assert "Plasmid replicons" not in rows and "Serovar (SISTR)" not in rows


def test_an_empty_replicon_screen_reads_as_none_detected():
    rows = dict(reporting.typing_stats_items({"plasmid_replicons": []}))
    assert rows["Plasmid replicons"] == "none detected"


def test_html_report_shows_the_typing_sections(tmp_path):
    from reporting.html_report import write_html

    ctx = {"sample": "SRR37707748", "date": "2026-11-24", "fastq_qc": {}, "qc": {},
           "detection": {"dominant_species": "Salmonella enterica"}, "manifest": {},
           "amr_rows": [], "amr_summary": {}, "typing": _typing_summary(), "mlst": {}}
    out = tmp_path / "report.html"
    write_html(ctx, out, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "Serovar and lineage" in text and "Dublin" in text
    assert "IncX1" in text
    assert "No virulence genes detected." in text
    assert "DISAGREE" not in text and "different serovars" not in text


def test_html_report_flags_a_serovar_disagreement(tmp_path):
    from reporting.html_report import write_html

    typing = {"serovar": {"sistr": {"serovar": "Dublin"}, "seqsero2": {"serovar": "Enteritidis"},
                          "agreement": "disagree", "serovar": None}}
    ctx = {"sample": "s", "date": "d", "fastq_qc": {}, "qc": {}, "detection": {}, "manifest": {},
           "amr_rows": [], "amr_summary": {}, "typing": typing, "mlst": {}}
    out = tmp_path / "report.html"
    write_html(ctx, out, tmp_path)
    assert "different serovars" in out.read_text(encoding="utf-8")


def test_html_report_without_typing_has_no_typing_sections(tmp_path):
    from reporting.html_report import write_html

    ctx = {"sample": "s", "date": "d", "fastq_qc": {}, "qc": {}, "detection": {}, "manifest": {},
           "amr_rows": [], "amr_summary": {}, "typing": {}, "mlst": {}}
    out = tmp_path / "report.html"
    write_html(ctx, out, tmp_path)
    text = out.read_text(encoding="utf-8")
    # "Virulence genes" alone is also an AMRFinderPlus summary row, so check the
    # VFDB section by its full heading.
    for heading in ("Serovar and lineage", "Plasmid replicons", "Virulence genes (VFDB)"):
        assert heading not in text
