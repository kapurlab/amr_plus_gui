#!/usr/bin/env python
"""
typing_steps.py: assembly-based typing steps that run after AMRFinderPlus.

Steps (each reuses the assembly the pipeline already made):
  - SISTR and SeqSero2: Salmonella serovar, called two independent ways.
    SISTR also reports a cgMLST sequence type and its own QC verdict.
  - abricate with the PlasmidFinder and VFDB databases: plasmid replicons and
    virulence genes.
  - CheckM2: genome completeness and contamination, for any organism.

The serovar and abricate steps run only for Salmonella. CheckM2 is not
organism-specific and runs whenever it is installed.

Every tool lives outside the main conda env, because their recipes pin
versions the main env cannot share. A tool is looked up under one "typing
root" (see find_tool). A tool that is not installed is skipped with a note,
and a tool that fails is reported as a warning. Neither stops the run.

Artifacts written to the sample's output directory:
  sistr.json, seqsero2.tsv, abricate_plasmidfinder.tsv, abricate_vfdb.tsv,
  checkm2.tsv, and typing.json (the summary the report reads).
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

Log = Callable[[str], None]

@dataclass(frozen=True)
class Screen:
    """One abricate database screen: where its hits go and its cutoffs (%)."""

    key: str
    min_identity: int
    min_coverage: int
    # Hits below this identity are kept but marked low identity. None: no mark.
    confident_identity: Optional[int] = None


# The databases abricate is asked for.
#   plasmidfinder: reported from 80% identity and 60% coverage, and marked low
#     identity below 95%, the cutoff PlasmidFinder itself applies to
#     Enterobacterales replicons. Both ends matter. In 36 Salmonella Dublin
#     isolates a 95% floor hid real small Col plasmids (92% and 85% identity),
#     while an unmarked 80% floor listed a neighbouring IncFII variant at 85%
#     beside the true IncFII(S) at 98% as if the two were equally sure.
#   vfdb: abricate's defaults, 80% identity and 80% coverage.
ABRICATE_DATABASES: Dict[str, Screen] = {
    "plasmidfinder": Screen("plasmid_replicons", min_identity=80, min_coverage=60,
                            confident_identity=95),
    "vfdb": Screen("virulence_genes", min_identity=80, min_coverage=80),
}


# ---------------------------------------------------------------------------
# Locating the tools
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Tool:
    """An executable and the conda env that owns it."""

    exe: Path
    env_dir: Path

    def environ(self) -> Dict[str, str]:
        """This process's environment with the tool's env bin first on PATH,
        so the helpers a tool shells out to (blastn, mash, prodigal, diamond)
        come from the env the tool was solved with."""
        env = dict(os.environ)
        env["PATH"] = f"{self.env_dir / 'bin'}{os.pathsep}{env.get('PATH', '')}"
        env["CONDA_PREFIX"] = str(self.env_dir)
        return env


def default_typing_root() -> str:
    """Where the typing envs are installed on this machine, or "".

    BDTOOLS_TYPING_ROOT wins. Otherwise a `typing` folder beside the suite's
    tool checkouts, found the same way the pipeline finds its sibling tools.
    No site path is written here."""
    explicit = os.environ.get("BDTOOLS_TYPING_ROOT", "").strip()
    if explicit:
        return explicit
    candidates: List[Path] = []
    tools_root = os.environ.get("BDTOOLS_TOOLS_ROOT", "").strip()
    if tools_root:
        candidates.append(Path(tools_root) / "typing")
    candidates.append(Path(__file__).resolve().parents[2] / "typing")
    for cand in candidates:
        try:
            if cand.is_dir():
                return str(cand)
        except OSError:
            continue
    return ""


def find_tool(typing_root: Optional[str], exe: str, env_name: str) -> Optional[Tool]:
    """Find `exe` under the typing root.

    Two layouts are accepted, so a site can install one shared env or one env
    per tool (the recipes do not always solve together):
      <root>/<env_name>/bin/<exe>   one env per tool
      <root>/env/bin/<exe>          one shared env
      <root>/bin/<exe>              the root is itself an env
    """
    if not typing_root:
        return None
    root = Path(typing_root)
    for env_dir in (root / env_name, root / "env", root):
        candidate = env_dir / "bin" / exe
        try:
            if candidate.is_file():
                return Tool(exe=candidate, env_dir=env_dir)
        except OSError:
            continue
    return None


def is_salmonella(detection: Dict[str, Any]) -> bool:
    """True when the resolved organism is Salmonella.

    Reads the Kraken species first and falls back to the resolved --organism
    token, so the check still works for an assembly input or --no-kraken."""
    for key in ("dominant_species", "organism_token"):
        if "salmonella" in str(detection.get(key) or "").lower():
            return True
    return False


# ---------------------------------------------------------------------------
# Parsers (pure functions; the tests exercise these)
# ---------------------------------------------------------------------------
def parse_sistr_json(text: str) -> Dict[str, Any]:
    """Reduce SISTR's JSON output (a one-element list) to the reported fields."""
    data = json.loads(text)
    rec = data[0] if isinstance(data, list) and data else data
    if not isinstance(rec, dict):
        return {}
    return {
        "serovar": rec.get("serovar"),
        "serovar_antigen": rec.get("serovar_antigen"),
        "serovar_cgmlst": rec.get("serovar_cgmlst"),
        "serogroup": rec.get("serogroup"),
        "antigenic_formula": rec.get("antigenic_formula"),
        "o_antigen": rec.get("o_antigen"),
        "h1": rec.get("h1"),
        "h2": rec.get("h2"),
        "cgmlst_st": rec.get("cgmlst_ST"),
        "cgmlst_matching_alleles": rec.get("cgmlst_matching_alleles"),
        "cgmlst_found_loci": rec.get("cgmlst_found_loci"),
        "cgmlst_distance": rec.get("cgmlst_distance"),
        "cgmlst_genome_match": rec.get("cgmlst_genome_match"),
        "cgmlst_subspecies": rec.get("cgmlst_subspecies"),
        "qc_status": rec.get("qc_status"),
        "qc_messages": rec.get("qc_messages"),
    }


