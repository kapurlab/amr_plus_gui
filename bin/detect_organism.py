#!/usr/bin/env python
"""
detect_organism.py — conservative, corroborated, auditable organism resolution
for AMRFinderPlus.

AMRFinderPlus does NOT identify the organism; the caller must pass --organism.
Passing the WRONG token yields wrong point-mutation calls and wrong gene
blacklisting — worse than omitting it. So this module resolves the organism
upstream, conservatively, and writes a fully-auditable organism_detection.json.

Inputs (any combination):
  - A Kraken2 report  (--kraken-report)         -> dominant species + share
  - An MLST result    (--mlst-json)             -> scheme/ST -> token (corroborate)
  - A forced token    (--force-organism)        -> always wins; source="forced"

Algorithm (see SPEC):
  1. Parse the Kraken2 report; consider species-level rows (rank starts "S").
     Each species' share = its clade reads / total CLASSIFIED reads.
  2. Dominance policy (defaults configurable):
       dominant_pct >= 70 AND runner_up_pct < 10  -> pure, confidence high
       top two in the same species-complex         -> pure (collapsed), medium
       otherwise                                    -> mixed/contaminated:
                                                       no auto -O, confidence none
  3. Map species -> token via config/organism_map.yaml (exact, then complex
     collapse, then genus fallback). Unmapped -> token null, run without -O.
  4. Validate the token against the LIVE `amrfinder -l` list (or the shipped
     fallback). An invalid token is dropped (token null) with a note.
  5. force_organism wins outright (source="forced") but the detection that
     WOULD have happened is still recorded.
  6. MLST corroboration: if the MLST scheme maps to a token and it agrees with
     Kraken, bump confidence to high and source="both"; if it disagrees, flag
     the conflict and prefer NOT auto-assigning (source stays kraken but a note
     records the conflict; the user can force).

Run standalone for testing:
  python detect_organism.py --kraken-report report.txt --out organism_detection.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
_CONFIG_DIR = _REPO_ROOT / "config"
_ORGANISM_MAP = _CONFIG_DIR / "organism_map.yaml"
_ORGANISMS_FALLBACK = _CONFIG_DIR / "amrfinder_organisms.txt"

# Dominance policy defaults (percent of classified reads).
DEFAULT_DOMINANT_MIN = 70.0
DEFAULT_RUNNER_UP_MAX = 10.0


# ---------------------------------------------------------------------------
# YAML loading (PyYAML if present; otherwise a minimal fallback parser that
# understands the simple structure of organism_map.yaml).
# ---------------------------------------------------------------------------
def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load a YAML mapping. Uses PyYAML when available (it is in the conda env
    + requirements). Falls back to a small indentation parser that handles the
    exact structure of the files we ship: a top-level mapping whose values are
    either scalars, flat `key: value` sub-mappings, or `- item` lists."""
    try:
        import yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data or {}
    except ImportError:
        return _fallback_yaml(path)
    except Exception:
        return {}


def _coerce(v: str):
    s = v.strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1]
    if s in ("null", "~", ""):
        return None
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s


def _strip_key(k: str) -> str:
    k = k.strip()
    if len(k) >= 2 and k[0] in "\"'" and k[-1] == k[0]:
        return k[1:-1]
    return k


