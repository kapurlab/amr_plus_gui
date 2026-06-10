# AMRFinderPlus GUI — Claude Code Context

> Read this file before touching any code. It contains deployment-critical
> constraints that will cause silent breakage if ignored.

## What This Is

A web GUI for **NCBI AMRFinderPlus** — acquired AMR genes, resistance point
mutations, and (with `--plus`) virulence + stress/biocide/metal/acid-resistance
content. A **FastAPI backend + React (Vite) SPA** deployed as an **Open OnDemand
batch_connect interactive application**, and a **sibling of `vsnp_gui` and
`kraken_id_parse_gui`** — it MUST share their look, feel, conventions, and
project layout.

Authoritative build spec: `SPEC_BUILD.md`.

## Repository Layout

```
/srv/kapurlab/tools/amr_plus_gui/
  backend/app/
    main.py        FastAPI app — all routes (clone of Kraken + AMR routes)
    config.py      load_config()/save_config(); per-user ~/.config/amr_plus_gui/
    jobs.py        JobManager (marker "amr_plus"); reused verbatim
    sra.py         SRA accession helpers; reused verbatim
  bin/
    amr_pipeline.py    orchestrator (kraken → assemble → QC → mlst → amrfinder)
    detect_organism.py conservative organism resolution + organism_detection.json
    run_amrfinder.py   AMRFinderPlus runner + run_manifest.json provenance
  config/
    organism_map.yaml         species → --organism token (exact/complex/genus)
    amrfinder_organisms.txt   fallback valid-token list (live: `amrfinder -l`)
    genome_sizes.yaml         expected genome sizes for assembly QC
  frontend/
    src/App.jsx    React UI (adapted from Kraken; AMR run panel + results table)
    src/App.css    theme — copied verbatim from Kraken; DO NOT restyle
    vite.config.js base: "./" — DO NOT CHANGE
    dist/          built output served by uvicorn (rebuild after edits)
  conda_setup/environment.yml   env name amr_plus
  deploy/install.sh, INSTALL.md
  ood/apps/amr_plus_gui{,_dev}/ OOD app definitions (prod + dev branch-picker)
```

## ⚠️ CRITICAL CONSTRAINTS — READ BEFORE WRITING ANY CODE

### 1. All frontend URLs must be relative — no exceptions
OOD proxies via Apache `mod_proxy` at `/rnode/<host>/<port>/<path>`. Use
`fetch("./api/...")` and `new EventSource("./api/jobs/${id}/log")`. Hardcoding a
host/port/absolute URL 404s under the proxy. `vite.config.js` has
`base: "./"` — never change it.

### 2. FastAPI serves the React frontend
`main.py` mounts `frontend/dist/` as StaticFiles and serves `index.html` at `/`.
Do not add a separate static server — it breaks the single-port OOD model.

### 3. Rebuild the frontend after any frontend edit
```bash
cd frontend && npm run build      # or node_modules/.bin/vite build
```
If `node_modules` is missing, symlink the sibling's:
`ln -s /srv/kapurlab/tools/kraken_id_parse_gui/frontend/node_modules node_modules`.

### 4. Reuse the EXACT App.css theme classes — do NOT restyle
The look must be visually identical to the Kraken/vSNP GUIs (warm sage/terracotta
theme, rounded panels, header with logo + version tag + status pill, status
strip, collapsible `.row-header` sections, `.row-grid`/`.row-grid-split`,
`.panel`, dark monospace `.log`). New visual elements reuse existing classes
(e.g. the results-table Method/confidence chips reuse `run-status run-status-*`).

### 5. The conda env owns the bioinformatics tools
`amrfinder`, `mlst`, `kraken2`, `shovill`, `spades.py`, `seqkit` come from the
`amr_plus` env. `script.sh.erb` puts the env `bin/` on `PATH` and sets
`PYTHONPATH=<repo>/bin` so the `bin/` scripts import each other.

### 6. Organism resolution is conservative and auditable
Passing the wrong `--organism` is worse than none. `detect_organism.py`:
- pure (dominant ≥70%, runner-up <10%) → token, high; complex collapse → medium;
  mixed → `contamination_flag=true`, no `-O`, confidence none.
- validate the token against the live `amrfinder -l` set;
- MLST agreement → `both`/high, conflict → no auto-assign;
- `force_organism` wins (source `forced`) but records the would-be choice.
Every decision is in `organism_detection.json`; every amrfinder option +
tool/DB versions + thresholds + ISO refs are in `run_manifest.json`.

## Development Workflow

- **Backend change**: edit `backend/app/`, start a new OOD session (or in dev,
  `--reload` picks it up).
- **Frontend change**: edit `frontend/src/`, `npm run build`, new OOD session.
- **Pipeline change**: edit `bin/*.py`; verify with `python -m py_compile`.
- **OOD template change**: edit `ood/apps/.../template/*`, new session.

## Key Paths

| Item | Path |
|---|---|
| Backend entry | `backend/app/main.py` (`uvicorn app.main:app`) |
| Conda env (shared) | `/srv/kapurlab/tools/amr_plus_gui/env` |
| AMRFinderPlus DB | `$CONDA_PREFIX/share/amrfinderplus/data/latest` (via `amrfinder -u`) |
| Kraken2 DB | `/srv/kapurlab/databases/kraken2/k2_standard_pluspf` (else `k2_standard_08gb`) |
| Shared projects | `/srv/kapurlab/projects` |
| Personal projects | `~/projects` |
| Per-user config | `~/.config/amr_plus_gui/config.json` |
| MLST sibling | `/srv/kapurlab/tools/mlst_gui/bin/mlst_pipeline.py` (guarded — built in parallel) |

## Output Layout (per sample)

`<project>/amr/<sample>/`: `amrfinder.tsv`, `mutation_all.tsv`,
`organism_detection.json`, `qc.json`, `assembly.fasta`, `mlst_result.json`,
`kraken_report.txt`, `run_manifest.json`, `pipeline.log`.

## AMRFinderPlus TSV columns (for parsing)

Protein id, Contig id, Start, Stop, Strand, Element symbol, Element name, Scope
(core|plus), Type (AMR|STRESS|VIRULENCE), Subtype, Class, Subclass, Method,
Target/Reference lengths, % Coverage, % Identity, Alignment length, Closest
reference accession/name, HMM accession/description. Plus a leading `name`
column (`--name`) and a trailing `Hierarchy node` (`--print_node`). The backend
parser (`_parse_amrfinder_tsv`) normalizes header drift across DB versions.
