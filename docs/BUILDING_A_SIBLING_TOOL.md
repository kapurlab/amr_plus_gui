# Building a New Kapur Lab Pipeline Tool (OOD GUI)

A field guide for creating a new web tool that matches the existing family —
**vsnp_gui**, **kraken_id_parse_gui**, **amr_plus_gui**, **mlst_gui** — so every
tool shares the same look, deploy model, and conventions. It also records the
specific traps we hit building amr_plus_gui + mlst_gui so you can avoid them.

> Reference implementations live at `/srv/kapurlab/tools/<tool>`. `amr_plus_gui`
> is the most complete (pipeline + organism detection + PDF/Excel reporting).
> Clone its shape; read this for the *why* and the *gotchas*.

---

## 1. What every tool is

A **FastAPI backend + React (Vite) SPA**, deployed as an **Open OnDemand (OOD)
batch_connect interactive app**. One uvicorn process per user session, reached
through OOD's Apache reverse proxy at `/rnode/<host>/<port>/`. FastAPI serves the
compiled React app from `frontend/dist/`. Bioinformatics work runs as background
jobs (subprocess) tracked by a `JobManager`, with live logs streamed over SSE.

Tools share the filesystem so a project made in one is usable in another:

```
/srv/kapurlab/projects/<project>/      (shared)   ~/projects/<project>/ (personal)
  download/            input FASTQ
  step1/  step2/       vSNP-compatible layout
  <project>_VCFs/
  <toolname>/<sample>/ this tool's per-sample outputs   (e.g. amr/<sample>/)
```

Shared infrastructure:
- Conda env per tool at `/srv/kapurlab/tools/<tool>/env` (personal fallback `~/miniforge3/envs/<tool>`).
- Shared databases under `/srv/kapurlab/databases/` (kraken2, amrfinderplus, …).
- OOD apps registered (by root) under `/var/www/ood/apps/sys/<tool>` and `<tool>_dev`.

---

## 2. Hard constraints (break these → silent breakage)

1. **All frontend URLs relative** — `fetch("./api/...")`, `new EventSource("./api/jobs/<id>/log")`.
   Never hardcode host/port/absolute URLs (the browser origin is the OOD server,
   not the app). `vite.config.js` must keep `base: "./"`.
2. **FastAPI serves the SPA** from `frontend/dist/` (StaticFiles mount). No separate
   static server — it breaks the single-port session model.
3. **Rebuild the frontend after any `frontend/src` edit** (`npm run build`). uvicorn
   serves `dist/`, not your source.
4. **Use the tool's conda env Python** (`/srv/kapurlab/tools/<tool>/env/bin/python`),
   never system/base Python.
5. **`before.sh` runs in the OOD parent** (only place `find_port` works);
   **`script.sh.erb` runs in the session** and starts uvicorn.

---

## 3. Quickstart: scaffold from an existing tool

The fastest correct start is to copy a sibling and adapt — you inherit the theme
CSS, `jobs.py`, `sra.py`, the frontend shell, and the OOD app structure.

```bash
SRC=/srv/kapurlab/tools/kraken_id_parse_gui          # or amr_plus_gui
DST=/srv/kapurlab/tools/<newtool>
rsync -a --exclude env/ --exclude .git/ --exclude __pycache__/ \
  --exclude 'frontend/dist/' --exclude 'frontend/node_modules/' \
  --exclude 'backend/jobs/' --exclude bin/ --exclude 'HANDOFF*' --exclude README.md \
  "$SRC/" "$DST/"
mkdir -p "$DST"/{bin,backend/jobs,deploy}
```

Then adapt, in order:
1. Rename OOD app dirs: `ood/apps/<newtool>` and `ood/apps/<newtool>_dev`.
2. In `ood/**`, replace the old tool path/name (`/srv/kapurlab/tools/<old>` →
   `…/<newtool>`, and the cosmetic `KRAKEN_*`/`AMR_*` shell vars).
