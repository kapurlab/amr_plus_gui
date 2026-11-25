#!/usr/bin/env python
"""
cohort_tree.py: read a Newick tree, root it, and lay it out for drawing.

Used by cohort_report.py to draw a phylogeny beside a per-isolate matrix.
Pure Python with no dependencies, so it runs in any of the suite's envs.

The tree is held as an undirected graph while it is being rooted, then turned
into a rooted `Node` tree. Rooting is on the reference tip when the tree has
one (vSNP names it "root"), else at the midpoint of the longest tip-to-tip
path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Node:
    """One node of a rooted tree. `length` is the branch above the node."""

    name: str = ""
    length: float = 0.0
    children: List["Node"] = field(default_factory=list)
    # Filled by layout(): distance from the root, and the row the node sits on.
    x: float = 0.0
    y: float = 0.0
    # How much of the branch a drawing leaves out (see shorten_long_branches).
    # `length` stays the true length.
    cut: float = 0.0

    @property
    def is_tip(self) -> bool:
        return not self.children

    def tips(self) -> List["Node"]:
        if self.is_tip:
            return [self]
        out: List[Node] = []
        for child in self.children:
            out.extend(child.tips())
        return out


class NewickError(ValueError):
    """The text is not a Newick tree this reader understands."""


def parse_newick(text: str) -> Node:
    """Parse one Newick tree. Branch lengths default to 0; internal node
    labels (support values) are kept as the node's name."""
    s = text.strip()
    if not s:
        raise NewickError("empty tree")
    if s.endswith(";"):
        s = s[:-1]
    pos = 0

    def label() -> str:
        nonlocal pos
        if pos < len(s) and s[pos] == "'":
            end = s.find("'", pos + 1)
            if end < 0:
                raise NewickError("unclosed quote")
            out = s[pos + 1:end]
            pos = end + 1
            return out
        start = pos
        while pos < len(s) and s[pos] not in "(),:;":
            pos += 1
        return s[start:pos].strip()

    def length() -> float:
        nonlocal pos
        if pos < len(s) and s[pos] == ":":
            pos += 1
            start = pos
            while pos < len(s) and s[pos] not in "(),;":
                pos += 1
            try:
                return float(s[start:pos])
            except ValueError:
                raise NewickError(f"bad branch length: {s[start:pos]!r}")
        return 0.0

    def node() -> Node:
        nonlocal pos
        n = Node()
        if pos < len(s) and s[pos] == "(":
            pos += 1
            while True:
                n.children.append(node())
                if pos >= len(s):
                    raise NewickError("unbalanced parentheses")
                if s[pos] == ",":
                    pos += 1
                    continue
                if s[pos] == ")":
                    pos += 1
                    break
                raise NewickError(f"unexpected {s[pos]!r} at {pos}")
        n.name = label()
        n.length = max(0.0, length())
        return n

    root = node()
    if pos != len(s):
        raise NewickError(f"trailing text at {pos}")
    return root


# ---------------------------------------------------------------------------
# Rooting, on an undirected graph
# ---------------------------------------------------------------------------
Graph = Dict[int, Dict[int, float]]


def _to_graph(root: Node) -> Tuple[Graph, Dict[int, str]]:
    graph: Graph = {}
    names: Dict[int, str] = {}
    counter = [0]

    def walk(n: Node, parent: Optional[int]) -> None:
        me = counter[0]
        counter[0] += 1
        graph[me] = {}
        names[me] = n.name if n.is_tip else ""
        if parent is not None:
            graph[me][parent] = n.length
            graph[parent][me] = n.length
        for child in n.children:
            walk(child, me)

    walk(root, None)
    # A root with two children is not a real node of an unrooted tree: join
    # its two branches into one, so later rooting can land anywhere on it.
    if len(graph[0]) == 2:
        (a, la), (b, lb) = graph[0].items()
        del graph[a][0], graph[b][0], graph[0]
        graph[a][b] = graph[b][a] = la + lb
        names.pop(0, None)
    return graph, names


def _from_graph(graph: Graph, names: Dict[int, str], root_id: int) -> Node:
    def build(me: int, parent: Optional[int], length: float) -> Node:
        n = Node(name=names.get(me, ""), length=length)
        for other, d in graph[me].items():
            if other != parent:
                n.children.append(build(other, me, d))
        return n

    return build(root_id, None, 0.0)


def _split_edge(graph: Graph, names: Dict[int, str], u: int, v: int, from_u: float) -> int:
    """Put a new node on edge u-v, `from_u` along it, and return its id."""
    total = graph[u][v]
    from_u = min(max(from_u, 0.0), total)
    new = max(graph) + 1
    del graph[u][v], graph[v][u]
    graph[new] = {u: from_u, v: total - from_u}
    graph[u][new] = from_u
    graph[v][new] = total - from_u
    names[new] = ""
    return new


def _farthest(graph: Graph, start: int) -> Tuple[int, float, Dict[int, Optional[int]]]:
    dist = {start: 0.0}
    prev: Dict[int, Optional[int]] = {start: None}
    stack = [start]
    while stack:
        me = stack.pop()
        for other, d in graph[me].items():
            if other not in dist:
                dist[other] = dist[me] + d
                prev[other] = me
                stack.append(other)
    far = max(dist, key=lambda k: dist[k])
    return far, dist[far], prev