def parse_seqsero2_tsv(text: str) -> Dict[str, Any]:
    """Reduce SeqSero2's SeqSero_result.tsv (header plus one row)."""
    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
    if not rows:
        return {}
    row = {(k or "").strip(): (v or "").strip() for k, v in rows[0].items()}

    def pick(prefix: str) -> Optional[str]:
        for key, value in row.items():
            if key.lower().startswith(prefix.lower()):
                return value or None
        return None

    return {
        "serovar": pick("Predicted serotype"),
        "antigenic_profile": pick("Predicted antigenic profile"),
        "identification": pick("Predicted identification"),
        "o_antigen": pick("O antigen prediction"),
        "h1": pick("H1 antigen prediction"),
        "h2": pick("H2 antigen prediction"),
        "note": pick("Note"),
    }


def parse_abricate_tab(text: str) -> List[Dict[str, Any]]:
    """Read abricate's tab output into one dict per hit."""
    hits: List[Dict[str, Any]] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return hits
    header = [h.lstrip("#").strip() for h in lines[0].split("\t")]
    for line in lines[1:]:
        row = dict(zip(header, line.split("\t")))

        def num(key: str) -> Optional[float]:
            try:
                return float(row.get(key, ""))
            except (TypeError, ValueError):
                return None

        hits.append({
            "gene": replicon_name(row.get("GENE", "")) if row.get("DATABASE") == "plasmidfinder"
            else row.get("GENE", ""),
            "allele": row.get("GENE", ""),
            "contig": row.get("SEQUENCE", ""),
            "start": row.get("START", ""),
            "end": row.get("END", ""),
            "coverage": num("%COVERAGE"),
            "identity": num("%IDENTITY"),
            "accession": row.get("ACCESSION", ""),
            "product": row.get("PRODUCT", ""),
        })
    return hits


