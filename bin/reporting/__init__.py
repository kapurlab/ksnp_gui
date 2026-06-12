"""
kSNP4 GUI — report builder.

Produces two deliverables from a completed run directory:

  <label>_<date>_stats.xlsx
      A single labeled column of statistics (column A = label, column B =
      value), modelled on the vSNP3 stats workbook so the tools read the same
      way. Input-genome QC, run options, SNP/tree results and provenance in one
      flat, labeled list.

  report.pdf
      A human-readable PDF: input genome quality, analysis summary (with figures
      when matplotlib is available — a genome-length bar and the ML tree), the
      main SNP/tree results, and a methods/provenance page with the quality
      standards referenced.

Both are best-effort: a missing artifact or a missing optional dependency
(reportlab / matplotlib) degrades gracefully and is reported in the log rather
than failing the pipeline.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _fmt_int(v: Any) -> str:
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "—" if v in (None, "") else str(v)


def _fmt_pct(v: Any, dp: int = 2) -> str:
    try:
        return f"{float(v):.{dp}f}%"
    except (TypeError, ValueError):
        return "—" if v in (None, "") else str(v)


# ---------------------------------------------------------------------------
# Build the ordered, labeled stats list (one metric per row)
# ---------------------------------------------------------------------------
def build_stats_items(label: str, date_stamp: str, qc: Dict[str, Any],
                      manifest: Dict[str, Any]) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    opts = manifest.get("options", {}) or {}
    vers = manifest.get("versions", {}) or {}
    res = manifest.get("results", {}) or {}
    kch = manifest.get("kchooser", {}) or {}
    summ = qc.get("summary", {}) or {}

    # — Run —
    items.append(("run_label", label))
    items.append(("date", date_stamp))
    items.append(("Pipeline", manifest.get("tool", "kSNP4")))
    items.append(("Genomes analyzed", _fmt_int(manifest.get("genome_count"))))

    # — Input genome quality (set level) —
    items.append(("Median genome length (bp)", _fmt_int(summ.get("median_length"))))
    items.append(("Min genome length (bp)", _fmt_int(summ.get("min_length"))))
    items.append(("Max genome length (bp)", _fmt_int(summ.get("max_length"))))
    items.append(("Mean GC (%)", _fmt_pct(summ.get("mean_gc_pct"))))
    outliers = summ.get("length_outliers") or []
    items.append(("Length outliers (>20% from median)",
                  ", ".join(outliers) if outliers else "none"))

    # — k selection —
    items.append(("Optimum k (Kchooser4)", str(kch.get("optimum_k") or "—")))
    items.append(("k used", str(opts.get("k") or "—")))
    items.append(("k source", opts.get("k_source", "—")))
    items.append(("FCK (fraction of core k-mers)", str(kch.get("fck") if kch.get("fck") is not None else "—")))

    # — kSNP results —
    items.append(("Total SNPs (all)", _fmt_int(res.get("snps_all"))))
    items.append(("Core SNPs", _fmt_int(res.get("core_snps"))))
    items.append(("min_frac", str(opts.get("min_frac", "—"))))
    items.append(("Core analysis (-core)", "yes" if opts.get("core") else "no"))
    items.append(("ML tree (-ML)", "yes" if opts.get("ML") else "no"))
    items.append(("Per-SNP VCF (-vcf)", "yes" if opts.get("vcf") else "no"))
    items.append(("Trees produced", _fmt_int(len(res.get("trees") or []))))
    items.append(("SNP matrices produced", _fmt_int(len(res.get("matrices") or []))))
    items.append(("VCF files produced", _fmt_int(len(res.get("vcf") or []))))

    # — Methods / provenance —
    items.append(("threads", str(opts.get("threads", "—"))))
    items.append(("kSNP4 version", vers.get("kSNP4") or "—"))
    items.append(("Kchooser4 version", vers.get("Kchooser4") or "—"))
    items.append(("seqkit version", vers.get("seqkit") or "—"))
    iso = [r.get("standard") for r in (manifest.get("iso_references") or []) if r.get("standard")]
    items.append(("Standards referenced", ", ".join(iso) if iso else "—"))
    items.append(("Run return code", str(manifest.get("return_code", "—"))))
    return items


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def build(outdir: Path, label: str, log=print) -> Dict[str, Optional[str]]:
    """Build stats.xlsx + report.pdf for a finished run dir. Returns the paths
    (or None for any artifact that couldn't be produced). Never raises."""
    outdir = Path(outdir)
    result: Dict[str, Optional[str]] = {"stats_xlsx": None, "report_pdf": None}

    qc = _load_json(outdir / "input_qc.json")
    manifest = _load_json(outdir / "run_manifest.json")

    date_stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    items = build_stats_items(label, date_stamp, qc, manifest)

    # --- stats workbook (single labeled column) ---
    try:
        from .stats_excel import write_stats_xlsx
        xlsx_path = outdir / f"{label}_{date_stamp}_stats.xlsx"
        write_stats_xlsx(items, xlsx_path, label)
        result["stats_xlsx"] = str(xlsx_path)
        log(f"  wrote {xlsx_path.name}")
    except Exception as exc:  # noqa: BLE001
        log(f"  WARNING: stats workbook not written: {exc}")

    # --- PDF report ---
    try:
        from .pdf_report import write_pdf
        pdf_path = outdir / "report.pdf"
        ctx = {"label": label, "date": date_stamp, "qc": qc, "manifest": manifest,
               "stats_items": items}
        write_pdf(ctx, pdf_path, outdir)
        result["report_pdf"] = str(pdf_path)
        log(f"  wrote {pdf_path.name}")
    except Exception as exc:  # noqa: BLE001
        log(f"  WARNING: PDF report not written ({exc}). Is reportlab installed?")

    return result


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Build kSNP stats.xlsx + report.pdf for a run dir.")
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--label", required=True)
    args = ap.parse_args()
    print(json.dumps(build(args.outdir, args.label), indent=2))