def _fallback_yaml(path: Path) -> Dict[str, Any]:
    """Indentation parser for the limited 2-3 level YAML we ship. Not general.

    Tracks a container stack by indent. A `key:` with no value opens a child
    container whose kind (dict vs list) is decided by its first child line.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    root: Dict[str, Any] = {}
    # stack entries: (indent, container, parent, key) where container may still
    # be a placeholder dict that gets swapped to a list on first "- " child.
    stack: List[List[Any]] = [[-1, root, None, None]]
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        _, container, parent, key = stack[-1]

        if line.startswith("- "):
            # Ensure the current container is a list.
            if not isinstance(container, list):
                new_list: List[Any] = []
                if parent is not None and key is not None:
                    parent[key] = new_list
                stack[-1][1] = new_list
                container = new_list
            container.append(_coerce(line[2:]))
            continue

        if ":" in line:
            k, _, val = line.partition(":")
            k = _strip_key(k)
            val = val.strip()
            if isinstance(container, list):
                continue
            if val == "":
                child: Dict[str, Any] = {}
                container[k] = child
                stack.append([indent, child, container, k])
            else:
                container[k] = _coerce(val)
    return root


# ---------------------------------------------------------------------------
# Kraken2 report parsing
# ---------------------------------------------------------------------------
def parse_kraken_report(path: Path) -> List[Dict[str, Any]]:
    """Parse a Kraken2 report into species-level rows.

    Kraken2 report columns (tab or whitespace separated):
      1 pct  2 clade_reads  3 taxon_reads  4 rank_code  5 taxid  6.. name
    Species rows have a rank code beginning with 'S'. Returns a list of
    {"name", "clade_reads", "pct"} for species rows.
    """
    species: List[Dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return species
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 6:
            cols = re.split(r"\s{2,}|\t", line.strip())
        if len(cols) < 6:
            cols = line.split()
            if len(cols) < 6:
                continue
            # Recombine the name (cols 6..)
            name = " ".join(cols[5:]).strip()
            cols = cols[:5] + [name]
        rank = cols[3].strip()
        if not rank.upper().startswith("S"):
            continue
        try:
            clade_reads = int(cols[1].strip())
        except (ValueError, IndexError):
            continue
        name = cols[5].strip() if len(cols) > 5 else "\t".join(cols[5:]).strip()
        # Only keep top-level species rank "S" (not S1/S2 subspecies) to avoid
        # double counting; but subspecies still carry a useful species name —
        # fold them into their species by trimming to the first two tokens.
        species.append({"name": name, "clade_reads": clade_reads, "rank": rank})
    return species


def _species_binomial(name: str) -> str:
    """Reduce a taxon name to 'Genus species' (drop subspecies/strain)."""
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1]}"
    return name


def rank_species(species_rows: List[Dict[str, Any]]) -> List[Tuple[str, int]]:
    """Collapse to binomials, sum clade reads, return sorted [(name, reads)]."""
    agg: Dict[str, int] = {}
    for row in species_rows:
        if row["rank"].upper() != "S":
            # subspecies rows (S1/S2) — fold into the binomial
            pass
        binom = _species_binomial(row["name"])
        agg[binom] = agg.get(binom, 0) + row["clade_reads"]
    return sorted(agg.items(), key=lambda kv: kv[1], reverse=True)


# ---------------------------------------------------------------------------
# Valid token list
# ---------------------------------------------------------------------------
def get_valid_organisms() -> Tuple[List[str], Optional[str], str]:
    """Return (valid_tokens, db_version, source). Tries `amrfinder -l`, falls
    back to the shipped list."""
    organisms: List[str] = []
    db_version: Optional[str] = None
    source = "fallback"
    try:
        proc = subprocess.run(["amrfinder", "-l"], capture_output=True,
                              text=True, timeout=60)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        for line in out.splitlines():
            low = line.lower()
            if "database version" in low:
                m = re.search(r"version[:\s]+([0-9][\w.\-]*)", line, re.IGNORECASE)
                if m and db_version is None:
                    db_version = m.group(1)
            if "available --organism" in low or "--organism options" in low or "valid options" in low:
                tail = line.split(":", 1)[-1]
                organisms = [t.strip() for t in re.split(r"[,\s]+", tail) if t.strip()]
        if not organisms:
            for line in out.splitlines():
                s = line.strip()
                if re.fullmatch(r"[A-Z][a-z]+(?:_[a-z]+)*", s):
                    organisms.append(s)
        if organisms:
            source = "amrfinder"
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    if not organisms:
        try:
            for line in _ORGANISMS_FALLBACK.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    organisms.append(s)
        except OSError:
            pass
        source = "fallback"
    seen = set()
    uniq = [o for o in organisms if not (o in seen or seen.add(o))]
    return uniq, db_version, source


# ---------------------------------------------------------------------------
# Mapping species -> token
# ---------------------------------------------------------------------------
def _complex_for(name: str, complexes: Dict[str, Any]) -> Optional[str]:
    for cx_key, cx in (complexes or {}).items():
        members = (cx or {}).get("members", []) or []
        if name in members or any(name.startswith(m) for m in members):
            return cx_key
    return None


def map_species_to_token(species: str, omap: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Map a binomial species name to an AMRFinderPlus token.

    Returns (token_or_None, how) where `how` describes the path:
    'species' | 'complex' | 'genus' | 'none'.
    """
    species_map = omap.get("species", {}) or {}
    genus_map = omap.get("genus_fallback", {}) or {}
    if species in species_map:
        return species_map[species], "species"
    # complex collapse
    cx_key = _complex_for(species, omap.get("complexes", {}) or {})
    if cx_key:
        token = (omap["complexes"][cx_key] or {}).get("token")
        if token:
            return token, "complex"
    genus = species.split()[0] if species else ""
    if genus in genus_map:
        return genus_map[genus], "genus"
    return None, "none"


