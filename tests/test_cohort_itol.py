"""Tests for the iTOL export: the tree file, the annotation files and the
bundle the cohort report writes. The layouts checked here follow iTOL's own
template files.

Run with:  pytest tests/
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cohort_itol  # noqa: E402
import cohort_report  # noqa: E402
import cohort_tree  # noqa: E402
from test_cohort_report import _isolate, _project  # noqa: E402


def test_ids_are_safe_and_unique():
    ids = cohort_itol.leaf_ids(["ISO-1", "farm 12 (2024)", "farm,12:2024;", "a|b.c"])
    assert ids["ISO-1"] == "ISO-1" and ids["a|b.c"] == "a|b.c"
    assert ids["farm 12 (2024)"] == "farm_12__2024_"
    assert len(set(ids.values())) == 4
    for value in ids.values():
        assert not set(value) & set(" (),:;'\"\t")


def test_newick_keeps_the_rooted_shape_and_uses_the_ids():
    root, tips, _ = cohort_tree.prepared("((A:1,B:1):1,(C x:1,root:2):1);", outgroup="root")
    text = cohort_itol.to_newick(root, cohort_itol.leaf_ids([t.name for t in tips]))
    assert text.endswith(";") and "root" not in text and "C_x:1" in text
    again = cohort_tree.parse_newick(text)
    assert sorted(t.name for t in again.tips()) == ["A", "B", "C_x"]
    # Depths survive the round trip: C is 1 from the root, A and B are 3.
    cohort_tree.layout(again)
    assert {t.name: round(t.x, 6) for t in again.tips()} == {"C_x": 1.0, "A": 3.0, "B": 3.0}


def test_colour_strip_follows_itols_template():
    strip = cohort_itol.Strip("state", {"ISO-1": "PR", "ISO-2": "SP", "ISO-3": ""},
                              {"PR": "#D98C9A", "SP": "#6FB7AB"})
    lines = cohort_itol.colorstrip_text(strip, {"ISO-1": "ISO-1", "ISO-2": "ISO-2", "ISO-3": "ISO-3"}).splitlines()
    assert lines[0] == "DATASET_COLORSTRIP" and lines[1] == "SEPARATOR TAB"
    assert "DATASET_LABEL\tstate" in lines and "LEGEND_LABELS\tPR\tSP" in lines
    assert "LEGEND_COLORS\t#D98C9A\t#6FB7AB" in lines
    data = lines[lines.index("DATA") + 1:]
    assert data == ["ISO-1\t#D98C9A\tPR", "ISO-2\t#6FB7AB\tSP"]      # no value, no row


def test_binary_block_distinguishes_absent_from_not_screened():
    block = cohort_itol.Binary("Resistance", "#C96A7B", ["gyrA_S83F", "tet(A)"],
                               {"gyrA_S83F": {"ISO-1"}, "tet(A)": {"ISO-2"}}, known={"ISO-1", "ISO-2"})
    lines = cohort_itol.binary_text(block, {"ISO-1": "ISO-1", "ISO-2": "ISO-2", "ISO-3": "ISO-3"}).splitlines()
    assert lines[:2] == ["DATASET_BINARY", "SEPARATOR TAB"]
    assert "FIELD_LABELS\tgyrA_S83F\ttet(A)" in lines and "FIELD_SHAPES\t1\t1" in lines
    data = lines[lines.index("DATA") + 1:]
    # 1 = found, 0 = screened and not found; ISO-3 was not screened and has no row.
    assert data == ["ISO-1\t1\t0", "ISO-2\t0\t1"]


def test_labels_file_only_when_an_id_differs():
    assert cohort_itol.labels_text({"A": "A"}) is None
    text = cohort_itol.labels_text({"farm 12": "farm_12", "A": "A"})
    assert text.splitlines() == ["LABELS", "SEPARATOR TAB", "DATA", "farm_12\tfarm 12"]


def test_popup_text_escapes_html_and_keeps_one_line_per_tip():
    text = cohort_itol.popup_text({"A": "A"}, {"A": [("Note", "x <b> y\tz\n"), ("Empty", "")]})
    data = text.splitlines()[3:]
    assert len(data) == 1 and data[0].count("\t") == 2
    assert "x &lt;b&gt; y z" in data[0] and "Empty" not in data[0]


def test_the_report_writes_a_bundle_from_the_chosen_fields(tmp_path):
    project = _project(tmp_path)
    result = cohort_report.build(project, cohort_report.Settings(group_by=["state"]),
                                 make_pdf=False, log=lambda _m: None)
    out = project / "cohort_report"
    names = sorted(p.name for p in (out / "itol").iterdir())
    assert "proj.tree" in names and "README.txt" in names and "popup.txt" in names
    assert "itol_editor.xlsx" in names
    assert "colorstrip_01_mlst_sequence_type.txt" in names and "colorstrip_02_state.txt" in names
    assert "binary_01_resistance.txt" in names and "binary_02_plasmid_replicons.txt" in names
    assert sorted(result["itol"]) == names
    tree = (out / "itol" / "proj.tree").read_text()
    assert "ISO-1" in tree and "root" not in tree
    strip = (out / "itol" / "colorstrip_02_state.txt").read_text()
    assert "ISO-1\t" in strip and "\tPR" in strip and "\tSP" in strip
    # S2's Col156 was a low-identity match: its own column, never a confident one.
    replicons = (out / "itol" / "binary_02_plasmid_replicons.txt").read_text()
    labels = next(l for l in replicons.splitlines() if l.startswith("FIELD_LABELS")).split("\t")[1:]
    assert "Col156, low identity" in labels and "Col156" not in labels
    with zipfile.ZipFile(out / "itol_bundle.zip") as zf:
        assert sorted(zf.namelist()) == names
    # Batch upload reads every file but the tree as an annotation, so its zip
    # holds the tree and the annotation files and nothing else.
    with zipfile.ZipFile(out / "itol_batch.zip") as zf:
        batch = zf.namelist()
    assert sorted(batch) == sorted(n for n in names if n not in ("README.txt", "itol_editor.xlsx"))
    assert sum(n.endswith(".tree") for n in batch) == 1
    assert "itol_bundle.zip" in (out / "cohort_report.html").read_text()
    assert "uploaded" in (out / "itol" / "README.txt").read_text()


def test_editor_workbook_keeps_the_annotation_editors_layout(tmp_path):
    from openpyxl import load_workbook
    ids = cohort_itol.leaf_ids(["ISO-1", "farm 12", "ISO-3"])
    strip = cohort_itol.Strip("state", {"ISO-1": "PR", "farm 12": "SP", "ISO-3": ""},
                              {"PR": "#D98C9A", "SP": "#6FB7AB"})
    block = cohort_itol.Binary("Resistance", "#C96A7B", ["gyrA_S83F", "tet(A)"],
                               {"gyrA_S83F": {"ISO-1"}, "tet(A)": {"farm 12"}}, known={"ISO-1", "farm 12"},
                               column_colors={"tet(A)": "#B9BBE0"})
    facts = {"ISO-1": [("Sample", "S1"), ("host", "cattle")], "farm 12": [("Sample", "S2")]}
    path = tmp_path / "itol_editor.xlsx"
    sheets = cohort_itol.write_editor_workbook(path, ids, [strip], [block], facts)
    assert sheets == [("strip state", "Color strip"), ("binary Resistance", "Binary data"),
                      ("all fields", "table")]
    wb = load_workbook(path)
    assert wb.sheetnames[0] == "How to use"

    ws = wb["strip state"]
    # Data from E1, options from A14: where the editor reads them.
    assert [ws.cell(1, c).value for c in range(5, 9)] == ["Tree node ID", "Tree node label", "Color", "Label"]
    assert [[ws.cell(r, c).value for c in range(5, 9)] for r in (2, 3)] == [
        ["ISO-1", "ISO-1", "#D98C9A", "PR"], ["farm_12", "farm 12", "#6FB7AB", "SP"]]
    assert ws.cell(4, 5).value is None                      # no value, no row
    assert ws.cell(2, 7).fill.fgColor.rgb.endswith("D98C9A")
    assert [(ws.cell(r, 1).value, ws.cell(r, 2).value) for r in (14, 15, 16)] == [
        ("Type", "Color strip"), ("Label", "state"), ("Legend color", "#4C8C8A")]
    assert "E1:H3" in ws.cell(1, 1).value

    ws = wb["binary Resistance"]
    assert [ws.cell(r, 6).value for r in (1, 2, 3, 4)] == ["Field labels", "Field colors", "Field shapes",
                                                           "Tree node label"]
    assert ws.cell(4, 5).value == "Tree node ID" and "INSERT YOUR DATA BELOW" in ws.cell(4, 7).value
    assert [[ws.cell(r, c).value for c in (7, 8)] for r in (1, 2, 3)] == [
        ["gyrA_S83F", "tet(A)"], ["#C96A7B", "#B9BBE0"], ["rectangle", "rectangle"]]
    assert [[ws.cell(r, c).value for c in range(5, 9)] for r in (5, 6)] == [
        ["ISO-1", "ISO-1", 1, 0], ["farm_12", "farm 12", 0, 1]]
    assert ws.cell(7, 5).value is None                      # ISO-3 was not screened
    assert ws.cell(14, 2).value == "Binary data" and "E1:H6" in ws.cell(1, 1).value

    ws = wb["all fields"]
    assert [c.value for c in ws[1]] == ["Tree node ID", "Tree node label", "Sample", "host"]
    assert [c.value for c in ws[3]][:3] == ["farm_12", "farm 12", "S2"]


def test_sheet_names_fit_excel_and_stay_unique():
    taken = set()
    a = cohort_itol._sheet_name("strip", "collection date / year [of sampling] in full", taken)
    b = cohort_itol._sheet_name("strip", "collection date / year [of sampling] in full", taken)
    assert len(a) <= 31 and len(b) <= 31 and a != b
    assert not set(a + b) & set("[]:*?/\\")


def test_the_report_bundle_holds_the_editor_workbook(tmp_path):
    from openpyxl import load_workbook
    project = _project(tmp_path)
    cohort_report.build(project, cohort_report.Settings(group_by=["state"]), make_pdf=False,
                        log=lambda _m: None)
    out = project / "cohort_report"
    wb = load_workbook(out / "itol" / "itol_editor.xlsx")
    assert {"strip MLST sequence type", "strip state", "binary Resistance", "all fields"} <= set(wb.sheetnames)
    tree = (out / "itol" / "proj.tree").read_text()
    ws = wb["strip state"]
    tip_ids = [ws.cell(r, 5).value for r in range(2, ws.max_row + 1) if ws.cell(r, 5).value]
    assert tip_ids and all(f"{i}:" in tree for i in tip_ids)      # every id is a tip of the tree
    readme = (out / "itol" / "README.txt").read_text()
    assert "itol_editor.xlsx" in readme and "itol_batch.zip" in readme


def test_no_tree_means_no_itol_files(tmp_path):
    project = _project(tmp_path, with_tree=False)
    result = cohort_report.build(project, cohort_report.Settings(), make_pdf=False, log=lambda _m: None)
    assert result["itol"] == [] and not (project / "cohort_report" / "itol_bundle.zip").exists()


def test_an_untyped_isolate_has_no_row_in_the_typing_blocks(tmp_path):
    project = _project(tmp_path)
    _isolate(project, "S4", "10", typing=False)
    with open(project / "sample_crosswalk.tsv", "a") as fh:
        fh.write("S4\tISO-4\tSP\n")
    tree = next((project / "step2").rglob("x-All_2026.tre"))
    tree.write_text("((ISO-1:0.1,ISO-2:0.1):0.2,((ISO-3:0.4,ISO-4:0.1):0.1,root:0.01):0.1);")
    cohort_report.build(project, cohort_report.Settings(), make_pdf=False, log=lambda _m: None)
    out = project / "cohort_report" / "itol"
    replicon_rows = (out / "binary_02_plasmid_replicons.txt").read_text().split("DATA\n")[1]
    resistance_rows = (out / "binary_01_resistance.txt").read_text().split("DATA\n")[1]
    assert "ISO-4" not in replicon_rows and "ISO-4" in resistance_rows
