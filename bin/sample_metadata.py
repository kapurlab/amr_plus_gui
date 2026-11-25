#!/usr/bin/env python
"""
sample_metadata.py: one metadata sheet per project, filled from any source.

The sheet is <project>/sample_metadata.tsv: a `sample` column and any other
columns. Nothing in it is fixed beyond `sample`, so it serves any organism and
any tool that reads a project folder (the cohort report reads it; a vSNP
metadata sheet can be made from it).

Rows come from sources. A source turns a list of sample names into rows of
{column: value}. Two are built in:

  sra    public records for samples named by an SRA/ENA/DDBJ run accession:
         every attribute of the run's BioSample (strain, collection date,
         location, isolation source, host, serovar, whatever the submitter
         gave), plus the run's instrument and yield.
  sheet  any .tsv, .csv or .xlsx the lab already has (a LIMS export, a
         submission sheet). The column holding the project's sample names is
         found by matching, and every other column is taken as it is.

Adding a source is one class with a `fetch` method, registered in SOURCES.

Merging never destroys what is there:
  - a value already in the sheet is kept, whatever a source says later;
  - a source fills empty cells and adds columns;
  - where a source disagrees with a filled cell, the sheet keeps its value and
    the difference is listed in the result.
sample_metadata.provenance.json records which source filled each column, and
when.

Usage:
  sample_metadata.py --project DIR fetch [--source sra] [--samples A B ...]
  sample_metadata.py --project DIR import --sheet FILE
  sample_metadata.py --project DIR show
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

SHEET_NAME = "sample_metadata.tsv"
PROVENANCE_NAME = "sample_metadata.provenance.json"
KEY = "sample"

# What submitters write when they have nothing to say. Treated as empty, so a
# placeholder never blocks a real value from another source.
NULL_TERMS = {"", "missing", "not collected", "not applicable", "not provided", "unknown",
              "n/a", "na", "none", "null", "-", "restricted access", "not available"}

Rows = Dict[str, Dict[str, str]]
HttpGet = Callable[[str], str]


def is_null(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() in NULL_TERMS


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------
def read_table(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """Read a .tsv, .csv or .xlsx sheet with a header row."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
        rows = [["" if c is None else str(c).strip() for c in r] for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(r)]
        if not rows:
            return [], []
        header = [h for h in rows[0]]
        return header, [dict(zip(header, r)) for r in rows[1:]]
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines:
        return [], []
    delimiter = "\t" if "\t" in lines[0] else ","
    reader = csv.DictReader(lines, delimiter=delimiter)
    rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader]
    return [h.strip() for h in (reader.fieldnames or [])], rows


def load_sheet(project: Path) -> Tuple[List[str], Rows]:
    """The project's sheet as (column order, {sample: row}). Empty if absent."""
    path = Path(project) / SHEET_NAME
    if not path.is_file():
        return [KEY], {}
    header, rows = read_table(path)
    if KEY not in header:
        raise ValueError(f"{path.name} has no '{KEY}' column")
    return header, {r[KEY]: r for r in rows if r.get(KEY)}