def map_mlst_scheme_to_token(scheme: str, omap: Dict[str, Any]) -> Optional[str]:
    """Map an MLST scheme/organism_token to an AMRFinderPlus token. The
    mlst_gui sibling already emits an `organism_token` field that may be a
    binomial ('Klebsiella pneumoniae') or a PubMLST scheme key; try species map
    then a coarse genus match."""
    if not scheme:
        return None
    scheme = scheme.strip()
    # If it already is a valid-looking token, return as-is (validated later).
    if re.fullmatch(r"[A-Z][a-z]+(?:_[a-z]+)*", scheme):
        return scheme
    species_map = omap.get("species", {}) or {}
    # exact binomial
    binom = _species_binomial(scheme.replace("_", " ").capitalize())
    for key, tok in species_map.items():
        if key.lower() == scheme.lower() or key.lower() == binom.lower():
            return tok
    # genus fallback
    genus = scheme.split()[0].capitalize() if scheme else ""
    token, _ = map_species_to_token(f"{genus} sp", omap)
    return token


# ---------------------------------------------------------------------------
# Main resolution
# ---------------------------------------------------------------------------
def resolve(
    kraken_report: Optional[Path],
    mlst_json: Optional[Path],
    force_organism: Optional[str],
    dominant_min: float = DEFAULT_DOMINANT_MIN,
    runner_up_max: float = DEFAULT_RUNNER_UP_MAX,
) -> Dict[str, Any]:
    omap = _load_yaml(_ORGANISM_MAP)
    valid_tokens, db_version, valid_source = get_valid_organisms()
    valid_set = set(valid_tokens)
    notes: List[str] = []

    result: Dict[str, Any] = {
        "dominant_species": None,
        "dominant_pct": None,
        "runner_up_species": None,
        "runner_up_pct": None,
        "contamination_flag": False,
        "organism_token": None,
        "organism_source": "none",   # kraken | mlst | both | forced | none
        "confidence": "none",        # high | medium | low | none
        "mlst_scheme": None,
        "mlst_st": None,
        "used_plus": None,
        "valid_organisms": valid_tokens,
        "valid_organisms_source": valid_source,
        "db_version": db_version,
        "notes": notes,
    }

    # ---- Kraken detection ----
    kraken_token: Optional[str] = None
    if kraken_report and kraken_report.is_file():
        rows = parse_kraken_report(kraken_report)
        ranked = rank_species(rows)
        total_classified = sum(r for _, r in ranked)
        if total_classified > 0 and ranked:
            dom_name, dom_reads = ranked[0]
            dom_pct = 100.0 * dom_reads / total_classified
            result["dominant_species"] = dom_name
            result["dominant_pct"] = round(dom_pct, 2)
            ru_name, ru_pct = None, 0.0
            if len(ranked) > 1:
                ru_name, ru_reads = ranked[1]
                ru_pct = 100.0 * ru_reads / total_classified
                result["runner_up_species"] = ru_name
                result["runner_up_pct"] = round(ru_pct, 2)

            same_complex = (
                ru_name is not None
                and _complex_for(dom_name, omap.get("complexes", {}) or {})
                and _complex_for(dom_name, omap.get("complexes", {}) or {})
                == _complex_for(ru_name, omap.get("complexes", {}) or {})
            )

            if dom_pct >= dominant_min and ru_pct < runner_up_max:
                token, how = map_species_to_token(dom_name, omap)
                kraken_token = token
                result["confidence"] = "high" if token else "low"
                result["organism_source"] = "kraken" if token else "none"
                if not token:
                    notes.append(
                        f"Dominant species '{dom_name}' is pure but has no "
                        f"AMRFinderPlus --organism token; running without -O."
                    )
                else:
                    notes.append(f"Kraken: pure '{dom_name}' ({dom_pct:.1f}%) -> {token} (via {how}).")
            elif same_complex:
                cx_key = _complex_for(dom_name, omap.get("complexes", {}) or {})
                token = (omap["complexes"][cx_key] or {}).get("token")
                kraken_token = token
                result["confidence"] = "medium" if token else "low"
                result["organism_source"] = "kraken" if token else "none"
                notes.append(
                    f"Kraken: top two species both in complex '{cx_key}' "
                    f"({dom_name} {dom_pct:.1f}% / {ru_name} {ru_pct:.1f}%) -> "
                    f"{token or 'no token'}."
                )
            else:
                result["contamination_flag"] = True
                result["confidence"] = "none"
                result["organism_source"] = "none"
                notes.append(
                    f"Kraken: mixed/contaminated — {dom_name} {dom_pct:.1f}% / "
                    f"{ru_name or '-'} {ru_pct:.1f}%. NOT auto-assigning --organism."
                )
        else:
            notes.append("Kraken report had no classified species-level reads.")
    elif kraken_report:
        notes.append(f"Kraken report not found: {kraken_report}")

    # ---- MLST corroboration ----
    mlst_token: Optional[str] = None
    if mlst_json and mlst_json.is_file():
        try:
            mlst = json.loads(mlst_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            mlst = {}
        scheme = mlst.get("organism_token") or mlst.get("scheme") or mlst.get("species")
        result["mlst_scheme"] = mlst.get("scheme") or scheme
        result["mlst_st"] = mlst.get("st") or mlst.get("ST") or mlst.get("sequence_type")
        if scheme:
            mlst_token = map_mlst_scheme_to_token(str(scheme), omap)
            if mlst_token:
                notes.append(f"MLST: scheme '{scheme}' -> {mlst_token}.")
    elif mlst_json:
        notes.append("MLST result not present (mlst step skipped or unavailable).")

    # ---- Reconcile Kraken + MLST (only when not contaminated) ----
    if not result["contamination_flag"]:
        if kraken_token and mlst_token:
            if kraken_token == mlst_token:
                result["organism_token"] = kraken_token
                result["organism_source"] = "both"
                result["confidence"] = "high"
                notes.append("Kraken and MLST agree -> confidence high.")
            else:
                # Conflict: prefer NOT auto-assigning; let the user force.
                result["organism_token"] = None
                result["organism_source"] = "none"
                result["confidence"] = "low"
                notes.append(
                    f"CONFLICT: Kraken -> {kraken_token} but MLST -> {mlst_token}. "
                    f"Not auto-assigning; force the organism to override."
                )
        elif kraken_token:
            result["organism_token"] = kraken_token
        elif mlst_token:
            result["organism_token"] = mlst_token
            result["organism_source"] = "mlst"
            result["confidence"] = "medium"

    # ---- Validate the chosen token against the live/fallback valid set ----
    if result["organism_token"] and valid_set and result["organism_token"] not in valid_set:
        notes.append(
            f"Token '{result['organism_token']}' is not in the valid "
            f"AMRFinderPlus organism list ({valid_source}); dropping it (run without -O)."
        )
        result["organism_token"] = None
        if result["organism_source"] not in ("forced",):
            result["organism_source"] = "none"
            result["confidence"] = "low"

    # ---- Forced override wins outright ----
    if force_organism:
        forced = force_organism.strip()
        result["detection_would_have_chosen"] = result["organism_token"]
        result["organism_token"] = forced
        result["organism_source"] = "forced"
        result["confidence"] = "high"
        if valid_set and forced not in valid_set:
            notes.append(
                f"WARNING: forced organism '{forced}' is not in the valid "
                f"AMRFinderPlus list ({valid_source}); AMRFinderPlus may reject it."
            )
        else:
            notes.append(f"Forced organism override: {forced}.")

    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Resolve AMRFinderPlus --organism.")
    ap.add_argument("--kraken-report", type=Path, default=None)
    ap.add_argument("--mlst-json", type=Path, default=None)
    ap.add_argument("--force-organism", default=None)
    ap.add_argument("--dominant-min", type=float, default=DEFAULT_DOMINANT_MIN)
    ap.add_argument("--runner-up-max", type=float, default=DEFAULT_RUNNER_UP_MAX)
    ap.add_argument("--out", type=Path, default=None,
                    help="Write organism_detection.json here (also prints to stdout).")
    args = ap.parse_args(argv)

    result = resolve(
        args.kraken_report, args.mlst_json, args.force_organism,
        dominant_min=args.dominant_min, runner_up_max=args.runner_up_max,
    )
    payload = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
