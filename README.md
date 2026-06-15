# kSNP4 GUI

A web interface for **kSNP4** — reference-free, alignment-free SNP discovery and
phylogenetics from genome FASTAs — deployed as an Open OnDemand interactive app.
Part of the Kapur Lab pipeline family (vSNP3, Kraken ID Parse, AMRFinderPlus,
MLST, …) and sharing their look, project layout, and deploy model.

## What it does

Give it a set of genome **FASTA** assemblies in a project; it:

1. Sanitises genome names (kSNP rejects spaces/special characters) and QC's each
   genome with `seqkit` (contigs, length, N50, GC; flags length outliers).
2. Picks the optimum k-mer size with **Kchooser4** (and records FCK).
3. Runs **kSNP4** to find SNPs and build parsimony / ML / NJ SNP trees,
   core- and pan-SNP matrices, and per-SNP VCFs.
4. Captures every option + tool version + quality standard in
   `run_manifest.json`.
5. Produces a **PDF report** (input QC, run summary, SNP/tree results with a
   rendered tree, methods/provenance) and a **single-labeled-column Excel
   stats workbook** in the vSNP3 style.

kSNP is a *set* analysis: one run uses a whole project's selected genomes and
writes to `<project>/ksnp/<label>/`.

### Getting genomes in

A project is a collection of genome **FASTAs**. Populate `download/` by:
- **Download by accession** — GCA/GCF assembly accessions (via NCBI `datasets`)
  or nucleotide accessions (via eutils efetch). Files are named from the
  assembly's organism/strain metadata (the Name differs from the GCA number),
  with a `fasta_download_crosswalk.tsv` for provenance.
- **Link / upload / drag-drop** existing FASTAs (SRA reads are also supported
  but must be assembled to FASTA before kSNP).
- **Rename** any genome (✎ in the UI) so it carries meaningful metadata — the
  filename becomes the genome label in the kSNP trees and matrices, à la vSNP.

## Quick start (deploy)

```bash
# 1. Install: conda env + kSNP4 (downloaded from SourceForge) + frontend build
deploy/install.sh --conda-base /srv/kapurlab/tools/miniforge3      # --dry-run to preview

# 2. Register the OOD apps (needs root; apps dir is root-owned)
sudo deploy/register_ood_apps.sh

# 3. Launch from the OOD dashboard → Bioinformatics → kSNP4
```

The apps also appear on the curated "Kapur Lab Pipelines" landing page
(`/etc/ood/config/wgs_pipelines.yml`).

## CLI (without the GUI)

```bash
export PATH=/srv/kapurlab/tools/ksnp_gui/env/bin:/srv/kapurlab/tools/ksnp_gui/vendor/kSNP4-bin:$PATH
export PYTHONPATH=/srv/kapurlab/tools/ksnp_gui/bin
python bin/ksnp_pipeline.py --label demo --outdir /path/to/out \
    --inputs g1.fasta g2.fasta g3.fasta [--min-frac 0.8] [--k 21] [--no-vcf]
```

## Portability to other OnDemand hosts

Everything site-specific is in `backend/app/config.py` (project roots) and
`deploy/install.sh` (`--conda-base`, kSNP4 URL). `install.sh` is idempotent and
no-sudo; only `register_ood_apps.sh` needs root. See `CLAUDE.md` and
`/srv/kapurlab/tools/amr_plus_gui/docs/BUILDING_A_SIBLING_TOOL.md`.
