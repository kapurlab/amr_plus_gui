#!/usr/bin/env python
"""
cohort_figures.py: the cohort report's figures, as inline SVG text.

Three figures, each a pure function from data to an SVG string, so the report
is one self-contained HTML file and the figures need no plotting library:

  stacked_bars   lineage composition by a metadata group
  tree_matrix    the phylogeny with a presence/absence matrix beside it
  heatmap        pairwise SNP distances, rows and columns in tree order
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from cohort_tree import Node

INK = "#1F2A2E"
MUTED = "#6E7B82"
GRID = "#ECEAE5"
EMPTY = "#EFEDE8"
FONT = "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif"

# Lineage colours: soft, and distinct from one another in order.
LINEAGE_COLORS = ["#D98C9A", "#6FB7AB", "#7C7FBF", "#E3B66B", "#8FB8DE", "#B79AC8",
                  "#A5C48C", "#E0A07E"]
NO_LINEAGE = "#BDBDBD"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def lineage_palette(lineages: Sequence[str]) -> Dict[str, str]:
    """A colour per lineage, commonest first. The empty lineage is grey, and
    lineages past the palette share the grey as "other"."""
    out: Dict[str, str] = {}
    for i, name in enumerate(n for n in lineages if n):
        out[name] = LINEAGE_COLORS[i] if i < len(LINEAGE_COLORS) else NO_LINEAGE
    out[""] = NO_LINEAGE
    return out


def _text_width(text: str, size: float) -> float:
    """A rough width in px; SVG cannot measure text without a renderer."""
    return len(text) * size * 0.58


# ---------------------------------------------------------------------------
# Stacked bars
# ---------------------------------------------------------------------------
def stacked_bars(counts: Dict[str, Dict[str, int]], palette: Dict[str, str],
                 lineage_label: Dict[str, str], title: str) -> str:
    """counts[group][lineage] = number of isolates. Each bar is stacked in
    palette order. Groups that are all numbers (years, weeks, herd sizes) run
    in number order; any other groups are drawn largest first."""
    if not counts:
        return ""

    def as_number(name: str) -> Optional[float]:
        try:
            return float(name)
        except ValueError:
            return None

    numeric = [g for g in counts if as_number(g) is not None]
    if len(numeric) >= 2 and len(numeric) >= len(counts) - 1:
        # At most one group is not a number ("not given"); it goes last.
        groups = sorted(numeric, key=as_number) + [g for g in counts if as_number(g) is None]
    else:
        groups = sorted(counts, key=lambda g: (-sum(counts[g].values()), g))
    order = [k for k in palette if any(counts[g].get(k) for g in groups)]
    top = max(sum(counts[g].values()) for g in groups)
    bar_w, gap, plot_h, left, head = 46, 26, 190, 20, 34
    width = left * 2 + len(groups) * (bar_w + gap)
    legend_w = max(_text_width(lineage_label.get(k, k), 11) for k in order) + 40
    width = max(width, int(len(order) * legend_w) + left * 2,
                int(_text_width(title, 13) * 1.08) + left * 2, 320)
    height = head + plot_h + 64
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'width="{width}" height="{height}" style="{FONT}" role="img" aria-label="{esc(title)}">',
             f'<text x="{left}" y="16" font-size="13" font-weight="600" fill="{INK}">{esc(title)}</text>']
    base = head + plot_h
    for i, g in enumerate(groups):
        x = left + i * (bar_w + gap)
        y = float(base)
        for k in order:
            n = counts[g].get(k, 0)
            if not n:
                continue
            h = plot_h * n / top
            y -= h
            parts.append(f'<rect x="{x}" y="{y:.1f}" width="{bar_w}" height="{h:.1f}" fill="{palette[k]}"/>')
            if h >= 13:
                parts.append(f'<text x="{x + bar_w / 2}" y="{y + h / 2 + 4:.1f}" font-size="11" '
                             f'text-anchor="middle" fill="#fff">{n}</text>')
        parts.append(f'<text x="{x + bar_w / 2}" y="{y - 5:.1f}" font-size="11" text-anchor="middle" '
                     f'fill="{MUTED}">{sum(counts[g].values())}</text>')
        parts.append(f'<text x="{x + bar_w / 2}" y="{base + 16}" font-size="11" font-weight="600" '
                     f'text-anchor="middle" fill="{INK}">{esc(g)}</text>')
    parts.append(f'<line x1="{left - 6}" y1="{base}" x2="{width - left}" y2="{base}" stroke="{MUTED}" stroke-width="1"/>')
    lx = float(left)
    for k in order:
        parts.append(f'<rect x="{lx:.0f}" y="{base + 36}" width="11" height="11" fill="{palette[k]}"/>')
        parts.append(f'<text x="{lx + 16:.0f}" y="{base + 46}" font-size="11" fill="{INK}">'
                     f'{esc(lineage_label.get(k, k))}</text>')
        lx += legend_w
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Tree with a matrix
# ---------------------------------------------------------------------------
@dataclass
class ColumnGroup:
    """One block of matrix columns, such as the resistance determinants."""

    title: str
    color: str
    columns: List[str]
    # values[column] = {tip label: "full" | "low"}; a missing label is absent.
    values: Dict[str, Dict[str, str]] = field(default_factory=dict)
    low_color: Optional[str] = None
    # Tip labels this block has results for. A tip outside it gets no squares
    # at all, so a step that did not run cannot read as "absent". None: all.
    known: Optional[set] = None


def _scale_bar(span: float) -> float:
    """A round length near a fifth of the tree's depth."""
    if span <= 0:
        return 0.0
    raw = span / 5
    power = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * power:
            return m * power
    return 10 * power


