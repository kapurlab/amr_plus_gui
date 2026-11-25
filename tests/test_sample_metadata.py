"""Tests for the project's sample metadata sheet: merging, the sources, and
how the cohort report picks the sheet up.

No test touches the network. The SRA source is given a stand-in for HTTP GET
that answers from tests/fixtures/biosample_set.xml, which is three real NCBI
BioSample records with the owner and contact details removed.

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

import sample_metadata as sm  # noqa: E402

BIOSAMPLES = (FIXTURES / "biosample_set.xml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------
def test_merge_fills_blanks_adds_columns_and_keeps_what_is_there():
    header = ["sample", "state"]
    rows = {"S1": {"sample": "S1", "state": "PR"}, "S2": {"sample": "S2", "state": ""}}
    provenance = {}
    result = sm.merge(header, rows, {
        "S1": {"state": "Parana", "host": "Bos taurus"},      # state is filled: kept
        "S2": {"state": "SP", "host": "missing"},             # blank is filled; "missing" is not a value
        "S3": {"host": "Sus scrofa"},                         # a new sample
    }, "NCBI BioSample", provenance)
    assert rows["S1"] == {"sample": "S1", "state": "PR", "host": "Bos taurus"}
    assert rows["S2"] == {"sample": "S2", "state": "SP"}
    assert rows["S3"] == {"sample": "S3", "host": "Sus scrofa"}
    assert header == ["sample", "state", "host"]
    assert result["added_samples"] == ["S3"] and result["added_columns"] == ["host"]
    assert result["filled"] == 3
    assert result["conflicts"] == [{"sample": "S1", "column": "state", "kept": "PR",
                                    "offered": "Parana", "source": "NCBI BioSample"}]
    assert provenance["columns"] == {"host": ["NCBI BioSample"], "state": ["NCBI BioSample"]}


@pytest.mark.parametrize("value", ["", "missing", "Not collected", "not applicable", "N/A", "-", None])
def test_placeholders_count_as_empty(value):
    assert sm.is_null(value)


def test_sheet_round_trip(tmp_path):
    header = ["sample", "strain", "geo_region"]
    rows = {"B": {"sample": "B", "strain": "x"}, "A": {"sample": "A", "strain": "y", "geo_region": "PR"}}
    sm.save_sheet(tmp_path, header, rows)
    lines = (tmp_path / sm.SHEET_NAME).read_text().splitlines()
    assert lines == ["sample\tstrain\tgeo_region", "A\ty\tPR", "B\tx\t"]
    assert sm.load_sheet(tmp_path) == (header, {"A": {"sample": "A", "strain": "y", "geo_region": "PR"},
                                                "B": {"sample": "B", "strain": "x", "geo_region": ""}})


# ---------------------------------------------------------------------------
# Which samples a project has
# ---------------------------------------------------------------------------
def test_project_samples_come_from_inputs_and_tool_folders(tmp_path):
    (tmp_path / "download").mkdir()
    for name in ("SRR1_1.fastq.gz", "SRR1_2.fastq.gz", "iso-7_R1_001.fastq.gz", "asm9.fasta",
                 "sra_crosswalk.tsv", "download_sra.sh"):
        (tmp_path / "download" / name).write_text("")
    (tmp_path / "amr" / "SRR2").mkdir(parents=True)
    (tmp_path / "step1" / "SRR3").mkdir(parents=True)
    (tmp_path / "step2" / "vcf_database").mkdir(parents=True)     # not a sample
    (tmp_path / "amr" / "_scratch").mkdir()                        # not a sample
    assert sm.project_samples(tmp_path) == ["SRR1", "SRR2", "SRR3", "asm9", "iso-7"]


# ---------------------------------------------------------------------------
# The SRA source, with real records and no network
# ---------------------------------------------------------------------------
def test_biosample_records_keep_every_attribute_under_its_own_name():
    records = sm.parse_biosample_xml(BIOSAMPLES)
    assert set(records) == {"SAMN56654382", "SAMN56611937", "SAMN56394601"}
    one = records["SAMN56611937"]
    assert one["strain"] == "PNUSAS567949" and one["sample_name"] == "PNUSAS567949"
    assert one["organism"] == "Salmonella enterica" and one["sra_sample"] == "SRS28472860"
    assert one["geo_loc_name"] == "Brazil:PR" and one["collection_date"] == "2023-06-05"
    assert one["host"] == "Bos taurus" and one["isolation_source"] == "feces"
    # The third record was submitted under a different package: it has a
    # serovar and no host, and both come through without any per-field code.
    other = records["SAMN56394601"]
    assert other["serovar"] == "Dublin" and "host" not in other


@pytest.mark.parametrize("value, expected", [
    ("Brazil:PR", {"geo_country": "Brazil", "geo_region": "PR"}),
    ("USA: Pennsylvania, Centre County", {"geo_country": "USA", "geo_region": "Pennsylvania"}),
    ("India", {"geo_country": "India"}),
    ("missing", {}),
])
def test_location_is_split_into_country_and_region(value, expected):
    assert sm.split_location(value) == expected


def _fake_ncbi(ena_records, link_map=None):
    """A stand-in for HTTP GET: ENA answers from `ena_records`, NCBI from the
    fixture. Records which URLs were asked for."""
    calls = []

    def get(url: str) -> str:
        calls.append(url)
        if "ebi.ac.uk" in url:
            return json.dumps([r for r in ena_records if f'"{r["run_accession"]}"' in _decoded(url)])
        if "esearch.fcgi" in url and "db=sra" in url:
            run = _decoded(url).split("term=")[1].split("[")[0]
            return json.dumps({"esearchresult": {"idlist": ["777"] if run in (link_map or {}) else []}})
        if "elink.fcgi" in url:
            return json.dumps({"linksets": [{"linksetdbs": [{"links": ["56611937"]}]}]})
        if "esearch.fcgi" in url:
            return json.dumps({"esearchresult": {"idlist": ["1", "2", "3"]}})
        if "efetch.fcgi" in url:
            return BIOSAMPLES
        raise AssertionError(url)

    return get, calls


def _decoded(url: str) -> str:
    from urllib.parse import unquote_plus
    return unquote_plus(url)


def test_sra_source_joins_the_run_to_its_biosample(tmp_path):
    ena = [{"run_accession": "SRR37707748", "sample_accession": "SAMN56611937",
            "instrument_model": "Illumina MiSeq", "library_layout": "PAIRED",
            "base_count": "163545020", "read_count": "335558"}]
    get, _calls = _fake_ncbi(ena)
    rows = sm.SraSource(http_get=get, log=lambda _m: None).fetch(["SRR37707748", "my-local-sample"])
    assert set(rows) == {"SRR37707748"}                # a locally named sample is not looked up
    row = rows["SRR37707748"]
    assert row["biosample"] == "SAMN56611937" and row["strain"] == "PNUSAS567949"
    assert row["geo_country"] == "Brazil" and row["geo_region"] == "PR"
    assert row["instrument"] == "Illumina MiSeq" and row["bases"] == "163545020"


def test_a_run_ena_does_not_list_is_found_through_ncbi_links(tmp_path):
    get, calls = _fake_ncbi([], link_map={"SRR37707748": "SAMN56611937"})
    rows = sm.SraSource(http_get=get, log=lambda _m: None).fetch(["SRR37707748"])
    assert "elink.fcgi" in " ".join(calls)
    assert len(rows) == 1 and rows["SRR37707748"]["biosample"] in ("SAMN56654382", "SAMN56611937", "SAMN56394601")


def test_a_failing_lookup_is_reported_and_does_not_raise():
    def broken(_url):
        raise RuntimeError("no network")
    notes = []
    rows = sm.SraSource(http_get=broken, log=notes.append).fetch(["SRR37707748"])
    assert rows == {} and any("no public record" in n for n in notes)


def test_requests_name_the_tool_and_carry_an_email_only_when_one_is_set(monkeypatch):
    monkeypatch.delenv("NCBI_EMAIL", raising=False)
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    assert "tool=bdtools_sample_metadata" in sm._eutils("esearch.fcgi", db="sra") and "email=" not in sm._eutils("esearch.fcgi", db="sra")
    monkeypatch.setenv("NCBI_EMAIL", "lab@example.org")
    assert "email=lab%40example.org" in sm._eutils("esearch.fcgi", db="sra")


# ---------------------------------------------------------------------------
# fetch and import, end to end on a project folder
# ---------------------------------------------------------------------------
def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / "download").mkdir(parents=True)
    for name in ("SRR37707748_1.fastq.gz", "SRR37707748_2.fastq.gz", "farm12_R1.fastq.gz"):
        (project / "download" / name).write_text("")
    return project


def test_fetch_writes_the_sheet_and_says_where_columns_came_from(tmp_path):
    project = _project(tmp_path)
    ena = [{"run_accession": "SRR37707748", "sample_accession": "SAMN56611937",
            "instrument_model": "Illumina MiSeq", "library_layout": "PAIRED", "base_count": "1", "read_count": "1"}]
    get, _ = _fake_ncbi(ena)
    result = sm.fetch(project, "sra", http_get=get, log=lambda _m: None)
    assert result["applicable"] == 1 and result["fetched"] == 1 and result["not_found"] == []
    header, rows = sm.load_sheet(project)
    assert header[0] == "sample" and {"strain", "geo_region", "collection_date"} <= set(header)
    assert rows["SRR37707748"]["geo_region"] == "PR"
    provenance = json.loads((project / sm.PROVENANCE_NAME).read_text())
    assert provenance["columns"]["strain"] == ["NCBI BioSample"]
    assert provenance["history"][-1]["source"] == "NCBI BioSample"


def test_a_lab_sheet_is_merged_and_a_fetch_afterwards_does_not_overwrite_it(tmp_path):
    project = _project(tmp_path)
    lab = tmp_path / "lims_export.csv"
    lab.write_text("Lab ID,Run,Herd,strain\nL-1,SRR37707748,H7,our-name-1\nL-2,farm12,H9,our-name-2\nL-3,elsewhere,H1,x\n")
    result = sm.import_sheet(project, lab, log=lambda _m: None)
    assert result["key_column"] == "Run" and result["unmatched_rows"] == 1
    _, rows = sm.load_sheet(project)
    assert rows["farm12"]["Herd"] == "H9" and rows["SRR37707748"]["Lab ID"] == "L-1"

    ena = [{"run_accession": "SRR37707748", "sample_accession": "SAMN56611937",
            "instrument_model": "", "library_layout": "", "base_count": "", "read_count": ""}]
    get, _ = _fake_ncbi(ena)
    result = sm.fetch(project, "sra", http_get=get, log=lambda _m: None)
    _, rows = sm.load_sheet(project)
    assert rows["SRR37707748"]["strain"] == "our-name-1"            # the lab's value stands
    assert rows["SRR37707748"]["geo_region"] == "PR"                # the public record fills the rest
    assert {"sample": "SRR37707748", "column": "strain", "kept": "our-name-1",
            "offered": "PNUSAS567949", "source": "NCBI BioSample"} in result["conflicts"]
    assert rows["farm12"]["Herd"] == "H9" and "geo_region" not in {k for k, v in rows["farm12"].items() if v}


def test_no_applicable_sample_writes_nothing(tmp_path):
    project = tmp_path / "p"
    (project / "download").mkdir(parents=True)
    (project / "download" / "farm12_R1.fastq.gz").write_text("")
    result = sm.fetch(project, "sra", http_get=lambda _u: "", log=lambda _m: None)
    assert result["applicable"] == 0 and not (project / sm.SHEET_NAME).exists()


def test_an_unknown_source_is_refused(tmp_path):
    with pytest.raises(ValueError):
        sm.fetch(tmp_path, "nope")


def test_the_cohort_report_prefers_the_projects_own_sheet(tmp_path):
    import cohort_report

    (tmp_path / "download").mkdir()
    (tmp_path / "download" / "sample_crosswalk.tsv").write_text("run\tstate\nS1\tPR\n")
    assert cohort_report.find_metadata(tmp_path).name == "sample_crosswalk.tsv"
    (tmp_path / "sample_metadata.tsv").write_text("sample\tstate\nS1\tPR\n")
    assert cohort_report.find_metadata(tmp_path).name == "sample_metadata.tsv"
