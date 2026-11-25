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
    typing_steps.py    typing after AMRFinderPlus: CheckM2 for any organism;
                       for Salmonella, serovar (SISTR, SeqSero2) and abricate
                       PlasmidFinder / VFDB screens -> typing.json
  config/
    organism_map.yaml         species → --organism token (exact/complex/genus)
    amrfinder_organisms.txt   fallback valid-token list (live: `amrfinder -l`)
    genome_sizes.yaml         expected genome sizes for assembly QC
  frontend/
    src/App.jsx    React UI (adapted from Kraken; AMR run panel + results table)
    src/App.css    theme — copied verbatim from Kraken; DO NOT restyle
    vite.config.js base: "./" — DO NOT CHANGE
    dist/          built output served by uvicorn (rebuild after edits)
    sample_metadata.py the project's metadata sheet (sample_metadata.tsv),
                       filled from pluggable sources: public run records
                       (NCBI BioSample, ENA) or a sheet the lab already has
    cohort_report.py   one report across a project's finished samples ->
                       <project>/cohort_report/ (HTML, PDF, table, checks)
    cohort_tree.py     Newick reading, rooting and layout for that report
    cohort_figures.py  its figures as inline SVG (bars, tree + matrix, heatmap)
    cohort_itol.py     the same tree and annotations as files for iTOL and
                       as sheets for the iTOL annotation editor (itol/,
                       itol_bundle.zip, itol_batch.zip); writes files, uploads
                       nothing
  conda_setup/environment.yml   env name amr_plus
  conda_setup/typing/*.yml      one env per typing tool (they do not solve together)
  deploy/install.sh, INSTALL.md
  deploy/setup_typing.sh        installs the typing envs and the CheckM2 database
  tests/                        pytest tests for typing_steps and its report rows
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

### 7. Typing steps are optional and never fail a run
`bin/typing_steps.py` runs after AMRFinderPlus on the same assembly. Its tools
live in their own conda envs under one "typing root" (`<tools root>/typing` by
default, or `BDTOOLS_TYPING_ROOT`, or `typing_root` in config.json), installed
by `deploy/setup_typing.sh`. A tool that is not installed is skipped with a
note, so the GUI behaves as before on a machine without them. The serovar and
abricate steps run only when the organism is Salmonella. A step that did not
run adds no row to the report, so a missing tool never reads as a negative
result.

### 8. The cohort report reads results; it does not recompute them
`bin/cohort_report.py --project DIR` (the "Build cohort report" button) reads
each `amr/<sample>/` folder, an optional metadata sheet
(`sample_crosswalk.tsv` or `metadata.*` in the project or its `download/`),
and the newest vSNP Step 2 run that holds a tree. It writes
`<project>/cohort_report/`. Every count in its text is computed from the
per-isolate table, and `cohort_checks.json` records where the layers do not
line up (an isolate with no typing while others have one, a tree tip with no
isolate, a metadata row with no results).

Nothing about a cohort is assumed. Which field colours the isolates, which
fields get a chart and which column names them are choices, saved in
`<project>/cohort_report/settings.json` and picked in the Cohort report panel
from what the results and the metadata sheet actually hold. A field is `mlst`,
`organism`, `serovar`, a metadata column, or `COLUMN|year`. No column name is
hard-coded, a field that is not there is reported and not replaced by a guess,
and a section or table column appears only when the project has data for it. A
tree and alignment from any tool can be named in place of a vSNP Step 2 run.

### 9. One metadata sheet per project, filled from any source
`<project>/sample_metadata.tsv` has a `sample` column and any other columns.
Nothing else about it is fixed, so it serves any organism and any tool that
reads the project folder. `bin/sample_metadata.py` fills it from sources: `sra`
(every attribute of each run's BioSample, kept under its own name) and `import`
(any sheet; the sample column is found by matching). A new source is one class
with `applies` and `fetch`, registered in `SOURCES`. Merging keeps what is
already in the sheet: a source fills blanks and adds columns, and a value it
disagrees with is listed, never overwritten. `sample_metadata.provenance.json`
records which source filled each column. An SRA download runs the fetch for its
runs when the reads finish, and a failed lookup never fails the download.

## Development Workflow

- **Backend change**: edit `backend/app/`, start a new OOD session (or in dev,
  `--reload` picks it up).
- **Frontend change**: edit `frontend/src/`, `npm run build`, new OOD session.
- **Pipeline change**: edit `bin/*.py`; verify with `python -m py_compile`
  and `pytest tests/`.
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

When the typing tools are installed, also: `typing.json` (the summary the
report and the results export read), `checkm2.tsv`, and for Salmonella
`sistr.json`, `seqsero2.tsv`, `abricate_plasmidfinder.tsv`, `abricate_vfdb.tsv`.

## AMRFinderPlus TSV columns (for parsing)

Protein id, Contig id, Start, Stop, Strand, Element symbol, Element name, Scope
(core|plus), Type (AMR|STRESS|VIRULENCE), Subtype, Class, Subclass, Method,
Target/Reference lengths, % Coverage, % Identity, Alignment length, Closest
reference accession/name, HMM accession/description. Plus a leading `name`
column (`--name`) and a trailing `Hierarchy node` (`--print_node`). The backend
parser (`_parse_amrfinder_tsv`) normalizes header drift across DB versions.