def tree_matrix(root: Node, tips: List[Node], tip_text: Dict[str, str],
                tip_lineage: Dict[str, str], palette: Dict[str, str],
                lineage_label: Dict[str, str], groups: List[ColumnGroup],
                title: str, scale_unit: str = "", color_title: str = "") -> str:
    """Draw the rooted, laid-out tree with one matrix row per tip.

    tip_text maps a tip's name to the label drawn; a tip missing from it is
    drawn in grey with its own name (the reference, or a sample the report has
    no typing for). tip_lineage maps a tip's name to its lineage key."""
    row, cell, gap = 15, 12, 3
    tree_w = 250
    labels = {t.name: tip_text.get(t.name, t.name) for t in tips}
    label_w = max(_text_width(v, 10.5) for v in labels.values()) + 10
    col_names = [c for g in groups for c in g.columns] + ([color_title] if color_title else [])
    head = max([_text_width(c, 10) for c in col_names] + [40]) * 0.94 + 46
    left = 12
    x_label = left + tree_w + 8
    x_lineage = x_label + label_w
    x_matrix = x_lineage + cell + 12
    group_gap = 12
    width_matrix = sum(len(g.columns) * (cell + gap) + group_gap for g in groups)
    width = int(x_matrix + width_matrix + 12)
    lineages_present = [k for k in palette if k in set(tip_lineage.values())]
    height = int(head + len(tips) * row + 56)
    depth = max(t.x for t in tips) or 1.0
    sx = (tree_w - 6) / depth

    def yy(v: float) -> float:
        return head + v * row + row / 2

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
         f'height="{height}" style="{FONT}" role="img" aria-label="{esc(title)}">',
         f'<text x="{left}" y="16" font-size="13" font-weight="600" fill="{INK}">{esc(title)}</text>']
    # Row bands, so the eye can follow a tip across to its matrix row.
    for i, t in enumerate(tips):
        if i % 2 == 0:
            p.append(f'<rect x="{x_label - 4:.0f}" y="{head + i * row:.0f}" width="{width - x_label - 4:.0f}" '
                     f'height="{row}" fill="#F7F6F3"/>')

    def draw(n: Node) -> None:
        x1 = left + (n.x - n.length) * sx
        x2 = left + n.x * sx
        p.append(f'<line x1="{x1:.1f}" y1="{yy(n.y):.1f}" x2="{x2:.1f}" y2="{yy(n.y):.1f}" stroke="{INK}" stroke-width="1"/>')
        if n.is_tip:
            p.append(f'<line x1="{x2:.1f}" y1="{yy(n.y):.1f}" x2="{x_label - 6:.1f}" y2="{yy(n.y):.1f}" '
                     f'stroke="{GRID}" stroke-width="1" stroke-dasharray="1 3"/>')
            return
        p.append(f'<line x1="{x2:.1f}" y1="{yy(n.children[0].y):.1f}" x2="{x2:.1f}" '
                 f'y2="{yy(n.children[-1].y):.1f}" stroke="{INK}" stroke-width="1"/>')
        for child in n.children:
            draw(child)

    draw(root)
    for t in tips:
        known = t.name in tip_text
        style = "" if known else ' font-style="italic"'
        p.append(f'<text x="{x_label:.0f}" y="{yy(t.y) + 3.5:.1f}" font-size="10.5" '
                 f'fill="{INK if known else MUTED}"{style}>{esc(labels[t.name])}</text>')
        if t.name in tip_lineage:
            p.append(f'<rect x="{x_lineage:.0f}" y="{yy(t.y) - cell / 2:.1f}" width="{cell}" height="{cell}" '
                     f'rx="2" fill="{palette.get(tip_lineage[t.name], NO_LINEAGE)}"/>')
    if color_title:
        p.append(f'<text transform="translate({x_lineage + cell / 2 + 3:.0f},{head - 8:.0f}) rotate(-70)" '
                 f'font-size="10" fill="{MUTED}">{esc(color_title)}</text>')

    x = float(x_matrix)
    for g in groups:
        x0 = x
        for c in g.columns:
            p.append(f'<text transform="translate({x + cell / 2 + 3:.0f},{head - 22:.0f}) rotate(-70)" '
                     f'font-size="10" fill="{INK}">{esc(c)}</text>')
            present = g.values.get(c, {})
            for t in tips:
                if t.name not in tip_lineage or (g.known is not None and t.name not in g.known):
                    continue
                state = present.get(t.name)
                fill = g.color if state == "full" else (g.low_color or g.color) if state == "low" else EMPTY
                p.append(f'<rect x="{x:.0f}" y="{yy(t.y) - cell / 2:.1f}" width="{cell}" height="{cell}" '
                         f'rx="2" fill="{fill}"/>')
            x += cell + gap
        if g.columns:
            p.append(f'<line x1="{x0:.0f}" y1="{head - 14:.0f}" x2="{x - gap:.0f}" y2="{head - 14:.0f}" '
                     f'stroke="{g.color}" stroke-width="2"/>')
            p.append(f'<text x="{(x0 + x - gap) / 2:.0f}" y="{head - 3:.0f}" font-size="9.5" text-anchor="middle" '
                     f'fill="{g.color}">{esc(g.title)}</text>')
        x += group_gap

    base = head + len(tips) * row + 18
    bar = _scale_bar(depth)
    if bar:
        p.append(f'<line x1="{left}" y1="{base}" x2="{left + bar * sx:.1f}" y2="{base}" stroke="{INK}" stroke-width="1.5"/>')
        p.append(f'<text x="{left}" y="{base + 14}" font-size="10" fill="{MUTED}">{bar:g} {esc(scale_unit)}</text>')
    lx = float(x_label)
    for k in lineages_present:
        text = lineage_label.get(k, k)
        p.append(f'<rect x="{lx:.0f}" y="{base - 6}" width="11" height="11" rx="2" fill="{palette[k]}"/>')
        p.append(f'<text x="{lx + 16:.0f}" y="{base + 4}" font-size="10.5" fill="{INK}">{esc(text)}</text>')
        lx += _text_width(text, 10.5) + 40
    p.append("</svg>")
    return "".join(p)


