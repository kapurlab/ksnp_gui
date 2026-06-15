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

  const [status, setStatus] = useState(project && path ? "Loading…" : "Missing project or path.");
  const [showLabels, setShowLabels] = useState(false);
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
          if (data.name === "root") element.select("text").text("");
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
        <label style={{ fontSize: "0.9em" }}><input type="checkbox" checked={showLabels} onChange={(e) => setShowLabels(e.target.checked)} /> Node labels</label>
        <label style={{ fontSize: "0.9em" }}><input type="checkbox" checked={rerootMode} onChange={(e) => setRerootMode(e.target.checked)} /> Reroot (click a branch)</label>
        <button onClick={midpointRoot}>Midpoint root</button>
        <button onClick={resetRoot}>Reset</button>
        <button onClick={downloadTre}>Download .tre</button>
        {status ? <span style={{ color: "#c0392b", fontSize: "0.9em" }}>{status}</span> : null}
      </div>
      <div style={{ padding: "4px 12px", fontSize: "0.78em", color: "#6e7b82", background: "#fbfaf8", borderBottom: "1px solid #f1ede6" }}>
        kSNP trees are <strong>unrooted</strong> — the apparent root is for drawing only. Branch lengths are changes per number of SNPs. Use Midpoint root or click a branch (Reroot) to orient.
      </div>
      <div ref={containerRef} style={{ flex: 1, overflow: "auto", background: "#fff" }} />
    </div>
  );
}
