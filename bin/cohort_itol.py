#!/usr/bin/env python
"""
cohort_itol.py: the cohort's tree and annotations as files for iTOL.

iTOL (Interactive Tree Of Life, itol.embl.de) draws a tree with rings or
columns of annotation and lets a person restyle it by hand. It takes a tree
file and plain-text annotation files that are dropped onto the tree. This
module writes those files from what the cohort report already has, so the
same chosen fields that colour and group the report annotate the iTOL tree:

  <name>.tree                 the rooted tree, tips named by iTOL-safe ids
  labels.txt                  id -> display name, when an id had to be changed
  colorstrip_NN_<field>.txt   one colour strip per chosen field
  binary_NN_<block>.txt       presence/absence, one column per feature
  popup.txt                   what to show when a tip is clicked
  itol_editor.xlsx            the same annotations as sheets for the iTOL
                              annotation editor (the spreadsheet add-on)
  README.txt                  how to use the files

Two zips sit beside the folder. itol_bundle.zip holds everything, for a
person. itol_batch.zip holds only the tree and the annotation files, which is
what iTOL's batch upload accepts: one file ending in .tree, and every other
file read as an annotation. Nothing here uploads anything. Sending a tree to
iTOL sends its names and annotations to a server outside the lab, which is the
user's decision to make.

Every text file uses SEPARATOR TAB: display names and values may hold spaces
and commas, never tabs. The layouts follow iTOL's own templates. The workbook
follows the sheet the annotation editor lays out: its data area starts at E1
and its dataset options at A14, so a block copied from a sheet here pastes
into the editor at the same cells.
"""

from __future__ import annotations

import html
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cohort_tree import Node

# iTOL binary dataset values and the rectangle shape.
PRESENT, ABSENT = "1", "0"
RECTANGLE = "1"
STRIP_COLOR = "#4C8C8A"

# The annotation editor's sheet: data from E1, dataset options from A14, and
# the names it uses for a dataset type and for the rectangle shape.
EDITOR_COLUMN = 5
EDITOR_OPTIONS_ROW = 14
EDITOR_STRIP, EDITOR_BINARY = "Color strip", "Binary data"
EDITOR_RECTANGLE = "rectangle"
EDITOR_INSERT_BELOW = "\u2193 \u2193 INSERT YOUR DATA BELOW \u2193 \u2193"
EDITOR_WORKBOOK = "itol_editor.xlsx"


@dataclass
class Strip:
    """One colour strip: a field, each tip's value, and a colour per value."""

    title: str
    values: Dict[str, str]              # tip label -> value ("" = none)
    colors: Dict[str, str]              # value -> hex colour
    names: Dict[str, str] = field(default_factory=dict)   # value -> text for the legend


@dataclass
class Binary:
    """One presence/absence block. `present[column]` is the set of tip labels
    that have the feature; `known` is the tips the block has any result for."""

    title: str
    color: str
    columns: List[str]
    present: Dict[str, set]
    known: set
    column_colors: Dict[str, str] = field(default_factory=dict)


def safe_id(label: str) -> str:
    """A leaf id iTOL and Newick both read without quoting: letters, digits,
    and . - _ |. Anything else becomes an underscore."""
    cleaned = re.sub(r"[^A-Za-z0-9._|-]", "_", label.strip())
    return cleaned or "unnamed"


def leaf_ids(labels: List[str]) -> Dict[str, str]:
    """label -> unique safe id. Two labels that clean to the same id get a
    numbered suffix, so no two tips share an id."""
    ids: Dict[str, str] = {}
    used: Dict[str, int] = {}
    for label in labels:
        base = safe_id(label)
        n = used.get(base, 0)
        used[base] = n + 1
        ids[label] = base if n == 0 else f"{base}_{n + 1}"
    return ids


def to_newick(root: Node, ids: Dict[str, str]) -> str:
    """The rooted tree as Newick, tips written with their safe ids."""
    def write(n: Node) -> str:
        length = f":{n.length:.10g}"
        if n.is_tip:
            return ids.get(n.name, safe_id(n.name)) + length
        return "(" + ",".join(write(c) for c in n.children) + ")" + length

    if root.is_tip:
        return ids.get(root.name, safe_id(root.name)) + ";"
    return "(" + ",".join(write(c) for c in root.children) + ");"


def _clean(text: object) -> str:
    """A value safe inside a TAB-separated iTOL file."""
    return re.sub(r"[\t\r\n]+", " ", str(text)).strip()


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "field"


