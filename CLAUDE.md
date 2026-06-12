# kSNP4 GUI — Claude Code Context

> Read this before touching code. This tool is one of the Kapur Lab OOD GUI
> family (vsnp_gui, kraken_id_parse_gui, amr_plus_gui, mlst_gui, …). The shared
> conventions and deploy model are documented in
> `/srv/kapurlab/tools/amr_plus_gui/docs/BUILDING_A_SIBLING_TOOL.md` — read that
> first; this file only covers what is kSNP-specific.

## What this is

A web GUI for **kSNP4** — reference-free, alignment-free SNP discovery and
phylogenetics. Given a *set* of genome **FASTA** files it finds SNPs by k-mer
analysis (no reference, no alignment) and builds parsimony / ML / NJ SNP trees,
core- and pan-SNP matrices, and per-SNP VCFs.

FastAPI backend + React (Vite) SPA, deployed as an Open OnDemand batch_connect
interactive app. One uvicorn per session behind OOD's Apache rnode proxy.

## kSNP is a SET analysis (not per-sample)

Unlike the per-sample siblings, **one run consumes a whole project's selected
genome FASTAs at once** and writes to `<project>/ksnp/<label>/`. The UI selects
genomes (checkboxes, default all), then runs kSNP4 once; the Results pane lists
runs and shows each run's SNP/tree summary + downloads.

## Pipeline (`bin/ksnp_pipeline.py`) — the validated NVSL workflow

1. **Stage + sanitise** every input FASTA into `<run>/genomes/` as
   `<sanitised>.fasta`. **kSNP is strict about names** — spaces and special
   characters break it, so names are reduced to `[A-Za-z0-9_.-]` and
   de-duplicated. `name_crosswalk.tsv` records the mapping.
2. **Input QC** — `seqkit stats -a` per genome → `input_qc.json`, flagging any
   genome >20% from the set median length (ISO 15189 validity check).
3. **`MakeKSNP4infile -indir genomes -outfile myInfile A`** (auto, non-
   interactive). Falls back to writing the 2-column infile directly.
4. **`Kchooser4 -in myInfile`** → optimum k + FCK (fraction of core k-mers).
5. **`kSNP4 -in myInfile -outdir ksnp_run -CPU N -k K -vcf -core -ML -min_frac 0.8`**
   (flags toggled from the UI; min_frac default 0.8). **FASTA input required.**
6. **`run_manifest.json`** — every option, tool versions, thresholds, ISO refs.
7. **Report** — `<label>_<date>_stats.xlsx` (single labeled column, vSNP-style)
   and `report.pdf` (input QC, summary, SNP/tree results with rendered tree,
   methods/provenance).

## kSNP4 install (NOT conda)

kSNP4 has no conda package. `deploy/install.sh` downloads the **kSNP4.1 Linux
package** (~545 MB) from SourceForge into `vendor/`, unpacks it, and points the
stable symlink **`vendor/kSNP4-bin`** at the executables. The conda env provides
the Python web/report stack + `seqkit`, `tcsh`, `perl`. The OOD launcher puts
`<env>/bin` then `vendor/kSNP4-bin` on PATH. `vendor/` is gitignored (never
committed).

## Critical constraints (same as the family)

1. **All frontend URLs relative** (`fetch("./api/...")`, `new EventSource(...)`).
   `vite.config.js` keeps `base: "./"`.
2. **FastAPI serves `frontend/dist/`** — no separate static server.
3. **Rebuild the frontend after `frontend/src` edits** (`npm run build`).
4. **Use the tool's env python** (`/srv/kapurlab/tools/ksnp_gui/env/bin/python`).
5. **`before.sh` runs in the OOD parent** (only place `find_port` works);
   **`script.sh.erb` runs in the session** and starts uvicorn.

## Layout

```
backend/app/{main,config,jobs,sra}.py   FastAPI (project-level run model)
bin/ksnp_pipeline.py                    orchestrator (subprocess; marker "ksnp_pipeline")
bin/reporting/                          stats.xlsx + report.pdf
frontend/src/{App.jsx,App.css}          SPA (App.css is the shared theme — do not restyle)
conda_setup/environment.yml             env (no kSNP — that's vendored)
deploy/install.sh                       env + kSNP4 download + frontend build
deploy/register_ood_apps.sh             copies ood/apps/* into /var/www/ood/apps/sys (root)
ood/apps/ksnp_gui{,_dev}/               OOD batch_connect apps (prod + branch-picker dev)
vendor/kSNP4-bin -> …                   kSNP4 executables (gitignored)
```

## Dev / prod OOD apps

- **ksnp_gui** (prod) serves the committed on-disk `frontend/dist/`.
- **ksnp_gui_dev** takes a `branch` field, checks out `origin/<branch>` into a
  `/tmp` worktree, rebuilds the frontend, and runs uvicorn with `--reload`.
  → A feature branch must be **committed AND pushed to origin** to be testable in
  the dev app (it serves `origin/<branch>`, not the working tree).

## What reloads when

- `bin/` scripts: next pipeline run.
- `backend/app/`: new OOD session (or `--reload` in the dev app).
- `frontend/src`: `npm run build`, then a new session.
- `ood/**`: re-run `sudo deploy/register_ood_apps.sh`.
- Dashboard card: `/etc/ood/config/wgs_pipelines.yml` (source mirror lives in
  vsnp_gui `deploy/ood/portal/wgs_pipelines.yml`).
