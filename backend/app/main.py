"""
kSNP4 GUI — FastAPI backend.

Serves the React SPA from frontend/dist/ and provides:
  /api/projects                          — list shared + personal projects
  /api/projects/{n}/{inputs,upload,link-local,samples,sra/download}
  /api/config                            — get/set user config (run defaults)
  /api/browse-dirs                       — folder picker
  /api/run                               — start a project-level kSNP4 run
  /api/projects/{n}/runs                 — list kSNP runs in a project
  /api/projects/{n}/runs/{label}/results — result files for a run
  /api/projects/{n}/runs/{label}/summary — parsed manifest + input QC
  /api/jobs, /api/jobs/{id}, /api/jobs/{id}/log (SSE), .../results, .../file

kSNP is a *set* analysis: one run consumes the genome FASTAs of a whole project
and produces SNP matrices and trees. This backend is a sibling of vsnp_gui /
kraken_id_parse_gui / amr_plus_gui and shares their project layout. All URLs are
served from / (uvicorn is behind the OOD rnode proxy — relative paths only).
"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import load_config, save_config
from .jobs import JobManager
from .sra import (
    SRAExpansionError,
    build_download_script,
    expand_accessions_with_mapping,
    write_crosswalk_tsv,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent          # /srv/kapurlab/tools/ksnp_gui
_BIN_DIR = _REPO_ROOT / "bin"
_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"
_SHARED_PROJECTS = Path("/srv/kapurlab/projects")
_JOBS_DIR = _REPO_ROOT / "backend" / "jobs"

# Accepted genome FASTA extensions. kSNP requires FASTA input.
_FASTA_EXTS = (".fasta", ".fa", ".fna", ".fas", ".ffn", ".fsa")

# ---------------------------------------------------------------------------
# App & job manager
# ---------------------------------------------------------------------------
app = FastAPI(title="kSNP4 GUI")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
job_manager = JobManager(_JOBS_DIR)

_SCOPE_SHARED = "shared"
_SCOPE_PERSONAL = "personal"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime if p.is_dir() else 0
    except PermissionError:
        return 0


def _is_fasta(name: str) -> bool:
    return name.lower().endswith(_FASTA_EXTS)


def _count_fastas(download_dir: Path) -> int:
    try:
        if not download_dir.is_dir():
            return 0
        return sum(1 for p in download_dir.iterdir()
                   if p.is_file() and _is_fasta(p.name) and not p.name.startswith("."))
    except (PermissionError, OSError):
        return -1


def _list_ksnp_runs(project_dir: Path) -> List[str]:
    ksnp_dir = project_dir / "ksnp"
    try:
        if ksnp_dir.is_dir():
            return [d.name for d in sorted(ksnp_dir.iterdir(), key=_safe_mtime, reverse=True)
                    if d.is_dir()]
    except (PermissionError, OSError):
        pass
    return []


def _list_projects_from_root(root: Path, scope: str) -> List[Dict]:
    if not root.is_dir():
        return []
    projects = []
    try:
        entries = sorted(root.iterdir(), key=_safe_mtime, reverse=True)
    except PermissionError:
        return []
    for p in entries:
        try:
            if not p.is_dir() or p.name.startswith("."):
                continue
        except PermissionError:
            continue
        projects.append({
            "name": p.name,
            "path": str(p),
            "scope": scope,
            "fasta_count": _count_fastas(p / "download"),
            "ksnp_runs": _list_ksnp_runs(p),
        })
    return projects


def _get_project_dir(name: str) -> Optional[Path]:
    if "/" in name or name.startswith("."):
        return None
    cfg = load_config()
    for root in [_SHARED_PROJECTS, Path(cfg.get("projects_root", ""))]:
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return None


_PROJECT_NAME_OK_CHARSET = re.compile(r"^[A-Za-z0-9._-]+$")


def _normalize_project_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("Project name must be a string")
    cleaned = re.sub(r"\s+", "_", name.strip())
    if not cleaned:
        raise ValueError("Project name is empty")
    if cleaned.startswith("."):
        raise ValueError("Project name cannot start with '.'")
    if len(cleaned) > 100:
        raise ValueError("Project name too long (max 100 characters)")
    if not _PROJECT_NAME_OK_CHARSET.match(cleaned):
        bad = sorted(set(ch for ch in cleaned if not re.match(r"[A-Za-z0-9._-]", ch)))
        raise ValueError(
            f"Project name contains unsupported characters: {''.join(bad)!r}. "
            "Only letters, digits, _ - . are allowed (spaces become underscores)."
        )
    return cleaned


def _ensure_project_dirs(project_dir: Path) -> None:
    (project_dir / "download").mkdir(parents=True, exist_ok=True)
    (project_dir / "ksnp").mkdir(parents=True, exist_ok=True)
    # vSNP-compatible layout so the project is shared cleanly between tools.
    (project_dir / "step1").mkdir(parents=True, exist_ok=True)
    (project_dir / "step2" / "vcf_source").mkdir(parents=True, exist_ok=True)
    (project_dir / f"{project_dir.name}_VCFs").mkdir(parents=True, exist_ok=True)


def _create_project(name: str, scope: str) -> Path:
    name = _normalize_project_name(name)
    cfg = load_config()
    root = _SHARED_PROJECTS if scope == _SCOPE_SHARED else Path(
        cfg.get("projects_root", "") or (Path.home() / "projects"))
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"Cannot create projects root {root}: {exc}")
    project_dir = root / name
    if project_dir.exists():
        raise ValueError(f"Project already exists: {name}")
    try:
        _ensure_project_dirs(project_dir)
    except PermissionError:
        raise ValueError(
            f"No permission to create a project under {root}. Shared projects require "
            "lab write access; create it as a personal project instead."
        )
    try:
        with open(project_dir / "project.json", "w", encoding="utf-8") as f:
            json.dump({"name": name, "created_at": _now_iso(), "status": "created"},
                      f, indent=2, sort_keys=True)
    except OSError:
        pass
    return project_dir


def _list_genomes(download_dir: Path) -> List[Dict]:
    """List genome FASTA files in download/ as {sample, path, name, size} dicts."""
    out: List[Dict] = []
    try:
        files = sorted(p for p in download_dir.iterdir()
                       if p.is_file() and _is_fasta(p.name) and not p.name.startswith("."))
    except (PermissionError, FileNotFoundError):
        return []
    for p in files:
        stem = p.name
        for ext in _FASTA_EXTS:
            if stem.lower().endswith(ext):
                stem = stem[: -len(ext)]
                break
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        out.append({"sample": stem, "name": p.name, "path": str(p), "size": size})
    return out


# ---------------------------------------------------------------------------
# Project routes
# ---------------------------------------------------------------------------
@app.get("/api/projects")
def api_list_projects():
    cfg = load_config()
    projects = _list_projects_from_root(_SHARED_PROJECTS, _SCOPE_SHARED)
    personal_root = Path(cfg.get("projects_root", ""))
    if personal_root != _SHARED_PROJECTS:
        personal = _list_projects_from_root(personal_root, _SCOPE_PERSONAL)
        seen = {p["name"] for p in projects}
        projects += [p for p in personal if p["name"] not in seen]
    return JSONResponse(projects)


class ProjectCreate(BaseModel):
    name: str
    scope: Optional[str] = None


@app.post("/api/projects")
def api_create_project(payload: ProjectCreate):
    scope = (payload.scope or _SCOPE_PERSONAL).strip() or _SCOPE_PERSONAL
    if scope not in (_SCOPE_PERSONAL, _SCOPE_SHARED):
        raise HTTPException(400, f"Invalid scope: {scope!r}")
    try:
        project_dir = _create_project(payload.name, scope)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return JSONResponse({"name": project_dir.name, "path": str(project_dir), "scope": scope})


def _writable_project_dir(name: str) -> Path:
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    (project_dir / "download").mkdir(parents=True, exist_ok=True)
    return project_dir


@app.get("/api/projects/{name}/inputs")
def api_project_inputs(name: str):
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    download_dir = project_dir / "download"
    files: List[Dict] = []
    total = 0
    if download_dir.is_dir():
        for p in sorted(download_dir.iterdir()):
            if not p.is_file() or p.name.startswith("."):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            files.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime,
                          "is_fasta": _is_fasta(p.name)})
            total += st.st_size
    return JSONResponse({"files": files, "total_bytes": total, "count": len(files)})


@app.delete("/api/projects/{name}/inputs/{filename}")
def api_project_input_delete(name: str, filename: str):
    if not filename or "/" in filename or "\\" in filename or filename.startswith(".") or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    target = project_dir / "download" / filename
    if not target.is_file() and not target.is_symlink():
        raise HTTPException(404, f"File not found: {filename}")
    target.unlink()
    return JSONResponse({"deleted": filename})


@app.post("/api/projects/{name}/upload")
async def api_project_upload(name: str, files: List[UploadFile] = File(...)):
    project_dir = _writable_project_dir(name)
    download_dir = project_dir / "download"
    saved = 0
    for f in files:
        if not f.filename:
            continue
        target = download_dir / Path(f.filename).name
        async with aiofiles.open(target, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                await out.write(chunk)
        saved += 1
    return JSONResponse({"uploaded": saved})


class LinkLocalRequest(BaseModel):
    path: str


@app.post("/api/projects/{name}/link-local")
def api_project_link_local(name: str, payload: LinkLocalRequest):
    """Symlink every genome FASTA (and *.fastq.gz, for cross-tool projects) under
    a server-side directory into download/ — no copying."""
    project_dir = _writable_project_dir(name)
    src = Path((payload.path or "").strip()).expanduser()
    if not src.exists():
        raise HTTPException(400, f"Input path not found: {src}")
    download_dir = project_dir / "download"
    accept = _FASTA_EXTS + (".fastq.gz",)
    if src.is_file():
        candidates = [src]
    else:
        candidates = sorted(f for f in src.iterdir()
                            if f.is_file() and f.name.lower().endswith(accept))
    count = 0
    for f in candidates:
        if not f.name.lower().endswith(accept):
            continue
        target = download_dir / f.name
        if not target.exists():
            target.symlink_to(f.resolve())
            count += 1
    return JSONResponse({"linked": count})


class SraRequest(BaseModel):
    accessions: List[str]
    folder: Optional[str] = None


@app.post("/api/projects/{name}/sra/download")
def api_project_sra_download(name: str, payload: SraRequest):
    project_dir = _writable_project_dir(name)
    try:
        expanded, mapping = expand_accessions_with_mapping(payload.accessions, strict=True)
    except SRAExpansionError as e:
        raise HTTPException(502, f"Could not resolve SRA accessions via NCBI eutils: {e}. "
                                 "This is usually NCBI rate-limiting; wait ~30 s and retry.")
    download_root = project_dir / "download"
    if payload.folder:
        download_root = download_root / Path(payload.folder).name
    download_root.mkdir(parents=True, exist_ok=True)
    try:
        write_crosswalk_tsv(download_root, mapping)
    except OSError as e:
        logger.warning("Failed to write sra_crosswalk.tsv: %s", e)
    script = build_download_script(download_root, expanded, allow_insecure_https=False)
    script_path = download_root / "download_sra.sh"
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o755)
    job_id = job_manager.start_job(
        name=f"sra_download — {name}",
        command=["bash", str(script_path)],
        cwd=download_root,
        env={"PATH": os.environ.get("PATH", "")},
    )
    return JSONResponse({"job_id": job_id})


class FastaDownloadRequest(BaseModel):
    accessions: List[str]
    rename: bool = True      # save metadata-derived names (organism/strain) vs bare accession


@app.post("/api/projects/{name}/fasta/download")
def api_project_fasta_download(name: str, payload: FastaDownloadRequest):
    """Download genome FASTAs by accession into download/ as a background job.

    GCA/GCF assembly accessions go through the NCBI `datasets` CLI (with
    metadata-derived names); other (nucleotide) accessions go through eutils
    efetch. kSNP runs on FASTA assemblies, so this is the primary input path."""
    project_dir = _writable_project_dir(name)
    accs = [a.strip() for a in (payload.accessions or []) if a.strip()]
    if not accs:
        raise HTTPException(400, "No accessions provided.")
    download_dir = project_dir / "download"
    script = _BIN_DIR / "download_fasta.py"
    command = [sys.executable, "-u", str(script), "--outdir", str(download_dir)]
    if not payload.rename:
        command.append("--no-rename")
    command += ["--accessions", *accs]
    env = {
        "PYTHONPATH": str(_BIN_DIR),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
    }
    job_id = job_manager.start_job(
        name=f"fasta_download — {name} ({len(accs)})",
        command=command, cwd=download_dir, env=env,
    )
    return JSONResponse({"job_id": job_id, "count": len(accs)})


class RenameRequest(BaseModel):
    old: str
    new: str


def _sanitize_filename(stem: str) -> str:
    """kSNP-safe basename stem (no extension): keep [A-Za-z0-9_-].

    Dots are replaced with '_' — kSNP permits only one '.' per filename (the
    extension separator), so the name stem must be dot-free."""
    s = re.sub(r"[^A-Za-z0-9_-]", "_", stem.strip())
    s = re.sub(r"_{2,}", "_", s).strip("_-")
    return s


@app.post("/api/projects/{name}/inputs/rename")
def api_project_input_rename(name: str, payload: RenameRequest):
    """Rename a file in download/ so genome names carry useful metadata in the
    kSNP output (like vSNP's renaming). The new name is sanitised to kSNP-safe
    characters; the original extension is preserved."""
    old = (payload.old or "").strip()
    if not old or "/" in old or "\\" in old or old.startswith(".") or ".." in old:
        raise HTTPException(400, "Invalid source filename")
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    download_dir = project_dir / "download"
    src = download_dir / old
    if not (src.is_file() or src.is_symlink()):
        raise HTTPException(404, f"File not found: {old}")

    # Preserve the on-disk extension; sanitise the requested base name.
    suffix = "".join(Path(old).suffixes) if old.lower().endswith(".fastq.gz") else Path(old).suffix
    new_raw = (payload.new or "").strip()
    # Let the user type a name with or without an extension; strip a trailing
    # known extension before sanitising, then re-attach the original suffix.
    for ext in _FASTA_EXTS + (".fastq.gz",):
        if new_raw.lower().endswith(ext):
            new_raw = new_raw[: -len(ext)]
            break
    new_base = _sanitize_filename(new_raw)
    if not new_base:
        raise HTTPException(400, "New name is empty after sanitising.")
    dst = download_dir / f"{new_base}{suffix}"
    if dst == src:
        return JSONResponse({"old": old, "new": dst.name, "unchanged": True})
    if dst.exists():
        raise HTTPException(409, f"A file named {dst.name} already exists.")
    src.rename(dst)
    return JSONResponse({"old": old, "new": dst.name})


class MetadataRow(BaseModel):
    old: str            # current filename in download/ (e.g. GCA_000195835_3.fasta)
    new: str            # desired tree label (extension optional; sanitised)


class MetadataApplyRequest(BaseModel):
    rows: List[MetadataRow]


@app.post("/api/projects/{name}/metadata/apply")
def api_project_metadata_apply(name: str, payload: MetadataApplyRequest):
    """Batch-apply tree-tip labels. kSNP labels tips by genome filename, so a
    label is applied by renaming the file (same as the per-row pencil). This is
    the vSNP-style metadata pane's 'Apply' — one call for the whole set, with a
    per-row result so the UI can report what changed / collided."""
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    download_dir = project_dir / "download"
    results: List[Dict[str, str]] = []
    renamed = 0
    for row in payload.rows or []:
        old = (row.old or "").strip()
        new_raw = (row.new or "").strip()
        if not old or "/" in old or "\\" in old or old.startswith(".") or ".." in old:
            results.append({"old": old, "status": "error: invalid source name"})
            continue
        src = download_dir / old
        if not (src.is_file() or src.is_symlink()):
            results.append({"old": old, "status": "error: file not found"})
            continue
        if not new_raw:
            results.append({"old": old, "status": "skipped: empty label"})
            continue
        suffix = "".join(Path(old).suffixes) if old.lower().endswith(".fastq.gz") else Path(old).suffix
        for ext in _FASTA_EXTS + (".fastq.gz",):
            if new_raw.lower().endswith(ext):
                new_raw = new_raw[: -len(ext)]
                break
        new_base = _sanitize_filename(new_raw)
        if not new_base:
            results.append({"old": old, "status": "error: empty after sanitising"})
            continue
        dst = download_dir / f"{new_base}{suffix}"
        if dst == src:
            results.append({"old": old, "new": dst.name, "status": "unchanged"})
            continue
        if dst.exists():
            results.append({"old": old, "new": dst.name, "status": "error: name already exists"})
            continue
        src.rename(dst)
        renamed += 1
        results.append({"old": old, "new": dst.name, "status": "renamed"})
    return JSONResponse({"renamed": renamed, "results": results})


@app.get("/api/projects/{name}/samples")
def api_project_samples(name: str):
    """List genome FASTAs in download/ (kSNP operates on FASTA assemblies)."""
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    download_dir = project_dir / "download"
    if not download_dir.is_dir():
        return JSONResponse([])
    return JSONResponse(_list_genomes(download_dir))


# ---------------------------------------------------------------------------
# kSNP run
# ---------------------------------------------------------------------------
_LABEL_OK = re.compile(r"^[A-Za-z0-9._-]+$")


def _normalize_label(label: str) -> str:
    cleaned = re.sub(r"\s+", "_", (label or "").strip())
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", cleaned).strip("_.-")
    return cleaned


class RunPayload(BaseModel):
    project: str
    label: Optional[str] = None
    genomes: Optional[List[str]] = None     # absolute FASTA paths; default = all in download/
    min_frac: Optional[float] = None
    run_core: Optional[bool] = None
    run_ml: Optional[bool] = None
    run_vcf: Optional[bool] = None
    k: Optional[int] = None
    threads: Optional[int] = None


@app.post("/api/run")
def api_run(payload: RunPayload):
    cfg = load_config()
    project_dir = _get_project_dir(payload.project)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {payload.project}")

    # Resolve the genome set: explicit selection, else every FASTA in download/.
    if payload.genomes:
        genomes = [Path(g) for g in payload.genomes]
    else:
        genomes = [Path(g["path"]) for g in _list_genomes(project_dir / "download")]
    genomes = [g for g in genomes if g.exists() and _is_fasta(g.name)]
    if len(genomes) < 2:
        raise HTTPException(400, "kSNP requires at least 2 genome FASTAs. Add FASTA "
                                 "assemblies to the project's download/ folder.")

    label = _normalize_label(payload.label or "")
    if not label:
        label = "ksnp_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    elif not _LABEL_OK.match(label):
        raise HTTPException(400, f"Invalid run label: {label!r}")

    run_dir = project_dir / "ksnp" / label
    for existing in job_manager.list_jobs():
        if existing.get("status") == "running" and existing.get("cwd") == str(run_dir):
            raise HTTPException(409, f"A kSNP run is already in progress for '{label}'.")
    if run_dir.exists() and any(run_dir.iterdir()):
        raise HTTPException(409, f"Run '{label}' already exists in this project. "
                                 "Choose a new label or delete the existing run.")
    run_dir.mkdir(parents=True, exist_ok=True)

    min_frac = payload.min_frac if payload.min_frac is not None else cfg.get("min_frac", 0.8)
    run_core = payload.run_core if payload.run_core is not None else cfg.get("run_core", True)
    run_ml = payload.run_ml if payload.run_ml is not None else cfg.get("run_ml", True)
    run_vcf = payload.run_vcf if payload.run_vcf is not None else cfg.get("run_vcf", True)

    script = _BIN_DIR / "ksnp_pipeline.py"
    command = [sys.executable, "-u", str(script),
               "--label", label, "--outdir", str(run_dir),
               "--min-frac", str(min_frac)]
    if not run_core:
        command.append("--no-core")
    if not run_ml:
        command.append("--no-ml")
    if not run_vcf:
        command.append("--no-vcf")
    if payload.k:
        command += ["--k", str(int(payload.k))]
    threads = payload.threads or cfg.get("threads")
    if threads:
        command += ["--threads", str(int(threads))]
    command += ["--inputs", *[str(g) for g in genomes]]

    env = {
        "PYTHONPATH": str(_BIN_DIR),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    job_name = f"{payload.project}/{label} — kSNP4 ({len(genomes)} genomes)"
    job_id = job_manager.start_job(name=job_name, command=command, cwd=run_dir, env=env)
    return JSONResponse({"job_id": job_id, "run_dir": str(run_dir), "label": label,
                         "genome_count": len(genomes)})


# ---------------------------------------------------------------------------
# kSNP run results (read straight off disk so any past run is revisitable)
# ---------------------------------------------------------------------------
def _run_status(run_dir: Path) -> str:
    run_dir_str = str(run_dir)
    for job in job_manager.list_jobs():
        if job.get("cwd") == run_dir_str and job.get("status") == "running":
            return "running"
    try:
        if (run_dir / "run_manifest.json").is_file():
            return "done"
        if run_dir.is_dir() and any(p.is_file() for p in run_dir.rglob("*")):
            return "done"
    except PermissionError:
        pass
    return "none"


@app.get("/api/projects/{name}/runs")
def api_project_runs(name: str):
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    ksnp_dir = project_dir / "ksnp"
    runs: List[Dict] = []
    if ksnp_dir.is_dir():
        for d in sorted(ksnp_dir.iterdir(), key=_safe_mtime, reverse=True):
            if not d.is_dir():
                continue
            man = {}
            mp = d / "run_manifest.json"
            if mp.is_file():
                try:
                    man = json.loads(mp.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    man = {}
            runs.append({
                "label": d.name,
                "path": str(d),
                "status": _run_status(d),
                "genome_count": man.get("genome_count"),
                "snps_all": (man.get("results") or {}).get("snps_all"),
                "core_snps": (man.get("results") or {}).get("core_snps"),
                "k": (man.get("options") or {}).get("k"),
            })
    return JSONResponse({"project": name, "runs": runs})


@app.delete("/api/projects/{name}/runs/{label}")
def api_delete_run(name: str, label: str):
    if not _LABEL_OK.match(label or ""):
        raise HTTPException(400, "Invalid run label")
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    run_dir = (project_dir / "ksnp" / label).resolve()
    if (project_dir / "ksnp").resolve() not in run_dir.parents or not run_dir.is_dir():
        raise HTTPException(404, f"Run not found: {label}")
    if _run_status(run_dir) == "running":
        raise HTTPException(409, "Run is still in progress.")
    import shutil
    shutil.rmtree(run_dir, ignore_errors=True)
    return JSONResponse({"deleted": label})


@app.get("/api/projects/{name}/runs/{label}/results")
def api_run_results(name: str, label: str, all: int = Query(0)):
    if not _LABEL_OK.match(label or ""):
        raise HTTPException(400, "Invalid run label")
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    run_dir = project_dir / "ksnp" / label
    return JSONResponse({
        "project": name, "label": label,
        "present": run_dir.is_dir(),
        "status": _run_status(run_dir),
        "run_dir": str(run_dir),
        "files": _collect_result_files(run_dir, bool(all)),
    })


@app.get("/api/projects/{name}/runs/{label}/summary")
def api_run_summary(name: str, label: str):
    """Parsed run_manifest.json + input_qc.json for the Results pane."""
    if not _LABEL_OK.match(label or ""):
        raise HTTPException(400, "Invalid run label")
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    run_dir = project_dir / "ksnp" / label

    def _load(p):
        try:
            return json.loads((run_dir / p).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    manifest = _load("run_manifest.json")
    qc = _load("input_qc.json")
    return JSONResponse({
        "project": name, "label": label,
        "present": (run_dir / "run_manifest.json").is_file(),
        "status": _run_status(run_dir),
        "manifest": manifest,
        "input_qc": qc,
    })


# ---------------------------------------------------------------------------
# Config / browse
# ---------------------------------------------------------------------------
@app.get("/api/config")
def api_get_config():
    return JSONResponse(load_config())


class ConfigPayload(BaseModel):
    projects_root: Optional[str] = None
    shared_projects_root: Optional[str] = None
    saved_project_roots: Optional[List[str]] = None
    min_frac: Optional[float] = None
    run_core: Optional[bool] = None
    run_ml: Optional[bool] = None
    run_vcf: Optional[bool] = None
    threads: Optional[Any] = None


@app.post("/api/config")
def api_save_config(payload: ConfigPayload):
    cfg = load_config()
    updates = payload.model_dump(exclude_none=True)
    cfg.update(updates)
    roots = cfg.get("saved_project_roots") or []
    if isinstance(roots, list):
        seen, cleaned = set(), []
        for r in roots:
            r = (r or "").strip()
            if r and r not in seen:
                seen.add(r); cleaned.append(r)
        cfg["saved_project_roots"] = cleaned
    save_config(cfg)
    return JSONResponse({"ok": True})


@app.get("/api/browse-dirs")
def api_browse_dirs(path: str = ""):
    try:
        p = (Path(path).expanduser() if path.strip() else Path.home()).resolve()
    except (OSError, RuntimeError):
        raise HTTPException(400, "Invalid path")
    if not p.is_dir():
        raise HTTPException(400, f"Not a directory: {p}")
    entries: List[Dict[str, str]] = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    entries.append({"name": child.name, "path": str(child)})
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(403, f"Permission denied: {p}")
    parent = str(p.parent) if p.parent != p else None
    return JSONResponse({"path": str(p), "parent": parent, "entries": entries})


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
@app.get("/api/jobs")
def api_list_jobs():
    return JSONResponse(job_manager.list_jobs())


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str):
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return JSONResponse(job)


@app.get("/api/jobs/{job_id}/log")
async def api_job_log(job_id: str, request: Request):
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    log_path = Path(job["log_path"])
    _ansi_re = re.compile(r'\x1b\[[0-9;]*[mGKHFABCDJsur]')

    async def event_stream():
        position = 0
        while True:
            if await request.is_disconnected():
                break
            current_job = job_manager.get_job(job_id)
            if log_path.exists():
                async with aiofiles.open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    await f.seek(position)
                    chunk = await f.read(4096)
                    if chunk:
                        for line in chunk.splitlines(keepends=True):
                            clean = _ansi_re.sub("", line.rstrip())
                            if clean:
                                yield f"data: {clean}\n\n"
                        position += len(chunk.encode("utf-8"))
            if current_job and current_job["status"] in ("succeeded", "failed"):
                yield "data: [DONE]\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Result categorization + file serving
# ---------------------------------------------------------------------------
_INLINE_MEDIA = {
    ".pdf": "application/pdf", ".html": "text/html", ".htm": "text/html",
    ".txt": "text/plain", ".log": "text/plain", ".json": "application/json",
    ".tsv": "text/plain", ".tre": "text/plain", ".nwk": "text/plain",
    ".newick": "text/plain", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".csv": "text/plain",
}
_DOWNLOAD_MEDIA = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel", ".vcf": "text/plain",
    ".fasta": "text/plain", ".fa": "text/plain", ".fna": "text/plain",
    ".fas": "text/plain", ".gz": "application/gzip",
}


# Extensions that are genuinely binary and can't be shown in a browser tab.
# Everything else — including the many extension-less kSNP text files
# (COUNT_SNPs, core_SNPs, SNPs_all, *_matrix, tip_SNP_counts, …) — is served
# inline as text so users can click to view it, with a separate download link.
_BINARY_EXTS = {".xlsx", ".xls", ".gz", ".zip", ".bam", ".bai", ".bcf"}


def _can_open_inline(name: str) -> bool:
    """True if the file can be shown in a browser tab (text, or a format the
    browser renders like pdf/html/image). Only true binaries are excluded."""
    return Path(name).suffix.lower() not in _BINARY_EXTS


def _media_type_for(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in _INLINE_MEDIA:
        return _INLINE_MEDIA[ext]
    if ext in _DOWNLOAD_MEDIA:
        return _DOWNLOAD_MEDIA[ext]
    # Unknown / no extension: kSNP's are text, so serve as text/plain (viewable);
    # only the explicit binaries above fall back to octet-stream.
    return "application/octet-stream" if ext in _BINARY_EXTS else "text/plain"


def _result_category(rel: str) -> Optional[str]:
    path = Path(rel)
    name = path.name
    parts = path.parts
    if any(part.startswith(".") for part in parts):
        return None
    low = name.lower()

    if name == "report.pdf":
        return "report_pdf"
    if low.endswith("_stats.xlsx"):
        return "stats_xlsx"
    if name == "run_manifest.json":
        return "run_manifest"
    if name == "input_qc.json":
        return "input_qc"
    if name == "name_crosswalk.tsv":
        return "name_crosswalk"
    if low.endswith(".tre") or low.endswith(".nwk"):
        if "ml" in low:
            return "tree_ml"
        if "parsimony" in low:
            return "tree_parsimony"
        if "nj" in low:
            return "tree_nj"
        if "core" in low:
            return "tree_core"
        return "tree"
    if "core_snps_matrix" in low and (low.endswith(".fasta") or low.endswith(".fa")):
        return "core_matrix"
    if "matrix" in low and (low.endswith(".fasta") or low.endswith(".fa")):
        return "snp_matrix"
    if name.startswith("VCF") or low.endswith(".vcf"):
        return "vcf"
    if name == "COUNT_SNPs":
        return "count_snps"
    if name == "COUNT_coreSNPs":
        return "count_core_snps"
    if low.startswith("kchooser") and low.endswith(".report"):
        return "kchooser_report"
    if name == "pipeline.log":
        return "log"
    return None


_CATEGORY_ORDER = {
    "report_pdf": 0, "stats_xlsx": 1, "tree_ml": 2, "tree_parsimony": 3,
    "tree_core": 4, "tree_nj": 5, "tree": 6, "core_matrix": 7, "snp_matrix": 8,
    "count_core_snps": 9, "count_snps": 10, "vcf": 11, "input_qc": 12,
    "kchooser_report": 13, "name_crosswalk": 14, "run_manifest": 15, "log": 99,
}


def _tree_label(name: str) -> str:
    """Build a distinct, descriptive label for a kSNP .tre file from its name,
    e.g. tree.core_SNPs.ML.tre -> 'Core-SNP maximum-likelihood tree — tree.core_SNPs.ML.tre'.
    Without this every ML tree would read identically; the filename is appended
    so users see exactly which file they're opening."""
    low = name.lower()
    snp_set = ("Core-SNP" if "core_snps" in low else
               "All-SNP" if "snps_all" in low else
               "Majority-SNP" if "majority" in low else "")
    method = ("maximum-likelihood" if ".ml." in low or low.endswith(".ml.tre") else
              "neighbor-joining" if ".nj." in low or low.endswith(".nj.tre") else
              "parsimony" if "parsimony" in low else "")
    head = " ".join(p for p in (snp_set, (f"{method} tree" if method else "tree")) if p)
    extras = []
    if "tree_tipallelecounts" in low:
        extras.append("per-isolate SNP counts on tips")
    elif "tree_allelecounts" in low:
        extras.append("clade SNP counts on branches")
    if "nodelabel" in low:
        extras.append("node numbers")
    if extras:
        head += " (" + ", ".join(extras) + ")"
    return f"{head[:1].upper()}{head[1:]} — {name}"


def _result_label(rel: str, category: Optional[str]) -> str:
    if category and category.startswith("tree"):
        return _tree_label(Path(rel).name)
    return {
        "report_pdf": "Report (PDF)",
        "stats_xlsx": "Statistics workbook (Excel)",
        "tree_ml": "Maximum-likelihood tree (Newick)",
        "tree_parsimony": "Parsimony tree (Newick)",
        "tree_core": "Core-SNP tree (Newick)",
        "tree_nj": "Neighbor-joining tree (Newick)",
        "tree": "Phylogenetic tree (Newick)",
        "core_matrix": "Core-SNP matrix (FASTA)",
        "snp_matrix": "SNP matrix (FASTA)",
        "count_snps": "Total SNP count",
        "count_core_snps": "Core SNP count",
        "vcf": "Per-SNP variants (VCF)",
        "input_qc": "Input genome QC (JSON)",
        "kchooser_report": "Kchooser4 report (k / FCK)",
        "name_crosswalk": "Genome name crosswalk (TSV)",
        "run_manifest": "Run manifest / provenance (JSON)",
        "log": "Pipeline log",
    }.get(category, rel)


def _collect_result_files(run_dir: Path, include_all: bool) -> List[Dict]:
    files: List[Dict] = []
    if not run_dir.is_dir():
        return files
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.name.endswith(".log"):
            continue
        rel = str(p.relative_to(run_dir))
        category = _result_category(rel)
        if not include_all and category is None:
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        files.append({
            "name": rel, "path": str(p),
            "label": _result_label(rel, category),
            "size": size, "openable": _can_open_inline(rel), "category": category,
        })

    def sort_key(f):
        category = f.get("category")
        return (_CATEGORY_ORDER.get(category, 50), f["name"])

    files.sort(key=sort_key)
    for f in files:
        if include_all and f.get("category") is None:
            f["label"] = f["name"]
    return files


@app.get("/api/projects/{name}/file")
def api_project_file(name: str, path: str = Query(...), inline: int = 0):
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    root = project_dir.resolve()
    target = Path(path).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(403, "Path outside project directory")
    if not target.is_file():
        raise HTTPException(404, f"File not found: {path}")
    media_type = _media_type_for(target.name)
    want_inline = bool(inline) and _can_open_inline(target.name)
    disposition = "inline" if want_inline else "attachment"
    headers = {"Content-Disposition": f'{disposition}; filename="{target.name}"'}
    return FileResponse(target, media_type=media_type, headers=headers)


@app.get("/api/jobs/{job_id}/results")
def api_job_results(job_id: str, all: int = Query(0)):
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    files = []
    cwd = job.get("cwd")
    if cwd and Path(cwd).is_dir():
        files = _collect_result_files(Path(cwd), bool(all))
    log_path = Path(job.get("log_path", ""))
    if log_path.is_file():
        files.append({
            "name": "pipeline_log.txt", "label": "Pipeline log",
            "size": log_path.stat().st_size, "openable": True,
            "category": "log", "is_log": True,
        })
    return JSONResponse(files)


@app.get("/api/jobs/{job_id}/file")
def api_job_file(job_id: str, path: str = Query(...), inline: int = 0):
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if path == "pipeline_log.txt":
        target = Path(job.get("log_path", ""))
        display_name = f"{job_id[:8]}_pipeline_log.txt"
    else:
        cwd = job.get("cwd")
        if not cwd:
            raise HTTPException(404, "No run directory for job")
        run_dir = Path(cwd).resolve()
        target = (run_dir / path).resolve()
        if run_dir != target and run_dir not in target.parents:
            raise HTTPException(403, "Path outside run directory")
        display_name = target.name
    if not target.is_file():
        raise HTTPException(404, f"File not found: {path}")
    media_type = _media_type_for(target.name)
    want_inline = bool(inline) and _can_open_inline(target.name)
    disposition = "inline" if want_inline else "attachment"
    headers = {"Content-Disposition": f'{disposition}; filename="{display_name}"'}
    return FileResponse(target, media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# Static frontend — must be last
# ---------------------------------------------------------------------------
if _FRONTEND_DIST.is_dir():
    _INDEX_HTML = _FRONTEND_DIST / "index.html"

    @app.get("/")
    def index():
        return FileResponse(_INDEX_HTML,
                            headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="static")
else:
    @app.get("/")
    def root():
        return JSONResponse(
            {"error": "Frontend not built. Run: cd frontend && npm run build"},
            status_code=503,
        )
