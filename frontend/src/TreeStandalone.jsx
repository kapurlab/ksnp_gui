import React, { useEffect, useRef, useState } from "react";
import { phylotree, computeMidpoint } from "phylotree";
import "phylotree/dist/phylotree.css";

// Standalone Newick tree viewer (phylotree), mounted by main.jsx on ?view=tree.
// Fetches the .tre file through the project file endpoint and renders it as an
// interactive tree (search, midpoint/manual reroot, node labels), matching the
// vsnp_gui viewer. kSNP trees are unrooted; rerooting is for display only.

function fileUrl(project, absPath, inline) {
  return `./api/projects/${encodeURIComponent(project)}/file?path=${encodeURIComponent(absPath)}&inline=${inline ? 1 : 0}`;
}

export default function TreeStandalone() {
  const params = new URLSearchParams(window.location.search);
  const project = params.get("project") || "";
  const path = params.get("path") || "";

  const lowerPath = (path || "").toLowerCase();
  const isAlleleTree = lowerPath.includes("allelecounts");      // internal labels = SNP counts
  const isTipAlleleTree = lowerPath.includes("tipallelecounts"); // tip names also end in _N (SNP count)
  const isNodeLabelTree = lowerPath.includes("nodelabel");       // labels are node#+support_count — messy

  const [status, setStatus] = useState(project && path ? "Loading…" : "Missing project or path.");
  // Allele-count trees carry the SNP counts as node/tip labels — show them by default.
  const [showLabels, setShowLabels] = useState(isAlleleTree);
  const [searchTerm, setSearchTerm] = useState("");
  const [rerootMode, setRerootMode] = useState(false);
  const [counts, setCounts] = useState({ leaves: 0 });

  const treeRef = useRef(null);
  const containerRef = useRef(null);
  const originalNewickRef = useRef("");
  const rerootModeRef = useRef(false);

  useEffect(() => { rerootModeRef.current = rerootMode; }, [rerootMode]);

  function render(tree) {
    if (!containerRef.current) return;
    containerRef.current.innerHTML = "";
    const width = Math.max(400, containerRef.current.clientWidth || 800);
    const height = Math.max(400, containerRef.current.clientHeight || 600);
    const display = tree.render({
      container: containerRef.current,
      "left-right-spacing": "fit-to-size",
      "top-bottom-spacing": "fit-to-size",
      width,
      height,
      "show-scale": "top",
      "draw-size-bubbles": false,
      "internal-names": showLabels,
      selectable: false,
      collapsible: false,
      brush: false,
      zoom: true,
      "node-styler": (element, node) => {
        const data = (node && node.data) || {};
        if (node && node.children) {
          if (data.name === "root") { element.select("text").text(""); return; }
          // NodeLabel trees label internal nodes "node#+support_count" (e.g.
          // "61.00_0"); show just the SNP count (after the last underscore).
          if (isNodeLabelTree && typeof data.name === "string" && data.name.includes("_")) {
            element.select("text").text(data.name.split("_").pop());
          }
          return;
        }
        const name = data.name || "";
        if (searchTerm && name.toLowerCase().includes(searchTerm.toLowerCase())) {
          element.select("text").style("fill", "#c0392b").style("font-weight", "bold");
        }
      },
      "edge-styler": (element, edge) => {
        if (rerootModeRef.current) element.style("cursor", "pointer");
        element.on("click.tree-reroot", () => {
          if (!rerootModeRef.current) return;
          try { tree.reroot(edge.target); render(tree); }
          catch (e) { setStatus(`Reroot failed: ${e && e.message ? e.message : e}`); }
        });
      },
    });
    const svgNode = display.show ? display.show() : null;
    if (svgNode) {
      svgNode.style.width = "100%";
      svgNode.style.height = "100%";
      containerRef.current.appendChild(svgNode);
    } else {
      setStatus("phylotree returned no SVG node");
    }
  }

  useEffect(() => {
    if (!project || !path) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(fileUrl(project, path, true));
        if (!res.ok) { setStatus(`Fetch failed: HTTP ${res.status}`); return; }
        const newick = (await res.text()).trim();
        if (cancelled) return;
        originalNewickRef.current = newick;
        const tree = new phylotree(newick);
        if (tree.nodes && tree.nodes.data && tree.nodes.data.name === "root") {
          tree.nodes.data.name = "";
        }
        treeRef.current = tree;
        const tips = tree.getTips ? tree.getTips() : [];
        setCounts({ leaves: tips.length || 0 });
        render(tree);
        setStatus("");
      } catch (err) {
        setStatus(`Load failed: ${err && err.message ? err.message : err}`);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (treeRef.current) render(treeRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showLabels, searchTerm, rerootMode]);

  function midpointRoot() {
    if (!treeRef.current) return;
    try {
      const mid = computeMidpoint(treeRef.current);
      if (mid && mid.location) {
        treeRef.current.reroot(mid.location, mid.breakpoint || 0);
        render(treeRef.current);
      } else { setStatus("Midpoint not computable on this tree."); }
    } catch (err) { setStatus(`Midpoint failed: ${err && err.message ? err.message : err}`); }
  }

  function resetRoot() {
    if (!originalNewickRef.current) return;
    try {
      const tree = new phylotree(originalNewickRef.current);
      if (tree.nodes && tree.nodes.data && tree.nodes.data.name === "root") {
        tree.nodes.data.name = "";
      }
      treeRef.current = tree;
      render(tree);
    } catch (err) { setStatus(`Reset failed: ${err && err.message ? err.message : err}`); }
  }

  function downloadTre() {
    if (!project || !path) return;
    const a = document.createElement("a");
    a.href = fileUrl(project, path, false);
    a.rel = "noopener";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
  }

  useEffect(() => {
    const fname = path ? path.split("/").pop() : "";
    document.title = fname ? `Tree · ${fname}` : "Tree viewer";
  }, [path]);

  const filename = path ? path.split("/").pop() : "";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100vw", fontFamily: "system-ui, sans-serif" }}>
      <div style={{ padding: "0.5rem 0.8rem", borderBottom: "1px solid #e3ded6", background: "#f1ede6", display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
        <strong style={{ color: "#1f2a2e" }}>🌳 Tree viewer</strong>
        <span style={{ color: "#6e7b82", fontSize: "0.9em" }}>
          {filename}{counts.leaves ? ` · ${counts.leaves} leaves` : ""}{project ? ` · ${project}` : ""}
        </span>
        <span style={{ flex: 1 }} />
        <input placeholder="Search tip…" value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} style={{ width: 180 }} />
        <label style={{ fontSize: "0.9em" }}><input type="checkbox" checked={showLabels} onChange={(e) => setShowLabels(e.target.checked)} /> {isAlleleTree ? "SNP-count node labels" : "Node labels"}</label>
        <label style={{ fontSize: "0.9em" }}><input type="checkbox" checked={rerootMode} onChange={(e) => setRerootMode(e.target.checked)} /> Reroot (click a branch)</label>
        <button onClick={midpointRoot}>Midpoint root</button>
        <button onClick={resetRoot}>Reset</button>
        <button onClick={downloadTre}>Download .tre</button>
        {status ? <span style={{ color: "#c0392b", fontSize: "0.9em" }}>{status}</span> : null}
      </div>
      <div style={{ padding: "4px 12px", fontSize: "0.78em", color: "#6e7b82", background: "#fbfaf8", borderBottom: "1px solid #f1ede6", lineHeight: 1.45 }}>
        {isTipAlleleTree ? (
          <span><strong>SNP counts:</strong> each <strong>strain name ends in <code>_N</code></strong> = the number of SNPs <strong>unique to that isolate</strong> (these vary and are the most useful). Internal numbers = SNPs that <em>perfectly and uniquely</em> mark that clade — often <strong>0</strong>, because most SNPs recur elsewhere (homoplasy); that is expected, not a bug. </span>
        ) : isAlleleTree ? (
          <span><strong>SNP counts:</strong> internal numbers = SNPs that <em>perfectly and uniquely</em> mark that clade and occur nowhere else. These are <strong>often 0</strong> (most SNPs recur elsewhere — homoplasy), so don't be surprised by zeros. For per-isolate SNP counts that actually vary, open the <strong>“per-isolate SNP counts on tips”</strong> tree (<code>tree_tipAlleleCounts…parsimony.tre</code>). </span>
        ) : (
          <span>Internal node numbers here are <strong>branch support</strong> (0–1 from FastTreeMP), <em>not</em> SNP counts. For SNP counts open a <strong>“clade SNP counts”</strong> or <strong>“per-isolate SNP counts”</strong> tree (<code>tree_AlleleCounts…</code> / <code>tree_tipAlleleCounts…</code>); the <strong>parsimony</strong> versions are clearest (avoid the <code>NodeLabel</code> ones). </span>
        )}
        The <strong>scale / branch lengths are substitutions per site (relative)</strong>, not SNP counts — kSNP only expresses SNP differences as the allele-count labels above. Trees are <strong>unrooted</strong>; use Midpoint root or click a branch (Reroot) to orient.
      </div>
      <div ref={containerRef} style={{ flex: 1, overflow: "auto", background: "#fff" }} />
    </div>
  );
}
