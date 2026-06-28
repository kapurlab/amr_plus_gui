#!/usr/bin/env bash
# Clone + index the CGE finder databases used by the AMRFinderPlus GUI's
# Phase-1 finders (PlasmidFinder, SerotypeFinder, VirulenceFinder).
#
# Idempotent: skips a DB that's already cloned, re-indexes with the CGE env's
# kma_index, and writes a DB_COMMITS.txt provenance file recording each repo's
# pinned commit so runs are reproducible and the report can cite DB versions.
#
# Usage:
#   setup_cge_databases.sh <CGE_ENV> <CGE_DB_ROOT>
# e.g.
#   setup_cge_databases.sh /srv/icar/tools/cge/env /srv/icar/databases/cge
set -euo pipefail

CGE_ENV="${1:?usage: setup_cge_databases.sh <CGE_ENV> <CGE_DB_ROOT>}"
DB_ROOT="${2:?usage: setup_cge_databases.sh <CGE_ENV> <CGE_DB_ROOT>}"

KMA_INDEX="${CGE_ENV}/bin/kma_index"
PY="${CGE_ENV}/bin/python"
[ -x "$KMA_INDEX" ] || { echo "ERROR: kma_index not found in CGE env: $KMA_INDEX" >&2; exit 1; }

mkdir -p "$DB_ROOT"
COMMITS="${DB_ROOT}/DB_COMMITS.txt"
: > "$COMMITS"

# DB repos (Bitbucket — the GitHub mirrors require auth).
DBS=(
  "plasmidfinder_db https://bitbucket.org/genomicepidemiology/plasmidfinder_db.git"
  "serotypefinder_db https://bitbucket.org/genomicepidemiology/serotypefinder_db.git"
  "virulencefinder_db https://bitbucket.org/genomicepidemiology/virulencefinder_db.git"
)

for entry in "${DBS[@]}"; do
  name="${entry%% *}"; url="${entry##* }"
  dest="${DB_ROOT}/${name}"
  if [ ! -d "$dest/.git" ]; then
    echo ">> cloning ${name}"
    git clone --depth 1 "$url" "$dest"
  else
    echo ">> ${name} already present — pulling"
    git -C "$dest" pull --ff-only || true
  fi
  echo ">> indexing ${name}"
  ( cd "$dest" && "$PY" INSTALL.py "$KMA_INDEX" >/dev/null )
  # The DBs are root-owned but read by per-user OOD sessions, and VirulenceFinder
  # reads the DB's git commit for provenance — mark it safe for the CGE env's git
  # across users (else git's dubious-ownership guard aborts the step). Idempotent.
  ENV_GIT="${CGE_ENV}/bin/git"
  [ -x "$ENV_GIT" ] || ENV_GIT="git"
  "$ENV_GIT" config --system --get-all safe.directory 2>/dev/null | grep -qxF "$dest" \
    || "$ENV_GIT" config --system --add safe.directory "$dest"
  commit="$("$ENV_GIT" -C "$dest" rev-parse HEAD)"
  echo "${name} ${commit}" | tee -a "$COMMITS"
done

echo "Done. DBs under ${DB_ROOT}; commits recorded in ${COMMITS}."
