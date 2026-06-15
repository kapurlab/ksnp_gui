"""
kSNP4 PDF report (reportlab + matplotlib).

Pure-Python PDF — no headless browser — so it renders reliably on any OOD host.
matplotlib / Biopython figures are best-effort: if a dependency is unavailable
the report is still produced, just without that figure.

Layout: title + run banner, a plain-language analysis summary, input-genome
quality (with a length bar), the SNP/tree results (with the ML tree drawn when
Biopython is present), and a methods/provenance page with the standards
referenced and an interpretation note.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Theme (matches the GUI's App.css palette)
TEAL = colors.HexColor("#4C8C8A")
TERRA = colors.HexColor("#C88F7A")
INK = colors.HexColor("#1F2A2E")
MUTED = colors.HexColor("#6E7B82")
BORDER = colors.HexColor("#E3DED6")
DANGER = colors.HexColor("#C46A6A")
SUCCESS = colors.HexColor("#6BAA75")
WARN = colors.HexColor("#D8B26E")


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("H1", parent=ss["Title"], textColor=INK, fontSize=20, spaceAfter=2))
    ss.add(ParagraphStyle("Sub", parent=ss["Normal"], textColor=MUTED, fontSize=10, spaceAfter=10))
    ss.add(ParagraphStyle("H2", parent=ss["Heading2"], textColor=TEAL, fontSize=13,
                          spaceBefore=12, spaceAfter=4))
    ss.add(ParagraphStyle("Body", parent=ss["Normal"], textColor=INK, fontSize=9.5,
                          leading=13, alignment=TA_LEFT, spaceAfter=4))
    ss.add(ParagraphStyle("Small", parent=ss["Normal"], textColor=MUTED, fontSize=8, leading=10))
    ss.add(ParagraphStyle("Cell", parent=ss["Normal"], textColor=INK, fontSize=8.5, leading=11))
    return ss


def _kv_table(rows: List[Tuple[str, str]], ss, col0=2.6 * inch, col1=4.2 * inch) -> Table:
    data = [[Paragraph(f"<b>{k}</b>", ss["Cell"]), Paragraph(str(v), ss["Cell"])] for k, v in rows]
    t = Table(data, colWidths=[col0, col1])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDER),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#FBFAF8")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _banner(text: str, fill, ss) -> Table:
    t = Table([[Paragraph(f'<font color="white"><b>{text}</b></font>', ss["Body"])]],
              colWidths=[6.9 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), fill),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return t


def _grid(data, ss, col_in, small=False):
    style = ss["Small"] if small else ss["Cell"]
    # Header cells are Paragraph flowables, which ignore the table's TEXTCOLOR,
    # so give the first row its own white, bold style for contrast on the teal.
    hdr_style = ParagraphStyle("GridHdr", parent=style, textColor=colors.white,
                               fontName="Helvetica-Bold")
    body = [[Paragraph(str(c), hdr_style if i == 0 else style) for c in row]
            for i, row in enumerate(data)]
    t = Table(body, colWidths=[c * inch for c in col_in], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F5F2")]),
        ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


# ---------------------------------------------------------------------------
# Figures (best-effort)
# ---------------------------------------------------------------------------
def _bar_genome_lengths(qc: Dict[str, Any], outpath: Path) -> bool:
    genomes = [g for g in (qc.get("genomes") or []) if g.get("length")]
    if len(genomes) < 2:
        return False
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        genomes = sorted(genomes, key=lambda g: g["length"])
        names = [g["name"][:28] for g in genomes]
        vals = [g["length"] / 1e6 for g in genomes]
        colours = ["#C88F7A" if g.get("verdict") == "review" else "#4C8C8A" for g in genomes]
        fig, ax = plt.subplots(figsize=(6.6, max(1.6, 0.26 * len(names) + 0.7)))
        ax.barh(names, vals, color=colours)
        ax.set_xlabel("genome length (Mbp)")
        ax.set_title("Input genome lengths (terracotta = >20% from median)",
                     color="#1F2A2E", fontsize=10)
        ax.tick_params(labelsize=7)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(outpath, dpi=150)
        plt.close(fig)
        return True
    except Exception:
        return False


def _draw_tree(run_dir: Path, outpath: Path) -> Tuple[bool, str]:
    """Render a kSNP Newick tree to PNG via Biopython. Prefer ML > parsimony >
    core > any .tre. Returns (ok, tree_filename)."""
    if not run_dir.is_dir():
        return False, ""
    trees = sorted(run_dir.rglob("*.tre"))
    if not trees:
        return False, ""

    def rank(p: Path) -> int:
        n = p.name.lower()
        if "ml" in n:
            return 0
        if "parsimony" in n:
            return 1
        if "core" in n:
            return 2
        return 3

    chosen = sorted(trees, key=rank)[0]
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from Bio import Phylo

        tree = Phylo.read(str(chosen), "newick")
        n_tips = tree.count_terminals()
        fig, ax = plt.subplots(figsize=(6.6, max(2.0, 0.28 * n_tips + 0.8)))
        Phylo.draw(tree, do_show=False, axes=ax,
                   label_func=lambda c: (c.name or "")[:36] if c.is_terminal() else None)
        ax.set_title(f"{chosen.name}", fontsize=9, color="#1F2A2E")
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.tick_params(labelsize=7)
        fig.tight_layout()
        fig.savefig(outpath, dpi=150)
        plt.close(fig)
        return True, chosen.name
    except Exception:
        return False, chosen.name


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def write_pdf(ctx: Dict[str, Any], path: Path, outdir: Path) -> None:
    ss = _styles()
    label = ctx["label"]
    qc = ctx.get("qc") or {}
    man = ctx.get("manifest") or {}
    opts = man.get("options", {}) or {}
    vers = man.get("versions", {}) or {}
    res = man.get("results", {}) or {}
    kch = man.get("kchooser", {}) or {}
    summ = qc.get("summary", {}) or {}
    run_dir = Path(man.get("outputs", {}).get("ksnp_run_dir", outdir / "ksnp_run"))

    assets = outdir / "_report_assets"
    assets.mkdir(exist_ok=True)

    story: List[Any] = []
    story.append(Paragraph("kSNP4 Phylogenomic SNP Report", ss["H1"]))
    story.append(Paragraph(
        f"Run <b>{label}</b> &nbsp;·&nbsp; {ctx['date']} &nbsp;·&nbsp; "
        f"{man.get('genome_count','?')} genomes &nbsp;·&nbsp; kSNP4 {vers.get('kSNP4','?')}",
        ss["Sub"]))

    n_genomes = man.get("genome_count", 0)
    rc = man.get("return_code", 0)
    bfill = SUCCESS if rc == 0 else DANGER
    story.append(_banner(
        f"{n_genomes} genomes · k={opts.get('k','?')} · "
        f"{res.get('snps_all') if res.get('snps_all') is not None else '?'} total SNPs · "
        f"{res.get('core_snps') if res.get('core_snps') is not None else '?'} core SNPs · "
        f"{'completed' if rc == 0 else 'completed with warnings'}", bfill, ss))
    story.append(Spacer(1, 8))

    # --- Analysis summary (plain language) ---
    story.append(Paragraph("Analysis summary", ss["H2"]))
    fck = kch.get("fck")
    summary_txt = (
        f"kSNP4 compared <b>{n_genomes}</b> genome assemblies using a reference-free, "
        f"alignment-free k-mer approach (k = <b>{opts.get('k','?')}</b>, chosen by Kchooser4 "
        f"as the optimum for this set"
        + (f"; FCK = <b>{fck}</b>" if fck is not None else "")
        + f"). It identified <b>{res.get('snps_all','?')}</b> SNP loci across the pan-genome "
        f"and <b>{res.get('core_snps','?')}</b> core SNPs present in all genomes, and built "
        f"{len(res.get('trees') or [])} phylogenetic tree(s)"
        + (" including a maximum-likelihood tree" if opts.get("ML") else "")
        + f". SNP loci were required in at least <b>{int(float(opts.get('min_frac',0.8))*100)}%</b> "
        f"of genomes (min_frac = {opts.get('min_frac','?')})."
    )
    story.append(Paragraph(summary_txt, ss["Body"]))
    if fck is not None and fck < 0.2:
        story.append(Spacer(1, 4))
        story.append(_banner(
            f"⚠ Low FCK ({fck}): the genomes share few core k-mers and may be too "
            "divergent for a confident core-SNP tree. Treat deep branching cautiously.",
            WARN, ss))

    # --- Input genome quality ---
    story.append(Paragraph("Input genome quality", ss["H2"]))
    story.append(Paragraph(
        "Per-genome assembly statistics from <i>seqkit stats</i>. Genomes whose total "
        "length deviates more than 20% from the set median are flagged for review — an "
        "outlier is usually contamination or a mis-assembly and weakens the SNP set "
        "(ISO 15189 validity check).", ss["Body"]))
    figL = assets / "genome_lengths.png"
    if _bar_genome_lengths(qc, figL):
        story.append(Image(str(figL), width=6.4 * inch, height=_img_h(figL, 6.4)))
    genomes = qc.get("genomes") or []
    if genomes:
        hdr = ["Genome", "Contigs", "Length (bp)", "N50", "GC%", "QC"]
        data = [hdr]
        for g in genomes[:80]:
            data.append([
                g.get("name", "")[:32], _i(g.get("contigs")), _i(g.get("length")),
                _i(g.get("n50")), _f(g.get("gc_pct")), (g.get("verdict") or "—").upper(),
            ])
        story.append(_grid(data, ss, [2.4, 0.8, 1.3, 1.0, 0.7, 0.7], small=True))
        if len(genomes) > 80:
            story.append(Paragraph(f"… {len(genomes) - 80} more in input_qc.json.", ss["Small"]))
    else:
        story.append(Paragraph("No per-genome QC available (seqkit not found at run time).", ss["Body"]))

    # --- Understanding your results: the three SNP counts, explained ---
    interp = man.get("interpretation", {}) or {}
    mfrac = res.get("majority_fraction") or opts.get("min_frac") or 0.8
    story.append(Paragraph("Understanding your results", ss["H2"]))
    story.append(Paragraph(
        "A <b>SNP</b> (single-nucleotide polymorphism) is a single DNA letter that "
        "differs between genomes. kSNP reports the SNPs three ways — think of them as "
        "three widening circles of evidence:", ss["Body"]))
    counts = [["SNP set", "Count", "% of all", "What it is — in plain terms"]]
    counts.append([
        "All", _i(res.get("snps_all")), "100%",
        "Every SNP found in any genome (the pan-genome). The most data and the finest "
        "detail, but some positions are missing in some genomes."])
    counts.append([
        "Core", _i(res.get("core_snps")),
        (f"{res.get('core_pct')}%" if res.get("core_pct") is not None else "—"),
        "SNPs present in EVERY genome — no missing data. The most trustworthy set; the "
        "core tree is usually the one to believe when this share is high."])
    counts.append([
        f"Majority (≥{mfrac})", _i(res.get("majority_snps")),
        (f"{res.get('majority_pct')}%" if res.get("majority_pct") is not None else "—"),
        f"SNPs present in at least {int(float(mfrac)*100)}% of genomes. A middle ground — "
        "more SNPs than core, less missing data than all."])
    story.append(_grid(counts, ss, [1.1, 0.8, 0.7, 4.3], small=True))
    story.append(Spacer(1, 6))

    # Sample-set verdict banner + bullets
    lvl = interp.get("level", "ok")
    bfill = {"good": SUCCESS, "ok": WARN, "caution": DANGER}.get(lvl, WARN)
    story.append(_banner(f"Is this a good sample set?  {interp.get('headline', '—')}", bfill, ss))
    story.append(Spacer(1, 3))
    for pt in (interp.get("points") or []):
        story.append(Paragraph(f"• {pt}", ss["Small"]))
    story.append(Paragraph(
        "Rule of thumb: a high core % and FCK ≥ 0.1 mean the genomes are related "
        "closely enough that kSNP finds &gt;97% of SNPs and the tree is accurate. A low "
        "core % means the genomes are diverse or some assemblies are incomplete.",
        ss["Small"]))
    story.append(Spacer(1, 6))

    # Run parameters (compact)
    story.append(_kv_table([
        ("Optimum k (Kchooser4) / k used", f"{kch.get('optimum_k') or '—'} / {opts.get('k') or '—'}"),
        ("FCK (relatedness; ≥0.1 is good)", str(fck if fck is not None else "—")),
        ("min_frac (majority cutoff)", str(opts.get("min_frac", "—"))),
        ("Trees produced", f"{len(res.get('trees') or [])} (.tre files)"),
        ("SNP matrices / VCF files", f"{len(res.get('matrices') or [])} / {len(res.get('vcf') or [])}"),
    ], ss))
    figT = assets / "tree.png"
    ok, tree_name = _draw_tree(run_dir, figT)
    if ok:
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"Phylogeny ({tree_name}):", ss["Body"]))
        story.append(Image(str(figT), width=6.4 * inch, height=_img_h(figT, 6.4)))
    elif tree_name:
        story.append(Paragraph(
            f"Tree <b>{tree_name}</b> produced (rendering skipped — open the .tre in "
            "FigTree / iTOL).", ss["Small"]))

    # --- Guide to the output files ---
    guide = man.get("file_guide", []) or []
    groups = res.get("file_groups", {}) or {}
    if guide:
        story.append(Paragraph("Guide to the output files", ss["H2"]))
        story.append(Paragraph(
            "kSNP writes many files. They fall into a few groups — here is what each "
            "group is and when you'd open it, so the file count isn't overwhelming:",
            ss["Body"]))
        rows = [["File group", "n", "What it is  ·  when to use it"]]
        for g in guide:
            key = g.get("key")
            if key == "report":
                n = "2"
            else:
                n = str(groups.get(key, 0))
            if key != "report" and groups.get(key, 0) == 0:
                continue  # don't list groups this run didn't produce
            rows.append([g.get("label", key), n,
                         f"{g.get('what','')}  ·  {g.get('use','')}"])
        story.append(_grid(rows, ss, [1.7, 0.35, 4.85], small=True))
        story.append(Spacer(1, 8))

    # --- Methods & provenance ---
    story.append(Paragraph("Methods &amp; provenance", ss["H2"]))
    iso = ", ".join(r.get("standard", "") for r in (man.get("iso_references") or []) if r.get("standard"))
    story.append(_kv_table([
        ("kSNP4", vers.get("kSNP4", "—")),
        ("Kchooser4 / seqkit", f"{vers.get('Kchooser4','—')} / {vers.get('seqkit','—')}"),
        ("Command", " ".join(man.get("command") or []) or "—"),
        ("Options", f"k={opts.get('k')} · min_frac={opts.get('min_frac')} · "
                    f"core={opts.get('core')} · ML={opts.get('ML')} · vcf={opts.get('vcf')} · "
                    f"threads={opts.get('threads')}"),
        ("Standards referenced", iso or "—"),
    ], ss))
    story.append(Spacer(1, 6))
    story.append(Paragraph(man.get("thresholds_note", ""), ss["Small"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Interpretation: kSNP is reference-free and alignment-free; SNP counts and "
        "branch support depend on k, min_frac, and assembly quality. Core-SNP trees are "
        "most reliable when FCK is high and genome lengths are consistent. Results are "
        "genomic relatedness estimates that support — but do not replace — epidemiological "
        "context. Genome names were sanitised (special characters removed) for kSNP "
        "compatibility; see name_crosswalk.tsv for the mapping.", ss["Small"]))

    doc = SimpleDocTemplate(
        str(path), pagesize=letter,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        title=f"kSNP4 report — {label}", author="ksnp_gui",
    )
    doc.build(story)


# ---- small helpers ----
def _i(v):
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "—" if v in (None, "") else str(v)


def _f(v):
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "—" if v in (None, "") else str(v)


def _img_h(path: Path, width_in: float) -> float:
    """Preserve aspect ratio for an embedded PNG given a target width (inches)."""
    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as im:
            w, h = im.size
        return width_in * (h / w) * inch
    except Exception:
        return 2.0 * inch
