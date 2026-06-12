"""CLI: regenerate the stats workbook + PDF for an existing run dir.

    python -m reporting --outdir <dir> --label <name>
"""
import argparse
import json
from pathlib import Path

from . import build

ap = argparse.ArgumentParser(description="Build kSNP stats.xlsx + report.pdf for a run dir.")
ap.add_argument("--outdir", type=Path, required=True)
ap.add_argument("--label", required=True)
args = ap.parse_args()
print(json.dumps(build(args.outdir, args.label), indent=2))