# A low-identity hit this close to a confident hit of the same replicon family
# is taken as a second, weaker match to that replicon.
BESIDE_WINDOW_BP = 1000


def _family(name: str) -> str:
    """IncFII(S) and IncFII(pAR0022) -> IncFII; Col(pHAD28) -> Col."""
    return name.split("(")[0].strip()


def _position(hit: Dict[str, Any]) -> Optional[int]:
    try:
        return (int(hit["start"]) + int(hit["end"])) // 2
    except (KeyError, TypeError, ValueError):
        return None


def mark_low_identity(hits: List[Dict[str, Any]], screen: Screen) -> List[Dict[str, Any]]:
    """Set two fields on each hit.

    low_identity: True when the screen has a confident cutoff and the hit's
      identity is under it.
    beside: for a low-identity hit, the confident hit of the same family on
      the same contig within BESIDE_WINDOW_BP, else None. Such a hit is a weak
      second match to a replicon already named (in Salmonella Dublin, an
      IncFII variant at 85% next to IncFII(S) at 98% in every isolate), so the
      summaries leave it out. It stays in typing.json and in the report table."""
    for hit in hits:
        identity = hit.get("identity")
        hit["low_identity"] = bool(
            screen.confident_identity is not None
            and identity is not None and identity < screen.confident_identity)
        hit["beside"] = None
    confident = [h for h in hits if not h["low_identity"]]
    for hit in hits:
        if not hit["low_identity"]:
            continue
        here = _position(hit)
        for other in confident:
            there = _position(other)
            if (here is not None and there is not None
                    and other.get("contig") == hit.get("contig")
                    and _family(other.get("gene", "")) == _family(hit.get("gene", ""))
                    and abs(here - there) <= BESIDE_WINDOW_BP):
                hit["beside"] = other.get("gene")
                break
    return hits


def replicon_name(allele: str) -> str:
    """The replicon a PlasmidFinder database entry belongs to.

    Entries are named <replicon>_<n>, for example IncFII(S)_1. PlasmidFinder
    reports the part before the variant number, and so does this."""
    head, sep, tail = allele.rpartition("_")
    return head if sep and tail.isdigit() and head else allele


def parse_checkm2_tsv(text: str) -> Dict[str, Any]:
    """Read the single data row of CheckM2's quality_report.tsv."""
    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
    if not rows:
        return {}
    row = rows[0]

    def num(key: str) -> Optional[float]:
        try:
            return float(row.get(key, ""))
        except (TypeError, ValueError):
            return None

    return {
        "completeness": num("Completeness"),
        "contamination": num("Contamination"),
        "model": row.get("Completeness_Model_Used") or None,
        "coding_density": num("Coding_Density"),
    }


def _serovar_names(value: Optional[str]) -> List[str]:
    """Split a serovar call into its alternatives, lower-cased.

    Both tools join the candidates with "|" when the antigens fit more than
    one serovar, and SeqSero2 can append a parenthetical note."""
    if not value:
        return []
    names = []
    for part in str(value).split("|"):
        name = part.split("(")[0].strip().lower()
        if name and name not in ("-", "n/a", "na"):
            names.append(name)
    return names


def serovar_agreement(sistr: Dict[str, Any], seqsero2: Dict[str, Any]) -> Dict[str, Any]:
    """Compare the two serovar calls.

    agreement is one of:
      agree       both tools gave the same single serovar
      consistent  the calls overlap, but at least one tool listed alternatives
      disagree    both tools gave a call and they share no serovar
      single      only one tool gave a call
      none        neither tool gave a call
    `serovar` is filled only for agree and single."""
    a = _serovar_names(sistr.get("serovar"))
    b = _serovar_names(seqsero2.get("serovar"))
    if not a and not b:
        return {"agreement": "none", "serovar": None}
    if not a or not b:
        only = sistr.get("serovar") if a else seqsero2.get("serovar")
        return {"agreement": "single", "serovar": only}
    shared = [name for name in a if name in b]
    if not shared:
        return {"agreement": "disagree", "serovar": None}
    if len(a) == 1 and len(b) == 1:
        return {"agreement": "agree", "serovar": sistr.get("serovar")}
    return {"agreement": "consistent", "serovar": None}