def root_tree(tree: Node, outgroup: Optional[str] = None) -> Tuple[Node, str]:
    """Root the tree and say how: "outgroup" or "midpoint".

    With `outgroup` naming a tip, the root goes on that tip's branch, at the
    tip's end, so the outgroup hangs from the root by its full branch.
    Otherwise the root goes at the midpoint of the longest tip-to-tip path."""
    graph, names = _to_graph(tree)
    if len(graph) < 2:
        return tree, "single"
    if outgroup:
        tip = next((i for i, n in names.items() if n == outgroup and len(graph[i]) == 1), None)
        if tip is not None:
            (neighbour, length), = graph[tip].items()
            new = _split_edge(graph, names, neighbour, tip, length)
            return _from_graph(graph, names, new), "outgroup"
    a, _, _ = _farthest(graph, next(iter(graph)))
    b, span, prev = _farthest(graph, a)
    # Walk back from b until half the span is covered.
    walked, node = 0.0, b
    while prev[node] is not None:
        parent = prev[node]
        step = graph[node][parent]
        if walked + step >= span / 2:
            new = _split_edge(graph, names, node, parent, span / 2 - walked)
            return _from_graph(graph, names, new), "midpoint"
        walked += step
        node = parent
    return _from_graph(graph, names, node), "midpoint"


# ---------------------------------------------------------------------------
# Ordering and layout
# ---------------------------------------------------------------------------
def ladderize(node: Node) -> Node:
    """Order each node's children by clade size, smallest first, so the tree
    reads as a ladder. Ties keep a stable order by tip name."""
    for child in node.children:
        ladderize(child)
    node.children.sort(key=lambda c: (len(c.tips()), min(t.name for t in c.tips())))
    return node


def layout(root: Node) -> List[Node]:
    """Set x (drawn distance from the root) and y (row) on every node. Tips
    take rows 0, 1, 2, ... top to bottom; an internal node sits midway between
    its first and last child. Returns the tips in row order."""
    tips: List[Node] = []

    def place(n: Node, x: float) -> None:
        n.x = x + n.length - n.cut
        if n.is_tip:
            n.y = float(len(tips))
            tips.append(n)
            return
        for child in n.children:
            place(child, n.x)
        n.y = (n.children[0].y + n.children[-1].y) / 2

    root.length = 0.0
    place(root, 0.0)
    return tips


def drop_outgroup(root: Node, outgroup: str) -> Node:
    """Take the outgroup tip off a tree rooted on it, leaving the ingroup.

    The outgroup is what places the root; drawing it as well spends most of
    the figure's width on the one long branch out to it and squeezes the
    isolates, which are what the figure is for."""
    rest = [c for c in root.children if not (c.is_tip and c.name == outgroup)]
    if len(rest) == len(root.children) or not rest:
        return root
    if len(rest) == 1:
        new_root = rest[0]
        new_root.length = 0.0
        return new_root
    root.children = rest
    return root


# A branch this many times deeper than everything else in the tree is drawn
# shortened, to this share of that depth.
LONG_BRANCH_FACTOR = 5.0
LONG_BRANCH_DRAWN = 0.5


def _nodes(root: Node) -> List[Node]:
    out = [root]
    for child in root.children:
        out.extend(_nodes(child))
    return out


def _drawn_depth(n: Node, ignore: Node) -> float:
    """Longest drawn path from n down to a tip, with one branch counted as 0."""
    below = max((_drawn_depth(c, ignore) for c in n.children), default=0.0)
    return below + (0.0 if n is ignore else n.length - n.cut)


def shorten_long_branches(root: Node) -> List[Node]:
    """Mark the branches that would flatten the rest of the tree.

    One isolate far from the others (another lineage, or a mislabelled
    record) takes the whole width of a drawing and leaves every other branch
    too short to see. A branch more than LONG_BRANCH_FACTOR times the depth of
    the rest of the tree gets a `cut`, so layout() draws it short. Its true
    `length` is untouched. Returns the marked nodes, longest first."""
    marked: List[Node] = []
    below_root = [n for n in _nodes(root) if n is not root]
    for _ in range(3):
        longest = max((n for n in below_root if not n.cut), key=lambda n: n.length, default=None)
        if longest is None or longest.length <= 0:
            break
        rest = max((_drawn_depth(c, longest) for c in root.children), default=0.0)
        if rest <= 0 or longest.length <= LONG_BRANCH_FACTOR * rest:
            break
        longest.cut = longest.length - LONG_BRANCH_DRAWN * rest
        marked.append(longest)
    return marked


def cut_branches(root: Node) -> List[Node]:
    """The nodes whose branch is drawn shortened, longest first."""
    return sorted((n for n in _nodes(root) if n.cut), key=lambda n: -n.length)


def prepared(text: str, outgroup: Optional[str] = None,
             keep_outgroup: bool = False) -> Tuple[Node, List[Node], str]:
    """Parse, root, ladderize and lay out a Newick tree in one call. A tree
    rooted on its outgroup has that tip removed unless keep_outgroup is set.
    A branch far longer than the rest of the tree is laid out shortened."""
    root, how = root_tree(parse_newick(text), outgroup)
    if how == "outgroup" and outgroup and not keep_outgroup:
        root = drop_outgroup(root, outgroup)
    ladderize(root)
    shorten_long_branches(root)
    return root, layout(root), how