def colorstrip_text(strip: Strip, ids: Dict[str, str]) -> str:
    order = [v for v in strip.colors if v and any(x == v for x in strip.values.values())]
    lines = ["DATASET_COLORSTRIP", "SEPARATOR TAB",
             f"DATASET_LABEL\t{_clean(strip.title)}", f"COLOR\t{STRIP_COLOR}", "COLOR_BRANCHES\t0",
             f"LEGEND_TITLE\t{_clean(strip.title)}",
             "LEGEND_SHAPES\t" + "\t".join(RECTANGLE for _ in order),
             "LEGEND_COLORS\t" + "\t".join(strip.colors[v] for v in order),
             "LEGEND_LABELS\t" + "\t".join(_clean(strip.names.get(v, v)) for v in order),
             "STRIP_WIDTH\t25", "MARGIN\t5", "BORDER_WIDTH\t0.5", "BORDER_COLOR\t#ffffff",
             "DATA"]
    for label, value in strip.values.items():
        if value and value in strip.colors and label in ids:
            lines.append(f"{ids[label]}\t{strip.colors[value]}\t{_clean(strip.names.get(value, value))}")
    return "\n".join(lines) + "\n"


def binary_text(block: Binary, ids: Dict[str, str]) -> str:
    lines = ["DATASET_BINARY", "SEPARATOR TAB",
             f"DATASET_LABEL\t{_clean(block.title)}", f"COLOR\t{block.color}",
             "FIELD_SHAPES\t" + "\t".join(RECTANGLE for _ in block.columns),
             "FIELD_LABELS\t" + "\t".join(_clean(c) for c in block.columns),
             "FIELD_COLORS\t" + "\t".join(block.column_colors.get(c, block.color) for c in block.columns),
             "MARGIN\t8", "HEIGHT_FACTOR\t0.9", "SYMBOL_SPACING\t2",
             "DATA"]
    # A tip the block has no result for gets no row, so iTOL draws nothing
    # there. A tip with a result gets a filled or an empty rectangle per column.
    for label in ids:
        if label in block.known:
            lines.append(ids[label] + "\t" + "\t".join(
                PRESENT if label in block.present.get(c, set()) else ABSENT for c in block.columns))
    return "\n".join(lines) + "\n"


def labels_text(ids: Dict[str, str]) -> Optional[str]:
    changed = {label: i for label, i in ids.items() if label != i}
    if not changed:
        return None
    lines = ["LABELS", "SEPARATOR TAB", "DATA"] + [f"{i}\t{_clean(label)}" for label, i in changed.items()]
    return "\n".join(lines) + "\n"


def popup_text(ids: Dict[str, str], facts: Dict[str, List[Tuple[str, str]]]) -> str:
    lines = ["POPUP_INFO", "SEPARATOR TAB", "DATA"]
    for label, i in ids.items():
        rows = "".join(f"<tr><td><b>{html.escape(k)}</b></td><td>{html.escape(_clean(v))}</td></tr>"
                       for k, v in facts.get(label, []) if _clean(v))
        if rows:
            lines.append(f"{i}\t{_clean(label)}\t<table>{rows}</table>")
    return "\n".join(lines) + "\n"


def _sheet_name(prefix: str, title: str, taken: set) -> str:
    """A worksheet name Excel accepts: 31 characters, none of []:*?/\\, unique."""
    base = re.sub(r"[\[\]:*?/\\]", " ", f"{prefix} {title}").strip()[:31].strip() or prefix
    name, n = base, 1
    while name.lower() in taken:
        n += 1
        tail = f" {n}"
        name = base[:31 - len(tail)].strip() + tail
    taken.add(name.lower())
    return name