# ---------------------------------------------------------------------------
# Heatmap
# ---------------------------------------------------------------------------
# Sequential scale for distances, pale yellow through amber to maroon.
_HEAT_STOPS = [(0.0, (255, 250, 222)), (0.35, (247, 205, 110)), (0.7, (201, 96, 60)), (1.0, (112, 16, 44))]


def heat_color(fraction: float) -> str:
    f = min(max(fraction, 0.0), 1.0)
    for (a, ca), (b, cb) in zip(_HEAT_STOPS, _HEAT_STOPS[1:]):
        if f <= b:
            t = 0.0 if b == a else (f - a) / (b - a)
            return "#%02X%02X%02X" % tuple(round(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))
    return "#%02X%02X%02X" % _HEAT_STOPS[-1][1]


def heatmap(labels: List[str], matrix: List[List[Optional[int]]], lineage_colors: List[str],
            title: str, legend_label: str) -> str:
    """Square distance heatmap. Colour is on a square-root scale so that small
    within-lineage distances keep some contrast beside large ones."""
    n = len(labels)
    if n == 0:
        return ""
    cell = max(4.0, min(15.0, 520.0 / n))
    show_labels = cell >= 9
    label_w = (max(_text_width(v, 9.5) for v in labels) + 8) if show_labels else 8
    head = (label_w * 0.94 + 34) if show_labels else 34
    left = 12 + label_w + 8
    side = n * cell
    width = int(left + side + 78)
    height = int(head + side + 16)
    top = max((v for r in matrix for v in r if v is not None), default=0) or 1
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
         f'height="{height}" style="{FONT}" role="img" aria-label="{esc(title)}">',
         f'<text x="12" y="16" font-size="13" font-weight="600" fill="{INK}">{esc(title)}</text>']
    for i in range(n):
        y = head + i * cell
        p.append(f'<rect x="{left - 7:.1f}" y="{y:.1f}" width="4" height="{cell:.1f}" fill="{lineage_colors[i]}"/>')
        p.append(f'<rect x="{left + i * cell:.1f}" y="{head - 7:.1f}" width="{cell:.1f}" height="4" fill="{lineage_colors[i]}"/>')
        if show_labels:
            p.append(f'<text x="{left - 11:.1f}" y="{y + cell / 2 + 3.2:.1f}" font-size="9.5" text-anchor="end" '
                     f'fill="{INK}">{esc(labels[i])}</text>')
            p.append(f'<text transform="translate({left + i * cell + cell / 2 + 3:.1f},{head - 11:.1f}) rotate(-70)" '
                     f'font-size="9.5" fill="{INK}">{esc(labels[i])}</text>')
        for j in range(n):
            v = matrix[i][j]
            fill = "#FFFFFF" if i == j else EMPTY if v is None else heat_color(math.sqrt(v / top))
            p.append(f'<rect x="{left + j * cell:.1f}" y="{y:.1f}" width="{cell + 0.3:.1f}" height="{cell + 0.3:.1f}" fill="{fill}"/>')
    # Legend bar, with ticks at the values the square-root scale puts at each quarter.
    bx, bh = left + side + 22, min(side, 220.0)
    steps = 40
    for s in range(steps):
        f = 1 - s / (steps - 1)
        p.append(f'<rect x="{bx:.0f}" y="{head + s * bh / steps:.1f}" width="12" height="{bh / steps + 0.5:.1f}" fill="{heat_color(f)}"/>')
    for f in (1.0, 0.75, 0.5, 0.25, 0.0):
        value = round(top * f * f)
        p.append(f'<text x="{bx + 17:.0f}" y="{head + (1 - f) * bh + 3.5:.1f}" font-size="9.5" fill="{MUTED}">{value:,}</text>')
    p.append(f'<text transform="translate({bx + 62:.0f},{head + bh / 2:.0f}) rotate(90)" font-size="9.5" '
             f'text-anchor="middle" fill="{MUTED}">{esc(legend_label)}</text>')
    p.append("</svg>")
    return "".join(p)
