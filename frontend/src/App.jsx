import { useState, useEffect, useRef } from "react";
import "./App.css";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const APP_VERSION = "0.1.0";

function fileIcon(name) {
  if (name.endsWith(".json")) return "📁";
  if (name.endsWith(".tsv")) return "📊";
  if (name.endsWith(".xlsx")) return "📊";
  if (name.endsWith(".pdf")) return "📄";
  if (name.endsWith(".png")) return "🖼";
  if (name.endsWith(".tre") || name.endsWith(".nwk")) return "🌳";
  if (name.endsWith(".vcf")) return "🧬";
  if (name.endsWith(".fasta") || name.endsWith(".fa")) return "🧬";
  if (name.endsWith(".txt") || name === "COUNT_SNPs" || name === "COUNT_coreSNPs") return "📝";
  return "📁";
}

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fmtInt(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString() : String(v);
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
export default function App() {
  const [projects, setProjects] = useState([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [newProjectName, setNewProjectName] = useState("");
  const [creatingProject, setCreatingProject] = useState(false);
  const [activeProject, setActiveProject] = useState("");
  const [addPath, setAddPath] = useState({});
  const [sraText, setSraText] = useState({});
  const [accText, setAccText] = useState({});        // FASTA-by-accession (GCA/GCF/nucleotide)
  const [accRename, setAccRename] = useState(true);  // save metadata-derived names
  const [addStatus, setAddStatus] = useState({});
  const [inputsByProj, setInputsByProj] = useState({});
  const uploadProjRef = useRef("");
  const uploadInputRef = useRef(null);
  const [expanded, setExpanded] = useState({});
  const [genomes, setGenomes] = useState({});       // project -> [genome]
  const [excluded, setExcluded] = useState({});      // `${project}::${path}` -> true
  const [runsByProj, setRunsByProj] = useState({});  // project -> [run]

  // Run config
  const [label, setLabel] = useState("");
  const [minFrac, setMinFrac] = useState(0.8);
  const [runCore, setRunCore] = useState(true);
  const [runMl, setRunMl] = useState(true);
  const [runVcf, setRunVcf] = useState(true);
  const [kOverride, setKOverride] = useState("");
  const [threads, setThreads] = useState("");

  const [running, setRunning] = useState(false);
  const [jobId, setJobId] = useState(null);
  const [jobStatus, setJobStatus] = useState("idle");
  const [logLines, setLogLines] = useState([]);
  const [currentStep, setCurrentStep] = useState("");
  const [activeRun, setActiveRun] = useState(null);  // {project, label, genome_count}

  const [settingsDraft, setSettingsDraft] = useState({});
  const [folderBrowser, setFolderBrowser] = useState({ open: false, path: "", parent: null, entries: [], loading: false, error: "" });

  const [showSettings, setShowSettings] = useState(false);
  const [showProjects, setShowProjects] = useState(true);
  const [showRun, setShowRun] = useState(true);
  const [showResults, setShowResults] = useState(true);
  const [showLogs, setShowLogs] = useState(true);

  // Results pane state
  const [selectedRun, setSelectedRun] = useState(null);   // {project, label}
  const [runSummaries, setRunSummaries] = useState({});   // key -> {loading, manifest, input_qc}
  const [runResults, setRunResults] = useState({});       // key -> {files}

  const logRef = useRef(null);
  const eventSourceRef = useRef(null);

  const runKey = (project, lbl) => `${project}::${lbl}`;
  const genKey = (project, g) => `${project}::${g.path}`;

  useEffect(() => {
    fetch("./api/config")
      .then((r) => r.json())
      .then((cfg) => {
        setSettingsDraft(cfg);
        if (cfg.min_frac != null) setMinFrac(cfg.min_frac);
        if (cfg.run_core != null) setRunCore(!!cfg.run_core);
        if (cfg.run_ml != null) setRunMl(!!cfg.run_ml);
        if (cfg.run_vcf != null) setRunVcf(!!cfg.run_vcf);
        if (cfg.threads) setThreads(String(cfg.threads));
      })
      .catch(() => {});
    loadProjects();
    fetch("./api/jobs")
      .then((r) => r.json())
      .then((jobs) => {
        const live = jobs.find((j) => j.status === "running");
        if (live) {
          setJobId(live.id);
          setJobStatus("running");
          setRunning(true);
          const m = (live.name || "").match(/^(.*?)\/(.*?) — kSNP4/);
          if (m) setActiveRun({ project: m[1], label: m[2] });
          streamLogUntilDone(live.id, m ? { project: m[1], label: m[2] } : null, () => {});
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [logLines]);

  useEffect(() => {
    if (!projects.length) {
      if (activeProject) setActiveProject("");
      return;
    }
    if (!activeProject || !projects.find((p) => p.name === activeProject)) {
      const first = projects[0].name;
      setActiveProject(first);
      if (inputsByProj[first] === undefined) loadInputs(first);
    }
  }, [projects]);

  function loadProjects() {
    setProjectsLoading(true);
    fetch("./api/projects")
      .then((r) => r.json())
      .then((data) => { setProjects(data); setProjectsLoading(false); })
      .catch(() => setProjectsLoading(false));
  }

  async function createProject() {
    const name = newProjectName.trim();
    if (!name || creatingProject) return;
    setCreatingProject(true);
    try {
      const res = await fetch("./api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        window.alert(`Could not create project: ${detail.detail || res.status}`);
        return;
      }
      const created = await res.json().catch(() => ({}));
      setNewProjectName("");
      loadProjects();
      if (created.name) {
        const n = created.name;
        setExpanded((e) => ({ ...e, [n]: true }));
        setActiveProject(n);
        await Promise.all([fetchGenomes(n), loadInputs(n), loadRuns(n)]);
      }
    } finally {
      setCreatingProject(false);
    }
  }

  function fetchGenomes(name) {
    return fetch(`./api/projects/${encodeURIComponent(name)}/samples`)
      .then((r) => r.json())
      .then((data) => setGenomes((s) => ({ ...s, [name]: data })))
      .catch(() => setGenomes((s) => ({ ...s, [name]: [] })));
  }

  function loadRuns(name) {
    return fetch(`./api/projects/${encodeURIComponent(name)}/runs`)
      .then((r) => r.json())
      .then((data) => setRunsByProj((m) => ({ ...m, [name]: data.runs || [] })))
      .catch(() => setRunsByProj((m) => ({ ...m, [name]: [] })));
  }

  function loadInputs(name) {
    return fetch(`./api/projects/${encodeURIComponent(name)}/inputs`)
      .then((r) => r.json())
      .then((data) => setInputsByProj((m) => ({ ...m, [name]: data })))
      .catch(() => setInputsByProj((m) => ({ ...m, [name]: { files: [], count: 0, total_bytes: 0 } })));
  }

  function toggleProject(name) {
    const isExpanded = expanded[name];
    setExpanded((e) => ({ ...e, [name]: !isExpanded }));
    setActiveProject(name);
    if (!isExpanded) {
      if (!genomes[name]) fetchGenomes(name);
      if (!runsByProj[name]) loadRuns(name);
      loadInputs(name);
    }
  }

  function selectProject(name) {
    setActiveProject(name);
    if (genomes[name] === undefined) fetchGenomes(name);
    if (runsByProj[name] === undefined) loadRuns(name);
    if (inputsByProj[name] === undefined) loadInputs(name);
  }

  const setStat = (name, msg) => setAddStatus((m) => ({ ...m, [name]: msg }));

  async function refreshAfterLoad(name) {
    await Promise.all([fetchGenomes(name), loadInputs(name)]);
    loadProjects();
  }

  async function linkLocal(name) {
    const path = (addPath[name] || "").trim();
    if (!path) return;
    setStat(name, "Linking…");
    try {
      const res = await fetch(`./api/projects/${encodeURIComponent(name)}/link-local`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setStat(name, `Import failed: ${data.detail || res.status}`); return; }
      setStat(name, `Linked ${data.linked} file${data.linked === 1 ? "" : "s"}.`);
      setAddPath((m) => ({ ...m, [name]: "" }));
      await refreshAfterLoad(name);
    } catch (e) {
      setStat(name, `Import failed: ${e.message}`);
    }
  }

  function pickFiles(name) {
    uploadProjRef.current = name;
    uploadInputRef.current?.click();
  }

  async function uploadFiles(name, fileList) {
    const files = Array.from(fileList || []).filter(
      (f) => /\.(fasta|fa|fna|fas|ffn|fsa)$/i.test(f.name) || f.name.endsWith(".fastq.gz")
    );
    if (!name || !files.length) return;
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    setStat(name, `Uploading ${files.length} file${files.length === 1 ? "" : "s"}…`);
    try {
      const res = await fetch(`./api/projects/${encodeURIComponent(name)}/upload`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setStat(name, `Upload failed: ${data.detail || res.status}`); return; }
      setStat(name, `Uploaded ${data.uploaded} file${data.uploaded === 1 ? "" : "s"}.`);
      await refreshAfterLoad(name);
    } catch (e) {
      setStat(name, `Upload failed: ${e.message}`);
    }
  }

  function parseAccessions(text) {
    return (text || "").split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
  }

  async function sraDownload(name) {
    const accessions = parseAccessions(sraText[name]);
    if (!accessions.length) return;
    setStat(name, `Resolving ${accessions.length} accession${accessions.length === 1 ? "" : "s"}…`);
    setShowLogs(true);
    try {
      const res = await fetch(`./api/projects/${encodeURIComponent(name)}/sra/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accessions }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setStat(name, `Download failed: ${data.detail || res.status}`); return; }
      setStat(name, "Downloading… progress shows in the Pipeline Log below.");
      setSraText((m) => ({ ...m, [name]: "" }));
      setJobId(data.job_id);
      setJobStatus("running");
      setLogLines([]);
      streamLogUntilDone(data.job_id, null, () => {
        setStat(name, "Download finished — see inputs below.");
        refreshAfterLoad(name);
      });
    } catch (e) {
      setStat(name, `Download failed: ${e.message}`);
    }
  }

  function parseAccList(text) {
    return (text || "").split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
  }

  async function fastaDownload(name) {
    const accessions = parseAccList(accText[name]);
    if (!accessions.length) return;
    setStat(name, `Fetching ${accessions.length} genome FASTA${accessions.length === 1 ? "" : "s"}…`);
    setShowLogs(true);
    try {
      const res = await fetch(`./api/projects/${encodeURIComponent(name)}/fasta/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accessions, rename: accRename }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setStat(name, `Download failed: ${data.detail || res.status}`); return; }
      setStat(name, "Downloading genomes… progress shows in the Pipeline Log below.");
      setAccText((m) => ({ ...m, [name]: "" }));
      setJobId(data.job_id);
      setJobStatus("running");
      setLogLines([]);
      streamLogUntilDone(data.job_id, null, () => {
        setStat(name, "Genome download finished — see genomes below.");
        refreshAfterLoad(name);
      });
    } catch (e) {
      setStat(name, `Download failed: ${e.message}`);
    }
  }

  async function renameInput(name, oldFile) {
    const suggestion = oldFile.replace(/\.(fasta|fa|fna|fas|ffn|fsa)$/i, "");
    const nn = window.prompt(
      `Rename "${oldFile}".\nGive it a meaningful label — it becomes the genome name in the kSNP trees & matrices.\nThe extension is kept; spaces and special characters become "_".`,
      suggestion
    );
    if (nn === null || !nn.trim()) return;
    try {
      const res = await fetch(`./api/projects/${encodeURIComponent(name)}/inputs/rename`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old: oldFile, new: nn }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setStat(name, `Rename failed: ${data.detail || res.status}`); return; }
      setStat(name, `Renamed to ${data.new}.`);
      await refreshAfterLoad(name);
    } catch (e) {
      setStat(name, `Rename failed: ${e.message}`);
    }
  }

  async function deleteInput(name, filename) {
    if (!window.confirm(`Remove ${filename} from this project's download/ folder?`)) return;
    try {
      await fetch(`./api/projects/${encodeURIComponent(name)}/inputs/${encodeURIComponent(filename)}`, { method: "DELETE" });
      await refreshAfterLoad(name);
    } catch (e) {
      setStat(name, `Delete failed: ${e.message}`);
    }
  }

  function toggleExclude(project, g) {
    const key = genKey(project, g);
    setExcluded((m) => {
      const next = { ...m };
      if (next[key]) delete next[key];
      else next[key] = true;
      return next;
    });
  }

  function includedGenomes(project) {
    return (genomes[project] || []).filter((g) => !excluded[genKey(project, g)]);
  }

  // ---- Run ----
  function runProject() {
    if (running || !activeProject) return;
    const incl = includedGenomes(activeProject);
    if (incl.length < 2) {
      window.alert("kSNP needs at least 2 genome FASTAs. Add FASTA assemblies to the project, or include more genomes.");
      return;
    }
    if (eventSourceRef.current) { eventSourceRef.current.close(); eventSourceRef.current = null; }
    setRunning(true);
    setJobStatus("running");
    setLogLines([]);
    setCurrentStep("");
    setShowLogs(true);

    fetch("./api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project: activeProject,
        label: label.trim() || null,
        genomes: incl.map((g) => g.path),
        min_frac: parseFloat(minFrac),
        run_core: runCore,
        run_ml: runMl,
        run_vcf: runVcf,
        k: kOverride ? parseInt(kOverride, 10) : null,
        threads: threads ? parseInt(threads, 10) : null,
      }),
    })
      .then((r) => (r.ok ? r.json() : r.json().then((e) => { throw new Error(e.detail || "Run failed"); })))
      .then(({ job_id, label: lbl, genome_count }) => {
        const samp = { project: activeProject, label: lbl, genome_count };
        setActiveRun(samp);
        setSelectedRun({ project: activeProject, label: lbl });
        setShowResults(true);
        setJobId(job_id);
        streamLogUntilDone(job_id, samp, () => {});
      })
      .catch((err) => {
        setLogLines((prev) => [...prev, `ERROR: ${err.message}`]);
        setRunning(false);
        setJobStatus("failed");
      });
  }

  function streamLogUntilDone(id, samp, done) {
    const es = new EventSource(`./api/jobs/${id}/log`);
    eventSourceRef.current = es;
    es.onmessage = (evt) => {
      const data = evt.data;
      if (data === "[DONE]") {
        es.close();
        setRunning(false);
        fetch(`./api/jobs/${id}`)
          .then((r) => r.json())
          .then((job) => {
            setJobStatus(job.status);
            setCurrentStep("");
            if (samp && samp.label) {
              loadRuns(samp.project);
              loadRunSummary(samp.project, samp.label);
              loadRunResults(samp.project, samp.label);
            }
            loadProjects();
          })
          .catch(() => {})
          .finally(() => done && done());
      } else {
        setLogLines((prev) => [...prev, data]);
        if (/Step \d+:/i.test(data) || /Pipeline completed/i.test(data)) {
          setCurrentStep(data.trim().replace(/^#+\s*/, ""));
        }
      }
    };
    es.onerror = () => { es.close(); setRunning(false); setJobStatus("failed"); done && done(); };
  }

  // ---- Results ----
  function loadRunSummary(project, lbl) {
    const key = runKey(project, lbl);
    setRunSummaries((m) => ({ ...m, [key]: { ...(m[key] || {}), loading: true } }));
    fetch(`./api/projects/${encodeURIComponent(project)}/runs/${encodeURIComponent(lbl)}/summary`)
      .then((r) => r.json())
      .then((data) => setRunSummaries((m) => ({ ...m, [key]: { loading: false, ...data } })))
      .catch(() => setRunSummaries((m) => ({ ...m, [key]: { loading: false, present: false } })));
  }

  function loadRunResults(project, lbl) {
    const key = runKey(project, lbl);
    setRunResults((m) => ({ ...m, [key]: { ...(m[key] || {}), loading: true } }));
    fetch(`./api/projects/${encodeURIComponent(project)}/runs/${encodeURIComponent(lbl)}/results`)
      .then((r) => r.json())
      .then((data) => setRunResults((m) => ({ ...m, [key]: { loading: false, ...data } })))
      .catch(() => setRunResults((m) => ({ ...m, [key]: { loading: false, files: [] } })));
  }

  function selectRun(project, lbl) {
    setSelectedRun({ project, label: lbl });
    setShowResults(true);
    const key = runKey(project, lbl);
    if (!runSummaries[key]) loadRunSummary(project, lbl);
    if (!runResults[key]) loadRunResults(project, lbl);
  }

  async function deleteRun(project, lbl) {
    if (!window.confirm(`Delete kSNP run "${lbl}" and all its outputs?`)) return;
    try {
      await fetch(`./api/projects/${encodeURIComponent(project)}/runs/${encodeURIComponent(lbl)}`, { method: "DELETE" });
      if (selectedRun && selectedRun.project === project && selectedRun.label === lbl) setSelectedRun(null);
      loadRuns(project);
      loadProjects();
    } catch (e) { /* ignore */ }
  }

  // ---- Settings / folder browser ----
  function browseDirs(path) {
    setFolderBrowser((s) => ({ ...s, loading: true, error: "" }));
    fetch(`./api/browse-dirs?path=${encodeURIComponent(path || "")}`)
      .then((r) => (r.ok ? r.json() : r.json().then((e) => { throw new Error(e.detail || "Cannot open folder"); })))
      .then((d) => setFolderBrowser((s) => ({ ...s, path: d.path, parent: d.parent, entries: d.entries, loading: false })))
      .catch((err) => setFolderBrowser((s) => ({ ...s, loading: false, error: err.message })));
  }
  function openFolderBrowser() {
    setFolderBrowser({ open: true, path: "", parent: null, entries: [], loading: true, error: "" });
    browseDirs(settingsDraft.projects_root || "");
  }
  function chooseFolder() {
    setSettingsDraft((d) => ({ ...d, projects_root: folderBrowser.path }));
    setFolderBrowser((s) => ({ ...s, open: false }));
  }

  function saveSettings() {
    fetch("./api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        projects_root: settingsDraft.projects_root,
        min_frac: settingsDraft.min_frac != null ? parseFloat(settingsDraft.min_frac) : undefined,
      }),
    })
      .then((r) => r.json())
      .then(() => loadProjects())
      .catch(() => {});
  }

  const logLineClass = (line) => {
    if (line.startsWith("$ ")) return "log-line cmd";
    if (line.startsWith("ERROR") || line.startsWith("error")) return "log-line error";
    if (line === "[DONE]") return "log-line done";
    return "log-line";
  };

  const statusText = { idle: "idle", running: "running", succeeded: "succeeded", failed: "failed" }[jobStatus];

  const selKey = selectedRun ? runKey(selectedRun.project, selectedRun.label) : null;
  const sum = selKey ? runSummaries[selKey] : null;
  const man = sum?.manifest || {};
  const qc = sum?.input_qc || {};
  const resFiles = selKey ? runResults[selKey] : null;
  const rres = man.results || {};
  const interp = man.interpretation || {};
  const fileGuide = man.file_guide || [];
  const fileGroups = rres.file_groups || {};
  const mfrac = rres.majority_fraction ?? man.options?.min_frac ?? 0.8;
  const lvlColor = { good: "#6BAA75", ok: "#D8B26E", caution: "#C46A6A" }[interp.level] || "#6E7B82";

  const inclCount = activeProject ? includedGenomes(activeProject).length : 0;
  const totalCount = activeProject ? (genomes[activeProject]?.length || 0) : 0;

  return (
    <div className="app">
      <input
        ref={uploadInputRef}
        type="file"
        multiple
        accept=".fasta,.fa,.fna,.fas,.ffn,.fsa,.fastq.gz,application/gzip"
        style={{ display: "none" }}
        onChange={(e) => {
          const files = Array.from(e.target.files);
          e.target.value = "";
          if (uploadProjRef.current) uploadFiles(uploadProjRef.current, files);
        }}
      />
      {/* ── Header ─────────────────────────────────────────────── */}
      <header className="app-header">
        <div className="app-brand">
          <img className="app-logo" src="./ksnp_icon.svg" alt="kSNP phylogenetic tree icon" />
          <div>
            <h1>
              kSNP4 <span className="version-tag">v{APP_VERSION}</span>
            </h1>
            <p>Reference-free, alignment-free SNP discovery &amp; phylogenetics from genome FASTAs</p>
          </div>
        </div>
        <div className="status-pill">
          <span className="dot" data-state={jobStatus} />
          <span>{statusText}</span>
        </div>
      </header>

      <main className="layout">
        {/* ── Status strip ─────────────────────────────────────── */}
        <section className="status-strip">
          <div className="status-item">
            <span className="status-label">Genomes selected</span>
            <span className="status-value">
              {activeProject ? `${inclCount} / ${totalCount}` : "—"}
            </span>
          </div>
          <div className="status-item">
            <span className="status-label">Core SNPs</span>
            <span className="status-value">{man?.results?.core_snps != null ? fmtInt(man.results.core_snps) : "—"}</span>
          </div>
          <div className="status-item">
            <span className="status-label">Total SNPs</span>
            <span className="status-value">{man?.results?.snps_all != null ? fmtInt(man.results.snps_all) : "—"}</span>
          </div>
          <div className="status-item">
            <span className="status-label">Job</span>
            <span className="status-value cap">
              {jobStatus === "running" ? <><span className="pulse-dot" />running</> : statusText}
            </span>
          </div>
        </section>

        {/* ════ Settings ════ */}
        <div className="row-header">
          <h2>Settings</h2>
          <button className="ghost" onClick={() => {
            if (!showSettings) fetch("./api/config").then((r) => r.json()).then(setSettingsDraft).catch(() => {});
            setShowSettings(!showSettings);
          }}>
            {showSettings ? "Hide" : "Show"}
          </button>
        </div>
        {showSettings && (
          <div className="row-grid row-grid-single">
            <section className="panel">
              <div className="form-section">
                <label className="form-label">Default min_frac (fraction of genomes a SNP must be in)</label>
                <input
                  type="number" min="0" max="1" step="0.05"
                  value={settingsDraft.min_frac ?? 0.8}
                  onChange={(e) => setSettingsDraft((d) => ({ ...d, min_frac: e.target.value }))}
                />
                <div className="form-hint">0.8 (the validated NVSL default). A SNP present in all genomes is a “core” SNP.</div>
              </div>
              <div className="form-section">
                <label className="form-label">Personal projects root</label>
                <div style={{ display: "flex", gap: 6 }}>
                  <input
                    style={{ flex: 1 }}
                    value={settingsDraft.projects_root || ""}
                    onChange={(e) => setSettingsDraft((d) => ({ ...d, projects_root: e.target.value }))}
                  />
                  <button type="button" className="ghost" onClick={openFolderBrowser}>Browse…</button>
                </div>
                {Array.isArray(settingsDraft.recent_projects_roots) && settingsDraft.recent_projects_roots.length > 0 && (
                  <select
                    style={{ marginTop: 6, width: "100%" }}
                    value=""
                    onChange={(e) => { if (e.target.value) setSettingsDraft((d) => ({ ...d, projects_root: e.target.value })); }}
                  >
                    <option value="">↻ Recent roots…</option>
                    {settingsDraft.recent_projects_roots.map((r) => (<option key={r} value={r}>{r}</option>))}
                  </select>
                )}
                <div className="form-hint">New projects are created here. Shared projects at /srv/kapurlab/projects/ are always visible. Click Save to apply.</div>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button onClick={saveSettings}>Save</button>
              </div>
            </section>
          </div>
        )}

        {/* ════ Projects & Genomes ════ */}
        <div className="row-header">
          <h2>Projects &amp; Genomes</h2>
          <button className="ghost" onClick={() => setShowProjects(!showProjects)}>
            {showProjects ? "Hide" : "Show"}
          </button>
        </div>
        {showProjects && (
          <div className="row-grid row-grid-split">
            {/* LEFT — project / genome browser */}
            <section className="panel">
              <div className="panel-header">
                <h2>Projects</h2>
                <div className="panel-actions">
                  <button className="ghost action" onClick={loadProjects}>↻ Refresh</button>
                </div>
              </div>
              <div className="row">
                <input
                  placeholder="New project name (e.g. Mbovis_outbreak)"
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value.replace(/\s+/g, "_"))}
                  onKeyDown={(e) => { if (e.key === "Enter") createProject(); }}
                  disabled={creatingProject}
                  title="Spaces become underscores. Letters, digits, _ - . are allowed. Shared with the sibling GUIs."
                />
                <button onClick={createProject} disabled={creatingProject || !newProjectName.trim()}>
                  {creatingProject ? "Creating…" : "Create"}
                </button>
              </div>
              <div className="form-hint" style={{ marginTop: -4, marginBottom: 8 }}>
                Created under your personal projects root — also visible in the sibling GUIs. Add genome <strong>FASTA</strong> files to the project’s <code>download/</code> folder.
              </div>
              <div className="list project-list">
                {projectsLoading && <div className="loading-text">Loading projects…</div>}
                {!projectsLoading && projects.length === 0 && (
                  <div className="note">No projects found. Check Settings for the projects path.</div>
                )}
                {projects.map((proj) => (
                  <div
                    key={proj.name}
                    className={`list-item ${activeProject === proj.name ? "active" : ""}`}
                  >
                    <div className="item-top" onClick={() => toggleProject(proj.name)}>
                      <span className="expand-icon">{expanded[proj.name] ? "▾" : "▸"}</span>
                      <div className="list-title" title={proj.name}>{proj.name}</div>
                      <span className={`scope-badge scope-${proj.scope}`}>{proj.scope}</span>
                    </div>
                    {proj.path && <div className="list-path" title={proj.path}>{proj.path}</div>}
                    <div className="list-meta">
                      {proj.fasta_count} FASTA
                      {proj.ksnp_runs?.length > 0 && ` · ${proj.ksnp_runs.length} kSNP run${proj.ksnp_runs.length > 1 ? "s" : ""}`}
                    </div>
                    {expanded[proj.name] && (
                      <div className="sample-list">
                        {/* Genomes */}
                        {!genomes[proj.name] && <div className="loading-text">Loading genomes…</div>}
                        {genomes[proj.name]?.length === 0 && (
                          <div className="empty-msg" style={{ paddingLeft: 4 }}>
                            No FASTA genomes yet — add some from the <strong>Inputs</strong> pane on the right.
                          </div>
                        )}
                        {genomes[proj.name]?.length > 0 && (
                          <div style={{ display: "flex", gap: 8, padding: "2px 4px 6px", fontSize: 11 }}>
                            <button className="ghost" style={{ fontSize: 11 }} onClick={() => setExcluded((m) => {
                              const next = { ...m }; genomes[proj.name].forEach((g) => delete next[genKey(proj.name, g)]); return next;
                            })}>Select all</button>
                            <button className="ghost" style={{ fontSize: 11 }} onClick={() => setExcluded((m) => {
                              const next = { ...m }; genomes[proj.name].forEach((g) => { next[genKey(proj.name, g)] = true; }); return next;
                            })}>Clear</button>
                            <span className="muted">{includedGenomes(proj.name).length} of {genomes[proj.name].length} selected for run</span>
                          </div>
                        )}
                        {genomes[proj.name]?.map((g) => {
                          const key = genKey(proj.name, g);
                          const included = !excluded[key];
                          return (
                            <div key={g.path} className="sample-item">
                              <div className="sample-name-row" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                <input type="checkbox" checked={included} onChange={() => toggleExclude(proj.name, g)} title="Include in kSNP run" />
                                <div className="sample-name" title={g.name} style={{ flex: 1 }}>{g.sample}</div>
                                <span className="read-badge badge-pe">FASTA</span>
                                <span className="file-size">{fmtSize(g.size)}</span>
                                <button className="ghost" style={{ fontSize: 11 }} title="Rename — set a meaningful label for the kSNP tree" onClick={() => renameInput(proj.name, g.name)}>✎</button>
                              </div>
                            </div>
                          );
                        })}

                        {/* kSNP runs */}
                        {runsByProj[proj.name]?.length > 0 && (
                          <div style={{ marginTop: 8 }}>
                            <div className="muted" style={{ fontSize: 11, fontWeight: 600, padding: "2px 4px" }}>kSNP runs</div>
                            {runsByProj[proj.name].map((run) => (
                              <div key={run.label} className={`sample-item ${selectedRun?.project === proj.name && selectedRun?.label === run.label ? "active" : ""}`}>
                                <div className="sample-name-row" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                  <span className="result-icon">🌳</span>
                                  <div className="sample-name" title={run.label} style={{ flex: 1, cursor: "pointer" }} onClick={() => selectRun(proj.name, run.label)}>
                                    {run.label}
                                  </div>
                                  <span className={`run-status run-status-${run.status === "running" ? "running" : run.status === "done" ? "done" : "none"}`} style={{ fontSize: 11, whiteSpace: "nowrap" }}>
                                    {run.status === "running" ? "● running" : run.status === "done" ? "✓ done" : run.status}
                                  </span>
                                  <button className="ghost" style={{ fontSize: 11 }} onClick={() => selectRun(proj.name, run.label)} title="View results">View</button>
                                  <button className="ghost" style={{ fontSize: 11 }} onClick={() => deleteRun(proj.name, run.label)} title="Delete run">✕</button>
                                </div>
                                {run.status === "done" && (
                                  <div className="list-meta" style={{ paddingLeft: 26 }}>
                                    {run.genome_count != null ? `${run.genome_count} genomes` : ""}
                                    {run.k != null ? ` · k=${run.k}` : ""}
                                    {run.core_snps != null ? ` · ${fmtInt(run.core_snps)} core SNPs` : ""}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>

            {/* RIGHT — Inputs + selection summary */}
            <div style={{ display: "flex", flexDirection: "column", gap: 20, minWidth: 0 }}>
              <section className="panel">
                <div className="panel-header">
                  <h2>Inputs</h2>
                  {projects.length > 0 && (
                    <select
                      value={activeProject}
                      onChange={(e) => selectProject(e.target.value)}
                      title="Project to add genome FASTA files to"
                      style={{ width: "auto", maxWidth: "60%", padding: "6px 10px" }}
                    >
                      {projects.map((p) => (<option key={p.name} value={p.name}>{p.name}</option>))}
                    </select>
                  )}
                </div>
                {!activeProject ? (
                  <div className="empty-msg">
                    Create a project first, then import, upload, or download genome FASTA files into it.
                  </div>
                ) : (
                  <div className="input-columns">
                    <div className="input-column">
                      <h3>Bring Your Own Genomes (FASTA)</h3>
                      <div className="row" style={{ margin: 0 }}>
                        <input
                          placeholder="/srv/kapurlab/… folder or a .fasta file"
                          value={addPath[activeProject] || ""}
                          onChange={(e) => setAddPath((m) => ({ ...m, [activeProject]: e.target.value }))}
                          onKeyDown={(e) => { if (e.key === "Enter") linkLocal(activeProject); }}
                        />
                        <button className="ghost action" onClick={() => linkLocal(activeProject)} disabled={!(addPath[activeProject] || "").trim()}>Link</button>
                      </div>
                      <div className="form-hint">Symlinks every genome FASTA found — no copying. kSNP requires FASTA assemblies.</div>

                      <div className="block">
                        <h3>Upload / Drag &amp; Drop</h3>
                        <div
                          className="dropzone"
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={(e) => { e.preventDefault(); uploadFiles(activeProject, e.dataTransfer.files); }}
                        >
                          <button type="button" onClick={() => pickFiles(activeProject)}>Choose Files</button>
                          <span className="drop-hint">Or drop FASTA files here</span>
                        </div>
                        {addStatus[activeProject] && <div className="note" style={{ marginBottom: 0 }}>{addStatus[activeProject]}</div>}
                      </div>

                      {inputsByProj[activeProject]?.files?.length > 0 && (
                        <div className="block">
                          <h3 style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <span style={{ flex: 1 }}>
                              Files in download/
                              <span className="muted" style={{ marginLeft: 6, fontWeight: 400, fontSize: 12 }}>
                                ({inputsByProj[activeProject].count}, {fmtSize(inputsByProj[activeProject].total_bytes)})
                              </span>
                            </span>
                            <button className="ghost" style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => loadInputs(activeProject)} title="Refresh">Refresh</button>
                          </h3>
                          <div className="input-files">
                            {inputsByProj[activeProject].files.map((f) => (
                              <div key={f.name} className="input-file-row">
                                <span className="file-name" title={f.name} style={{ flex: 1 }}>
                                  {f.name}{!f.is_fasta && <span className="muted" style={{ fontSize: 11 }}> (not FASTA)</span>}
                                </span>
                                <span className="file-size">{fmtSize(f.size)}</span>
                                <button className="ghost" style={{ fontSize: 11, padding: "2px 7px" }} title="Rename (sets the kSNP genome label)" onClick={() => renameInput(activeProject, f.name)}>✎</button>
                                <button className="ghost" style={{ fontSize: 11, padding: "2px 7px" }} title="Remove from download/" onClick={() => deleteInput(activeProject, f.name)}>✕</button>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="input-column">
                      <h3>Download genome FASTA by accession</h3>
                      <textarea
                        rows={5}
                        placeholder={"Assembly (GCA_/GCF_) or nucleotide (NC_/CP_/…) accessions\none per line, e.g.\nGCA_000195835.3\nNC_045512.2"}
                        value={accText[activeProject] || ""}
                        onChange={(e) => setAccText((m) => ({ ...m, [activeProject]: e.target.value }))}
                        style={{ resize: "vertical", fontFamily: "inherit" }}
                      />
                      <label className="checkbox-label" style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", margin: "4px 0" }}>
                        <input type="checkbox" checked={accRename} onChange={(e) => setAccRename(e.target.checked)} />
                        <span style={{ fontSize: 12 }}>Name files by organism / strain metadata (recommended)</span>
                      </label>
                      <button
                        style={{ width: "100%" }}
                        onClick={() => fastaDownload(activeProject)}
                        disabled={!parseAccList(accText[activeProject]).length || running}
                      >
                        Fetch FASTA{parseAccList(accText[activeProject]).length ? ` (${parseAccList(accText[activeProject]).length})` : ""}
                      </button>
                      <div className="form-hint">
                        Assemblies via NCBI <code>datasets</code>; nucleotide accessions via eutils. Files land in <code>download/</code> ready for kSNP — rename any with the ✎ button to set the tree label.
                      </div>

                      <div className="block">
                        <h3>SRA Download (reads)</h3>
                        <textarea
                          rows={4}
                          placeholder={"SRR/ERR/DRR accessions (one per line)\nNote: SRA gives FASTQ reads — assemble to FASTA before kSNP."}
                          value={sraText[activeProject] || ""}
                          onChange={(e) => setSraText((m) => ({ ...m, [activeProject]: e.target.value }))}
                          style={{ resize: "vertical", fontFamily: "inherit" }}
                        />
                        <button
                          style={{ width: "100%" }}
                          onClick={() => sraDownload(activeProject)}
                          disabled={!parseAccessions(sraText[activeProject]).length || running}
                        >
                          Download reads{parseAccessions(sraText[activeProject]).length ? ` (${parseAccessions(sraText[activeProject]).length})` : ""}
                        </button>
                        <div className="form-hint">Runs in the background. kSNP needs genome FASTAs, not raw reads — use the FASTA fetch above for ready-to-run genomes.</div>
                      </div>
                    </div>
                  </div>
                )}
              </section>

              <section className="panel">
                <div className="panel-header">
                  <h2>Genomes selected for run</h2>
                </div>
                {!activeProject || totalCount === 0 ? (
                  <div className="empty-msg">Add FASTA genomes to a project, then they appear here. Uncheck any to exclude from the run.</div>
                ) : (
                  <div className="selection-box">
                    <div className="sel-title">{inclCount} of {totalCount} genome(s) — project {activeProject}</div>
                    {includedGenomes(activeProject).map((g) => (
                      <div key={g.path} className="sel-row" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span className="sel-name" style={{ flex: 1 }}>{g.sample}</span>
                        <button className="ghost" style={{ fontSize: 11 }} onClick={() => toggleExclude(activeProject, g)} title="Exclude">✕</button>
                      </div>
                    ))}
                    {inclCount < 2 && <div className="note" style={{ marginTop: 6 }}>kSNP needs at least 2 genomes.</div>}
                  </div>
                )}
              </section>
            </div>
          </div>
        )}

        {/* ════ Run kSNP4 ════ */}
        <div className="row-header">
          <h2>Run kSNP4</h2>
          <button className="ghost" onClick={() => setShowRun(!showRun)}>{showRun ? "Hide" : "Show"}</button>
        </div>
        {showRun && (
          <div className="row-grid row-grid-split">
            {/* LEFT — configure & run */}
            <section className="panel">
              <h2>Configure &amp; Run</h2>

              <div className="form-section">
                <label className="form-label">Run label (output folder name)</label>
                <input
                  placeholder="(auto: ksnp_YYYYmmdd_HHMMSS)"
                  value={label}
                  onChange={(e) => setLabel(e.target.value.replace(/\s+/g, "_"))}
                  disabled={running}
                />
                <div className="form-hint">Saved under <code>{activeProject || "<project>"}/ksnp/&lt;label&gt;/</code>.</div>
              </div>

              <div className="form-section">
                <label className="form-label">min_frac — SNP must be present in this fraction of genomes</label>
                <input type="number" min="0" max="1" step="0.05" value={minFrac}
                       onChange={(e) => setMinFrac(e.target.value)} disabled={running} />
                <div className="note" style={{ marginTop: 4 }}>
                  0.8 = the validated default. SNPs present in <em>all</em> genomes are reported as core SNPs regardless.
                </div>
              </div>

              <div className="form-section">
                <label className="checkbox-label" style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                  <input type="checkbox" checked={runCore} onChange={(e) => setRunCore(e.target.checked)} disabled={running} />
                  <span>Core-SNP analysis (<code>-core</code>)</span>
                </label>
                <label className="checkbox-label" style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", marginTop: 10 }}>
                  <input type="checkbox" checked={runMl} onChange={(e) => setRunMl(e.target.checked)} disabled={running} />
                  <span>Maximum-likelihood tree (<code>-ML</code>)</span>
                </label>
                <label className="checkbox-label" style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", marginTop: 10 }}>
                  <input type="checkbox" checked={runVcf} onChange={(e) => setRunVcf(e.target.checked)} disabled={running} />
                  <span>Per-SNP VCF (<code>-vcf</code>)</span>
                </label>
              </div>

              <div className="form-section">
                <label className="form-label">k-mer size (blank = Kchooser4 optimum)</label>
                <input type="number" min="3" placeholder="(auto via Kchooser4)" value={kOverride}
                       onChange={(e) => setKOverride(e.target.value)} disabled={running} />
                <div className="form-hint">Leave blank to let Kchooser4 pick the optimum k for this genome set.</div>
              </div>

              <div className="form-section">
                <label className="form-label">Threads</label>
                <input type="number" min="1" placeholder="(auto)" value={threads}
                       onChange={(e) => setThreads(e.target.value)} disabled={running} />
              </div>

              <button className="run-btn" onClick={runProject} disabled={running || inclCount < 2}>
                {running ? "Running…" : `▶ Run kSNP4${inclCount ? ` (${inclCount} genomes)` : ""}`}
              </button>
              {inclCount < 2 && <div className="note">Select at least 2 genomes in the active project to enable the run.</div>}
            </section>

            {/* RIGHT — current run status */}
            <section className="panel">
              <div className="panel-header">
                <h2>Current run</h2>
                {jobId && <span className="muted" style={{ fontSize: 12 }}>job {jobId.slice(0, 8)}</span>}
              </div>
              {activeRun ? (
                <div className="selection-box">
                  <div className="sel-title">
                    {jobStatus === "running" ? "Running" : jobStatus === "succeeded" ? "Done" : jobStatus}
                  </div>
                  <div><span className="sel-name">{activeRun.label}</span></div>
                  <div style={{ marginTop: 2 }}><span className="muted">Project:</span> <strong>{activeRun.project}</strong></div>
                  {activeRun.genome_count != null && (
                    <div className="muted" style={{ marginTop: 2 }}>{activeRun.genome_count} genomes</div>
                  )}
                  {currentStep && <div className="muted" style={{ marginTop: 4 }}>{currentStep}</div>}
                  <div className="note" style={{ marginTop: 8 }}>
                    Trees, matrices, the PDF report and stats workbook appear in the Results section below when finished.
                  </div>
                </div>
              ) : (
                <div className="empty-msg">No active run. Select genomes, set options, and Run kSNP4.</div>
              )}
            </section>
          </div>
        )}

        {/* ════ Results ════ */}
        <div className="row-header">
          <h2>Results</h2>
          <button className="ghost" onClick={() => setShowResults(!showResults)}>{showResults ? "Hide" : "Show"}</button>
        </div>
        {showResults && (
          <div className="row-grid row-grid-single">
            <section className="panel">
              {!selectedRun ? (
                <div className="empty-msg">Click a kSNP run under a project to load its results here.</div>
              ) : sum?.loading ? (
                <div className="loading-text">Loading results…</div>
              ) : !sum?.present ? (
                <div className="empty-msg">
                  No completed results for {selectedRun.label} yet.
                  {sum?.status === "running" ? " Run in progress — check the log below." : ""}
                </div>
              ) : (
                <>
                  <div className="panel-header">
                    <h2>{selectedRun.label}</h2>
                    <div className="panel-actions" style={{ display: "flex", gap: 10, alignItems: "center" }}>
                      <span className="muted" style={{ fontSize: 12 }}>{man.genome_count} genomes · k={man.options?.k}</span>
                      <span className="muted" style={{ fontSize: 12 }}>
                        {fmtInt(man.results?.core_snps)} core · {fmtInt(man.results?.snps_all)} total SNPs
                      </span>
                    </div>
                  </div>

                  {/* The three SNP counts — explained */}
                  <div className="row-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10, marginBottom: 10 }}>
                    <div className="panel" style={{ padding: 12 }}>
                      <div className="status-label">All SNPs</div>
                      <div style={{ fontSize: 22, fontWeight: 700 }}>{fmtInt(rres.snps_all)}</div>
                      <div className="muted" style={{ fontSize: 11.5, marginTop: 4 }}>Every SNP found in any genome (the pan-genome). Most data, finest detail — but some positions are missing in some genomes.</div>
                    </div>
                    <div className="panel" style={{ padding: 12, borderColor: "#4c8c8a" }}>
                      <div className="status-label">Core SNPs {rres.core_pct != null && <span className="muted">· {rres.core_pct}% of all</span>}</div>
                      <div style={{ fontSize: 22, fontWeight: 700, color: "#4c8c8a" }}>{fmtInt(rres.core_snps)}</div>
                      <div className="muted" style={{ fontSize: 11.5, marginTop: 4 }}>Present in <strong>every</strong> genome — no missing data. The most trustworthy set; the core tree is usually the one to believe.</div>
                    </div>
                    <div className="panel" style={{ padding: 12 }}>
                      <div className="status-label">Majority SNPs (≥{mfrac}) {rres.majority_pct != null && <span className="muted">· {rres.majority_pct}% of all</span>}</div>
                      <div style={{ fontSize: 22, fontWeight: 700 }}>{fmtInt(rres.majority_snps)}</div>
                      <div className="muted" style={{ fontSize: 11.5, marginTop: 4 }}>Present in at least {Math.round(Number(mfrac) * 100)}% of genomes. A middle ground — more SNPs than core, less missing data than all.</div>
                    </div>
                  </div>

                  {/* Sample-set verdict */}
                  {interp.headline && (
                    <div style={{ borderLeft: `4px solid ${lvlColor}`, background: "var(--panel-2, #f6f5f2)", padding: "8px 12px", borderRadius: 6, marginBottom: 10 }}>
                      <div style={{ fontWeight: 700, color: lvlColor }}>Is this a good sample set? {interp.headline}</div>
                      {(interp.points || []).map((p, i) => (
                        <div key={i} className="muted" style={{ fontSize: 12, marginTop: 4 }}>• {p}</div>
                      ))}
                    </div>
                  )}

                  {/* Secondary metrics */}
                  <div className="status-strip" style={{ marginBottom: 12 }}>
                    <div className="status-item"><span className="status-label">k used</span><span className="status-value">{man.options?.k ?? "—"}</span></div>
                    <div className="status-item"><span className="status-label">FCK (≥0.1 good)</span><span className="status-value">{man.kchooser?.fck ?? "—"}</span></div>
                    <div className="status-item"><span className="status-label">min_frac</span><span className="status-value">{man.options?.min_frac ?? "—"}</span></div>
                    <div className="status-item"><span className="status-label">Trees</span><span className="status-value">{rres.trees?.length ?? 0}</span></div>
                    <div className="status-item"><span className="status-label">Non-core SNPs</span><span className="status-value">{fmtInt(rres.non_core_snps)}</span></div>
                  </div>

                  {/* Guide to the output files */}
                  {fileGuide.length > 0 && (
                    <details style={{ marginBottom: 12 }}>
                      <summary style={{ cursor: "pointer", fontWeight: 600 }}>Guide to the output files — what's what (kSNP writes a lot of files)</summary>
                      <div style={{ overflowX: "auto", marginTop: 8 }}>
                        <table className="result-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                          <thead>
                            <tr style={{ textAlign: "left", borderBottom: "2px solid var(--border, #ddd)" }}>
                              <th style={{ padding: "6px 8px" }}>File group</th>
                              <th style={{ padding: "6px 8px", textAlign: "right" }}>#</th>
                              <th style={{ padding: "6px 8px" }}>What it is · when to use it</th>
                            </tr>
                          </thead>
                          <tbody>
                            {fileGuide.filter((g) => g.key === "report" || (fileGroups[g.key] || 0) > 0).map((g) => (
                              <tr key={g.key} style={{ borderBottom: "1px solid var(--border, #eee)" }}>
                                <td style={{ padding: "5px 8px", fontWeight: 600 }}>{g.label}</td>
                                <td style={{ padding: "5px 8px", textAlign: "right" }}>{g.key === "report" ? 2 : (fileGroups[g.key] || 0)}</td>
                                <td style={{ padding: "5px 8px" }}><span>{g.what}</span> <span className="muted">{g.use}</span></td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  )}

                  {/* Download links */}
                  {resFiles?.files?.length > 0 && (
                    <div className="results-list" style={{ marginBottom: 12 }}>
                      {resFiles.files.map((f) => {
                        const base = `./api/projects/${encodeURIComponent(selectedRun.project)}/file?path=${encodeURIComponent(f.path)}`;
                        return (
                          <div key={f.name} className="results-item">
                            <span className="result-icon">{fileIcon(f.name)}</span>
                            <a className="result-name result-link" href={`${base}&inline=${f.openable ? 1 : 0}`}
                               target={f.openable ? "_blank" : undefined} rel="noopener noreferrer" title={f.name}>
                              {f.label || f.name}
                            </a>
                            <span className="result-size">{fmtSize(f.size)}</span>
                            <a className="result-download" href={`${base}&inline=0`} title={`Download ${f.name}`}>⬇</a>
                          </div>
                        );
                      })}
                    </div>
                  )}

                  {/* Input genome QC table */}
                  {(qc.genomes || []).length > 0 && (
                    <details open style={{ marginBottom: 12 }}>
                      <summary style={{ cursor: "pointer", fontWeight: 600 }}>Input genome quality ({qc.genomes.length})</summary>
                      <div style={{ overflowX: "auto", marginTop: 8 }}>
                        <table className="result-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                          <thead>
                            <tr style={{ textAlign: "left", borderBottom: "2px solid var(--border, #ddd)" }}>
                              <th style={{ padding: "6px 8px" }}>Genome</th>
                              <th style={{ padding: "6px 8px", textAlign: "right" }}>Contigs</th>
                              <th style={{ padding: "6px 8px", textAlign: "right" }}>Length</th>
                              <th style={{ padding: "6px 8px", textAlign: "right" }}>N50</th>
                              <th style={{ padding: "6px 8px", textAlign: "right" }}>GC%</th>
                              <th style={{ padding: "6px 8px" }}>QC</th>
                            </tr>
                          </thead>
                          <tbody>
                            {qc.genomes.map((g, i) => (
                              <tr key={i} style={{ borderBottom: "1px solid var(--border, #eee)" }}>
                                <td style={{ padding: "5px 8px", fontWeight: 600 }}>{g.name}</td>
                                <td style={{ padding: "5px 8px", textAlign: "right" }}>{fmtInt(g.contigs)}</td>
                                <td style={{ padding: "5px 8px", textAlign: "right" }}>{fmtInt(g.length)}</td>
                                <td style={{ padding: "5px 8px", textAlign: "right" }}>{fmtInt(g.n50)}</td>
                                <td style={{ padding: "5px 8px", textAlign: "right" }}>{g.gc_pct != null ? Number(g.gc_pct).toFixed(2) : "—"}</td>
                                <td style={{ padding: "5px 8px" }}>
                                  <span className={`run-status run-status-${g.verdict === "review" ? "running" : "done"}`} style={{ fontSize: 11 }}>
                                    {(g.verdict || "—").toUpperCase()}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      {(qc.notes || []).map((n, i) => (<div key={i} className="note" style={{ marginTop: 6 }}>{n}</div>))}
                    </details>
                  )}

                  {/* Provenance / options used */}
                  {man.options && (
                    <details style={{ marginTop: 4 }}>
                      <summary style={{ cursor: "pointer", fontWeight: 600 }}>Options used &amp; provenance</summary>
                      <div className="note" style={{ marginTop: 8 }}>
                        <div><strong>Command:</strong> <code style={{ wordBreak: "break-all" }}>{(man.command || []).join(" ")}</code></div>
                        <div style={{ marginTop: 6 }}>
                          <strong>Options:</strong> k={man.options.k} ({man.options.k_source}) · min_frac={man.options.min_frac} · core={String(man.options.core)} · ML={String(man.options.ML)} · vcf={String(man.options.vcf)} · threads={man.options.threads}
                        </div>
                        {man.versions && (
                          <div style={{ marginTop: 6 }}>
                            <strong>Versions:</strong> kSNP4 {man.versions.kSNP4 || "?"} · Kchooser4 {man.versions.Kchooser4 || "?"} · seqkit {man.versions.seqkit || "?"}
                          </div>
                        )}
                        {Array.isArray(man.iso_references) && (
                          <div style={{ marginTop: 6 }}>
                            <strong>Quality standards:</strong> {man.iso_references.map((r) => r.standard).join(", ")}
                          </div>
                        )}
                        {man.thresholds_note && <div style={{ marginTop: 6 }}>{man.thresholds_note}</div>}
                      </div>
                    </details>
                  )}
                </>
              )}
            </section>
          </div>
        )}

        {/* ════ Pipeline Log ════ */}
        <div className="row-header">
          <h2>Pipeline Log</h2>
          <button className="ghost" onClick={() => setShowLogs(!showLogs)}>{showLogs ? "Hide" : "Show"}</button>
        </div>
        {showLogs && (
          <div className="row-grid row-grid-single">
            <section className="panel">
              <div className="log-meta">
                <span className="dot" data-state={jobStatus} />
                <span style={{ fontWeight: 600 }}>
                  {jobStatus === "idle" && "Idle"}
                  {jobStatus === "running" && "Running"}
                  {jobStatus === "succeeded" && "Done"}
                  {jobStatus === "failed" && "Failed"}
                </span>
                {jobStatus === "running" && currentStep && (<span className="log-step" title={currentStep}>— {currentStep}</span>)}
              </div>
              <div className="log" ref={logRef}>
                {logLines.length === 0 ? (
                  <span className="log-placeholder">
                    {jobStatus === "idle" ? "Select genomes and click Run kSNP4 to start." : "Waiting for output…"}
                  </span>
                ) : (
                  logLines.map((line, i) => (<div key={i} className={logLineClass(line)}>{line}</div>))
                )}
              </div>
            </section>
          </div>
        )}
      </main>

      {folderBrowser.open && (
        <div
          onClick={() => setFolderBrowser((s) => ({ ...s, open: false }))}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ background: "var(--panel, #fff)", color: "inherit", borderRadius: 10, width: "min(640px, 92vw)", maxHeight: "80vh", display: "flex", flexDirection: "column", boxShadow: "0 10px 40px rgba(0,0,0,0.3)" }}
          >
            <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border, #ddd)", fontWeight: 700 }}>Select a projects root</div>
            <div style={{ padding: "10px 16px", display: "flex", gap: 6, alignItems: "center" }}>
              <button type="button" className="ghost" disabled={!folderBrowser.parent || folderBrowser.loading} onClick={() => browseDirs(folderBrowser.parent)}>↑ Up</button>
              <input
                style={{ flex: 1 }}
                value={folderBrowser.path}
                onChange={(e) => setFolderBrowser((s) => ({ ...s, path: e.target.value }))}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); browseDirs(folderBrowser.path); } }}
              />
              <button type="button" className="ghost" onClick={() => browseDirs(folderBrowser.path)}>Go</button>
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: "0 16px", minHeight: 160 }}>
              {folderBrowser.loading ? (
                <div className="note" style={{ padding: 12 }}>Loading…</div>
              ) : folderBrowser.error ? (
                <div className="note" style={{ padding: 12, color: "var(--danger, #c00)" }}>{folderBrowser.error}</div>
              ) : folderBrowser.entries.length === 0 ? (
                <div className="note" style={{ padding: 12 }}>No sub-folders here.</div>
              ) : (
                folderBrowser.entries.map((e) => (
                  <div
                    key={e.path}
                    onClick={() => browseDirs(e.path)}
                    style={{ padding: "7px 8px", cursor: "pointer", borderRadius: 6, display: "flex", gap: 8, alignItems: "center" }}
                    onMouseEnter={(ev) => (ev.currentTarget.style.background = "var(--panel-2, #f0f0f0)")}
                    onMouseLeave={(ev) => (ev.currentTarget.style.background = "transparent")}
                  >
                    <span>📁</span><span>{e.name}</span>
                  </div>
                ))
              )}
            </div>
            <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border, #ddd)", display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" className="ghost" onClick={() => setFolderBrowser((s) => ({ ...s, open: false }))}>Cancel</button>
              <button type="button" onClick={chooseFolder} disabled={folderBrowser.loading || !folderBrowser.path}>Select this folder</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
