#!/usr/bin/env bash
# setup_typing.sh: install the typing tools the pipeline runs after
# AMRFinderPlus (bin/typing_steps.py). Idempotent and safe to re-run.
#
# What it does:
#   1. Creates one conda env per tool under the typing root:
#        <typing root>/sistr      SISTR      (Salmonella serovar, cgMLST)
#        <typing root>/seqsero2   SeqSero2   (Salmonella serovar)
#        <typing root>/abricate   abricate   (PlasmidFinder and VFDB screens)
#        <typing root>/checkm2    CheckM2    (completeness, contamination)
#      An env that already exists is left as it is.
#   2. Builds the data each tool needs at first use, so a shared, read-only
#      install works for every user: SISTR's BLAST indexes (sistr_init) and
#      abricate's databases (abricate --setupdb).
#   3. Downloads the CheckM2 database (about 3 GB) into <db root>/checkm2.
#   4. Prints each tool's version as a self-check.
#
# The pipeline finds the typing root on its own when it is the `typing` folder
# beside the tool checkouts, which is the default here. For any other place,
# export BDTOOLS_TYPING_ROOT or set `typing_root` in the GUI's config.json.
#
# Usage:
#   deploy/setup_typing.sh [--typing-root DIR] [--checkm2-db DIR]
#                          [--conda-base DIR] [--skip-checkm2] [--dry-run]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC_DIR="${REPO_DIR}/conda_setup/typing"

TYPING_ROOT="${BDTOOLS_TYPING_ROOT:-$(dirname "${REPO_DIR}")/typing}"
CONDA_BASE="${HOME}/miniforge3"
CHECKM2_DB=""
SKIP_CHECKM2=0
DRY_RUN=0

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] $*"; else "$@"; fi; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --typing-root)   TYPING_ROOT="$2"; shift 2;;
    --checkm2-db)    CHECKM2_DB="$2"; shift 2;;
    --conda-base)    CONDA_BASE="$2"; shift 2;;
    --skip-checkm2)  SKIP_CHECKM2=1; shift;;
    --dry-run)       DRY_RUN=1; shift;;
    -h|--help)       sed -n '2,26p' "$0"; exit 0;;
    *) die "unknown arg: $1";;
  esac
done

# The database root, resolved the way backend/app/config.py resolves it:
# BDTOOLS_DB_ROOT, then the path `bdtools setup-databases` recorded, then
# ~/databases. No site path is written here.
db_root() {
  if [[ -n "${BDTOOLS_DB_ROOT:-}" ]]; then echo "${BDTOOLS_DB_ROOT}"; return; fi
  local home="${BDTOOLS_HOME:-${XDG_DATA_HOME:-${HOME}/.local/share}/bdtools}"
  if [[ -s "${home}/db-root" ]]; then head -n 1 "${home}/db-root"; return; fi
  echo "${HOME}/databases"
}
[[ -n "${CHECKM2_DB}" ]] || CHECKM2_DB="$(db_root)/checkm2"

log "Typing tools install"
echo "  typing root: ${TYPING_ROOT}"
echo "  CheckM2 DB:  ${CHECKM2_DB}"
[[ ${DRY_RUN} -eq 1 ]] && warn "DRY RUN: no changes will be made"

BUILDER="${CONDA_FRONTEND:-}"
if [[ -z "${BUILDER}" ]]; then
  if [[ -x "${CONDA_BASE}/bin/mamba" ]]; then BUILDER="${CONDA_BASE}/bin/mamba"
  elif command -v mamba >/dev/null 2>&1; then BUILDER="$(command -v mamba)"
  elif [[ -x "${CONDA_BASE}/bin/conda" ]]; then BUILDER="${CONDA_BASE}/bin/conda"
  else BUILDER="$(command -v conda 2>/dev/null || true)"; fi
fi
[[ -n "${BUILDER}" && -x "${BUILDER}" ]] || die "conda or mamba not found. Pass --conda-base."
ok "env builder: ${BUILDER}"

# make_env <name> <executable>: create <typing root>/<name> unless it is there.
make_env() {
  local name="$1" exe="$2" prefix="${TYPING_ROOT}/$1"
  if [[ -x "${prefix}/bin/${exe}" ]]; then
    ok "${name}: already installed (${prefix})"
    return
  fi
  log "creating ${name} env at ${prefix}"
  run mkdir -p "${TYPING_ROOT}"
  run "${BUILDER}" env create -y -p "${prefix}" -f "${SPEC_DIR}/${name}.yml"
}

# in_env <name> <command...>: run with that env's bin first on PATH.
in_env() {
  local name="$1"; shift
  run env PATH="${TYPING_ROOT}/${name}/bin:${PATH}" CONDA_PREFIX="${TYPING_ROOT}/${name}" "$@"
}

make_env sistr sistr
make_env seqsero2 SeqSero2_package.py
make_env abricate abricate
[[ ${SKIP_CHECKM2} -eq 1 ]] || make_env checkm2 checkm2

log "preparing tool data"
# SISTR builds BLAST indexes inside its own package folder the first time it
# runs. Do that now, as the installer, so later users only need read access.
in_env sistr sistr_init
# abricate's bundled databases need their BLAST indexes built once as well.
in_env abricate abricate --setupdb
in_env abricate abricate --check

if [[ ${SKIP_CHECKM2} -eq 1 ]]; then
  warn "CheckM2 skipped (--skip-checkm2); the pipeline will skip the completeness step"
elif compgen -G "${CHECKM2_DB}/**/*.dmnd" >/dev/null 2>&1 || compgen -G "${CHECKM2_DB}/*.dmnd" >/dev/null 2>&1 \
     || compgen -G "${CHECKM2_DB}/*/*.dmnd" >/dev/null 2>&1; then
  ok "CheckM2 database already present: ${CHECKM2_DB}"
else
  log "downloading the CheckM2 database into ${CHECKM2_DB}"
  run mkdir -p "${CHECKM2_DB}"
  in_env checkm2 checkm2 database --download --path "${CHECKM2_DB}"
fi

log "self-check"
in_env sistr sistr --version
in_env seqsero2 SeqSero2_package.py --version
in_env abricate abricate --version
in_env abricate abricate --list
[[ ${SKIP_CHECKM2} -eq 1 ]] || in_env checkm2 checkm2 --version

ok "typing tools ready under ${TYPING_ROOT}"
