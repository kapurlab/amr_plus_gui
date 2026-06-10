# AMRFinderPlus GUI — Environment Setup

Setup is automated by **`deploy/install.sh`** and documented in
**`deploy/INSTALL.md`**. This file is a short pointer to avoid drift.

## Quick start

```bash
cd /srv/kapurlab/tools/amr_plus_gui
deploy/install.sh            # shared env at ./env  (use --personal for ~/env)
```

That script:
1. creates the conda env from `conda_setup/environment.yml`,
2. `pip install -r backend/requirements.txt`,
3. `amrfinder -u` to download the AMRFinderPlus database,
4. ensures a Kraken2 DB is reachable (PlusPF preferred),
5. confirms `mlst` / PubMLST,
6. builds the React frontend (`frontend/dist/`).

It is idempotent — re-run any time. See `deploy/INSTALL.md` for flags, database
locations, paths to change for another site, and OOD app registration.
