#!/usr/bin/env bash
# install.sh — idempotent, no-sudo deployment of the AMRFinderPlus GUI.
#
# Mirrors the Kraken/vSNP sandbox pattern. Every heavy step is skippable and
# clearly logged. Safe to re-run.
#
# What it does:
#   1. Locate/create the conda env (shared at <repo>/env, else personal amr_plus).
#   2. pip install backend/requirements.txt into that env.
#   3. amrfinder -u  -> download the AMRFinderPlus DB (skip if present).
#   4. Ensure a Kraken2 DB (PlusPF preferred; reuse existing if present).
#   5. Ensure mlst / PubMLST data is reachable.
#   6. Build the React frontend (frontend/dist/).
#
# Usage:
#   deploy/install.sh [--personal] [--kraken-db DIR] [--skip-amrfinder-db]
#                     [--skip-kraken-db] [--skip-frontend] [--dry-run]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---- defaults ----
SHARED_ENV="${REPO_DIR}/env"
PERSONAL_ENV_NAME="amr_plus"
CONDA_BASE="${HOME}/miniforge3"
USE_PERSONAL=0
KRAKEN_DB_DIR=""
KRAKEN_PLUSPF_DEFAULT="/srv/kapurlab/databases/kraken2/k2_standard_pluspf"
KRAKEN_STD_FALLBACK="/srv/kapurlab/databases/kraken2/k2_standard_08gb"
KRAKEN_PLUSPF_URL="https://genome-idx.s3.amazonaws.com/kraken/k2_standard_20240904.tar.gz"
SKIP_AMRFINDER_DB=0
SKIP_KRAKEN_DB=0
SKIP_FRONTEND=0
DRY_RUN=0

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] $*"; else "$@"; fi; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --personal)           USE_PERSONAL=1; shift;;
    --kraken-db)          KRAKEN_DB_DIR="$2"; shift 2;;
    --conda-base)         CONDA_BASE="$2"; shift 2;;
    --skip-amrfinder-db)  SKIP_AMRFINDER_DB=1; shift;;
    --skip-kraken-db)     SKIP_KRAKEN_DB=1; shift;;
    --skip-frontend)      SKIP_FRONTEND=1; shift;;
    --dry-run)            DRY_RUN=1; shift;;
    -h|--help)            sed -n '2,30p' "$0"; exit 0;;
    *) die "unknown arg: $1";;
  esac
done

log "AMRFinderPlus GUI install"
echo "  repo:  ${REPO_DIR}"
[[ ${DRY_RUN} -eq 1 ]] && warn "DRY RUN — no changes will be made"

# ---------------------------------------------------------------------------
# 1. conda env
# ---------------------------------------------------------------------------
CONDA="${CONDA_BASE}/bin/conda"
[[ -x "${CONDA}" ]] || CONDA="$(command -v conda 2>/dev/null || true)"
[[ -n "${CONDA}" && -x "${CONDA}" ]] || die "conda not found. Install miniforge to ${CONDA_BASE} or pass --conda-base."
ok "conda: ${CONDA}"
# Prefer mamba for env creation — clearer progress and a faster solve on big
# bioconda envs. Falls back to conda (which on this box already uses the
# libmamba solver). Override with CONDA_FRONTEND=conda.
CONDA_FRONTEND="${CONDA_FRONTEND:-}"
if [[ -z "${CONDA_FRONTEND}" ]]; then
  if [[ -x "${CONDA_BASE}/bin/mamba" ]]; then CONDA_FRONTEND="${CONDA_BASE}/bin/mamba"
  elif command -v mamba >/dev/null 2>&1; then CONDA_FRONTEND="$(command -v mamba)"
  else CONDA_FRONTEND="${CONDA}"; fi
fi
ok "env builder: ${CONDA_FRONTEND}"

ENV_FILE="${REPO_DIR}/conda_setup/environment.yml"
if [[ ${USE_PERSONAL} -eq 1 ]]; then
  ENV_REF=("-n" "${PERSONAL_ENV_NAME}")
  ENV_BIN="$("${CONDA}" run -n "${PERSONAL_ENV_NAME}" sh -c 'echo $CONDA_PREFIX/bin' 2>/dev/null || true)"
  ENV_DESC="personal env ${PERSONAL_ENV_NAME}"
  ENV_EXISTS=$("${CONDA}" env list | awk '{print $1}' | grep -qx "${PERSONAL_ENV_NAME}" && echo 1 || echo 0)
  CREATE_FLAG=("-n" "${PERSONAL_ENV_NAME}")