3. `backend/app/jobs.py`: set `_PIPELINE_MARKER = "<newtool-substring>"` (guards
   against PID reuse after a restart — must appear in the pipeline's cmdline).
4. `backend/app/config.py`: XDG dir `~/.config/<newtool>/config.json` and the
   `DEFAULTS` (DB paths, projects roots) — keep all site paths here / in env vars.
5. Write `backend/app/main.py` routes (clone the sibling; swap `kraken`/`amr`),
   the `bin/` pipeline, `conda_setup/environment.yml`, `deploy/install.sh`,
   `frontend/src/App.jsx`, manifests, README, CLAUDE.md.
6. `git init`; work on a feature branch (`feature/initial-build`); never commit
   `node_modules`/`dist`/`env` (see §9).

---

## 4. Backend conventions (`backend/app/`)

- **`main.py`** holds all routes. Reuse the sibling's: `/api/projects`,
  `/api/projects/{n}/{inputs,upload,link-local,samples,sra/download}`,
  `/api/config`, `/api/browse-dirs`, `/api/jobs`, `/api/jobs/{id}/log` (SSE),
  `/api/jobs/{id}/results`, `/api/.../file`. Add tool-specific run + results
  routes. Per-sample results are read straight off disk from
  `<project>/<tool>/<sample>/` so any past run is revisitable.
- **`jobs.py`** (reuse verbatim): `JobManager.start_job(name, command, cwd, env)`
  runs a wrapped subprocess, logs to `backend/jobs/<id>.log`, survives uvicorn
  restarts by re-attaching to live PIDs.
- **`config.py`** (reuse): per-user XDG config, `DEFAULTS` merged on load.
- **Result categorization**: `_result_category` / `_CATEGORY_ORDER` /
  `_result_label` decide which files the GUI surfaces and their order. Put the
  primary deliverables (report PDF, stats xlsx, results TSV) first.
- **Media**: `_INLINE_MEDIA` (open in browser: pdf/html/png/txt) vs
  `_DOWNLOAD_MEDIA` (xlsx/vcf/gz). Add new types here.

## 5. Pipeline / `bin/` conventions

- The backend launches the pipeline as a **subprocess** with
  `PYTHONPATH=<repo>/bin` so `bin/` modules import each other, and `python -u`
  (unbuffered) so logs stream in real time.
- Each pipeline step that can fail independently should **soft-fail** (log a
  WARNING, keep going) — never let reporting or an optional tool kill the run.
- Write a **provenance manifest** (`run_manifest.json`) capturing every option,
  tool+DB versions, thresholds, and relevant standards.
- **Reporting pattern** (see `bin/reporting/`): a package with `build(outdir,
  sample)` that loads the run's JSON/TSV artifacts and emits:
  - `<sample>_<date>_stats.xlsx` — **single labeled column** (`Statistic |
    Value`, one metric per row), modelled on the vSNP3 stats workbook.
  - `report.pdf` — **reportlab + matplotlib** (pure-Python, NO headless browser
    — see gotcha §11.7). Sections: input QC, summary, results table, figures,
    methods/provenance + disclaimer.

## 6. Frontend conventions (`frontend/`)

- **Reuse `src/App.css` verbatim** — it is the shared theme (sage/terracotta,
  `--accent:#4c8c8a`, rounded `.panel`, `.status-strip`, collapsible
  `.row-header`, `.row-grid`/`.row-grid-split`, dark `.log`). Do not restyle.
- `App.jsx` structure: header (logo + `version-tag` + status pill) → status
  strip KPIs → collapsible sections (Settings, Projects & Samples with the
  Inputs split-pane, Run, Results, Pipeline Log).
- Talk to the backend only via relative `./api/...`; stream logs with
  `EventSource("./api/jobs/${id}/log")`.
- Rebuild (`npm run build`) and commit nothing under `dist/`/`node_modules/`.

## 7. Conda env + `deploy/install.sh`

- `conda_setup/environment.yml` (name = tool): channels conda-forge + bioconda;
  list your bioinformatics tools and the web deps (fastapi, uvicorn-standard,
  aiofiles, jinja2, pyyaml) + report deps (matplotlib-base, reportlab, pillow).
  **Pin versions of tools that matter** (see §11.4).
- `deploy/install.sh` (idempotent, no-sudo): locate/create the env (prefer
  `mamba`), `pip install -r backend/requirements.txt`, install/verify databases
  to the **shared** location, build the frontend. Support `--dry-run` and
  `--skip-*` flags. Run every tool command **with the env on PATH** (§11.1).
- `deploy/register_ood_apps.sh` (run by root): copies `ood/apps/*` into
  `/var/www/ood/apps/sys/`.

## 8. OOD app files (`ood/apps/<tool>{,_dev}/`)

- `manifest.yml` (name, description, `icon: fa://…`, category Bioinformatics,
  subcategory), `form.yml` (`bc_num_hours`; the `_dev` app adds a `branch`
  field), `submit.yml.erb` (`template: basic`, `conn_params: [port]`),
  `view.html.erb` (Connect button → `/rnode/<host>/<port>/`).
- `template/before.sh` (prod): `port=$(find_port); export port`.
- `template/script.sh.erb`: pick shared-vs-personal env, `export PATH=<env>/bin:$PATH`,
  **export any tool-specific env vars** (e.g. `CONDA_PREFIX` for amrfinder — §11.2),
  `export PYTHONPATH=<repo>/bin`, `cd backend`, `exec python -m uvicorn app.main:app
  --host 0.0.0.0 --port $port`.
- `_dev` app: `before.sh.erb` checks out the chosen git branch into a `/tmp`
  worktree, symlinks `node_modules` from the prod repo, builds the frontend, and
  runs uvicorn from the worktree with `--reload`. Prod serves the committed
  on-disk `dist/`; dev rebuilds per launch.

## 9. Deploy sequence

1. **Push**: create the GitHub repo, `git push -u origin feature/initial-build`.
   (`gh` not authed here → create the repo in the web UI first; the push itself
   works over SSH.)
2. **Install**: `deploy/install.sh --conda-base /srv/kapurlab/tools/miniforge3`
   (preview with `--dry-run`). Verify tools + DBs (`<tool>/env/bin/<tool> --version`).
3. **Register** (root): `sudo deploy/register_ood_apps.sh` — *after* install, so
   the cards don't launch envless.
4. The apps appear under **Interactive Apps → Bioinformatics**. The curated
   "Kapur Lab Pipelines" landing page is **separate** and edited by hand (§11.8).

---

## 10. Verification checklist (before calling it done)

- [ ] `py_compile` every `backend/app/*.py` and `bin/*.py`.
- [ ] Backend imports under the tool's env Python: `cd backend && env/bin/python -c "import app.main"`.
- [ ] `grep` the frontend for absolute URLs — there should be **none** (only `./api`).
- [ ] `npm run build` produces `dist/index.html` with the right `<title>`.
- [ ] Each external tool runs **with `<env>/bin` on PATH** (`<tool> --version`).
- [ ] Databases resolve from the path in `config.py` DEFAULTS.
- [ ] Run one real sample end-to-end on the CLI; confirm all artifacts + report.
- [ ] Fresh OOD session loads the UI through the proxy and a sample completes.

---

## 11. Gotchas we actually hit (read this section)

**11.1 bioconda Perl/compiled tools need the env on PATH, not just the binary path.**
`mlst` (and many tools) start with `#!/usr/bin/env perl`. Invoking
`<env>/bin/mlst` directly without `<env>/bin` on `PATH` resolves `env perl` to
**system Perl**, which lacks `List::MoreUtils` → `Can't locate List/MoreUtils.pm`.
Fix: `export PATH="<env>/bin:$PATH"` before any tool call (install.sh + OOD
launcher already do this). The same applies to tools that shell out to BLAST/HMMER.

**11.2 amrfinder needs `$CONDA_PREFIX` to find its database.**
AMRFinderPlus is built for bioconda and resolves its DB at
`$CONDA_PREFIX/share/amrfinderplus/data/latest`. With only `PATH` set it warns
"compiled for bioconda but $CONDA_PREFIX not found" and **fails to install/find
the DB**. Fix: `export CONDA_PREFIX=<env>` in install.sh and in the OOD
`script.sh.erb`. (Any tool that keys off `$CONDA_PREFIX` needs this.)

**11.3 conda's classic solver hangs on big bioconda envs.**
A spinning "Solving environment: |" is the classic SAT solver. Prefer **mamba**
(`<conda-base>/bin/mamba env create …`). If a solve is cancelled mid-run it
leaves a partial `env/` dir that makes the next `env create` abort with "prefix
already exists" — `rm -rf` it first (install.sh handles this).

**11.4 unpinned bioconda packages can regress to ancient versions.**
With `mlst` unpinned, the solver picked **2.11** (a years-old PubMLST snapshot,
129 schemes) instead of **2.33.1** (162 schemes). Pin tools whose DB/behavior
matters (`mlst>=2.23`). Verify the *version with the env on PATH* (see 11.1).

**11.5 never commit `node_modules`; the dev worktree breaks if you do.**
A build-time `frontend/node_modules` **symlink** got committed because
`.gitignore` only had `node_modules/` (trailing slash matches *dirs*, not a
symlink). The `_dev` app's worktree then recreated a dangling symlink → `vite`
missing → `{"error":"Frontend not built"}`. Fix: gitignore **both** `node_modules/`
and `node_modules`, and never track it. Likewise never commit `dist/` — the dev
app builds it; prod's `dist/` is built on disk by `install.sh`.

**11.6 shared DB location must match `config.py`, and the runner must validate it.**
`install.sh`'s `amrfinder -u` downloaded the DB into the conda env, but the
config default pointed at `/srv/kapurlab/databases/amrfinderplus/latest` → the
pipeline passed `-d <empty path>` and amrfinder aborted. Fixes: install DBs to
the **shared** `/srv/kapurlab/databases/<tool>` location (matching the config
default and the kraken2 convention), and have the runner pass `-d` **only when
the path is a valid DB**, else fall back to the env default. Keep this pattern
for any tool with a downloadable DB.

**11.7 PDF reports: use reportlab + matplotlib, not a headless browser.**
The Kraken HTML→PDF path depends on playwright/Chrome, which is fragile on a
shared node. Pure-Python `reportlab` (+ `matplotlib` for figures, `pillow` for
image sizing) always renders. Keep figures best-effort (wrap in try/except).

**11.8 the dashboard landing page is separate from registered apps.**
Registering under `/var/www/ood/apps/sys` makes a tool appear in **Interactive
Apps → Bioinformatics**, but the curated "Kapur Lab Pipelines" home page lists a
hand-picked set of cards — add new tools there by hand.

**11.9 changes don't appear until the right thing reloads.**
- Editing `bin/` scripts: picked up on the **next pipeline run** (subprocess reads from disk).
- Editing `backend/app/`: needs a **new OOD session** (or `--reload` in the dev app).
- Editing `ood/**`: re-run `sudo register_ood_apps.sh` (the registered copy is a snapshot).
- Editing `frontend/src`: `npm run build`, then a new session.

**11.10 permissions.** `/var/www/ood/apps/sys` is root-owned → app registration
needs sudo/an admin. `/srv/kapurlab/{projects,databases}` are group-writable.

**11.11 NCBI eutils rate-limits.** Back-to-back esearch/efetch trips HTTP 429.
Space calls (~0.4s) and retry with backoff (`sra.py` does this).

---

## 12. Stable cross-tool contracts

- A tool that another tool consumes should expose a **plain CLI** writing a
  small JSON, independent of its web layer. Example: `mlst_gui` exposes
  `python bin/mlst_pipeline.py --assembly X.fasta --outdir DIR --label NAME`
  writing `DIR/mlst_result.json` with an `organism_token`; `amr_plus_gui` shells
  out to it. Guard for the sibling's absence.
- Keep the shared project layout (§1) so projects are portable across tools.
