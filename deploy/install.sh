#!/usr/bin/env bash
# install.sh — idempotent, no-sudo deployment of the kSNP4 GUI.
#
# Mirrors the AMR/Kraken/vSNP install pattern. Every heavy step is skippable and
# clearly logged. Safe to re-run. Designed to be portable to other OnDemand
# hosts: pass --conda-base for the shared miniforge, everything else is derived
# from the repo location.
#
# What it does:
#   1. Locate/create the conda env (shared at <repo>/env, else personal `ksnp`).
#   2. pip install backend/requirements.txt into that env.
#   3. Download + unpack the kSNP4.1 Linux package into vendor/ and expose its
#      executables via the stable symlink vendor/kSNP4-bin (skip if present).
#   4. Verify kSNP4 / Kchooser4 / MakeKSNP4infile + seqkit resolve on PATH.
#   5. Build the React frontend (frontend/dist/).
#
# Usage:
#   deploy/install.sh [--personal] [--conda-base DIR] [--ksnp-url URL]
#                     [--skip-ksnp] [--skip-frontend] [--dry-run]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---- defaults ----
SHARED_ENV="${REPO_DIR}/env"
PERSONAL_ENV_NAME="ksnp"
CONDA_BASE="${CONDA_BASE:-/srv/kapurlab/tools/miniforge3}"
USE_PERSONAL=0
SKIP_KSNP=0
SKIP_FRONTEND=0
DRY_RUN=0
# SourceForge direct-download (follows mirror redirects with curl -L).
KSNP_URL="${KSNP_URL:-https://sourceforge.net/projects/ksnp/files/kSNP4.1%20Linux%20package.zip/download}"
VENDOR_DIR="${REPO_DIR}/vendor"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] $*"; else "$@"; fi; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --personal)      USE_PERSONAL=1; shift;;
    --conda-base)    CONDA_BASE="$2"; shift 2;;
    --ksnp-url)      KSNP_URL="$2"; shift 2;;
    --skip-ksnp)     SKIP_KSNP=1; shift;;
    --skip-frontend) SKIP_FRONTEND=1; shift;;
    --dry-run)       DRY_RUN=1; shift;;
    -h|--help)       sed -n '2,30p' "$0"; exit 0;;
    *) die "unknown arg: $1";;
  esac
done

log "kSNP4 GUI install"
echo "  repo:  ${REPO_DIR}"
[[ ${DRY_RUN} -eq 1 ]] && warn "DRY RUN — no changes will be made"

# ---------------------------------------------------------------------------
# 1. conda env
# ---------------------------------------------------------------------------
CONDA="${CONDA_BASE}/bin/conda"
[[ -x "${CONDA}" ]] || CONDA="$(command -v conda 2>/dev/null || true)"
[[ -n "${CONDA}" && -x "${CONDA}" ]] || die "conda not found. Install miniforge to ${CONDA_BASE} or pass --conda-base."
ok "conda: ${CONDA}"
CONDA_FRONTEND="${CONDA_FRONTEND:-}"
if [[ -z "${CONDA_FRONTEND}" ]]; then
  if [[ -x "${CONDA_BASE}/bin/mamba" ]]; then CONDA_FRONTEND="${CONDA_BASE}/bin/mamba"
  elif command -v mamba >/dev/null 2>&1; then CONDA_FRONTEND="$(command -v mamba)"
  else CONDA_FRONTEND="${CONDA}"; fi
fi
ok "env builder: ${CONDA_FRONTEND}"

ENV_FILE="${REPO_DIR}/conda_setup/environment.yml"
if [[ ${USE_PERSONAL} -eq 1 ]]; then
  ENV_BIN="$("${CONDA}" run -n "${PERSONAL_ENV_NAME}" sh -c 'echo $CONDA_PREFIX/bin' 2>/dev/null || true)"
  ENV_DESC="personal env ${PERSONAL_ENV_NAME}"
  ENV_EXISTS=$("${CONDA}" env list | awk '{print $1}' | grep -qx "${PERSONAL_ENV_NAME}" && echo 1 || echo 0)
  CREATE_FLAG=("-n" "${PERSONAL_ENV_NAME}")
else
  ENV_BIN="${SHARED_ENV}/bin"
  ENV_DESC="shared env ${SHARED_ENV}"
  ENV_EXISTS=$([[ -x "${SHARED_ENV}/bin/python" ]] && echo 1 || echo 0)
  CREATE_FLAG=("-p" "${SHARED_ENV}")
fi

if [[ "${ENV_EXISTS}" -eq 1 ]]; then
  ok "${ENV_DESC} already exists — skipping create"
else
  if [[ ${USE_PERSONAL} -eq 0 && -d "${SHARED_ENV}" ]]; then
    warn "removing incomplete env at ${SHARED_ENV} (no python found)"
    run rm -rf "${SHARED_ENV}"
  fi
  log "creating ${ENV_DESC} from ${ENV_FILE} (solve can take 2-5 min)"
  run "${CONDA_FRONTEND}" env create "${CREATE_FLAG[@]}" -f "${ENV_FILE}"