else
  ENV_REF=("-p" "${SHARED_ENV}")
  ENV_BIN="${SHARED_ENV}/bin"
  ENV_DESC="shared env ${SHARED_ENV}"
  ENV_EXISTS=$([[ -x "${SHARED_ENV}/bin/python" ]] && echo 1 || echo 0)
  CREATE_FLAG=("-p" "${SHARED_ENV}")
fi

if [[ "${ENV_EXISTS}" -eq 1 ]]; then
  ok "${ENV_DESC} already exists — skipping create"
else
  # A prior run cancelled mid-solve leaves a partial env dir with no python;
  # env create would then abort with "prefix already exists". Clear it first.
  if [[ ${USE_PERSONAL} -eq 0 && -d "${SHARED_ENV}" ]]; then
    warn "removing incomplete env at ${SHARED_ENV} (no python found)"
    run rm -rf "${SHARED_ENV}"
  fi
  log "creating ${ENV_DESC} from ${ENV_FILE} (solve can take 2-5 min)"
  run "${CONDA_FRONTEND}" env create "${CREATE_FLAG[@]}" -f "${ENV_FILE}"
fi

# A --personal env may have just been created above; if so, the ENV_BIN probed
# earlier (via `conda run` before the env existed) is empty, which would make
# PYTHON="/python". Re-resolve now that the env exists — prefer the live prefix,
# fall back to <conda base>/envs/<name> (where `conda env create -n` puts it).
if [[ ${USE_PERSONAL} -eq 1 && ! -x "${ENV_BIN}/python" ]]; then
  ENV_BIN="$("${CONDA}" run -n "${PERSONAL_ENV_NAME}" sh -c 'echo $CONDA_PREFIX/bin' 2>/dev/null || true)"
  [[ -x "${ENV_BIN}/python" ]] || ENV_BIN="$("${CONDA}" info --base 2>/dev/null)/envs/${PERSONAL_ENV_NAME}/bin"
fi
PYTHON="${ENV_BIN}/python"
[[ ${DRY_RUN} -eq 1 || -x "${PYTHON}" ]] || die "env python not found at '${PYTHON}' — ${ENV_DESC} did not build correctly."
# Put the env's bin on PATH for every tool call below. amrfinder needs its
# BLAST+/HMMER deps on PATH, and the `mlst` check is a Perl script whose
# `#!/usr/bin/env perl` shebang must resolve to the env Perl (which carries
# List::MoreUtils), not system Perl. The OOD session sets PATH the same way.
if [[ -d "${ENV_BIN}" ]]; then export PATH="${ENV_BIN}:${PATH}"; fi
# amrfinder is built for bioconda and resolves its DB under $CONDA_PREFIX/share/
# amrfinderplus/data/latest. Without CONDA_PREFIX it warns and fails to find the
# DB ("No valid AMRFinder database is found"), so `amrfinder -u` can't download
# and runtime can't read it. Export it for the shared env (skip for --personal,
# where the env name—not a fixed prefix—identifies it).
if [[ ${USE_PERSONAL} -eq 0 ]]; then export CONDA_PREFIX="${SHARED_ENV}"; fi
log "pip install backend requirements into ${ENV_DESC}"
run "${PYTHON}" -m pip install -r "${REPO_DIR}/backend/requirements.txt"

# ---------------------------------------------------------------------------
# 2. AMRFinderPlus database
# ---------------------------------------------------------------------------
if [[ ${SKIP_AMRFINDER_DB} -eq 1 ]]; then
  warn "skipping AMRFinderPlus DB download (--skip-amrfinder-db)"
