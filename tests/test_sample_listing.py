"""
The sample list is one row per isolate, whatever files the download left.

An SRA run can arrive as three files: the pair, and <run>.fastq.gz holding the
reads with no mate. That third file is not a second isolate.

What is pinned here:
  * beside a complete pair, the unpaired file is not listed as a sample, and
    the pair's row names it;
  * a run that is single-end only is still listed;
  * with one mate missing, nothing is hidden.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
sys.path.insert(0, str(REPO / "backend"))

from app import main as backend  # noqa: E402


def _touch(folder: Path, *names: str) -> None:
    for name in names:
        (folder / name).write_bytes(b"")


def test_unpaired_reads_beside_a_pair_are_not_a_sample(tmp_path):
    _touch(tmp_path, "SRR1_1.fastq.gz", "SRR1_2.fastq.gz", "SRR1.fastq.gz",
           "SRR2_1.fastq.gz", "SRR2_2.fastq.gz")
    rows = backend._list_fastq_pairs(tmp_path)
    assert [r["sample"] for r in rows] == ["SRR1", "SRR2"]
    assert all(r["paired"] for r in rows)
    assert rows[0]["unpaired"] == ["SRR1.fastq.gz"] and rows[1]["unpaired"] == []


def test_a_single_end_run_is_still_listed(tmp_path):
    _touch(tmp_path, "SRR3.fastq.gz")
    rows = backend._list_fastq_pairs(tmp_path)
    assert [(r["sample"], r["paired"], r["r1_name"]) for r in rows] == [("SRR3", False, "SRR3.fastq.gz")]


def test_with_one_mate_missing_nothing_is_hidden(tmp_path):
    _touch(tmp_path, "SRR4_1.fastq.gz", "SRR4.fastq.gz")
    rows = backend._list_fastq_pairs(tmp_path)
    assert sorted(r["r1_name"] for r in rows) == ["SRR4.fastq.gz", "SRR4_1.fastq.gz"]
    assert not any(r["paired"] for r in rows)
