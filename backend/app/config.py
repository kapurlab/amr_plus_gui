import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def _user_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg) / "amr_plus_gui"
    return Path.home() / ".config" / "amr_plus_gui"


DATA_DIR = _user_config_dir()
CONFIG_PATH = DATA_DIR / "config.json"

# The multi-user "shared projects" root, if this deployment has one. A laptop
# normally does not — only a lab server or an OOD site. Never assume a path:
# probe in order of authority and fall back to None (no shared root) rather than
# a fictional one, so macOS/WSL users aren't shown a directory that cannot exist.
#   1. BDTOOLS_SHARED_PROJECTS_ROOT — set by a site deployment or the launcher.
#      An explicitly empty value is authoritative: it DISABLES the shared root.
#   2. the user's own `shared_projects_root` setting — see shared_projects_root()
#   3. the historical lab-server path, LAST, so existing servers keep working
_LEGACY_SHARED_PROJECTS_ROOT = Path("/srv/kapurlab/projects")
_ENV_SHARED_PROJECTS_ROOT = "BDTOOLS_SHARED_PROJECTS_ROOT"


def _default_shared_projects_root() -> str:
    env = os.environ.get(_ENV_SHARED_PROJECTS_ROOT)
    if env is not None:
        return env.strip()
    try:
        if _LEGACY_SHARED_PROJECTS_ROOT.is_dir():
            return str(_LEGACY_SHARED_PROJECTS_ROOT)
    except OSError:
        pass
    return ""


_DEFAULT_SHARED_PROJECTS_ROOT = _default_shared_projects_root()


def shared_projects_root() -> Optional[Path]:
    """The resolved shared-projects root, or None when this deployment has none.

    Read through this rather than a module constant, so the Settings value is
    actually honoured: main.py used to carry its own hard-coded literal, which
    meant setting `shared_projects_root` in the GUI changed the default shown in
    Settings but nothing about where projects were discovered.

    Returns None — never Path("") — because Path("") is Path("."), the current
    working directory. An "unset" sentinel that silently means "look in ." would
    turn a missing shared root into project lookups against wherever uvicorn
    happens to be running."""
    env = os.environ.get(_ENV_SHARED_PROJECTS_ROOT)
    if env is not None:
        return Path(env.strip()) if env.strip() else None
    try:
        configured = str(load_config().get("shared_projects_root", "") or "").strip()
    except Exception:
        configured = ""
    if configured:
        return Path(configured)
    return Path(_DEFAULT_SHARED_PROJECTS_ROOT) if _DEFAULT_SHARED_PROJECTS_ROOT else None


def _first_existing(*paths: str) -> str:
    """Return the first path that actually exists, else "".

    Returns "" — not the first candidate — when none exist. A candidate that
    doesn't exist is not a useful default: on macOS or WSL it put a Linux server
    path like /srv/kapurlab/databases/... into Settings, which reads as "already
    configured" while pointing at a directory that can never be there. Empty is
    honest, and the GUI already renders it as "not configured".

    Paths under another account's home raise PermissionError from .exists()
    rather than returning False, so treat any OSError as absent."""
    for p in paths:
        if not p:
            continue
        try:
            if Path(p).exists():
                return p
        except OSError:
            continue
    return ""


def _db_root() -> Path:
    """Where reference databases live on THIS machine.

    `bdtools setup-databases` asks once and records the answer in
    <BDTOOLS_HOME>/db-root — home, shared, or an arbitrary directory. Read that
    rather than assuming a site layout, so the same code is right on a laptop, a
    WSL box, the lab server, and another institution's cluster."""
    env = os.environ.get("BDTOOLS_DB_ROOT", "").strip()
    if env:
        return Path(env)
    home = os.environ.get("BDTOOLS_HOME", "").strip()
    if not home:
        xdg = os.environ.get("XDG_DATA_HOME", "").strip()
        home = str(Path(xdg) / "bdtools") if xdg else str(Path.home() / ".local/share/bdtools")
    try:
        recorded = (Path(home) / "db-root").read_text(encoding="utf-8").strip()
        if recorded:
            return Path(recorded)
    except OSError:
        pass
    for cand in (Path.home() / "databases", Path("/srv/kapurlab/databases")):
        try:
            if cand.is_dir():
                return cand
        except OSError:
            continue
    return Path.home() / "databases"


_DB_ROOT = _db_root()

# Kraken2 DB used for organism detection. Prefer the richer PlusPF DB if it has
# been installed; fall back to the 8 GB standard DB `setup-databases` fetches.
# Both are resolved under this machine's db-root, not a fixed site path.
_KRAKEN_DB_DEFAULT = _first_existing(
    str(_DB_ROOT / "kraken2" / "k2_standard_pluspf"),
    str(_DB_ROOT / "kraken2" / "k2_standard_08gb"),
    "/srv/kapurlab/databases/kraken2/k2_standard_pluspf",   # legacy server layout
    "/srv/kapurlab/databases/kraken2/k2_standard_08gb",
)

# AMRFinderPlus database directory. Empty by default — `amrfinder` finds its
# own DB via $CONDA_PREFIX/share/amrfinderplus/data/latest when this is unset;
# set it explicitly only to pin a specific DB version.
_AMRFINDER_DB_DEFAULT = _first_existing(
    str(_DB_ROOT / "amrfinderplus" / "latest"),
    "/srv/kapurlab/databases/amrfinderplus/latest",         # legacy server layout
)

DEFAULTS: Dict[str, Any] = {
    "projects_root": str(Path.home() / "projects"),
    "shared_projects_root": _DEFAULT_SHARED_PROJECTS_ROOT,
    "saved_project_roots": [],
    "kraken_db": _KRAKEN_DB_DEFAULT,
    "amrfinder_db": _AMRFINDER_DB_DEFAULT,
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
