"""
Organism detection reads a Kraken2 report the way the reads were placed.

The two saved reports are real ones, from public Salmonella Dublin runs
classified with the 8 GB standard database, cut to rows of 200 reads or more.
That database places most Salmonella reads in the genus and few in a species.
Both runs map to the Dublin reference at more than 97 percent, so neither is a
mixed sample, and both were once called mixed.

What is pinned here:
  * reads that stop at a genus count toward that genus's species;
  * subspecies and strain rows are not added to their species a second time;
  * a sample that really holds two organisms is still flagged;
  * a genus with reads and no species row is named, not dropped;
  * a report with no indentation is read as it stands.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

import detect_organism as do  # noqa: E402
from typing_steps import is_salmonella  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kraken"


def _report(tmp_path: Path, rows: list[tuple[int, int, str, int, str]]) -> Path:
    """A Kraken2 report from (clade_reads, taxon_reads, rank, depth, name)."""
    total = sum(taxon for _, taxon, _, _, _ in rows) or 1
    lines = [f"{100 * clade / total:6.2f}\t{clade}\t{taxon}\t{rank}\t{i + 1}\t{'  ' * depth}{name}"
             for i, (clade, taxon, rank, depth, name) in enumerate(rows)]
    path = tmp_path / "report.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _shares(path: Path) -> dict[str, float]:
    ranked = do.rank_species(do.parse_kraken_report(path))
    total = sum(reads for _, reads in ranked)
    return {name: 100 * reads / total for name, reads in ranked}


def test_genus_level_reads_count_toward_the_species():
    shares = _shares(FIXTURES / "SRR15726404_k2_standard_08gb.report.txt")
    assert shares["Salmonella enterica"] > 90
    assert shares["Phocaeicola dorei"] < 10


def test_a_pure_run_on_a_small_database_is_not_called_mixed():
    for name in ("SRR15726404", "SRR6191060"):
        result = do.resolve(FIXTURES / f"{name}_k2_standard_08gb.report.txt", None, None)
        assert result["dominant_species"] == "Salmonella enterica", name
        assert result["contamination_flag"] is False, name
        assert result["organism_token"] == "Salmonella", name
        assert is_salmonella(result), name


def test_a_few_plasmid_reads_are_not_a_second_species():
    shares = _shares(FIXTURES / "SRR6191060_k2_standard_08gb.report.txt")
    assert shares["Escherichia coli"] < 1


def test_subspecies_rows_are_not_counted_twice(tmp_path):
    path = _report(tmp_path, [
        (1000, 0, "G", 0, "Alpha"),
        (1000, 100, "S", 1, "Alpha one"),
        (900, 900, "S1", 2, "Alpha one subsp. one"),
        (1000, 0, "G", 0, "Beta"),
        (1000, 1000, "S", 1, "Beta two"),
    ])
    assert do.rank_species(do.parse_kraken_report(path)) == [("Alpha one", 1000), ("Beta two", 1000)]


def test_two_organisms_are_still_flagged(tmp_path):
    path = _report(tmp_path, [
        (7000, 6000, "G", 0, "Salmonella"),
        (1000, 1000, "S", 1, "Salmonella enterica"),
        (3000, 0, "G", 0, "Escherichia"),
        (3000, 3000, "S", 1, "Escherichia coli"),
    ])
    result = do.resolve(path, None, None)
    assert result["dominant_pct"] == 70.0 and result["runner_up_pct"] == 30.0
    assert result["contamination_flag"] is True
    assert result["organism_token"] is None


def test_genus_reads_are_shared_in_proportion(tmp_path):
    path = _report(tmp_path, [
        (1000, 700, "G", 0, "Alpha"),
        (200, 200, "S", 1, "Alpha one"),
        (100, 100, "S", 1, "Alpha two"),
    ])
    ranked = dict(do.rank_species(do.parse_kraken_report(path)))
    assert ranked == {"Alpha one": 667, "Alpha two": 333}


def test_a_genus_with_no_species_row_is_kept(tmp_path):
    path = _report(tmp_path, [
        (500, 500, "G", 0, "Alpha"),
        (100, 0, "G", 0, "Beta"),
        (100, 100, "S", 1, "Beta two"),
    ])
    assert do.rank_species(do.parse_kraken_report(path)) == [("Alpha sp.", 500), ("Beta two", 100)]


def test_a_report_with_no_indentation_is_read_as_it_stands(tmp_path):
    path = tmp_path / "flat.txt"
    path.write_text("90.00 900 0 G 1 Alpha\n10.00 100 100 S 2 Alpha one\n"
                    "5.00 50 50 S 3 Beta two\n", encoding="utf-8")
    assert do.rank_species(do.parse_kraken_report(path)) == [("Alpha one", 100), ("Beta two", 50)]
