import json
import os
from pathlib import Path
from typing import Any, Dict


def _user_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg) / "amr_plus_gui"
    return Path.home() / ".config" / "amr_plus_gui"


DATA_DIR = _user_config_dir()
CONFIG_PATH = DATA_DIR / "config.json"

_SHARED_PROJECTS_ROOT = Path("/srv/kapurlab/projects")
_DEFAULT_SHARED_PROJECTS_ROOT = (
    str(_SHARED_PROJECTS_ROOT) if _SHARED_PROJECTS_ROOT.is_dir() else ""
)


def _first_existing(*paths: str) -> str:
    """Return the first path that exists, else the first candidate (so the
    default is informative even on a fresh box)."""
    for p in paths:
        if p and Path(p).exists():
            return p
    return paths[0] if paths else ""


# Kraken2 DB used for organism detection. Prefer the richer PlusPF DB if it has
# been installed; fall back to the 8 GB standard DB that already exists.
_KRAKEN_DB_DEFAULT = _first_existing(
    "/srv/kapurlab/databases/kraken2/k2_standard_pluspf",
    "/srv/kapurlab/databases/kraken2/k2_standard_08gb",
)

# AMRFinderPlus database directory. Empty by default — `amrfinder` finds its
# own DB via $CONDA_PREFIX/share/amrfinderplus/data/latest when this is unset;
# set it explicitly only to pin a specific DB version.
_AMRFINDER_DB_DEFAULT = _first_existing(
    "/srv/kapurlab/databases/amrfinderplus/latest",
    "",
)

# CGE finders (PlasmidFinder etc.) live in a separate conda env with their DBs
# under a sibling databases dir. Empty by default — the pipeline soft-skips the
# CGE steps when these aren't set, so the GUI still works where CGE isn't
# installed. Set both at a site to enable plasmid/serotype/virulence typing.
# Probe known site roots so EVERY user gets the CGE paths by default — not just
# whoever's config was hand-set. "" if none exists (soft-skips the finders).
# TODO(site-parameterize): like the other hard-coded /srv/<site> defaults here,
# this should derive from a site-root env var rather than an enumerated list.
_CGE_ENV_DEFAULT = next(
    (p for p in ("/srv/kapurlab/tools/cge/env", "/srv/icar/tools/cge/env")
     if Path(p).is_dir()), ""
)
_CGE_DB_ROOT_DEFAULT = next(
    (p for p in ("/srv/kapurlab/databases/cge", "/srv/icar/databases/cge")
     if Path(p).is_dir()), ""
)

DEFAULTS: Dict[str, Any] = {
    "projects_root": str(Path.home() / "projects"),
    "shared_projects_root": _DEFAULT_SHARED_PROJECTS_ROOT,
    "kraken_db": _KRAKEN_DB_DEFAULT,
    "amrfinder_db": _AMRFINDER_DB_DEFAULT,
    "cge_env": _CGE_ENV_DEFAULT,
    "cge_db_root": _CGE_DB_ROOT_DEFAULT,
}


def load_config() -> Dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(DEFAULTS)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
