import logging
import os
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# NCBI eutils rate limits: 3 req/s without an API key, 10 req/s with one.
# Stay slightly under either limit so we don't trip 429 under load. Once we
# do trip 429 NCBI typically blocks for several seconds before recovering.
_NCBI_API_KEY = (os.environ.get("NCBI_API_KEY") or "").strip()
_MIN_INTERVAL = 0.11 if _NCBI_API_KEY else 0.40
_RETRY_BACKOFFS = (1.0, 2.0, 4.0, 8.0)  # seconds to wait between retries on 429

_last_call_at = 0.0


class SRAExpansionError(Exception):
    """Raised when SRA-accession expansion against eutils fails so the caller
    can decide whether to abort or proceed with the unexpanded input."""


def _eutils_get(url: str, timeout: int) -> bytes:
    """GET an eutils URL with simple in-process rate limiting and 429 retry."""
    global _last_call_at
    if _NCBI_API_KEY and "api_key=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}api_key={_NCBI_API_KEY}"
    for attempt in range(len(_RETRY_BACKOFFS) + 1):
        # Throttle to MIN_INTERVAL since last call across the process.
        elapsed = time.monotonic() - _last_call_at
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                _last_call_at = time.monotonic()
                return response.read()
        except urllib.error.HTTPError as e:
            _last_call_at = time.monotonic()
            if e.code == 429 and attempt < len(_RETRY_BACKOFFS):
                wait = _RETRY_BACKOFFS[attempt]
                logger.warning("eutils 429; backing off %.1fs (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("unreachable")


def expand_accessions(accessions: List[str], *, strict: bool = False) -> List[str]:
    """Expand a list of SRA project / study / experiment / sample accessions to
    their underlying run accessions (SRR/ERR/DRR).

    Already-run accessions pass through unchanged. With strict=True, a failed
    expansion raises SRAExpansionError; with strict=False (default, legacy
    behavior) the unexpanded input is returned for that entry. Callers that
    care about expansion fidelity should use strict=True.
    """
    return expand_accessions_with_mapping(accessions, strict=strict)[0]


def expand_accessions_with_mapping(
    accessions: List[str], *, strict: bool = False
) -> tuple[List[str], List[tuple[str, List[str]]]]:
    """Like `expand_accessions`, but also returns the input→runs mapping so
    callers can persist a crosswalk file alongside the downloaded fastqs.

    Returns (flat_run_list, mapping) where:
      - flat_run_list is what `expand_accessions` returns (used to build the
        download script)
      - mapping is a list of (input_accession, [resolved_run, ...]) tuples
        preserving input order, including duplicates if the caller passed
        the same SRS twice. Each mapping entry tells you which sample-level
        accession produced which runs — essential for cross-walking results
        back to the original input list.
    """
    expanded: List[str] = []
    mapping: List[tuple[str, List[str]]] = []
    for acc in accessions:
        acc = acc.strip()
        if not acc:
            continue
        if acc.startswith(("SRR", "ERR", "DRR")):
            expanded.append(acc)
            mapping.append((acc, [acc]))
            continue
        try:
            runs = _expand_single(acc)
            expanded.extend(runs)
            mapping.append((acc, runs))
        except SRAExpansionError:
            if strict:
                raise
            logger.warning("SRA expansion failed for %s; using literal", acc)
            expanded.append(acc)
            mapping.append((acc, [acc]))
    return expanded, mapping


def write_crosswalk_tsv(download_dir: Path, mapping: List[tuple[str, List[str]]]) -> Path:
    """Write a 2-column TSV recording the input→runs resolution. One line per
    input accession; multiple runs joined by comma in the second column.

    Lives at <download_dir>/sra_crosswalk.tsv. Appended to (not overwritten)
    so multiple download batches into the same project preserve history;
    duplicate input rows are tolerated and intentional (they mean the user
    submitted the same accession in two batches)."""
    crosswalk_path = download_dir / "sra_crosswalk.tsv"
    is_new = not crosswalk_path.exists()
    with crosswalk_path.open("a", encoding="utf-8") as fh:
        if is_new:
            fh.write("# SRS/DRS/SRX → SRR/DRR resolution. Generated at download time.\n")
            fh.write("input\tresolved_runs\n")
        for inp, runs in mapping:
            fh.write(f"{inp}\t{','.join(runs)}\n")
    return crosswalk_path


def _expand_single(accession: str) -> List[str]:
    try:
        xml_data = _eutils_get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=sra&term={accession}&usehistory=y",
            timeout=10,
        )
        root = ET.fromstring(xml_data)
        ids = [e.text for e in root.findall(".//Id") if e.text]
        if not ids:
            return [accession]
        fetch_xml = _eutils_get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            f"?db=sra&id={','.join(ids)}",
            timeout=30,
        )
        fetch_root = ET.fromstring(fetch_xml)
        runs = [
            run.get("accession")
            for run in fetch_root.findall(".//RUN")
            if run.get("accession")
        ]
        return runs or [accession]
    except (urllib.error.HTTPError, urllib.error.URLError, ET.ParseError, TimeoutError) as e:
        raise SRAExpansionError(f"could not resolve {accession}: {type(e).__name__}: {e}") from e