# ---------------------------------------------------------------------------
# Running the tools
# ---------------------------------------------------------------------------
def _run(cmd: List[Any], tool: Tool, log: Log, stdout_path: Optional[Path] = None,
         timeout: int = 3600) -> int:
    """Run one command with the tool's env first on PATH. Returns the exit code."""
    argv = [str(c) for c in cmd]
    log(f"$ {' '.join(argv)}" + (f" > {stdout_path.name}" if stdout_path else ""))
    try:
        if stdout_path is not None:
            with open(stdout_path, "w", encoding="utf-8") as out:
                proc = subprocess.run(argv, env=tool.environ(), stdout=out, timeout=timeout)
        else:
            proc = subprocess.run(argv, env=tool.environ(), timeout=timeout)
        return proc.returncode
    except FileNotFoundError:
        log(f"ERROR: command not found: {argv[0]}")
        return 127
    except subprocess.TimeoutExpired:
        log(f"ERROR: timed out after {timeout} s: {argv[0]}")
        return 124


def _version(tool: Tool, args: List[str]) -> Optional[str]:
    try:
        proc = subprocess.run([str(tool.exe)] + args, env=tool.environ(),
                              capture_output=True, text=True, timeout=60)
        out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        return out.splitlines()[-1].strip() if out else None
    except (subprocess.SubprocessError, OSError):
        return None


def _fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_sistr(assembly: Path, outdir: Path, typing_root: Optional[str], threads: int,
              log: Log) -> Optional[Dict[str, Any]]:
    tool = find_tool(typing_root, "sistr", "sistr")
    if tool is None:
        log("NOTE: SISTR is not installed under the typing root; skipping.")
        return None
    work = _fresh_dir(outdir / "_sistr")
    prefix = work / "sistr"
    # --qc adds SISTR's own verdict on the assembly (genome size, cgMLST loci
    # found), which the report shows beside the serovar.
    rc = _run([tool.exe, "--qc", "-f", "json", "-o", prefix, "-t", threads,
               "-T", work / "tmp", assembly], tool, log)
    produced = Path(str(prefix) + ".json")
    if rc != 0 or not produced.is_file():
        log(f"WARNING: SISTR failed (rc={rc}); no SISTR serovar for this sample.")
        return None
    shutil.copyfile(produced, outdir / "sistr.json")
    try:
        result = parse_sistr_json(produced.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"WARNING: SISTR output could not be read: {exc}")
        return None
    result["version"] = _version(tool, ["--version"])
    log(f"  SISTR: serovar {result.get('serovar')}, cgMLST {result.get('cgmlst_matching_alleles')} "
        f"of {result.get('cgmlst_found_loci')} alleles match the nearest genome, "
        f"QC {result.get('qc_status')}")
    return result


