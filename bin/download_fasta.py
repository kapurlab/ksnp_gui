#!/usr/bin/env python
"""
download_fasta.py — fetch genome FASTAs by accession into a project's download/.

Two accession kinds, current best practice for each:

  * Assembly accessions (GCA_/GCF_) -> NCBI **datasets** CLI
        datasets download genome accession <acc> --include genome
    The assembly's metadata (organism name + strain/isolate) is read from the
    bundled assembly_data_report.jsonl and used to build a *meaningful* output
    name — the assembly Name differs from the GCA/GCF number, so the FASTA is
    saved as "<organism>_<strain>_<accession>.fasta" (sanitised) rather than the
    bare accession. (Mirrors vsnp3_download_GCA_fasta_get_metadata.py.)

  * Nucleotide accessions (everything else, e.g. NC_/CP_/MN…) -> NCBI eutils
        efetch.fcgi?db=nuccore&id=<acc>&rettype=fasta&retmode=text
    saved as "<organism>_<accession>.fasta" (organism parsed from the defline),
    with in-process rate limiting + 429 backoff (sturdier than a bare efetch).

Names are deliberately informative so kSNP trees/matrices carry useful metadata;
they can be edited afterwards in the GUI. A fasta_download_crosswalk.tsv records
accession -> organism/strain -> output file for provenance. Each accession
soft-fails independently so one bad ID doesn't sink the batch. kSNP needs FASTA,
so this is the primary way to assemble a project's genome set.

Usage:
  download_fasta.py --outdir DIR --accessions GCA_000195835.3 NC_045512.2 ...
      [--no-rename] [--email you@example.org]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_NCBI_API_KEY = (os.environ.get("NCBI_API_KEY") or "").strip()
_MIN_INTERVAL = 0.11 if _NCBI_API_KEY else 0.40
_RETRY_BACKOFFS = (1.0, 2.0, 4.0, 8.0)
_EMAIL = (os.environ.get("NCBI_EMAIL") or "ksnp_gui@kapurlab.local").strip()
_last_call_at = 0.0

_ASSEMBLY_RE = re.compile(r"^GC[AF]_\d+(?:\.\d+)?$", re.IGNORECASE)
_FNA_GLOBS = ("*_genomic.fna", "*.fna", "*.fasta", "*.fa")


def log(msg: str = "") -> None:
    print(msg, flush=True)


def _sanitize(stem: str, maxlen: int = 80) -> str:
    """kSNP-safe name stem: keep [A-Za-z0-9_-], replace the rest with '_'.

    Dots are removed too — kSNP allows only one '.' per filename (the extension
    separator), so an accession version like 'GCA_000195835.3' must become
    'GCA_000195835_3' in the name (the '.fasta' is added by the caller)."""
    name = re.sub(r"[^A-Za-z0-9_-]", "_", stem)
    name = re.sub(r"_{2,}", "_", name).strip("_-")
    return (name or "genome")[:maxlen].strip("_-") or "genome"


def _is_assembly(acc: str) -> bool:
    return bool(_ASSEMBLY_RE.match(acc.strip()))


# ---------------------------------------------------------------------------
# eutils GET (rate-limited, 429-backoff) — for nucleotide efetch
# ---------------------------------------------------------------------------
def _eutils_get(url: str, timeout: int = 60) -> bytes:
    global _last_call_at
    if _NCBI_API_KEY and "api_key=" not in url:
        url += ("&" if "?" in url else "?") + "api_key=" + _NCBI_API_KEY
    for attempt in range(len(_RETRY_BACKOFFS) + 1):
        elapsed = time.monotonic() - _last_call_at
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                _last_call_at = time.monotonic()
                return resp.read()
        except urllib.error.HTTPError as e:
            _last_call_at = time.monotonic()
            if e.code in (429, 500, 502, 503) and attempt < len(_RETRY_BACKOFFS):
                time.sleep(_RETRY_BACKOFFS[attempt])
                continue
            raise
    raise RuntimeError("unreachable")


def _organism_from_defline(defline: str) -> str:
    """'>NC_045512.2 Severe acute respiratory syndrome coronavirus 2 ...' ->
    'Severe acute respiratory syndrome coronavirus 2' (trimmed)."""
    text = defline.lstrip(">").strip()
    parts = text.split(None, 1)
    rest = parts[1] if len(parts) > 1 else ""
    # drop trailing ', complete genome' etc.
    rest = re.split(r",\s|\s\(", rest)[0]
    return " ".join(rest.split()[:8]).strip()


# ---------------------------------------------------------------------------
# Nucleotide accession -> efetch FASTA
# ---------------------------------------------------------------------------
def fetch_nucleotide(acc: str, outdir: Path, rename: bool) -> Dict[str, str]:
    rec = {"accession": acc, "type": "nucleotide", "organism": "", "strain": "",
           "output_file": "", "status": "ok"}
    params = urllib.parse.urlencode({
        "db": "nuccore", "id": acc, "rettype": "fasta", "retmode": "text",
        "tool": "ksnp_gui", "email": _EMAIL,
    })
    data = _eutils_get(f"{_EUTILS}/efetch.fcgi?{params}")
    text = data.decode("utf-8", "replace")
    if not text.lstrip().startswith(">"):
        raise ValueError(f"efetch did not return FASTA for {acc}: {text[:120]!r}")
    first = text.splitlines()[0]
    organism = _organism_from_defline(first)
    rec["organism"] = organism
    base = _sanitize(f"{organism}_{acc}") if (rename and organism) else _sanitize(acc)
    out = _unique(outdir / f"{base}.fasta")
    out.write_text(text, encoding="utf-8")
    rec["output_file"] = out.name
    log(f"  [nuccore] {acc} -> {out.name}  ({organism or 'no organism in defline'})")
    return rec


# ---------------------------------------------------------------------------
# Assembly accession -> datasets CLI
# ---------------------------------------------------------------------------
def _read_assembly_report(extract_dir: Path) -> Dict[str, str]:
    """Pull organism + strain/isolate from assembly_data_report.jsonl."""
    info = {"organism": "", "strain": "", "accession": ""}
    reports = list(extract_dir.rglob("assembly_data_report.jsonl"))
    if not reports:
        return info
    try:
        line = reports[0].read_text(encoding="utf-8", errors="replace").splitlines()[0]
        rec = json.loads(line)
    except (OSError, ValueError, IndexError):
        return info
    info["accession"] = rec.get("accession", "")
    org = rec.get("organism", {}) or {}
    info["organism"] = org.get("organismName", "") or ""
    infra = org.get("infraspecificNames", {}) or {}
    info["strain"] = infra.get("strain") or infra.get("isolate") or ""
    return info


def fetch_assembly(acc: str, outdir: Path, rename: bool) -> Dict[str, str]:
    rec = {"accession": acc, "type": "assembly", "organism": "", "strain": "",
           "output_file": "", "status": "ok"}
    if shutil.which("datasets") is None:
        raise RuntimeError("NCBI 'datasets' CLI not on PATH — cannot fetch assembly "
                           f"{acc}. Install ncbi-datasets-cli (deploy/install.sh).")
    work = outdir / ".fasta_dl_tmp" / _sanitize(acc)
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    zip_path = work / f"{_sanitize(acc)}.zip"
    cmd = ["datasets", "download", "genome", "accession", acc,
           "--include", "genome", "--no-progressbar", "--filename", str(zip_path)]
    log(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not zip_path.is_file():
        raise RuntimeError(f"datasets download failed for {acc}: "
                           f"{(proc.stderr or proc.stdout or '').strip()[:200]}")
    extract = work / "extracted"
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract)

    meta = _read_assembly_report(extract)
    rec["organism"] = meta.get("organism", "")
    rec["strain"] = meta.get("strain", "")

    fna = None
    data_dir = extract / "ncbi_dataset" / "data"
    for pat in _FNA_GLOBS:
        hits = sorted((data_dir).rglob(pat)) if data_dir.is_dir() else sorted(extract.rglob(pat))
        if hits:
            fna = hits[0]
            break
    if fna is None:
        raise RuntimeError(f"no genome FASTA found in datasets package for {acc}")

    if rename and rec["organism"]:
        stem = rec["organism"]
        if rec["strain"] and _sanitize(rec["strain"]) not in _sanitize(stem):
            stem = f"{stem}_{rec['strain']}"
        base = _sanitize(f"{stem}_{acc}")
    else:
        base = _sanitize(acc)
    out = _unique(outdir / f"{base}.fasta")
    shutil.copyfile(fna, out)
    rec["output_file"] = out.name
    shutil.rmtree(work, ignore_errors=True)
    log(f"  [assembly] {acc} -> {out.name}  ({rec['organism']} {rec['strain']})".rstrip())
    return rec


def _unique(path: Path) -> Path:
    """Avoid clobbering an existing file: append _2, _3, … before the suffix."""
    if not path.exists():
        return path
    i = 2
    while True:
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
        i += 1


def _write_crosswalk(outdir: Path, records: List[Dict[str, str]]) -> None:
    cw = outdir / "fasta_download_crosswalk.tsv"
    header = "accession\ttype\torganism\tstrain\toutput_file\tstatus\n"
    rows = [header]
    for r in records:
        rows.append("\t".join([r.get("accession", ""), r.get("type", ""),
                               r.get("organism", ""), r.get("strain", ""),
                               r.get("output_file", ""), r.get("status", "")]) + "\n")
    # Append to any existing crosswalk so repeat downloads accumulate.
    mode = "a" if cw.is_file() else "w"
    with cw.open(mode, encoding="utf-8") as fh:
        if mode == "w":
            fh.write(rows[0])
        fh.writelines(rows[1:])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Download genome FASTAs by accession.")
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--accessions", nargs="+", required=True)
    ap.add_argument("--no-rename", action="store_true",
                    help="Save files as the bare accession instead of metadata-derived names.")
    ap.add_argument("--email", default=None)
    args = ap.parse_args(argv)
    if args.email:
        global _EMAIL
        _EMAIL = args.email.strip()

    outdir: Path = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    rename = not args.no_rename

    # De-dupe while preserving order.
    seen, accs = set(), []
    for a in args.accessions:
        a = a.strip()
        if a and a not in seen:
            seen.add(a)
            accs.append(a)

    log("=" * 64)
    log(f"FASTA download — {len(accs)} accession(s) -> {outdir}")
    log(f"  metadata renaming: {'on' if rename else 'off'}")
    log("=" * 64)

    records: List[Dict[str, str]] = []
    ok = 0
    for acc in accs:
        try:
            if _is_assembly(acc):
                rec = fetch_assembly(acc, outdir, rename)
            else:
                rec = fetch_nucleotide(acc, outdir, rename)
            records.append(rec)
            ok += 1
        except Exception as exc:  # noqa: BLE001 — soft-fail per accession
            log(f"  ERROR: {acc}: {exc}")
            records.append({"accession": acc, "type": "assembly" if _is_assembly(acc) else "nucleotide",
                            "organism": "", "strain": "", "output_file": "", "status": f"failed: {exc}"})

    _write_crosswalk(outdir, records)
    # Clean any stray temp dir.
    shutil.rmtree(outdir / ".fasta_dl_tmp", ignore_errors=True)

    log("")
    log(f"Done: {ok}/{len(accs)} downloaded. Crosswalk: fasta_download_crosswalk.tsv")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
