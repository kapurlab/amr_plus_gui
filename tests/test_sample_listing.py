"""
The sample list is one row per isolate, whatever files the download left.

An SRA run can arrive as three files: the pair, and <run>.fastq.gz holding the
reads with no mate. That third file is not a second isolate.

What is pinned here:
  * beside its complete pair, the unpaired file of a run the folder's SRA
    download record names is not listed as a sample, and the pair's row names it;
  * a file the record does not name is always listed: a long-read file a lab
    put beside an Illumina pair of the same name is a sample in its own right;
  * a run that is single-end only is still listed;
  * with one mate missing, nothing is hidden.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
sys.path.insert(0, str(REPO / "backend"))

from app import main as backend, sra  # noqa: E402


def _touch(folder: Path, *names: str) -> None:
    for name in names:
        (folder / name).write_bytes(b"")


def _downloaded(folder: Path, *runs: str) -> None:
    """Record runs the way the SRA download does."""
    sra.write_crosswalk_tsv(folder, [(run, [run]) for run in runs])


def test_unpaired_reads_beside_a_pair_are_not_a_sample(tmp_path):
    _touch(tmp_path, "SRR1_1.fastq.gz", "SRR1_2.fastq.gz", "SRR1.fastq.gz",
           "SRR2_1.fastq.gz", "SRR2_2.fastq.gz")
    _downloaded(tmp_path, "SRR1", "SRR2")
    rows = backend._list_fastq_pairs(tmp_path)
    assert [r["sample"] for r in rows] == ["SRR1", "SRR2"]
    assert all(r["paired"] for r in rows)
    assert rows[0]["unpaired"] == ["SRR1.fastq.gz"] and rows[1]["unpaired"] == []


def test_a_file_the_download_record_does_not_name_stays_a_sample(tmp_path):
    # Hybrid data named alike: MinION reads beside the Illumina pair.
    _touch(tmp_path, "iso7_R1.fastq.gz", "iso7_R2.fastq.gz", "iso7.fastq.gz",
           "SRR5_1.fastq.gz", "SRR5_2.fastq.gz", "SRR5.fastq.gz")
    rows = backend._list_fastq_pairs(tmp_path)          # no download record at all
    assert sorted(r["r1_name"] for r in rows) == ["SRR5.fastq.gz", "SRR5_1.fastq.gz",
                                                  "iso7.fastq.gz", "iso7_R1.fastq.gz"]
    _downloaded(tmp_path, "SRR5")                       # a record that names only the run
    rows = backend._list_fastq_pairs(tmp_path)
    assert sorted(r["r1_name"] for r in rows) == ["SRR5_1.fastq.gz", "iso7.fastq.gz", "iso7_R1.fastq.gz"]
    assert all(r["unpaired"] == [] for r in rows if r["sample"] == "iso7")


def test_the_download_record_is_read_as_written(tmp_path):
    sra.write_crosswalk_tsv(tmp_path, [("SRX9", ["SRR8", "SRR9"])])
    sra.write_crosswalk_tsv(tmp_path, [("ERR1", ["ERR1"])])
    assert sra.downloaded_runs(tmp_path) == {"SRR8", "SRR9", "ERR1"}
    assert sra.downloaded_runs(tmp_path / "nowhere") == set()


def test_a_single_end_run_is_still_listed(tmp_path):
    _touch(tmp_path, "SRR3.fastq.gz")
    _downloaded(tmp_path, "SRR3")
    rows = backend._list_fastq_pairs(tmp_path)
    assert [(r["sample"], r["paired"], r["r1_name"]) for r in rows] == [("SRR3", False, "SRR3.fastq.gz")]


def test_with_one_mate_missing_nothing_is_hidden(tmp_path):
    _touch(tmp_path, "SRR4_1.fastq.gz", "SRR4.fastq.gz")
    _downloaded(tmp_path, "SRR4")
    rows = backend._list_fastq_pairs(tmp_path)
    assert sorted(r["r1_name"] for r in rows) == ["SRR4.fastq.gz", "SRR4_1.fastq.gz"]
    assert not any(r["paired"] for r in rows)