def build_download_script(
    download_dir: Path,
    accessions: List[str],
    allow_insecure_https: bool,
    concurrency: int = 4,
) -> str:
    """Generate a bash script that downloads each accession via xargs -P
    parallelism. concurrency caps simultaneous workers — past ~4-6 NCBI's
    per-IP S3 throttling and the local fasterq-dump CPU/disk start to bite,
    so 4 is the documented sweet spot."""
    curl_insecure = "-k" if allow_insecure_https else ""
    concurrency = max(1, int(concurrency))
    acc_block = "\n".join([f'    "{a}"' for a in accessions])

    return f"""#!/bin/bash
set -u

cd "{download_dir}"

# ── Tool detection ──────────────────────────────────────────────
HAS_WGET=0; HAS_FASTERQ=0; HAS_ENADATAGET=0; HAS_CURL=0; HAS_PIGZ=0

if command -v wget >/dev/null 2>&1; then
  HAS_WGET=1; echo "[OK] wget found: $(command -v wget)"
else
  echo "[MISSING] wget — Method 1 (S3 direct download) will be skipped"
fi

if command -v fasterq-dump >/dev/null 2>&1; then
  HAS_FASTERQ=1; echo "[OK] fasterq-dump found: $(command -v fasterq-dump)"
else
  echo "[MISSING] fasterq-dump — Method 1 (SRA conversion) will be skipped"
fi

if command -v enaDataGet >/dev/null 2>&1; then
  HAS_ENADATAGET=1; echo "[OK] enaDataGet found: $(command -v enaDataGet)"
else
  echo "[MISSING] enaDataGet — Method 2 (ENA Data Get) will be skipped"
fi

if command -v curl >/dev/null 2>&1; then
  HAS_CURL=1; echo "[OK] curl found: $(command -v curl)"
else
  echo "[MISSING] curl — Method 3 (ENA HTTPS/FTP) will be skipped"
fi

if command -v pigz >/dev/null 2>&1; then
  HAS_PIGZ=1; echo "[OK] pigz found (fast compression)"
else
  echo "[OK] pigz not found, will use gzip"
fi

# Check that at least one download method is available
CAN_METHOD1=0; CAN_METHOD2=0; CAN_METHOD3=0
if [ "$HAS_WGET" -eq 1 ] && [ "$HAS_FASTERQ" -eq 1 ]; then CAN_METHOD1=1; fi
if [ "$HAS_ENADATAGET" -eq 1 ]; then CAN_METHOD2=1; fi
if [ "$HAS_CURL" -eq 1 ]; then CAN_METHOD3=1; fi

if [ "$CAN_METHOD1" -eq 0 ] && [ "$CAN_METHOD2" -eq 0 ] && [ "$CAN_METHOD3" -eq 0 ]; then
  echo "[DEPENDENCY_ERROR] No download methods available. Install wget+fasterq-dump, enaDataGet, or curl."
  exit 1
fi

echo ""

# ── Helper functions ────────────────────────────────────────────

already_have() {{
  local acc="$1"
  # Check for paired-end files
  if ls "${{acc}}"_1.fastq.gz "${{acc}}"_2.fastq.gz 2>/dev/null | head -1 | grep -q .; then
    return 0
  fi
  # Check for single-end file
  if [ -f "${{acc}}.fastq.gz" ]; then
    return 0
  fi
  return 1
}}

compress_fastqs() {{
  local acc="$1"
  for fq in "${{acc}}"*.fastq; do
    if [ -f "$fq" ]; then
      echo "  Compressing $fq"
      if [ "$HAS_PIGZ" -eq 1 ]; then
        pigz "$fq"
      else
        gzip "$fq"
      fi
    fi
  done
}}

# ── Method 1: S3 wget + fasterq-dump ───────────────────────────

method1() {{
  local acc="$1"
  if [ "$CAN_METHOD1" -eq 0 ]; then return 1; fi

  echo "  [Method 1] S3 wget + fasterq-dump"
  local sra_file="${{acc}}.sra"

  # Download .sra from S3
  if ! wget -q -O "$sra_file" "https://sra-pub-run-odp.s3.amazonaws.com/sra/${{acc}}/${{acc}}" 2>&1; then
    echo "  [Method 1] wget download failed"
    rm -f "$sra_file"
    return 1
  fi

  if [ ! -s "$sra_file" ]; then
    echo "  [Method 1] Downloaded file is empty"
    rm -f "$sra_file"
    return 1
  fi

  # Convert with fasterq-dump --split-3 (handles single and paired)
  if ! fasterq-dump --split-3 "$sra_file" 2>&1; then
    echo "  [Method 1] fasterq-dump conversion failed"
    rm -f "$sra_file"
    return 1
  fi

  rm -f "$sra_file"
  compress_fastqs "$acc"
  return 0
}}

# ── Method 2: enaDataGet ───────────────────────────────────────

method2() {{
  local acc="$1"
  if [ "$CAN_METHOD2" -eq 0 ]; then return 1; fi

  echo "  [Method 2] enaDataGet"
  if ! enaDataGet --format fastq "$acc" 2>&1; then
    echo "  [Method 2] enaDataGet failed"
    return 1
  fi

  # enaDataGet puts files in a subdirectory named after the accession
  if [ -d "$acc" ]; then
    mv "$acc"/*.fastq.gz . 2>/dev/null || true
    mv "$acc"/*.fastq . 2>/dev/null || true
    rmdir "$acc" 2>/dev/null || true
  fi

  compress_fastqs "$acc"
  return 0
}}

# ── Method 3: ENA curl (HTTPS then FTP fallback) ───────────────

method3() {{
  local acc="$1"
  if [ "$CAN_METHOD3" -eq 0 ]; then return 1; fi

  echo "  [Method 3] ENA curl"
  local api_url="https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${{acc}}&result=read_run&fields=fastq_ftp"
  local urls
  urls=$(curl -s {curl_insecure} "$api_url" | tail -n1 | cut -f2)

  if [ -z "$urls" ] || [ "$urls" = "fastq_ftp" ]; then
    echo "  [Method 3] ENA did not return URLs for $acc"
    return 1
  fi

  local all_ok=1
  IFS=';' read -ra url_array <<< "$urls"
  for url in "${{url_array[@]}}"; do
    local filename
    filename=$(basename "$url")
    echo "  Downloading $filename"
    # Try HTTPS first, then FTP
    if curl -f {curl_insecure} "https://$url" -o "$filename" 2>&1; then
      echo "  [OK] $filename downloaded via HTTPS"
    elif curl -f {curl_insecure} "ftp://$url" -o "$filename" 2>&1; then
      echo "  [OK] $filename downloaded via FTP"
    else
      echo "  [FAILED] Could not download $filename"
      rm -f "$filename"
      all_ok=0
    fi
  done

  if [ "$all_ok" -eq 0 ]; then return 1; fi
  return 0
}}

# ── Per-accession worker (called by xargs in parallel) ─────────

download_one() {{
  local acc="$1"
  echo "── $acc ──"

  if already_have "$acc"; then
    echo "  [$acc] Already have reads, skipping"
    echo "ok" > ".status_${{acc}}"
    return 0
  fi

  local downloaded=0
  for method in method1 method2 method3; do
    if $method "$acc"; then
      downloaded=1
      break
    fi
  done

  if [ "$downloaded" -eq 1 ] && already_have "$acc"; then
    echo "  [$acc] [OK] downloaded"
    echo "ok" > ".status_${{acc}}"
  else
    echo "  [$acc] [FAILED] all methods exhausted"
    echo "fail" > ".status_${{acc}}"
  fi
}}

# xargs subshells need our functions and the HAS_*/CAN_* state.
export -f download_one already_have compress_fastqs method1 method2 method3
export HAS_WGET HAS_FASTERQ HAS_ENADATAGET HAS_CURL HAS_PIGZ
export CAN_METHOD1 CAN_METHOD2 CAN_METHOD3

# ── Main download loop (parallel via xargs -P) ────────────────

ACCESSIONS=(
{acc_block}
)

# Clear any stale status files from a prior run in this same dir.
for acc in "${{ACCESSIONS[@]}}"; do rm -f ".status_${{acc}}"; done

echo "Dispatching ${{#ACCESSIONS[@]}} accessions across {concurrency} parallel workers..."
echo "(per-accession lines will interleave; look for '[ACC]' prefix)"
echo ""

# -P {concurrency}: up to N workers. -I {{}}: substitute placeholder per call.
# -n 1: one accession per worker call. bash -c '...' "" {{}} passes the
# accession as $1 to download_one.
printf '%s\n' "${{ACCESSIONS[@]}}" | \
  xargs -P {concurrency} -n 1 -I {{}} bash -c 'download_one "$@"' _ {{}}

# ── Tally results from status files ────────────────────────────

SUCCEEDED=0
FAILED_COUNT=0
FAILED_LIST=""
for acc in "${{ACCESSIONS[@]}}"; do
  if [ -f ".status_${{acc}}" ] && [ "$(cat ".status_${{acc}}")" = "ok" ]; then
    SUCCEEDED=$((SUCCEEDED + 1))
  else
    FAILED_COUNT=$((FAILED_COUNT + 1))
    FAILED_LIST="$FAILED_LIST $acc"
  fi
  rm -f ".status_${{acc}}"
done

# ── Summary ─────────────────────────────────────────────────────

echo ""
echo "== Summary =="
echo "Succeeded: $SUCCEEDED / ${{#ACCESSIONS[@]}}"

if [ "$FAILED_COUNT" -gt 0 ]; then
  echo "[FAILED] Failed accessions:$FAILED_LIST"
fi

if ls *.fastq.gz >/dev/null 2>&1; then
  echo ""; echo "Downloaded files:"
  ls -lh *.fastq.gz
else
  echo "[FAILED] No .fastq.gz files found after download"
fi

if [ "$FAILED_COUNT" -gt 0 ] && [ "$SUCCEEDED" -eq 0 ]; then
  exit 1
fi
exit 0
"""
