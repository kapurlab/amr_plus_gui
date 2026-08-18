# Conda Setup — AMRFinderPlus GUI

The canonical, idempotent setup is **`deploy/install.sh`** (creates the env,
downloads the AMRFinderPlus DB, ensures the Kraken2 DB, builds the frontend).
See **`deploy/INSTALL.md`** for the full porting guide.

Manual env creation:

```bash
# shared env at <repo>/env
conda env create -p /srv/kapurlab/tools/amr_plus_gui/env -f conda_setup/environment.yml
# or a personal env named amr_plus
conda env create -f conda_setup/environment.yml

# then download the AMRFinderPlus database
conda run -p /srv/kapurlab/tools/amr_plus_gui/env amrfinder -u
```

The env (`environment.yml`) provides AMRFinderPlus (+ StxTyper), shovill,
spades, seqkit, and the FastAPI web layer. mlst and kraken2 are deliberately
NOT in it: they run from the sibling mlst_gui / kraken_id_parse_gui envs (see
`bin/amr_pipeline.py`), which is what lets this env track current
AMRFinderPlus releases. `environment_minimal.yml` is a backend-only subset
for quick testing.