def write_editor_workbook(path: Path, ids: Dict[str, str], strips: List[Strip], binaries: List[Binary],
                          facts: Dict[str, List[Tuple[str, str]]]) -> List[Tuple[str, str]]:
    """Write the annotations as sheets for the iTOL annotation editor.
    Returns (sheet name, dataset type) for each sheet written. Raises
    ImportError when openpyxl is not installed."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    bold = Font(bold=True)
    col = EDITOR_COLUMN
    wb = Workbook()
    guide = wb.active
    guide.title = "How to use"
    taken = {"how to use"}
    sheets: List[Tuple[str, str]] = []

    def swatch(cell, color: str) -> None:
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", color or ""):
            cell.fill = PatternFill("solid", fgColor=color[1:].upper())

    def options(ws, kind: str, label: str, color: str, last: str) -> None:
        ws.cell(1, 1, f"Copy E1:{last} and paste it at E1 in the editor.").font = bold
        ws.cell(2, 1, "Set the dataset options there as in rows 14 to 16 below.")
        row = EDITOR_OPTIONS_ROW
        ws.cell(row - 1, 1, "Dataset options").font = bold
        for offset, (key, value) in enumerate((("Type", kind), ("Label", label), ("Legend color", color))):
            ws.cell(row + offset, 1, key).font = bold
            ws.cell(row + offset, 2, value)
        swatch(ws.cell(row + 2, 2), color)
        ws.column_dimensions["A"].width = 16
        ws.column_dimensions["B"].width = 26
        ws.column_dimensions[get_column_letter(col)].width = 24
        ws.column_dimensions[get_column_letter(col + 1)].width = 24

    for strip in strips:
        rows = [(ids[label], label, strip.colors[value], strip.names.get(value, value))
                for label, value in ((label, strip.values.get(label, "")) for label in ids)
                if value and value in strip.colors]
        if not rows:
            continue
        ws = wb.create_sheet(_sheet_name("strip", strip.title, taken))
        for offset, head in enumerate(("Tree node ID", "Tree node label", "Color", "Label")):
            ws.cell(1, col + offset, head).font = bold
        for r, (tip_id, label, color, name) in enumerate(rows, start=2):
            ws.cell(r, col, tip_id)
            ws.cell(r, col + 1, _clean(label))
            swatch(ws.cell(r, col + 2, color), color)
            ws.cell(r, col + 3, _clean(name))
        options(ws, EDITOR_STRIP, _clean(strip.title), STRIP_COLOR,
                f"{get_column_letter(col + 3)}{len(rows) + 1}")
        sheets.append((ws.title, EDITOR_STRIP))

    for block in binaries:
        tips = [label for label in ids if label in block.known]
        if not (block.columns and tips):
            continue
        ws = wb.create_sheet(_sheet_name("binary", block.title, taken))
        # Three rows define the fields, the fourth is the header, as the editor lays them out.
        for r, (arrow, key) in enumerate(((" \u2192", "Field labels"), ("DEFINE FIELDS \u2192", "Field colors"),
                                          (" \u2192", "Field shapes")), start=1):
            ws.cell(r, col, arrow)
            ws.cell(r, col + 1, key).font = bold
        ws.cell(4, col, "Tree node ID").font = bold
        ws.cell(4, col + 1, "Tree node label").font = bold
        ws.cell(4, col + 2, EDITOR_INSERT_BELOW)
        for c, column in enumerate(block.columns, start=col + 2):
            color = block.column_colors.get(column, block.color)
            ws.cell(1, c, _clean(column))
            swatch(ws.cell(2, c, color), color)
            ws.cell(3, c, EDITOR_RECTANGLE)
        for r, label in enumerate(tips, start=5):
            ws.cell(r, col, ids[label])
            ws.cell(r, col + 1, _clean(label))
            for c, column in enumerate(block.columns, start=col + 2):
                ws.cell(r, c, 1 if label in block.present.get(column, set()) else 0)
        options(ws, EDITOR_BINARY, _clean(block.title), block.color,
                f"{get_column_letter(col + 1 + len(block.columns))}{len(tips) + 4}")
        sheets.append((ws.title, EDITOR_BINARY))

    # One plain table of everything known about each tip, keyed by the tree's
    # ids: the sheet to work from when adding an annotation of one's own.
    keys: List[str] = []
    for label in ids:
        for key, value in facts.get(label, []):
            if key not in keys and _clean(value):
                keys.append(key)
    if keys:
        ws = wb.create_sheet(_sheet_name("all", "fields", taken))
        for c, head in enumerate(["Tree node ID", "Tree node label"] + keys, start=1):
            ws.cell(1, c, _clean(head)).font = bold
        for r, label in enumerate(ids, start=2):
            row = dict(facts.get(label, []))
            ws.cell(r, 1, ids[label])
            ws.cell(r, 2, _clean(label))
            for c, key in enumerate(keys, start=3):
                ws.cell(r, c, _clean(row.get(key, "")))
        ws.column_dimensions["A"].width = 24
        ws.column_dimensions["B"].width = 24
        ws.freeze_panes = "C2"
        sheets.append((ws.title, "table"))

    lines = [
        "Sheets for the iTOL annotation editor (the spreadsheet add-on to iTOL)",
        "",
        "1. Upload the .tree file from this folder to your iTOL account.",
        "2. Open the iTOL annotation editor workbook, log in and select that tree.",
        "3. For each strip or binary sheet here: create a new dataset in the editor of the",
        "   type in cell B14, copy the block that starts at E1 and paste it at E1 in the",
        "   editor, set Label and Legend color as in B15 and B16, then save to iTOL.",
        "",
        "Each sheet keeps the editor's own layout: data from E1, dataset options from A14.",
        "Colors are plain hex values, so change a color here or in the editor.",
        "In a binary sheet 1 is found and 0 is screened and not found. A tip with no result has no row.",
        "",
        "The .txt files in this folder hold the same datasets with their legends. Dropped onto",
        "the tree in iTOL they become datasets the editor can then open and change.",
        "",
        "Sheets in this workbook",
    ] + [f"   {name}: {kind}" for name, kind in sheets]
    for r, text in enumerate(lines, start=1):
        guide.cell(r, 1, text)
    guide.cell(1, 1).font = bold
    guide.column_dimensions["A"].width = 100
    wb.save(path)
    return sheets


README = """Tree and annotation files for iTOL (https://itol.embl.de)

These files are written from the cohort report. Nothing has been uploaded.
Opening them in iTOL sends the tree, the isolate names and the annotations to
iTOL's server, so check that this is acceptable for these isolates first.

To use them
  1. Go to https://itol.embl.de/upload.cgi and upload {tree}.
  2. Open the tree, then drag every other .txt file here except this one onto
     the tree. They can all be dropped at once.
  3. Style and export the figure in iTOL. Without an iTOL subscription the
     annotations are not saved between visits, so export before leaving.
{editor}
What each file is
{files}

Reading the presence/absence blocks
  A filled rectangle means the feature was found. An empty rectangle means the
  isolate was screened and the feature was not found. No rectangle at all
  means there is no result for that isolate.
{low}
With an iTOL subscription, itol_batch.zip (beside this folder) can be sent
through iTOL's batch upload. It holds only the tree and the annotation files,
because batch upload reads every file that is not the tree as an annotation.
"""

README_EDITOR = """
With the iTOL annotation editor (the spreadsheet add-on)
  {workbook} holds the same strips and presence/absence blocks as
  sheets in the editor's own layout. Its first sheet gives the steps. Use it
  to set colors in a spreadsheet, or to add a dataset of your own from the
  table of all fields.
"""


def write_bundle(outdir: Path, name: str, root: Node, tip_labels: List[str], strips: List[Strip],
                 binaries: List[Binary], facts: Dict[str, List[Tuple[str, str]]],
                 low_note: str = "") -> Dict[str, object]:
    """Write the iTOL files into <outdir>/itol/ and zip them beside it.
    Returns the file names written and the id used for each tip."""
    folder = Path(outdir) / "itol"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():
        if old.is_file():
            old.unlink()
    ids = leaf_ids(tip_labels)
    tree_name = f"{_slug(name) or 'cohort'}.tree"
    written: List[Tuple[str, str]] = []

    def put(filename: str, text: str, what: str) -> None:
        (folder / filename).write_text(text, encoding="utf-8")
        written.append((filename, what))

    put(tree_name, to_newick(root, ids) + "\n", "the rooted tree")
    relabel = labels_text(ids)
    if relabel:
        put("labels.txt", relabel, "display names for tips whose id had to be simplified")
    for n, strip in enumerate(strips, start=1):
        if any(strip.values.values()):
            put(f"colorstrip_{n:02d}_{_slug(strip.title)}.txt", colorstrip_text(strip, ids),
                f"colour strip: {strip.title}")
    for n, block in enumerate(binaries, start=1):
        if block.columns and block.known:
            put(f"binary_{n:02d}_{_slug(block.title)}.txt", binary_text(block, ids),
                f"presence/absence: {block.title}")
    if facts:
        put("popup.txt", popup_text(ids, facts), "details shown when a tip is clicked")
    # What iTOL itself reads: the tree and the annotation files, nothing else.
    for_itol = [f for f, _ in written]
    extras: List[str] = []
    editor_note = ""
    try:
        sheets = write_editor_workbook(folder / EDITOR_WORKBOOK, ids, strips, binaries, facts)
        written.append((EDITOR_WORKBOOK, f"sheets for the iTOL annotation editor ({len(sheets)})"))
        extras.append(EDITOR_WORKBOOK)
        editor_note = README_EDITOR.format(workbook=EDITOR_WORKBOOK)
    except ImportError:
        pass        # no openpyxl: the text files still hold every dataset
    listing = "\n".join(f"  {f:38s} {what}" for f, what in written)
    (folder / "README.txt").write_text(
        README.format(tree=tree_name, files=listing, editor=editor_note,
                      low=(f"  {low_note}\n" if low_note else "")),
        encoding="utf-8")
    extras.append("README.txt")
    zip_path = Path(outdir) / "itol_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(folder.iterdir()):
            zf.write(f, arcname=f.name)
    batch_path = Path(outdir) / "itol_batch.zip"
    with zipfile.ZipFile(batch_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in for_itol:
            zf.write(folder / f, arcname=f)
    return {"folder": str(folder), "zip": zip_path.name, "batch_zip": batch_path.name, "tree": tree_name,
            "files": for_itol + extras, "for_itol": for_itol, "editor": EDITOR_WORKBOOK in extras,
            "ids": ids}
