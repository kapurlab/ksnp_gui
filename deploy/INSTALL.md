# Deploying the kSNP4 GUI on an Open OnDemand system

This tool is a sibling of `vsnp_gui`, `kraken_id_parse_gui`, and `amr_plus_gui`:
a FastAPI backend serving a React SPA, launched as an OOD batch_connect
interactive app behind an Apache `mod_proxy` at `/rnode/<host>/<port>/`.

## 1. Get the code

Clone/copy the repo to a shared location (the Kapur Lab uses
`/srv/kapurlab/tools/ksnp_gui`). All site paths are kept in
`backend/app/config.py` DEFAULTS, so porting is mostly editing those plus the
install root in the OOD `script.sh.erb` files.

## 2. Install: conda env + kSNP4 + frontend

```bash
cd /path/to/ksnp_gui
deploy/install.sh --conda-base /srv/kapurlab/tools/miniforge3   # shared env at ./env
# or
deploy/install.sh --personal                                   # conda env named ksnp
```

`install.sh` is idempotent and needs no sudo. Useful flags:

| Flag | Effect |
|---|---|
| `--personal` | use a personal conda env instead of `<repo>/env` |
| `--conda-base DIR` | miniforge base holding `conda`/`mamba` |
| `--ksnp-url URL` | override the kSNP4 download URL (SourceForge by default) |
| `--skip-ksnp` | don't download/unpack the kSNP4 package |
| `--skip-frontend` | don't rebuild `frontend/dist/` |
| `--dry-run` | print what it would do |

### kSNP4 (not a conda package)

kSNP4 is distributed as a self-contained **Linux package** on SourceForge
(~545 MB, "install the executables, not the source"). `install.sh`:

1. Downloads `kSNP4.1 Linux package.zip` into `vendor/` (skip with `--skip-ksnp`;
   or pre-place the zip there and re-run).
2. Unpacks it and points the stable symlink **`vendor/kSNP4-bin`** at the
   directory holding the `kSNP4` executable.
3. Verifies `kSNP4`, `Kchooser4`, `MakeKSNP4infile` resolve on PATH.

`vendor/` is gitignored — the package is never committed. On a new host just run
`install.sh` (or copy the `vendor/` tree across).

The conda env provides the Python web/report stack plus `seqkit` (input QC),
`tcsh` and `perl` (kSNP4 helper-script shebangs).

## 3. Paths to change for another site

Edit `backend/app/config.py` `DEFAULTS`:

| Key | Default | Change to your site |
|---|---|---|
| `projects_root` | `~/projects` | per-user personal projects |
| `shared_projects_root` | `/srv/kapurlab/projects` | your shared project tree |
| `min_frac` | `0.8` | default SNP presence fraction |

Also update the install root (`/srv/kapurlab/tools/ksnp_gui`) in
`backend/app/main.py` (`_SHARED_PROJECTS` is the only shared path) and in the
OOD `ood/apps/ksnp_gui{,_dev}/template/script.sh.erb` files.

## 4. Register the OOD apps (root)

```bash
sudo deploy/register_ood_apps.sh        # copies ood/apps/* -> /var/www/ood/apps/sys
```

This installs two apps:
- **ksnp_gui** (prod) — serves the committed `frontend/dist/`.
- **ksnp_gui_dev** — adds a Git `branch` field; checks out `origin/<branch>`
  into a `/tmp` worktree, rebuilds the frontend, runs uvicorn with `--reload`.

Each app provides `manifest.yml`, `form.yml`, `submit.yml.erb` (basic template,
`port` conn param), `template/before.sh[.erb]` (allocates `$port`), and
`template/script.sh.erb` (env + `vendor/kSNP4-bin` on PATH, `PYTHONPATH=bin/`,
`exec uvicorn app.main:app`).

## 5. Dashboard landing card

The curated "Kapur Lab Pipelines" page reads `/etc/ood/config/wgs_pipelines.yml`.
The kSNP4 card (`id: ksnp`, launch_url
`/pun/sys/dashboard/batch_connect/sys/ksnp_gui/session_contexts/new`) is added
there; the source mirror lives in vsnp_gui `deploy/ood/portal/wgs_pipelines.yml`.

## 6. Critical constraints (shared with the siblings)

- **All frontend URLs relative** (`./api/...`); never hardcode host/port.
  `vite.config.js` keeps `base: "./"`.
- **FastAPI serves `frontend/dist/`** — no separate static server.
- **Rebuild the frontend** after any `frontend/src` edit, then a fresh session.
- The env's `bin/` then `vendor/kSNP4-bin` are on `PATH` in `script.sh.erb`.

## 7. Smoke test

```bash
export PATH=/path/to/ksnp_gui/env/bin:/path/to/ksnp_gui/vendor/kSNP4-bin:$PATH
export PYTHONPATH=/path/to/ksnp_gui/bin
kSNP4 | head -3                          # tool resolves
cd /path/to/ksnp_gui/backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080 &
curl -s localhost:8080/api/projects | head
```
