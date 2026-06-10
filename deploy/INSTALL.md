# Deploying the AMRFinderPlus GUI on an Open OnDemand system

This tool is a sibling of `vsnp_gui` and `kraken_id_parse_gui`: a FastAPI
backend serving a React SPA, launched as an OOD batch_connect interactive app
behind an Apache `mod_proxy` at `/rnode/<host>/<port>/`.

## 1. Get the code

Clone/copy the repo to a shared location (the Kapur Lab uses
`/srv/kapurlab/tools/amr_plus_gui`). All site paths are kept in
`backend/app/config.py` DEFAULTS + env vars, so porting is mostly editing those.

## 2. Build the conda env + databases

```bash
cd /path/to/amr_plus_gui
deploy/install.sh                 # shared env at ./env
# or
deploy/install.sh --personal      # conda env named amr_plus
```

`install.sh` is idempotent. Useful flags:

| Flag | Effect |
|---|---|
| `--personal` | use a personal conda env instead of `<repo>/env` |
| `--kraken-db DIR` | point at an existing Kraken2 DB |
| `--skip-amrfinder-db` | don't run `amrfinder -u` |
| `--skip-kraken-db` | don't check/download the Kraken2 DB |
| `--skip-frontend` | don't rebuild `frontend/dist/` |
| `--dry-run` | print what it would do |

### Databases

- **AMRFinderPlus DB** — downloaded by `amrfinder -u` into
  `$CONDA_PREFIX/share/amrfinderplus/data/latest`. The GUI auto-detects it; set
  `amrfinder_db` in config only to pin a specific version.
- **Kraken2 DB** — used for read-based organism detection. PlusPF preferred at
  `/srv/kapurlab/databases/kraken2/k2_standard_pluspf`, else the existing
  `k2_standard_08gb`. Configurable via Settings or `config.json`.
- **PubMLST schemes** — ship with the bioconda `mlst` package.

## 3. Paths to change for another site

Edit `backend/app/config.py` `DEFAULTS`:

| Key | Default | Change to your site |
|---|---|---|
| `projects_root` | `~/projects` | per-user personal projects |
| `shared_projects_root` | `/srv/kapurlab/projects` | your shared project tree |
| `kraken_db` | PlusPF, else 8 GB std | your Kraken2 DB dir |
| `amrfinder_db` | first existing / blank | usually leave blank |

Also update the shared paths in `backend/app/main.py`
(`_SHARED_PROJECTS`) and the OOD `script.sh.erb` files
(`/srv/kapurlab/tools/amr_plus_gui`) to your install root.

## 4. Register the OOD app

Copy (or symlink) the app definition into your OOD apps tree:

```bash
# system app:
sudo cp -r ood/apps/amr_plus_gui /var/www/ood/apps/sys/amr_plus_gui
# or a sandbox/dev app under your home:
ln -s /path/to/amr_plus_gui/ood/apps/amr_plus_gui_dev ~/ondemand/dev/amr_plus_gui
```

The app provides:
- `manifest.yml` — display name "AMRFinderPlus", icon `fa://shield-virus`,
  category Bioinformatics / Antimicrobial Resistance.
- `form.yml` — session duration (dev variant adds a Git branch picker).
- `submit.yml.erb` — basic batch_connect template, `port` conn param.
- `template/before.sh` — allocates `$port` via the OOD `find_port` helper.
- `template/script.sh.erb` — activates the conda env, sets `PYTHONPATH` to
  `bin/`, and execs `uvicorn app.main:app` on `$port`.

The dev variant (`amr_plus_gui_dev`) checks out a chosen Git branch into a
per-session `/tmp` worktree and rebuilds the frontend from that branch.

## 5. Critical constraints (shared with the siblings)

- **All frontend URLs are relative** (`./api/...`). Never hardcode host/port.
  `vite.config.js` has `base: "./"` — keep it.
- **FastAPI serves `frontend/dist/`** as StaticFiles. No separate static server.
- **Rebuild the frontend** after any `frontend/src` edit
  (`cd frontend && npm run build`), then start a fresh OOD session.
- The conda env owns `amrfinder`, `mlst`, `kraken2`, `shovill`, `spades`,
  `seqkit`. `PATH` is set to the env's `bin/` in `script.sh.erb`.

## 6. Smoke test

```bash
ENVPY=/path/to/amr_plus_gui/env/bin/python   # or your personal env
cd /path/to/amr_plus_gui/backend
$ENVPY -m uvicorn app.main:app --host 127.0.0.1 --port 8080
curl -s localhost:8080/api/organism-options | head
```
