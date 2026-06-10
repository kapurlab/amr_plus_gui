"""CLI: regenerate the stats workbook + PDF for an existing run dir.

    python -m reporting --outdir <dir> --sample <name>
"""
import argparse
import json
from pathlib import Path

from . import build

ap = argparse.ArgumentParser(description="Build AMR stats.xlsx + report.pdf for a run dir.")
ap.add_argument("--outdir", type=Path, required=True)
ap.add_argument("--sample", required=True)
args = ap.parse_args()
print(json.dumps(build(args.outdir, args.sample), indent=2))