fi

PYTHON="${ENV_BIN}/python"
# Put the env's bin on PATH so seqkit, tcsh, perl resolve correctly (the OOD
# launcher sets PATH the same way).
if [[ -d "${ENV_BIN}" ]]; then export PATH="${ENV_BIN}:${PATH}"; fi
log "pip install backend requirements into ${ENV_DESC}"
run "${PYTHON}" -m pip install -r "${REPO_DIR}/backend/requirements.txt"

# ---------------------------------------------------------------------------
# 2. kSNP4 (SourceForge Linux package)
# ---------------------------------------------------------------------------
KSNP_LINK="${VENDOR_DIR}/kSNP4-bin"
if [[ ${SKIP_KSNP} -eq 1 ]]; then
  warn "skipping kSNP4 download (--skip-ksnp)"
elif [[ -x "${KSNP_LINK}/kSNP4" ]]; then
  ok "kSNP4 already installed: ${KSNP_LINK}"
else
  mkdir -p "${VENDOR_DIR}"
  ZIP="${VENDOR_DIR}/kSNP4.1_Linux_package.zip"
  if [[ ! -s "${ZIP}" ]]; then
    log "downloading kSNP4.1 Linux package (~545 MB) from SourceForge"
    run curl -L --fail --retry 3 -o "${ZIP}" "${KSNP_URL}" \
      || die "kSNP4 download failed. Download the Linux package manually to ${ZIP} and re-run, or pass --ksnp-url."
  else
    ok "kSNP4 zip already downloaded: ${ZIP}"
  fi
  log "unpacking kSNP4 package"
  run unzip -q -o "${ZIP}" -d "${VENDOR_DIR}"
  # Find the directory that actually contains the kSNP4 executable and point the
  # stable symlink at it (the archive's top-level folder name carries a space
  # and a version, so don't hard-code it).
  if [[ ${DRY_RUN} -eq 0 ]]; then
    KSNP_EXE="$(find "${VENDOR_DIR}" -type f -name kSNP4 ! -path "${KSNP_LINK}/*" 2>/dev/null | head -1)"
    [[ -n "${KSNP_EXE}" ]] || die "kSNP4 executable not found after unzip. Inspect ${VENDOR_DIR}."
    KSNP_PKG_DIR="$(dirname "${KSNP_EXE}")"
    chmod -R u+rx "${KSNP_PKG_DIR}" 2>/dev/null || true
    rm -f "${KSNP_LINK}"
    ln -s "${KSNP_PKG_DIR}" "${KSNP_LINK}"
    ok "kSNP4 executables: ${KSNP_PKG_DIR}"
    ok "stable symlink:    ${KSNP_LINK}"
  fi
fi

# Expose kSNP4 on PATH for the verification below + at runtime (OOD launcher
# also prepends it).
[[ -d "${KSNP_LINK}" ]] && export PATH="${KSNP_LINK}:${PATH}"

# ---------------------------------------------------------------------------
# 3. Verify the toolchain (with the env + kSNP4 on PATH)
# ---------------------------------------------------------------------------
log "verifying toolchain on PATH"
for t in kSNP4 Kchooser4 MakeKSNP4infile; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t -> $(command -v "$t")"
  else warn "$t NOT on PATH (kSNP install incomplete?)"; fi
done
if command -v seqkit >/dev/null 2>&1; then ok "seqkit: $(seqkit version 2>&1 | head -1)"
else warn "seqkit not on PATH — input QC will be skipped at runtime."; fi
if command -v datasets >/dev/null 2>&1; then ok "datasets: $(datasets --version 2>&1 | head -1)"
else warn "ncbi-datasets-cli not on PATH — GCA/GCF FASTA download disabled (nucleotide efetch still works)."; fi
"${PYTHON}" -c "import reportlab, openpyxl, matplotlib, Bio; print('  python report deps ok')" \
  || warn "python report deps missing — re-run pip install."

# ---------------------------------------------------------------------------
# 4. Frontend build
# ---------------------------------------------------------------------------
if [[ ${SKIP_FRONTEND} -eq 1 ]]; then
  warn "skipping frontend build (--skip-frontend)"
else
  log "building React frontend"
  pushd "${REPO_DIR}/frontend" >/dev/null
  if command -v npm >/dev/null 2>&1; then
    run npm ci || run npm install
    run npm run build
  elif [[ -x node_modules/.bin/vite ]]; then
    run node_modules/.bin/vite build
  else
    warn "no npm and no node_modules — frontend not built. Install Node and re-run."
  fi
  popd >/dev/null
  [[ -f "${REPO_DIR}/frontend/dist/index.html" ]] && ok "frontend built: ${REPO_DIR}/frontend/dist/"
fi

log "Done. Register the OOD app (sudo deploy/register_ood_apps.sh) and launch a session."
echo "  Backend entry:  ${REPO_DIR}/backend/app/main.py (uvicorn app.main:app)"
echo "  Env python:     ${PYTHON}"
echo "  kSNP4 bin:      ${KSNP_LINK}"