else
  AMRFINDER="${ENV_BIN}/amrfinder"
  # Install into the SHARED databases dir (matches config.py amrfinder_db default
  # and the kraken2 DB convention) so it survives env rebuilds and is shared
  # across hosts. Override with AMRFINDER_DB_DEST. amrfinder_update -d <parent>
  # writes <parent>/<version>/ and a <parent>/latest symlink.
  AMRFINDER_DB_DEST="${AMRFINDER_DB_DEST:-/srv/kapurlab/databases/amrfinderplus}"
  if [[ ! -x "${AMRFINDER}" ]]; then
    warn "amrfinder not found in env — DB step skipped (re-run after env build completes)"
  elif [[ -f "${AMRFINDER_DB_DEST}/latest/version.txt" ]]; then
    ok "AMRFinderPlus DB already present: ${AMRFINDER_DB_DEST}/latest"
  elif mkdir -p "${AMRFINDER_DB_DEST}" 2>/dev/null && [[ -w "${AMRFINDER_DB_DEST}" ]]; then
    log "downloading AMRFinderPlus DB into ${AMRFINDER_DB_DEST}"
    run "${ENV_BIN}/amrfinder_update" -d "${AMRFINDER_DB_DEST}" \
      || run "${AMRFINDER}" -u   # fall back to the env-default location
    [[ -d "${AMRFINDER_DB_DEST}/latest" ]] && run du -sh "${AMRFINDER_DB_DEST}/latest" || true
  else
    warn "${AMRFINDER_DB_DEST} not writable — installing DB to the env default instead"
    log "downloading AMRFinderPlus DB (amrfinder -u)"
    run "${AMRFINDER}" -u
  fi
  log "AMRFinderPlus version + DB:"
  run "${AMRFINDER}" -V -d "${AMRFINDER_DB_DEST}/latest" 2>/dev/null || run "${AMRFINDER}" -V || true
fi

# ---------------------------------------------------------------------------
# 3. Kraken2 database
# ---------------------------------------------------------------------------
if [[ ${SKIP_KRAKEN_DB} -eq 1 ]]; then
  warn "skipping Kraken2 DB check (--skip-kraken-db)"
else
  if [[ -z "${KRAKEN_DB_DIR}" ]]; then
    if [[ -d "${KRAKEN_PLUSPF_DEFAULT}" ]]; then
      KRAKEN_DB_DIR="${KRAKEN_PLUSPF_DEFAULT}"
    elif [[ -d "${KRAKEN_STD_FALLBACK}" ]]; then
      KRAKEN_DB_DIR="${KRAKEN_STD_FALLBACK}"
    fi
  fi
  if [[ -n "${KRAKEN_DB_DIR}" && -f "${KRAKEN_DB_DIR}/hash.k2d" ]]; then
    ok "Kraken2 DB present: ${KRAKEN_DB_DIR}"
  else
    warn "no Kraken2 DB found at ${KRAKEN_PLUSPF_DEFAULT} or ${KRAKEN_STD_FALLBACK}."
    warn "To install PlusPF (large): "
    echo "    mkdir -p ${KRAKEN_PLUSPF_DEFAULT}"
    echo "    curl -L '${KRAKEN_PLUSPF_URL}' | tar -xz -C ${KRAKEN_PLUSPF_DEFAULT}"
    warn "Organism detection from reads is skipped at runtime until a DB exists."
  fi
fi

# ---------------------------------------------------------------------------
# 4. mlst / PubMLST
# ---------------------------------------------------------------------------
if [[ -x "${ENV_BIN}/mlst" ]]; then
  ok "mlst present: $("${ENV_BIN}/mlst" --version 2>&1 | head -1)"
  "${ENV_BIN}/mlst" --list >/dev/null 2>&1 && ok "PubMLST schemes reachable" \
    || warn "mlst installed but scheme list unavailable; check the bundled db."
else
  warn "mlst not in env — MLST corroboration will be skipped at runtime."
fi

# ---------------------------------------------------------------------------
# 5. Frontend build
# ---------------------------------------------------------------------------
if [[ ${SKIP_FRONTEND} -eq 1 ]]; then
  warn "skipping frontend build (--skip-frontend)"
else
  log "building React frontend"
  pushd "${REPO_DIR}/frontend" >/dev/null
  if command -v npm >/dev/null 2>&1; then
    run npm ci || run npm install
    run npm run build
  elif [[ -x node_modules/.bin/vite ]]; then
    run node_modules/.bin/vite build
  else
    # Reuse the sibling Kraken GUI's node_modules if ours is missing.
    SIB="/srv/kapurlab/tools/kraken_id_parse_gui/frontend/node_modules"
    if [[ -d "${SIB}" && ! -e node_modules ]]; then
      run ln -s "${SIB}" node_modules
      run node_modules/.bin/vite build
    else
      warn "no npm and no node_modules — frontend not built. Install Node and re-run."
    fi
  fi
  popd >/dev/null
  [[ -f "${REPO_DIR}/frontend/dist/index.html" ]] && ok "frontend built: ${REPO_DIR}/frontend/dist/"
fi

log "Done. Register the OOD app (see deploy/INSTALL.md) and launch a session."
echo "  Backend entry:  ${REPO_DIR}/backend/app/main.py (uvicorn app.main:app)"
echo "  Env python:     ${PYTHON}"
