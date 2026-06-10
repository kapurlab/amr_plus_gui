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

The env (`environment.yml`) provides AMRFinderPlus (+ StxTyper), mlst, kraken2,
shovill, spades, seqkit, and the FastAPI web layer. `environment_minimal.yml`
is a backend-only subset for quick testing.
