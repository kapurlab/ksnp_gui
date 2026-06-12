#!/usr/bin/env python
"""
ksnp_pipeline.py — orchestrator for the kSNP4 GUI.

kSNP4 is a *set* analysis: given many genome FASTAs it identifies SNPs without a
reference or genome alignment (k-mer based) and builds parsimony / ML / NJ SNP
trees plus core- and pan-SNP matrices and per-SNP VCFs. Unlike the per-sample
sibling tools, one run consumes a whole set of genomes for one project.

Pipeline (per project run):
  1. Stage inputs   — copy every selected FASTA into <outdir>/genomes/, normalise
                      the extension to .fasta and SANITISE the genome name
                      (kSNP rejects spaces and special characters). A
                      name_crosswalk.tsv records original -> sanitised.
  2. Input QC       — `seqkit stats -a` on every staged genome -> input_qc.json
                      (contigs, length, N50, GC, ambiguous bases) with an ISO-aware
                      pass/review verdict. The "quality stats of the input files".
  3. Build infile   — MakeKSNP4infile -indir genomes -outfile myInfile A
                      (falls back to writing the 2-column infile directly).
  4. Choose k       — Kchooser4 -in myInfile -> optimum k + FCK (fraction of core
                      k-mers; a defensible measure of how related the set is).
  5. kSNP4          — kSNP4 -in myInfile -outdir ksnp_run -CPU N -k K
                      [-vcf] [-core] [-ML] -min_frac F  (FASTA input is required).
  6. Provenance     — run_manifest.json capturing EVERY option, tool versions,
                      thresholds and the quality standards referenced.
  7. Report         — <label>_<date>_stats.xlsx (single labeled column) and
                      report.pdf (input QC, run summary, SNP/tree results, methods).

Output dir is <project>/ksnp/<label>/ (passed via --outdir). FASTA input is
REQUIRED — the tool refuses to run without at least two genome FASTAs.

Usage:
  ksnp_pipeline.py --label RUN --outdir DIR --inputs a.fasta b.fasta ...
      [--min-frac 0.8] [--no-core] [--no-ml] [--no-vcf] [--k N] [--threads N]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent

# Quality standards referenced in the provenance (for traceability). kSNP has no
# single ISO; these are the lab-quality and reproducibility frameworks that make
# a WGS SNP-typing result defensible and verifiable.
ISO_REFERENCES = [
    {"standard": "ISO 15189:2022", "scope": "Medical laboratories — quality & competence (traceability, validation, version control, result reporting)"},
    {"standard": "ISO/IEC 17025:2017", "scope": "Testing & calibration laboratory competence (method validation, reproducibility — surveillance/veterinary use)"},
    {"standard": "ISO 23418:2022", "scope": "Microbiology of the food chain — WGS for typing & genomic characterization of bacterial isolates (general requirements & guidance)"},
    {"standard": "GA4GH / PHA4GE", "scope": "Reproducible, portable WGS analysis — fixed tool+parameter provenance for verifiable phylogenomic findings"},
]

# Accepted input extensions; everything is normalised to .fasta for kSNP.
_FASTA_EXTS = (".fasta", ".fa", ".fna", ".fas", ".ffn", ".fsa")


def log(msg: str = "") -> None:
    print(msg, flush=True)


def step(title: str) -> None:
    log("")
    log(f"### {title}")


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run(cmd: List[str], cwd: Optional[Path] = None, env: Optional[dict] = None,
         capture: bool = False) -> Tuple[int, str]:
    """Run a command, streaming or capturing output. Returns (rc, captured_stdout)."""
    log(f"$ {' '.join(str(c) for c in cmd)}")
    try:
        if capture:
            proc = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                                  env=env, capture_output=True, text=True)
            if proc.stdout:
                log(proc.stdout.rstrip())
            if proc.stderr:
                log(proc.stderr.rstrip())
            return proc.returncode, proc.stdout or ""
        proc = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env)
        return proc.returncode, ""
    except FileNotFoundError:
        log(f"ERROR: command not found: {cmd[0]}")
        return 127, ""


def _tool_version(cmd: List[str]) -> Optional[str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        return out.splitlines()[0].strip() if out else None
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None


def _ksnp_version() -> Optional[str]:
    """kSNP4 / Kchooser4 have no clean --version flag (running them with no args
    prints a usage error). Derive the version from the resolved install path,
    e.g. .../kSNP4.1pkg/kSNP4 -> 'kSNP4.1'."""
    exe = shutil.which("kSNP4")
    if not exe:
        return None
    m = re.search(r"kSNP(\d+(?:\.\d+)?)", str(Path(exe).resolve()))
    return f"kSNP{m.group(1)}" if m else "kSNP4"


# ---------------------------------------------------------------------------
# Step 1 — stage + sanitise
# ---------------------------------------------------------------------------
def _sanitize_name(stem: str) -> str:
    """Make a kSNP-safe genome name.

    kSNP4 is strict: genome names must contain no spaces or special characters.
    Replace anything outside [A-Za-z0-9_.-] with '_', collapse repeats, trim
    leading/trailing separators. Never returns an empty string.
    """
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", stem)
    name = re.sub(r"_{2,}", "_", name).strip("_.-")
    return name or "genome"


def stage_inputs(inputs: List[Path], genomes_dir: Path, outdir: Path) -> List[Dict[str, str]]:
    """Copy each FASTA into genomes_dir as <sanitised>.fasta. De-duplicate names.
    Writes name_crosswalk.tsv and returns the staged records."""
    genomes_dir.mkdir(parents=True, exist_ok=True)
    used: Dict[str, int] = {}
    records: List[Dict[str, str]] = []
    for src in inputs:
        if not src.is_file():
            log(f"  WARNING: input not found, skipping: {src}")
            continue
        stem = src.name
        for ext in _FASTA_EXTS:
            if stem.lower().endswith(ext):
                stem = stem[: -len(ext)]
                break
        clean = _sanitize_name(stem)
        if clean in used:
            used[clean] += 1
            clean = f"{clean}_{used[clean]}"
        else:
            used[clean] = 1
        dest = genomes_dir / f"{clean}.fasta"
        shutil.copyfile(src, dest)
        records.append({"original": src.name, "original_path": str(src),
                        "name": clean, "staged": str(dest)})
        if src.name != f"{clean}.fasta":
            log(f"  {src.name}  ->  {clean}.fasta")
        else:
            log(f"  {clean}.fasta")
    # Crosswalk so the rename is auditable.
    cw = outdir / "name_crosswalk.tsv"
    with cw.open("w", encoding="utf-8") as fh:
        fh.write("original_file\tsanitized_name\tstaged_file\n")
        for r in records:
            fh.write(f"{r['original']}\t{r['name']}\t{Path(r['staged']).name}\n")
    return records


# ---------------------------------------------------------------------------
# Step 2 — input FASTA QC (seqkit stats -a)
# ---------------------------------------------------------------------------
def fasta_qc(records: List[Dict[str, str]], outdir: Path) -> Dict[str, Any]:
    """`seqkit stats -a -T` on every staged genome -> input_qc.json.

    Per-genome contigs / length / N50 / GC / ambiguous-base metrics, plus a
    set-level summary used to flag outliers (a genome far from the median length
    is usually a contamination/assembly problem and weakens the SNP set)."""
    qc: Dict[str, Any] = {"genomes": [], "summary": {}, "notes": []}
    if not _have("seqkit"):
        qc["notes"].append("seqkit not on PATH — input genome QC unavailable.")
        (outdir / "input_qc.json").write_text(json.dumps(qc, indent=2) + "\n", encoding="utf-8")
        return qc

    lengths: List[float] = []
    for r in records:
        path = Path(r["staged"])
        try:
            proc = subprocess.run(["seqkit", "stats", "-a", "-T", str(path)],
                                  capture_output=True, text=True, timeout=300)
            lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
            if len(lines) < 2:
                continue
            row = dict(zip(lines[0].split("\t"), lines[1].split("\t")))

            def num(k):
                try:
                    return float(str(row.get(k, "")).replace(",", ""))
                except (ValueError, AttributeError):
                    return None

            g = {
                "name": r["name"],
                "original": r["original"],
                "contigs": num("num_seqs"),
                "length": num("sum_len"),
                "min_len": num("min_len"),
                "avg_len": num("avg_len"),
                "max_len": num("max_len"),
                "n50": num("N50"),
                "gc_pct": num("GC(%)"),
                "n_pct": num("N(%)") if "N(%)" in row else None,
            }
            qc["genomes"].append(g)
            if g["length"]:
                lengths.append(g["length"])
        except (subprocess.SubprocessError, OSError) as exc:
            qc["notes"].append(f"seqkit stats failed for {r['name']}: {exc}")

    if lengths:
        lengths_sorted = sorted(lengths)
        n = len(lengths_sorted)
        median = (lengths_sorted[n // 2] if n % 2 else
                  (lengths_sorted[n // 2 - 1] + lengths_sorted[n // 2]) / 2)
        qc["summary"] = {
            "genome_count": len(qc["genomes"]),
            "median_length": median,
            "min_length": min(lengths),
            "max_length": max(lengths),
            "mean_gc_pct": round(sum(g["gc_pct"] for g in qc["genomes"] if g.get("gc_pct"))
                                 / max(1, sum(1 for g in qc["genomes"] if g.get("gc_pct"))), 2),
        }
        # Flag genomes whose length deviates >20% from the median (ISO 15189
        # validity check: outliers must be visible, not silently averaged in).
        outliers = []
        for g in qc["genomes"]:
            L = g.get("length")
            if L and median and abs(L - median) / median > 0.20:
                g["verdict"] = "review"
                outliers.append(g["name"])
            else:
                g["verdict"] = "pass"
        qc["summary"]["length_outliers"] = outliers
        if outliers:
            qc["notes"].append(
                f"{len(outliers)} genome(s) deviate >20% from the median length "
                f"({int(median):,} bp): {', '.join(outliers)}. Review for "
                "contamination / mis-assembly before trusting the tree."
            )
    (outdir / "input_qc.json").write_text(json.dumps(qc, indent=2) + "\n", encoding="utf-8")
    return qc


# ---------------------------------------------------------------------------
# Step 3 — MakeKSNP4infile
# ---------------------------------------------------------------------------
def build_infile(genomes_dir: Path, outdir: Path, records: List[Dict[str, str]]) -> Path:
    """Create the kSNP infile (`myInfile`: <path>\\t<name>).

    Prefer the bundled MakeKSNP4infile (so behaviour matches the validated CLI
    workflow); if it is missing or yields nothing, write the 2-column infile
    directly. Either way we keep only lines that point at a staged .fasta."""
    infile = outdir / "myInfile"
    made = False
    if _have("MakeKSNP4infile"):
        # 'A' = automatic: derive genome names from the (already sanitised) file
        # basenames, non-interactively. (The 'S'/semi-automatic mode prompts.)
        rc, _ = _run(["MakeKSNP4infile", "-indir", str(genomes_dir),
                      "-outfile", str(infile), "A"], capture=True)
        made = rc == 0 and infile.is_file() and infile.stat().st_size > 0

    if made:
        # Keep only real genome lines (defends against stray files in the dir).
        kept = []
        for line in infile.read_text(encoding="utf-8", errors="replace").splitlines():
            if ".fasta" in line and line.strip():
                kept.append(line.rstrip("\n"))
        infile.write_text("\n".join(kept) + "\n", encoding="utf-8")
        log(f"  MakeKSNP4infile listed {len(kept)} genome(s).")
    else:
        log("  MakeKSNP4infile unavailable/empty — writing infile directly.")
        with infile.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(f"{r['staged']}\t{r['name']}\n")
    return infile


# ---------------------------------------------------------------------------
# Step 4 — Kchooser4
# ---------------------------------------------------------------------------
def choose_k(infile: Path, outdir: Path) -> Dict[str, Any]:
    """Run Kchooser4 to pick the optimum k and capture FCK. Returns
    {k, fck, report}. k is None if Kchooser4 is unavailable."""
    result: Dict[str, Any] = {"k": None, "fck": None, "report": None}
    if not _have("Kchooser4"):
        log("  WARNING: Kchooser4 not on PATH — caller must supply -k.")
        return result
    # Kchooser4 derives its report filename by appending to the -in argument and
    # writes it into the CWD, so the infile MUST be passed as a bare basename
    # (an absolute path produces a report name with embedded slashes -> crash).
    rc, out = _run(["Kchooser4", "-in", infile.name], cwd=outdir, capture=True)
    text = out
    reports = sorted(outdir.glob("Kchooser4*.report")) + sorted(outdir.glob("*.report"))
    if reports:
        result["report"] = str(reports[0])
        text += "\n" + reports[0].read_text(encoding="utf-8", errors="replace")
    m = re.search(r"optimum value of k is\s+(\d+)", text, re.IGNORECASE)
    if m:
        result["k"] = int(m.group(1))
    mf = re.search(r"FCK\s*(?:is|=|:)?\s*([0-9.]+)", text, re.IGNORECASE)
    if mf:
        try:
            result["fck"] = float(mf.group(1))
        except ValueError:
            pass
    log(f"  optimum k = {result['k']}  FCK = {result['fck']}")
    return result


# ---------------------------------------------------------------------------
# Step 5 — kSNP4
# ---------------------------------------------------------------------------
def run_ksnp(infile: Path, outdir: Path, run_name: str, k: int, threads: int,
             min_frac: float, core: bool, ml: bool, vcf: bool) -> Tuple[int, List[str]]:
    """Run kSNP4 into outdir/run_name. Returns (rc, argv).

    Run with cwd=outdir and relative -in / -outdir so the kSNP4 scripts (which
    resolve several intermediates relative to the CWD) behave like the validated
    CLI workflow."""
    run_dir = outdir / run_name
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)  # kSNP4 wants to create it
    cmd = ["kSNP4", "-in", infile.name, "-outdir", run_name,
           "-CPU", str(threads), "-k", str(k), "-min_frac", str(min_frac)]
    if vcf:
        cmd.append("-vcf")
    if core:
        cmd.append("-core")
    if ml:
        cmd.append("-ML")
    rc, _ = _run(cmd, cwd=outdir)
    return rc, [str(c) for c in cmd]


# ---------------------------------------------------------------------------
# Result harvest — read kSNP outputs for the manifest / report
# ---------------------------------------------------------------------------
def _read_count(path: Path) -> Optional[int]:
    """COUNT_SNPs / COUNT_coreSNPs hold a single integer (sometimes with text)."""
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"(\d[\d,]*)", txt)
    return int(m.group(1).replace(",", "")) if m else None


def harvest(run_dir: Path) -> Dict[str, Any]:
    """Summarise the kSNP4 output dir (SNP counts + tree inventory)."""
    res: Dict[str, Any] = {"snps_all": None, "core_snps": None, "trees": [], "matrices": [], "vcf": []}
    if not run_dir.is_dir():
        return res
    res["snps_all"] = _read_count(run_dir / "COUNT_SNPs")
    res["core_snps"] = _read_count(run_dir / "COUNT_coreSNPs")
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file():
            continue
        n = p.name
        if n.endswith(".tre"):
            res["trees"].append(n)
        elif "matrix" in n.lower() and (n.endswith(".fasta") or n.endswith(".fa")):
            res["matrices"].append(n)
        elif n.startswith("VCF") or n.endswith(".vcf"):
            res["vcf"].append(n)
    return res


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="kSNP4 pipeline orchestrator.")
    ap.add_argument("--label", required=True, help="Run label (output subdir name).")
    ap.add_argument("--outdir", type=Path, required=True, help="Run output dir <project>/ksnp/<label>.")
    ap.add_argument("--inputs", nargs="+", type=Path, required=True, help="Genome FASTA files.")
    ap.add_argument("--min-frac", type=float, default=0.8,
                    help="Fraction of genomes a SNP locus must be present in (kSNP -min_frac).")
    ap.add_argument("--k", type=int, default=None, help="k-mer size (default: Kchooser4 optimum).")
    ap.add_argument("--no-core", action="store_true", help="Skip core-SNP analysis (-core).")
    ap.add_argument("--no-ml", action="store_true", help="Skip ML tree (-ML).")
    ap.add_argument("--no-vcf", action="store_true", help="Skip per-SNP VCF (-vcf).")
    ap.add_argument("--threads", type=int, default=max(1, min(8, (os.cpu_count() or 4))))
    args = ap.parse_args(argv)

    outdir: Path = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    started = _now()

    core = not args.no_core
    ml = not args.no_ml
    vcf = not args.no_vcf

    log("=" * 70)
    log(f"kSNP4 pipeline — run: {args.label}")
    log(f"  outdir:   {outdir}")
    log(f"  inputs:   {len(args.inputs)} file(s)")
    log(f"  options:  min_frac={args.min_frac} core={core} ML={ml} vcf={vcf} threads={args.threads}")
    log("=" * 70)

    # ---- Step 1: stage + sanitise (FASTA required) ----
    step("Step 1: Stage inputs + sanitise genome names")
    genomes_dir = outdir / "genomes"
    records = stage_inputs(args.inputs, genomes_dir, outdir)
    if len(records) < 2:
        log(f"ERROR: kSNP needs at least 2 genome FASTAs; got {len(records)}. "
            "FASTA input is required.")
        return 2
    log(f"  staged {len(records)} genome FASTA(s).")

    # ---- Step 2: input QC ----
    step("Step 2: Input file QC (seqkit stats)")
    qc = fasta_qc(records, outdir)
    for note in qc.get("notes", []):
        log(f"  - {note}")

    # ---- Step 3: infile ----
    step("Step 3: Build kSNP infile (MakeKSNP4infile)")
    infile = build_infile(genomes_dir, outdir, records)

    # ---- Step 4: choose k ----
    step("Step 4: Choose optimum k (Kchooser4)")
    kinfo = choose_k(infile, outdir)
    k = args.k or kinfo.get("k")
    if not k:
        log("ERROR: no k available (Kchooser4 produced none and --k not given).")
        return 1
    log(f"  using k = {k}{' (user override)' if args.k else ' (Kchooser4 optimum)'}")

    # ---- Step 5: kSNP4 ----
    step("Step 5: kSNP4 SNP discovery + trees")
    run_dir = outdir / "ksnp_run"
    rc, ksnp_argv = run_ksnp(infile, outdir, "ksnp_run", k, args.threads, args.min_frac, core, ml, vcf)
    if rc != 0:
        log(f"WARNING: kSNP4 exited with code {rc}.")
    results = harvest(run_dir)
    log(f"  SNPs(all)={results['snps_all']}  coreSNPs={results['core_snps']}  "
        f"trees={len(results['trees'])}  matrices={len(results['matrices'])}  vcf={len(results['vcf'])}")

    # ---- Step 6: provenance manifest ----
    step("Step 6: Writing provenance (run_manifest.json)")
    finished = _now()
    manifest: Dict[str, Any] = {
        "tool": "kSNP4",
        "label": args.label,
        "started_at": started,
        "finished_at": finished,
        "return_code": rc,
        "genome_count": len(records),
        "genomes": [r["name"] for r in records],
        "name_crosswalk": str(outdir / "name_crosswalk.tsv"),
        "command": ksnp_argv,
        "options": {
            "k": k,
            "k_source": "user" if args.k else "Kchooser4_optimum",
            "min_frac": args.min_frac,
            "core": core,
            "ML": ml,
            "vcf": vcf,
            "threads": args.threads,
        },
        "kchooser": {"optimum_k": kinfo.get("k"), "fck": kinfo.get("fck"),
                     "report": kinfo.get("report")},
        "results": results,
        "outputs": {
            "ksnp_run_dir": str(run_dir),
            "infile": str(infile),
            "input_qc": str(outdir / "input_qc.json"),
        },
        "versions": {
            "kSNP4": _ksnp_version(),
            "Kchooser4": _ksnp_version(),   # bundled in the same kSNP package
            "seqkit": _tool_version(["seqkit", "version"]),
        },
        "thresholds_note": (
            "min_frac is the minimum fraction of genomes in which a SNP locus must "
            "be present to be reported (a 'core' SNP is present in ALL genomes). "
            "k is chosen by Kchooser4 as the shortest k at which most k-mers are "
            "unique, maximising SNP specificity. FCK (fraction of core k-mers) "
            "measures set relatedness; very low FCK means the genomes are too "
            "divergent for a confident core-SNP tree."
        ),
        "iso_references": ISO_REFERENCES,
        "input_qc_summary": qc.get("summary", {}),
    }
    (outdir / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # ---- Step 7: report ----
    step("Step 7: Building report (stats.xlsx + report.pdf)")
    try:
        import reporting  # bin/ is on PYTHONPATH
        reporting.build(outdir, args.label, log=log)
    except Exception as exc:  # noqa: BLE001 — never fail the run over the report
        log(f"  WARNING: report generation failed: {exc}")

    step("Pipeline completed")
    log(f"kSNP4 return code: {rc}")
    log(f"Outputs in: {outdir}")
    return 0 if rc == 0 else rc


if __name__ == "__main__":
    sys.exit(main())