def save_sheet(project: Path, header: List[str], rows: Rows) -> Path:
    path = Path(project) / SHEET_NAME
    tmp = path.with_suffix(".tsv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        for sample in sorted(rows):
            writer.writerow([rows[sample].get(c, "") for c in header])
    os.replace(tmp, path)
    return path


def merge(header: List[str], rows: Rows, incoming: Rows, source: str,
          provenance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Merge a source's rows into the sheet in place. Returns what changed:
    {"added_samples", "added_columns", "filled", "conflicts"}.

    A filled cell is never overwritten. A conflict is a filled cell whose value
    differs from what the source offers."""
    provenance = provenance if provenance is not None else {}
    columns = provenance.setdefault("columns", {})
    result: Dict[str, Any] = {"added_samples": [], "added_columns": [], "filled": 0, "conflicts": []}
    for sample in sorted(incoming):
        new = incoming[sample]
        if sample not in rows:
            rows[sample] = {KEY: sample}
            result["added_samples"].append(sample)
        row = rows[sample]
        for column, value in new.items():
            if column == KEY or is_null(value):
                continue
            value = str(value).strip()
            if column not in header:
                header.append(column)
                result["added_columns"].append(column)
            current = row.get(column, "")
            if is_null(current):
                row[column] = value
                result["filled"] += 1
                sources = columns.setdefault(column, [])
                if source not in sources:
                    sources.append(source)
            elif current.strip() != value:
                result["conflicts"].append(
                    {"sample": sample, "column": column, "kept": current, "offered": value, "source": source})
    return result


def load_provenance(project: Path) -> Dict[str, Any]:
    try:
        data = json.loads((Path(project) / PROVENANCE_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_provenance(project: Path, provenance: Dict[str, Any]) -> None:
    (Path(project) / PROVENANCE_NAME).write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Which samples a project has
# ---------------------------------------------------------------------------
_READ_TAG = re.compile(r"(?:_R?[12](?:_\d+)?)?\.(?:fastq|fq)(?:\.gz)?$", re.IGNORECASE)
_FASTA = re.compile(r"\.(?:fasta|fa|fna)(?:\.gz)?$", re.IGNORECASE)
# Folders at the top of a project whose sub-folders are named for samples.
_SAMPLE_FOLDERS = ("amr", "step1", "kraken", "mlst", "ksnp", "irma", "genoflu")


def project_samples(project: Path) -> List[str]:
    """Every sample name the project folder shows: input files in download/,
    and the per-sample folders the suite's tools write."""
    project = Path(project)
    names = set()
    download = project / "download"
    if download.is_dir():
        for f in download.iterdir():
            if f.is_file() or f.is_symlink():
                if _READ_TAG.search(f.name):
                    names.add(_READ_TAG.sub("", f.name))
                elif _FASTA.search(f.name):
                    names.add(_FASTA.sub("", f.name))
    for folder in _SAMPLE_FOLDERS:
        d = project / folder
        if d.is_dir():
            names.update(p.name for p in d.iterdir()
                         if p.is_dir() and not p.name.startswith((".", "_")) and p.name != "vcf_database")
    return sorted(names)


# ---------------------------------------------------------------------------
# Source: public run records (SRA / ENA / DDBJ)
# ---------------------------------------------------------------------------
RUN_ACCESSION = re.compile(r"^[SED]RR\d{5,}$")
_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
_ENA_SEARCH = "https://www.ebi.ac.uk/ena/portal/api/search"
_TOOL = "bdtools_sample_metadata"


def _default_get(url: str) -> str:
    """GET with NCBI's courtesy pause and a few retries on a busy server."""
    last: Optional[Exception] = None
    for wait in (0.0, 1.0, 3.0, 8.0):
        time.sleep(0.4 + wait)
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
    raise RuntimeError(f"request failed: {last}")


def _eutils(endpoint: str, **params: str) -> str:
    """An E-utilities URL. NCBI asks callers to name their tool, and to give a
    contact email and API key when they have them (NCBI_EMAIL, NCBI_API_KEY)."""
    params.setdefault("tool", _TOOL)
    for env, key in (("NCBI_EMAIL", "email"), ("NCBI_API_KEY", "api_key")):
        value = os.environ.get(env, "").strip()
        if value:
            params.setdefault(key, value)
    return _EUTILS + endpoint + "?" + urllib.parse.urlencode(params)


def _chunks(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def parse_biosample_xml(text: str) -> Dict[str, Dict[str, str]]:
    """{BioSample accession: {column: value}} from an efetch BioSample set.

    Every attribute is kept under its harmonized name, or the submitter's own
    name where NCBI has none, so nothing depends on the organism or package."""
    out: Dict[str, Dict[str, str]] = {}
    root = ET.fromstring(text)
    for bs in root.iter("BioSample"):
        accession = bs.get("accession") or ""
        if not accession:
            continue
        row: Dict[str, str] = {"biosample": accession}
        for ident in bs.iter("Id"):
            if ident.get("db_label") == "Sample name" and ident.text:
                row["sample_name"] = ident.text.strip()
            elif ident.get("db") == "SRA" and ident.text:
                row["sra_sample"] = ident.text.strip()
        organism = bs.find(".//Description/Organism")
        if organism is not None:
            name = organism.get("taxonomy_name") or (organism.findtext("OrganismName") or "")
            if name:
                row["organism"] = name.strip()
        for attr in bs.iter("Attribute"):
            name = (attr.get("harmonized_name") or attr.get("attribute_name") or "").strip()
            name = re.sub(r"\s+", "_", name.lower())
            value = (attr.text or "").strip()
            if name and not is_null(value) and name not in row:
                row[name] = value
        out[accession] = row
    return out


def split_location(geo_loc_name: str) -> Dict[str, str]:
    """INSDC writes a location as "Country: region, locality". Split it into
    geo_country and geo_region; a value with no colon is a country alone."""
    if is_null(geo_loc_name):
        return {}
    country, _, rest = geo_loc_name.partition(":")
    out = {"geo_country": country.strip()}
    region = rest.split(",")[0].strip()
    if region:
        out["geo_region"] = region
    return out


class SraSource:
    """Public metadata for samples named by a run accession."""

    name = "sra"
    label = "NCBI BioSample"

    def __init__(self, http_get: Optional[HttpGet] = None, log: Callable[[str], None] = print):
        self.get = http_get or _default_get
        self.log = log

    def applies(self, samples: List[str]) -> List[str]:
        return [s for s in samples if RUN_ACCESSION.match(s)]

    # -- run -> BioSample, with the run's own facts --
    def _runs_from_ena(self, runs: List[str]) -> Dict[str, Dict[str, str]]:
        found: Dict[str, Dict[str, str]] = {}
        fields = "run_accession,sample_accession,instrument_model,library_layout,base_count,read_count"
        for chunk in _chunks(runs, 25):
            query = " OR ".join(f'run_accession="{r}"' for r in chunk)
            url = _ENA_SEARCH + "?" + urllib.parse.urlencode(
                {"result": "read_run", "format": "json", "fields": fields, "query": query})
            try:
                records = json.loads(self.get(url) or "[]")
            except (RuntimeError, ValueError) as exc:
                self.log(f"NOTE: ENA lookup failed for {len(chunk)} run(s): {exc}")
                continue
            for rec in records:
                run = rec.get("run_accession", "")
                if run:
                    found[run] = {
                        "biosample": rec.get("sample_accession", ""),
                        "instrument": rec.get("instrument_model", ""),
                        "library_layout": rec.get("library_layout", ""),
                        "bases": rec.get("base_count", ""),
                        "reads": rec.get("read_count", ""),
                    }
        return found

    def _biosample_from_ncbi(self, run: str) -> str:
        """The BioSample of one run through NCBI's own links, for a run ENA
        does not list yet. Returns "" when NCBI has no link either."""
        try:
            ids = json.loads(self.get(_eutils("esearch.fcgi", db="sra", term=f"{run}[accn]", retmode="json")))
            uids = ids["esearchresult"]["idlist"]
            if not uids:
                return ""
            link = json.loads(self.get(_eutils("elink.fcgi", dbfrom="sra", db="biosample", id=uids[0], retmode="json")))
            linked = [l for ls in link.get("linksets", []) for db in ls.get("linksetdbs", [])
                      for l in db.get("links", [])]
            if not linked:
                return ""
            records = parse_biosample_xml(self.get(_eutils("efetch.fcgi", db="biosample", id=str(linked[0]), retmode="xml")))
            return next(iter(records), "")
        except (RuntimeError, ValueError, KeyError, ET.ParseError) as exc:
            self.log(f"NOTE: NCBI link lookup failed for {run}: {exc}")
            return ""

    def _biosamples(self, accessions: List[str]) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        for chunk in _chunks(sorted(set(a for a in accessions if a)), 40):
            term = " OR ".join(f"{a}[accn]" for a in chunk)
            try:
                found = json.loads(self.get(_eutils("esearch.fcgi", db="biosample", term=term,
                                                    retmode="json", retmax=str(len(chunk) * 2))))
                uids = found["esearchresult"]["idlist"]
                if uids:
                    out.update(parse_biosample_xml(
                        self.get(_eutils("efetch.fcgi", db="biosample", id=",".join(uids), retmode="xml"))))
            except (RuntimeError, ValueError, KeyError, ET.ParseError) as exc:
                self.log(f"NOTE: BioSample lookup failed for {len(chunk)} record(s): {exc}")
        return out

    def fetch(self, samples: List[str]) -> Rows:
        runs = self.applies(samples)
        if not runs:
            return {}
        by_run = self._runs_from_ena(runs)
        for run in runs:
            if not by_run.get(run, {}).get("biosample"):
                biosample = self._biosample_from_ncbi(run)
                if biosample:
                    by_run.setdefault(run, {})["biosample"] = biosample
        records = self._biosamples([r.get("biosample", "") for r in by_run.values()])
        rows: Rows = {}
        for run in runs:
            facts = by_run.get(run)
            if not facts:
                self.log(f"NOTE: no public record found for {run}")
                continue
            row = {"run": run}
            row.update(records.get(facts.get("biosample", ""), {}))
            row.update({k: v for k, v in facts.items() if not is_null(v) and k not in row})
            row.update(split_location(row.get("geo_loc_name", "")))
            rows[run] = row
        self.log(f"{self.label}: records for {len(rows)} of {len(runs)} run accession(s)")
        return rows


# ---------------------------------------------------------------------------
# Source: a sheet the lab already has
# ---------------------------------------------------------------------------
def rows_from_sheet(path: Path, samples: List[str]) -> Tuple[Rows, str, int]:
    """Rows of a user sheet keyed by this project's sample names.

    The key column is the one whose values match the most sample names, so the
    sheet needs no particular header. Returns (rows, key column, rows that
    matched no sample)."""
    header, records = read_table(Path(path))
    wanted = set(samples)
    best, hits = "", 0
    for column in header:
        n = len({r.get(column, "") for r in records} & wanted)
        if n > hits:
            best, hits = column, n
    if not best:
        raise ValueError(f"No column of {Path(path).name} holds this project's sample names.")
    rows: Rows = {}
    unmatched = 0
    for rec in records:
        sample = rec.get(best, "")
        if sample in wanted:
            rows[sample] = {c: v for c, v in rec.items() if c and c != best}
        else:
            unmatched += 1
    return rows, best, unmatched


# A source is a class with `name`, `label`, `applies(samples)` and
# `fetch(samples) -> {sample: {column: value}}`. Register it here.
SOURCES: Dict[str, Any] = {SraSource.name: SraSource}


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _apply(project: Path, incoming: Rows, source_label: str, log: Callable[[str], None]) -> Dict[str, Any]:
    header, rows = load_sheet(project)
    provenance = load_provenance(project)
    result = merge(header, rows, incoming, source_label, provenance)
    provenance.setdefault("history", []).append({
        "when": _stamp(), "source": source_label, "samples": len(incoming),
        "added_columns": result["added_columns"], "filled": result["filled"],
        "conflicts": len(result["conflicts"]),
    })
    save_sheet(project, header, rows)
    save_provenance(project, provenance)
    result.update({"sheet": str(Path(project) / SHEET_NAME), "samples_in_sheet": len(rows),
                   "columns": header})
    log(f"{SHEET_NAME}: {len(rows)} sample(s), {len(header)} column(s); "
        f"{result['filled']} cell(s) filled from {source_label}"
        + (f"; {len(result['conflicts'])} value(s) kept where the source differed" if result["conflicts"] else ""))
    return result


def fetch(project: Path, source: str = "sra", samples: Optional[List[str]] = None,
          http_get: Optional[HttpGet] = None, log: Callable[[str], None] = print) -> Dict[str, Any]:
    """Fill the project's sheet from one registered source."""
    project = Path(project).expanduser().resolve()
    if source not in SOURCES:
        raise ValueError(f"Unknown source '{source}'. Known: {', '.join(sorted(SOURCES))}")
    provider = SOURCES[source](http_get=http_get, log=log)
    names = samples or project_samples(project)
    applicable = provider.applies(names)
    if not applicable:
        log(f"No sample of this project is one the '{source}' source can look up "
            f"({len(names)} sample(s) seen).")
        return {"source": source, "applicable": 0, "fetched": 0, "filled": 0, "conflicts": [],
                "added_columns": [], "added_samples": []}
    incoming = provider.fetch(applicable)
    result = _apply(project, incoming, provider.label, log)
    result.update({"source": source, "applicable": len(applicable), "fetched": len(incoming),
                   "not_found": sorted(set(applicable) - set(incoming))})
    return result


def import_sheet(project: Path, sheet: Path, log: Callable[[str], None] = print) -> Dict[str, Any]:
    """Merge a sheet the lab already has into the project's sheet."""
    project = Path(project).expanduser().resolve()
    incoming, key_column, unmatched = rows_from_sheet(Path(sheet), project_samples(project))
    result = _apply(project, incoming, f"sheet {Path(sheet).name}", log)
    result.update({"key_column": key_column, "unmatched_rows": unmatched})
    if unmatched:
        log(f"NOTE: {unmatched} row(s) of {Path(sheet).name} match no sample in this project.")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The project's sample metadata sheet.")
    ap.add_argument("--project", type=Path, required=True)
    sub = ap.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="fill the sheet from a source")
    f.add_argument("--source", default="sra", choices=sorted(SOURCES))
    f.add_argument("--samples", nargs="*", default=None)
    i = sub.add_parser("import", help="merge a sheet the lab already has")
    i.add_argument("--sheet", type=Path, required=True)
    sub.add_parser("show", help="print what the sheet holds")
    args = ap.parse_args(argv)
    try:
        if args.command == "fetch":
            result = fetch(args.project, args.source, args.samples)
        elif args.command == "import":
            result = import_sheet(args.project, args.sheet)
        else:
            header, rows = load_sheet(args.project)
            print(f"{len(rows)} sample(s); columns: {', '.join(header)}")
            return 0
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}")
        return 1
    for c in result.get("conflicts", [])[:20]:
        print(f"  kept {c['sample']} {c['column']}={c['kept']!r}; {c['source']} has {c['offered']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
