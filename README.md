# AMRFinderPlus GUI

A web GUI for **NCBI AMRFinderPlus** — acquired antimicrobial-resistance genes,
resistance point mutations, and (with `--plus`) virulence + stress / biocide /
metal / acid-resistance content. Built as an Open OnDemand batch_connect tool
and a **sibling of `vsnp_gui` and `kraken_id_parse_gui`**, sharing their look,
project layout, and conventions.

## Why organism resolution matters

AMRFinderPlus does **not** identify the organism — the caller supplies
`--organism`. Passing the **wrong** organism produces wrong point-mutation calls
and wrong gene blacklisting (worse than omitting it). So this tool resolves the
organism **upstream**, conservatively, with corroboration and an audit trail:

1. **Kraken2** on the reads → dominant species + share of classified reads.
2. **Dominance policy** (configurable):
   - dominant ≥ 70% **and** runner-up < 10% → *pure*, confidence **high**;
   - top two in the same species complex → *pure (collapsed)*, **medium**;
   - otherwise → *mixed/contaminated*: **no** auto `--organism`, confidence none.
3. **Map** species → AMRFinderPlus token via `config/organism_map.yaml`
   (exact species, complex collapse, then genus fallback). Unmapped → no token.
4. **Validate** the token against the live `amrfinder -l` list (DB-version
   dependent), with `config/amrfinder_organisms.txt` as a fallback.
5. **MLST corroboration** (optional): agreement → confidence **high**,
   source `both`; conflict → flag it and prefer **not** auto-assigning.
6. **Force override**: a user-selected organism wins; detection's would-be
   choice is still recorded for the audit trail.

The full call is written to `organism_detection.json`.

## Pipeline (`bin/amr_pipeline.py`)

```
reads (R1[/R2]) or assembly FASTA
  → Kraken2 detection            (bin/detect_organism.py)
  → assembly                     (shovill, else spades.py --isolate)
  → assembly QC                  (seqkit stats → qc.json, pass/review)
  → MLST corroboration           (mlst_gui runner or `mlst` binary)
  → AMRFinderPlus                (bin/run_amrfinder.py: --plus, --mutation_all,
                                  --print_node, --name, thresholds)
  → provenance                   (run_manifest.json: every option, tool+DB
                                  versions, thresholds, QC, ISO references)
```

Outputs land in `<project>/amr/<sample>/`:
`amrfinder.tsv`, `mutation_all.tsv`, `organism_detection.json`, `qc.json`,
`assembly.fasta`, `mlst_result.json`, `kraken_report.txt`, `run_manifest.json`.

`--mutation_all` keeps "position assessed, no resistance mutation" so negative
findings are reportable. `run_manifest.json` records ISO 20776-1/-2, ISO
15189:2022, ISO/IEC 17025, and EUCAST/CLSI breakpoints for defensible reporting.

## Layout

```
backend/app/        FastAPI app (main.py), config.py, jobs.py, sra.py
bin/                amr_pipeline.py, detect_organism.py, run_amrfinder.py
config/             organism_map.yaml, amrfinder_organisms.txt, genome_sizes.yaml
frontend/           React (Vite) SPA — built to frontend/dist/
conda_setup/        environment.yml (amr_plus)
deploy/             install.sh, INSTALL.md
ood/apps/           amr_plus_gui (prod) + amr_plus_gui_dev (branch picker)
```

Projects are shared across all three GUIs: a project dir holds `download/`
(input reads/assemblies), `amr/<sample>/`, plus the vSNP-compatible `step1/`,
`step2/`, `<name>_VCFs/`. Projects live in `/srv/kapurlab/projects` (shared)
and `~/projects` (personal).

## Install & run

See [deploy/INSTALL.md](deploy/INSTALL.md). Quick start:

```bash
deploy/install.sh                 # conda env + DBs + frontend build
# register the OOD app, then launch a session from the dashboard
```

The backend is `uvicorn app.main:app`; it serves the SPA from `frontend/dist/`
on a single OOD-allocated port.

## API (relative URLs only)

`/api/projects`, `/api/projects/{n}/samples`, `/api/config`,
`/api/organism-options` (cached `amrfinder -l`), `/api/run`, `/api/jobs`,
`/api/jobs/{id}/log` (SSE), `/api/projects/{n}/samples/{s}/amr-results`,
`/api/projects/{n}/samples/{s}/amr-table` (parsed TSV + organism + provenance).
