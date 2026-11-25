"""Tests for the cohort report: the tree code, the distances, and the report
built from a small made-up project folder.

Run with:  pytest tests/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))

import cohort_figures  # noqa: E402
import cohort_report  # noqa: E402
import cohort_tree  # noqa: E402


# ---------------------------------------------------------------------------
# Tree
# ---------------------------------------------------------------------------
def test_newick_names_lengths_and_support_labels():
    root = cohort_tree.parse_newick("((A:1,B:2)95:0.5,(C:3,'D d':4):1.5);")
    assert [t.name for t in root.tips()] == ["A", "B", "C", "D d"]
    assert [t.length for t in root.tips()] == [1.0, 2.0, 3.0, 4.0]
    assert root.children[0].name == "95" and root.children[0].length == 0.5


@pytest.mark.parametrize("text", ["", "((A:1,B:2)", "(A:x,B:1);", "(A:1,B:1)); extra"])
def test_bad_newick_is_refused(text):
    with pytest.raises(cohort_tree.NewickError):
        cohort_tree.parse_newick(text)


def test_rooting_on_the_reference_tip_hangs_everything_else_from_it():
    root, tips, how = cohort_tree.prepared("((A:1,B:1):1,(C:1,root:2):1);", outgroup="root",
                                           keep_outgroup=True)
    assert how == "outgroup"
    reference = next(t for t in tips if t.name == "root")
    assert reference.x == 0.0                      # the root sits at the reference tip
    depth = {t.name: t.x for t in tips}
    assert depth["C"] == pytest.approx(3.0)        # 2 down the reference branch, 1 out to C
    assert depth["A"] == pytest.approx(5.0)
    assert sorted(t.y for t in tips) == [0.0, 1.0, 2.0, 3.0]


def test_the_outgroup_places_the_root_and_is_then_left_out():
    root, tips, how = cohort_tree.prepared("((A:1,B:1):1,(C:1,root:2):1);", outgroup="root")
    assert how == "outgroup"
    assert sorted(t.name for t in tips) == ["A", "B", "C"]
    depth = {t.name: t.x for t in tips}
    # The root is where the reference joined: C is 1 away, A and B are 3.
    assert depth == {"C": pytest.approx(1.0), "A": pytest.approx(3.0), "B": pytest.approx(3.0)}
    assert [t.name for t in tips][0] == "C"        # the lone tip is ladderized first


def test_without_the_outgroup_the_root_is_at_the_midpoint():
    root, tips, how = cohort_tree.prepared("((A:1,B:1):1,(C:1,D:5):1);", outgroup="root")
    assert how == "midpoint"
    depth = {t.name: t.x for t in tips}
    # The longest path is A (or B) to D, 8 long; the root sits 4 from each end.
    assert depth["D"] == pytest.approx(4.0)
    assert max(depth["A"], depth["B"]) == pytest.approx(4.0)


def test_ladderize_puts_the_smaller_clade_first():
    root, tips, _ = cohort_tree.prepared("(((A:1,B:1):1,C:1):1,D:9);")
    assert [t.name for t in tips][0] == "D"


# ---------------------------------------------------------------------------
# Distances
# ---------------------------------------------------------------------------
def test_snp_distance_counts_only_plain_base_differences():
    assert cohort_report.snp_distance("ACGTACGT", "ACGTACGT") == 0
    assert cohort_report.snp_distance("ACGTACGT", "TCGTACGA") == 2
    # N, a gap and an ambiguity code are not differences.
    assert cohort_report.snp_distance("ACGTACGT", "NCG-ACRT") == 0


def test_distance_matrix_is_symmetric_with_a_zero_diagonal():
    seqs = {"a": "AAAA", "b": "AAAT", "c": "TTTT"}
    m = cohort_report.distance_matrix(seqs, ["a", "b", "c", "missing"])
    assert [m[i][i] for i in range(4)] == [0, 0, 0, 0]
    assert m[0][1] == m[1][0] == 1 and m[0][2] == m[2][0] == 4 and m[1][2] == 3
    assert m[0][3] is None and m[3][0] is None


def test_heat_colour_runs_from_pale_yellow_to_maroon():
    assert cohort_figures.heat_color(0.0) == "#FFFADE"
    assert cohort_figures.heat_color(1.0) == "#70102C"
    assert cohort_figures.heat_color(5.0) == cohort_figures.heat_color(1.0)


# ---------------------------------------------------------------------------
# A small project folder
# ---------------------------------------------------------------------------
AMR_HEADER = "Name\tContig id\tElement symbol\tElement name\tScope\tType\tSubtype\tClass\tSubclass\tMethod\n"


def _isolate(project: Path, sample: str, st: str, genes=(), points=(), replicons=(), low=(),
             virulence=("invA",), serovar=("Dublin", "Dublin"), typing=True) -> None:
    d = project / "amr" / sample
    d.mkdir(parents=True)
    (d / "organism_detection.json").write_text(json.dumps(
        {"dominant_species": "Salmonella enterica", "organism_token": "Salmonella", "mlst_st": st}))
    (d / "qc.json").write_text(json.dumps(
        {"verdict": "pass", "metrics": {"num_seqs": 40, "total_length": 4_880_000, "n50": 400_000}}))
    rows = "".join(f"{sample}\tc1\t{g}\t{g}\tcore\tAMR\tAMR\tX\tX\tEXACTX\n" for g in ("mdsA", "mdsB", *genes))
    rows += "".join(f"{sample}\tc1\t{p}\t{p}\tcore\tAMR\tPOINT\tX\tX\tPOINTX\n" for p in points)
    (d / "amrfinder.tsv").write_text(AMR_HEADER + rows)
    (d / "run_manifest.json").write_text(json.dumps({"versions": {"amrfinder": "4.2.7"}}))
    if not typing:
        return
    agreement = "agree" if serovar[0] == serovar[1] else "disagree"
    hits = [{"gene": r, "low_identity": False, "beside": None} for r in replicons]
    hits += [{"gene": r, "low_identity": True, "beside": None} for r in low]
    (d / "typing.json").write_text(json.dumps({
        "serovar": {"sistr": {"serovar": serovar[0], "qc_status": "PASS", "cgmlst_matching_alleles": 312,
                              "cgmlst_found_loci": 330},
                    "seqsero2": {"serovar": serovar[1]}, "agreement": agreement,
                    "serovar": serovar[0] if agreement == "agree" else None},
        "plasmid_replicons": hits,
        "virulence_genes": [{"gene": v} for v in virulence],
        "checkm2": {"completeness": 100.0, "contamination": 0.1},
        "versions": {"sistr": "sistr_cmd 1.1.3"},
    }))


def _project(tmp_path: Path, with_tree: bool = True) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "project.json").write_text(json.dumps({"name": "proj", "reference": "RefX"}))
    _isolate(project, "S1", "10", points=("gyrA_S83F",), replicons=("IncX1",))
    _isolate(project, "S2", "10", genes=("tet(A)",), replicons=("IncX1",), low=("Col156",),
             virulence=("invA", "sspH2"))
    _isolate(project, "S3", "3734", replicons=("IncX1", "IncFIA"))
    (project / "sample_crosswalk.tsv").write_text(
        "run\tisolate\tstate\nS1\tISO-1\tPR\nS2\tISO-2\tPR\nS3\tISO-3\tSP\n")
    if with_tree:
        group = project / "step2" / "2026-01-01_00-00-00" / "x-All"
        group.mkdir(parents=True)
        (group / "x-All_2026.tre").write_text("((ISO-1:0.1,ISO-2:0.1):0.2,(ISO-3:0.4,root:0.01):0.1);")
        (group / "x-All_2026_labeled.tre").write_text("(ignored:1,alsoignored:1);")
        (group / "x-All.fasta").write_text(">ISO-1\nAAAAAAAA\n>ISO-2\nAAAAAAAT\n>ISO-3\nTTTTAAAA\n>root\nAAAAAAAA\n")
    return project


def test_report_is_built_from_the_project_folder(tmp_path):
    project = _project(tmp_path)
    settings = cohort_report.Settings(group_by=["state"])
    result = cohort_report.build(project, settings, make_pdf=False, log=lambda _m: None)
    out = project / "cohort_report"
    assert {"cohort_report.html", "cohort_table.tsv", "cohort_checks.json", "settings.json"} <= set(p.name for p in out.iterdir())
    assert result["isolates"] == 3 and result["typed"] == 3 and result["in_tree"] == 3
    assert result["problems"] == []
    # Display names come from the column that matches the tree's tips. The
    # chart is the one that was asked for, and the colouring is MLST by default.
    assert result["label_column"] == "isolate" and result["group_by"] == ["state"]
    assert result["color_by"] == "mlst"
    assert result["rooting"] == "outgroup" and result["pairs"] == 3
    # mdsA and mdsB are in every isolate, so only S2's tet(A) counts as acquired.
    assert result["with_acquired_resistance"] == 1
    text = (out / "cohort_report.html").read_text(encoding="utf-8")
    for expected in ("ST10 n=2, ST3734 n=1", "Serovar: Dublin n=3", "gyrA_S83F", "IncFIA",
                     "low-identity replicon: Col156", "minimum 1, median 4, maximum 5", "<svg",
                     "MLST sequence type by state: 3 isolates"):
        assert expected in text, expected
    table = (out / "cohort_table.tsv").read_text(encoding="utf-8").splitlines()
    assert table[0].split("\t")[:3] == ["Isolate", "Sample", "state"]
    assert len(table) == 4
    row = dict(zip(table[0].split("\t"), next(r for r in table[1:] if r.startswith("ISO-2")).split("\t")))
    assert row["Resistance determinants"] == "tet(A)" and row["Low-identity replicons"] == "Col156"
    assert row["MLST"] == "ST10" and row["Serovar"] == "Dublin"


def test_report_without_a_tree_says_so_and_still_builds(tmp_path):
    project = _project(tmp_path, with_tree=False)
    result = cohort_report.build(project, make_pdf=False, log=lambda _m: None)
    assert result["step2_run"] is None and result["pairs"] == 0 and result["problems"] == []
    assert any("no" in line.lower() and "tree" in line.lower() for line in result["info"])
    text = (project / "cohort_report" / "cohort_report.html").read_text(encoding="utf-8")
    assert "Pairwise distances" not in text and "Per isolate results" in text


def test_layers_that_do_not_line_up_are_reported(tmp_path):
    project = _project(tmp_path)
    _isolate(project, "S4", "10", typing=False)                       # no typing, no metadata, not in tree
    with open(project / "sample_crosswalk.tsv", "a") as fh:
        fh.write("S9\tISO-9\tSP\n")                                  # metadata with no results
    tree = next((project / "step2").rglob("x-All_2026.tre"))
    tree.write_text("((ISO-1:0.1,ISO-2:0.1):0.2,((ISO-3:0.4,STRANGER:0.1):0.1,root:0.01):0.1);")
    result = cohort_report.build(project, make_pdf=False, log=lambda _m: None)
    problems = " | ".join(result["problems"])
    for expected in ("1 metadata row with no results in this project: S9",
                     "1 isolate with no metadata row: S4",
                     "1 tree tip with no isolate in this project: STRANGER",
                     "1 isolate not in the tree: S4",
                     "1 isolate with no typing result while the others have one"):
        assert expected in problems, expected
    text = (project / "cohort_report" / "cohort_report.html").read_text(encoding="utf-8")
    assert "Check before using this report" in text


def test_an_isolate_with_no_typing_gets_no_squares_in_the_typing_blocks():
    root, tips, _ = cohort_tree.prepared("(A:1,B:1);")
    group = cohort_figures.ColumnGroup("Plasmid replicons", "#4B4F9C", ["IncX1"],
                                       {"IncX1": {"A": "full"}}, known={"A"})
    def squares(g):
        svg = cohort_figures.tree_matrix(root, tips, {"A": "A", "B": "B"}, {"A": "10", "B": "10"},
                                         cohort_figures.lineage_palette(["10"]), {"10": "ST10"}, [g], "t")
        # Matrix squares are the rounded rectangles; the block title shares the colour.
        return svg.count('rx="2" fill="#4B4F9C"') + svg.count(f'rx="2" fill="{cohort_figures.EMPTY}"')
    assert squares(group) == 1                       # A only: B has no result to draw
    group.known = None
    assert squares(group) == 2                       # with results for both, B is drawn as absent


def test_a_serovar_disagreement_becomes_a_note_on_the_isolate(tmp_path):
    project = _project(tmp_path)
    _isolate(project, "S5", "10", serovar=("Dublin", "Enteritidis"))
    result = cohort_report.build(project, make_pdf=False, log=lambda _m: None)
    text = (project / "cohort_report" / "cohort_report.html").read_text(encoding="utf-8")
    assert "serovar: SISTR Dublin, SeqSero2 Enteritidis" in text
    assert "agree on 3 of 4 typed isolates" in " ".join(result["summary"])


# ---------------------------------------------------------------------------
# Nothing is assumed: fields are chosen, and sections follow the data
# ---------------------------------------------------------------------------
def test_no_chart_is_drawn_unless_a_field_is_chosen(tmp_path):
    project = _project(tmp_path)
    result = cohort_report.build(project, cohort_report.Settings(), make_pdf=False, log=lambda _m: None)
    text = (project / "cohort_report" / "cohort_report.html").read_text(encoding="utf-8")
    assert result["group_by"] == [] and " by state" not in text
    # The sheet has a `state` column, and it is offered, not used.
    assert "state" in [f["field"] for f in result["fields"]]


def test_colour_and_charts_follow_the_chosen_metadata_fields(tmp_path):
    project = _project(tmp_path)
    (project / "sample_crosswalk.tsv").write_text(
        "run\tisolate\tHerd\tcollected\nS1\tISO-1\tH7\t2023-06-05\nS2\tISO-2\tH7\t2024-01-31\nS3\tISO-3\tH9\t2024-11\n")
    settings = cohort_report.Settings(color_by="Herd", group_by=["collected|year", "mlst"])
    result = cohort_report.build(project, settings, make_pdf=False, log=lambda _m: None)
    text = (project / "cohort_report" / "cohort_report.html").read_text(encoding="utf-8")
    assert result["problems"] == [] and result["color_by"] == "Herd"
    assert "Herd by collected (year): 3 isolates" in text and "Herd by MLST sequence type: 3 isolates" in text
    assert "Herd: H7 n=2, H9 n=1." in " ".join(result["summary"])
    assert "coloured by Herd" in text and "Columns after the total: isolates by Herd" in text
    assert ">H7 (n=2)<" in text and ">2023<" in text and ">2024<" in text
    # The choices are remembered and used by the next build that names none.
    again = cohort_report.build(project, make_pdf=False, log=lambda _m: None)
    assert again["color_by"] == "Herd" and again["group_by"] == ["collected|year", "mlst"]


def test_a_chosen_field_that_is_not_there_is_reported_not_guessed(tmp_path):
    project = _project(tmp_path)
    settings = cohort_report.Settings(color_by="province", group_by=["district", "state"])
    result = cohort_report.build(project, settings, make_pdf=False, log=lambda _m: None)
    problems = " | ".join(result["problems"])
    assert "'province', is not in the results or the metadata sheet" in problems
    assert "'district', is not in the results or the metadata sheet" in problems
    assert result["color_by"] == "" and result["group_by"] == ["state"]


def test_a_project_with_no_typing_and_no_metadata_still_reports(tmp_path):
    project = tmp_path / "plain"
    project.mkdir()
    for sample, st in (("A", "5"), ("B", "5"), ("C", "8")):
        _isolate(project, sample, st, genes=("blaZ",) if sample == "A" else (), typing=False)
        det = project / "amr" / sample / "organism_detection.json"
        det.write_text(json.dumps({"dominant_species": "Staphylococcus aureus", "organism_token": "Staphylococcus_aureus",
                                   "mlst_st": st, "mlst_scheme": "saureus"}))
    result = cohort_report.build(project, cohort_report.Settings(), make_pdf=False, log=lambda _m: None)
    text = (project / "cohort_report" / "cohort_report.html").read_text(encoding="utf-8")
    assert result["problems"] == []                      # untyped everywhere is not a fault
    assert "MLST (saureus scheme): ST5 n=2, ST8 n=1" in text
    for absent in ("Serovar", "SISTR", "SeqSero2", "CheckM2 compl.", "Plasmid replicons and virulence",
                   "Isolates and metadata", "Seven-gene", "cgMLST"):
        assert absent not in text.replace("CheckM2 completeness (%)", "").replace("CheckM2 contamination (%)", ""), absent
    assert "Per-isolate steps with results here" in text and "assembly, MLST, AMRFinderPlus<" in text


def test_many_values_are_pooled_into_other():
    values = {f"i{n}": f"v{n % 12}" for n in range(40)}
    pooled, hidden = cohort_report.pooled(values)
    kept = set(pooled.values())
    assert cohort_report.OTHER in kept and len(kept) == cohort_report.MAX_CATEGORIES
    assert hidden == 12 - (cohort_report.MAX_CATEGORIES - 1)
    assert cohort_report.pooled({"a": "x", "b": "y"}) == ({"a": "x", "b": "y"}, 0)


def test_fields_offered_come_from_results_and_the_sheet(tmp_path):
    project = _project(tmp_path)
    (project / "sample_crosswalk.tsv").write_text(
        "run\tisolate\tstate\tcollected\nS1\tISO-1\tPR\t2023-06-05\nS2\tISO-2\tPR\t2024-01-31\nS3\tISO-3\tSP\t\n")
    info = cohort_report.project_fields(project)
    fields = {f["field"]: f for f in info["fields"]}
    assert {"mlst", "organism", "serovar", "isolate", "state", "collected", "collected|year"} <= set(fields)
    assert fields["state"]["distinct"] == 2 and fields["state"]["filled"] == 3
    assert fields["collected|year"]["label"] == "collected (year)" and fields["collected"]["filled"] == 2
    assert info["settings"]["color_by"] == "mlst" and info["settings"]["group_by"] == []


def test_only_columns_that_tell_isolates_apart_are_printed():
    isolates = [cohort_report.Isolate(sample=s, label=l, meta=m) for s, l, m in [
        ("R1", "ISO-1", {"run": "R1", "strain": "ISO-1", "organism": "S. enterica", "state": "PR", "region": "PR", "host": "", "breed": "Nelore"}),
        ("R2", "ISO-2", {"run": "R2", "strain": "ISO-2", "organism": "S. enterica", "state": "SP", "region": "SP", "host": "", "breed": ""}),
        ("R3", "ISO-3", {"run": "R3", "strain": "ISO-3", "organism": "S. enterica", "state": "SP", "region": "SP", "host": "", "breed": "Nelore"}),
    ]]
    shown, constant, duplicate = cohort_report.informative_columns(
        ["run", "strain", "organism", "state", "region", "host", "breed"], isolates)
    assert shown == ["state"]
    # A column with one value says how many isolates carry it when some do not.
    assert constant == {"organism": "S. enterica",
                        "breed": "Nelore (2 of 3; the rest have no value)"}
    assert duplicate == {"run": "Sample", "strain": "Isolate", "region": "state"}


def test_no_results_at_all_is_an_error(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit):
        cohort_report.build(tmp_path / "empty", make_pdf=False, log=lambda _m: None)