def run_seqsero2(assembly: Path, outdir: Path, typing_root: Optional[str], threads: int,
                 log: Log) -> Optional[Dict[str, Any]]:
    tool = find_tool(typing_root, "SeqSero2_package.py", "seqsero2")
    if tool is None:
        log("NOTE: SeqSero2 is not installed under the typing root; skipping.")
        return None
    work = outdir / "_seqsero2"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    # -t 4 is "genome assembly" input and -m k is the k-mer workflow, the only
    # one SeqSero2 supports for assemblies. SeqSero2 creates -d itself.
    rc = _run([tool.exe, "-m", "k", "-t", "4", "-i", assembly, "-d", work,
               "-n", outdir.name, "-p", threads], tool, log)
    produced = work / "SeqSero_result.tsv"
    if rc != 0 or not produced.is_file():
        log(f"WARNING: SeqSero2 failed (rc={rc}); no SeqSero2 serovar for this sample.")
        return None
    shutil.copyfile(produced, outdir / "seqsero2.tsv")
    try:
        result = parse_seqsero2_tsv(produced.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        log(f"WARNING: SeqSero2 output could not be read: {exc}")
        return None
    result["version"] = _version(tool, ["--version"])
    log(f"  SeqSero2: serovar {result.get('serovar')} ({result.get('antigenic_profile')})")
    return result


def run_abricate(assembly: Path, outdir: Path, typing_root: Optional[str], database: str,
                 threads: int, log: Log) -> Optional[List[Dict[str, Any]]]:
    """Screen the assembly against one abricate database.

    Returns the hits (an empty list is a real negative result), or None when
    abricate is not installed or failed."""
    tool = find_tool(typing_root, "abricate", "abricate")
    if tool is None:
        log(f"NOTE: abricate is not installed under the typing root; skipping {database}.")
        return None
    screen = ABRICATE_DATABASES[database]
    out = outdir / f"abricate_{database}.tsv"
    rc = _run([tool.exe, "--db", database, "--minid", screen.min_identity,
               "--mincov", screen.min_coverage, "--threads", threads, "--nopath",
               assembly], tool, log, stdout_path=out)
    if rc != 0:
        log(f"WARNING: abricate --db {database} failed (rc={rc}).")
        return None
    hits = mark_low_identity(
        parse_abricate_tab(out.read_text(encoding="utf-8", errors="replace")), screen)
    low = sum(1 for h in hits if h["low_identity"] and not h["beside"])
    beside = sum(1 for h in hits if h["beside"])
    log(f"  abricate {database}: {len(hits)} hit(s)"
        + (f", {low} below {screen.confident_identity}% identity" if low else "")
        + (f", {beside} weak second match(es) beside a confident hit" if beside else ""))
    return hits


def _checkm2_database(checkm2_db: Optional[str]) -> Optional[Path]:
    """The DIAMOND database file, given the file or a folder that holds it."""
    if not checkm2_db:
        return None
    path = Path(checkm2_db)
    try:
        if path.is_file():
            return path
        if path.is_dir():
            found = sorted(path.rglob("*.dmnd"))
            return found[0] if found else None
    except OSError:
        return None
    return None


def run_checkm2(assembly: Path, outdir: Path, typing_root: Optional[str],
                checkm2_db: Optional[str], threads: int, log: Log) -> Optional[Dict[str, Any]]:
    tool = find_tool(typing_root, "checkm2", "checkm2")
    if tool is None:
        log("NOTE: CheckM2 is not installed under the typing root; skipping.")
        return None
    database = _checkm2_database(checkm2_db)
    if database is None:
        log(f"NOTE: no CheckM2 database at {checkm2_db or '(not configured)'}; skipping.")
        return None
    work = outdir / "_checkm2"
    rc = _run([tool.exe, "predict", "--input", assembly, "--output-directory", work,
               "--database_path", database, "--threads", threads, "--force"], tool, log)
    produced = work / "quality_report.tsv"
    if rc != 0 or not produced.is_file():
        log(f"WARNING: CheckM2 failed (rc={rc}); no completeness estimate.")
        return None
    shutil.copyfile(produced, outdir / "checkm2.tsv")
    result = parse_checkm2_tsv(produced.read_text(encoding="utf-8", errors="replace"))
    result["version"] = _version(tool, ["--version"])
    result["database"] = database.name
    log(f"  CheckM2: completeness {result.get('completeness')}%, "
        f"contamination {result.get('contamination')}%")
    return result


def _abricate_db_versions(typing_root: Optional[str]) -> Dict[str, str]:
    """{database: date} from `abricate --list`, for the methods table."""
    tool = find_tool(typing_root, "abricate", "abricate")
    if tool is None:
        return {}
    try:
        proc = subprocess.run([str(tool.exe), "--list"], env=tool.environ(),
                              capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return {}
    versions: Dict[str, str] = {}
    for line in (proc.stdout or "").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) >= 4 and cols[0] in ABRICATE_DATABASES:
            versions[cols[0]] = cols[3].strip()
    return versions


# ---------------------------------------------------------------------------
# Entry point used by amr_pipeline.py
# ---------------------------------------------------------------------------
def run_all(
    assembly: Path,
    outdir: Path,
    detection: Dict[str, Any],
    typing_root: Optional[str],
    checkm2_db: Optional[str],
    threads: int,
    log: Log,
    run_serovar: bool = True,
    run_gene_screens: bool = True,
    run_completeness: bool = True,
) -> Dict[str, Any]:
    """Run the typing steps that apply and write typing.json. Never raises for
    a tool failure; the summary records what ran and what was skipped."""
    summary: Dict[str, Any] = {"typing_root": typing_root or None, "steps": {}, "versions": {}}
    salmonella = is_salmonella(detection)
    summary["salmonella"] = salmonella

    def finish() -> Dict[str, Any]:
        (outdir / "typing.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary

    # No typing root at all: say so once instead of once per tool.
    root_ok = False
    if typing_root:
        try:
            root_ok = Path(typing_root).is_dir()
        except OSError:
            root_ok = False
    if not root_ok:
        log("NOTE: the typing tools are not installed on this machine "
            f"(typing root: {typing_root or 'not set'}); typing steps skipped. "
            "deploy/setup_typing.sh installs them.")
        for name in ("checkm2", "sistr", "seqsero2", *ABRICATE_DATABASES):
            summary["steps"][name] = "skipped"
        return finish()

    if run_completeness:
        checkm2 = run_checkm2(assembly, outdir, typing_root, checkm2_db, threads, log)
        summary["steps"]["checkm2"] = "ran" if checkm2 else "skipped"
        if checkm2:
            summary["checkm2"] = checkm2
            summary["versions"]["checkm2"] = checkm2.get("version")
    else:
        summary["steps"]["checkm2"] = "disabled"

    if not salmonella:
        log("NOTE: organism is not Salmonella; serovar and gene screens are skipped.")
        for name in ("sistr", "seqsero2", *ABRICATE_DATABASES):
            summary["steps"][name] = "not applicable"
    else:
        if run_serovar:
            sistr = run_sistr(assembly, outdir, typing_root, threads, log) or {}
            seqsero2 = run_seqsero2(assembly, outdir, typing_root, threads, log) or {}
            summary["steps"]["sistr"] = "ran" if sistr else "skipped"
            summary["steps"]["seqsero2"] = "ran" if seqsero2 else "skipped"
            if sistr or seqsero2:
                summary["serovar"] = {
                    "sistr": sistr or None,
                    "seqsero2": seqsero2 or None,
                    **serovar_agreement(sistr, seqsero2),
                }
                summary["versions"]["sistr"] = sistr.get("version")
                summary["versions"]["seqsero2"] = seqsero2.get("version")
        else:
            summary["steps"]["sistr"] = summary["steps"]["seqsero2"] = "disabled"

        if run_gene_screens:
            for database, screen in ABRICATE_DATABASES.items():
                hits = run_abricate(assembly, outdir, typing_root, database, threads, log)
                summary["steps"][database] = "ran" if hits is not None else "skipped"
                if hits is not None:
                    summary[screen.key] = hits
            tool = find_tool(typing_root, "abricate", "abricate")
            if tool is not None:
                summary["versions"]["abricate"] = _version(tool, ["--version"])
                summary["versions"]["abricate_databases"] = _abricate_db_versions(typing_root)
                summary["thresholds"] = {
                    database: {"min_identity": screen.min_identity,
                               "min_coverage": screen.min_coverage,
                               "confident_identity": screen.confident_identity}
                    for database, screen in ABRICATE_DATABASES.items()
                }
        else:
            for name in ABRICATE_DATABASES:
                summary["steps"][name] = "disabled"

    return finish()
