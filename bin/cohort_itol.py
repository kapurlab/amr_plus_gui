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
  README.txt                  how to use the files

and the same files zipped as itol_bundle.zip. The tree file ends in .tree
because iTOL's batch upload needs that; nothing here uploads anything. Sending
a tree to iTOL sends its names and annotations to a server outside the lab,
which is the user's decision to make.

Every file uses SEPARATOR TAB: display names and values may hold spaces and
commas, never tabs. The layouts follow iTOL's own templates.
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
             f"DATASET_LABEL\t{_clean(strip.title)}", "COLOR\t#4C8C8A", "COLOR_BRANCHES\t0",
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

What each file is
{files}

Reading the presence/absence blocks
  A filled rectangle means the feature was found. An empty rectangle means the
  isolate was screened and the feature was not found. No rectangle at all
  means there is no result for that isolate.
{low}
With an iTOL subscription, itol_bundle.zip can be sent as it is through iTOL's
batch upload, which needs the tree file to end in .tree.
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
    listing = "\n".join(f"  {f:38s} {what}" for f, what in written)
    (folder / "README.txt").write_text(
        README.format(tree=tree_name, files=listing, low=(f"  {low_note}\n" if low_note else "")),
        encoding="utf-8")
    zip_path = Path(outdir) / "itol_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(folder.iterdir()):
            zf.write(f, arcname=f.name)
    return {"folder": str(folder), "zip": zip_path.name, "tree": tree_name,
            "files": [f for f, _ in written] + ["README.txt"], "ids": ids}
