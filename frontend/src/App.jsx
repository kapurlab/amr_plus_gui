import { useState, useEffect, useRef } from "react";
import "./App.css";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const APP_VERSION = "0.1.0";

// AMRFinderPlus --organism tokens are loaded at runtime from
// /api/organism-options (cached `amrfinder -l`). This fallback is only used if
// that fetch fails — it mirrors config/amrfinder_organisms.txt.
const ORGANISM_FALLBACK = [
  "Acinetobacter_baumannii", "Campylobacter", "Clostridioides_difficile",
  "Enterococcus_faecalis", "Enterococcus_faecium", "Escherichia",
  "Klebsiella_oxytoca", "Klebsiella_pneumoniae", "Neisseria_gonorrhoeae",
  "Pseudomonas_aeruginosa", "Salmonella", "Staphylococcus_aureus",
  "Streptococcus_agalactiae", "Streptococcus_pneumoniae", "Streptococcus_pyogenes",
  "Vibrio_cholerae",
];

function fileIcon(name) {
  if (name.endsWith(".json")) return "📁";
  if (name.endsWith(".tsv")) return "📊";
  if (name.endsWith(".xlsx")) return "📊";
  if (name.endsWith(".pdf")) return "📄";
  if (name.endsWith(".png")) return "🖼";
  if (name.endsWith(".fasta") || name.endsWith(".fa")) return "🧬";
  if (name.endsWith(".txt")) return "📝";
  if (name.endsWith(".html")) return "🌐";
  return "📁";
}

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// Map an organism-detection confidence to a colored chip class (reuses the
// run-status palette so no new CSS is introduced).
function confClass(conf) {
  if (conf === "high") return "run-status run-status-done";
  if (conf === "medium") return "run-status run-status-running";
  return "run-status run-status-none";
}

// Color a row by its AMRFinderPlus Method: ALLELE/EXACT/POINT = high
// confidence; PARTIAL*/INTERNAL_STOP = review.
function methodClass(method) {
  const m = (method || "").toUpperCase();
  if (m.includes("PARTIAL") || m.includes("INTERNAL_STOP")) return "run-status run-status-running";
  if (m.startsWith("ALLELE") || m.startsWith("EXACT") || m.startsWith("POINT")) return "run-status run-status-done";
  return "run-status run-status-none";
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
  const [addStatus, setAddStatus] = useState({});
  const [inputsByProj, setInputsByProj] = useState({});
  const uploadProjRef = useRef("");
  const uploadInputRef = useRef(null);
  const [expanded, setExpanded] = useState({});
  const [samples, setSamples] = useState({});
  const [checkedKeys, setCheckedKeys] = useState({});
  const [openResults, setOpenResults] = useState({});
  const [sampleResults, setSampleResults] = useState({});  // key -> {loading,status,present,files}
  const [amrTables, setAmrTables] = useState({});          // key -> parsed amr-table
  const [activeRun, setActiveRun] = useState(null);
  const [queueInfo, setQueueInfo] = useState({ total: 0, done: 0 });

  // AMR run config
  const [organismOptions, setOrganismOptions] = useState(ORGANISM_FALLBACK);
  const [organismMeta, setOrganismMeta] = useState({ db_version: null, source: null });
  const [forceOrganism, setForceOrganism] = useState("");   // "" => auto-detect
  const [usePlus, setUsePlus] = useState(true);
  const [runKraken, setRunKraken] = useState(true);
  const [runMlst, setRunMlst] = useState(true);
  const [threads, setThreads] = useState("");
  const [krakenDb, setKrakenDb] = useState("");
  const [amrfinderDb, setAmrfinderDb] = useState("");

  const [running, setRunning] = useState(false);
  const [jobId, setJobId] = useState(null);
  const [jobStatus, setJobStatus] = useState("idle");
  const [logLines, setLogLines] = useState([]);
  const [settingsDraft, setSettingsDraft] = useState({});
  const [folderBrowser, setFolderBrowser] = useState({ open: false, path: "", parent: null, entries: [], loading: false, error: "" });
  const [currentStep, setCurrentStep] = useState("");

  const [showSettings, setShowSettings] = useState(false);
  const [showProjects, setShowProjects] = useState(true);
  const [showRun, setShowRun] = useState(true);
  const [showResults, setShowResults] = useState(true);
  const [showLogs, setShowLogs] = useState(true);

  // Which sample's results the bottom Results pane shows.
  const [selectedResultKey, setSelectedResultKey] = useState(null);

  const logRef = useRef(null);
  const eventSourceRef = useRef(null);
  const watchIdRef = useRef(null);   // id of the job the poll loop is currently watching

  useEffect(() => {
    fetch("./api/config")
      .then((r) => r.json())
      .then((cfg) => {
        setKrakenDb(cfg.kraken_db || "");
        setAmrfinderDb(cfg.amrfinder_db || "");
        setSettingsDraft(cfg);
      })
      .catch(() => {});
    fetch("./api/organism-options")
      .then((r) => r.json())
      .then((d) => {
        if (Array.isArray(d.organisms) && d.organisms.length) setOrganismOptions(d.organisms);
        setOrganismMeta({ db_version: d.db_version, source: d.source });
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
          let samp = null;
          const m = (live.name || "").match(/^(.*?)\/(.*?) — /);
          if (m) {
            samp = { project: m[1], sample: m[2] };
            setActiveRun(samp);
          }
          watchJob(live.id, samp, () => {});
        }
      })
      .catch(() => {});
  }, []);

  function loadProjects() {
    setProjectsLoading(true);
    fetch("./api/projects")
      .then((r) => r.json())
      .then((data) => {
        setProjects(data);
        setProjectsLoading(false);
      })
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
        await Promise.all([fetchSamples(n), loadInputs(n)]);
      }
    } finally {
      setCreatingProject(false);
    }
  }

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

  function fetchSamples(name) {
    return fetch(`./api/projects/${encodeURIComponent(name)}/samples`)
      .then((r) => r.json())
      .then((data) => setSamples((s) => ({ ...s, [name]: data })))
      .catch(() => setSamples((s) => ({ ...s, [name]: [] })));
  }

  function toggleProject(name) {
    const isExpanded = expanded[name];
    setExpanded((e) => ({ ...e, [name]: !isExpanded }));
    setActiveProject(name);
    if (!isExpanded) {
      if (!samples[name]) fetchSamples(name);
      loadInputs(name);
    }
  }

  function selectProject(name) {
    setActiveProject(name);
    if (inputsByProj[name] === undefined) loadInputs(name);
  }

  function loadInputs(name) {
    return fetch(`./api/projects/${encodeURIComponent(name)}/inputs`)
      .then((r) => r.json())
      .then((data) => setInputsByProj((m) => ({ ...m, [name]: data })))
      .catch(() => setInputsByProj((m) => ({ ...m, [name]: { files: [], count: 0, total_bytes: 0 } })));
  }

  const setStat = (name, msg) => setAddStatus((m) => ({ ...m, [name]: msg }));

  async function refreshAfterLoad(name) {
    await Promise.all([fetchSamples(name), loadInputs(name)]);
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
      (f) => f.name.endsWith(".fastq.gz") || /\.(fasta|fa|fna)$/i.test(f.name)
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
      watchJob(data.job_id, null, () => {
        setStat(name, "Download finished — see samples below.");
        refreshAfterLoad(name);
      });
    } catch (e) {
      setStat(name, `Download failed: ${e.message}`);
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

  const sampleKey = (project, s) => `${project}::${s.sample}`;
  const isActive = (project, s) =>
    activeRun && activeRun.project === project && activeRun.sample === s.sample;

  function toggleChecked(project, s) {
    const key = sampleKey(project, s);
    setCheckedKeys((m) => {
      const next = { ...m };
      if (next[key]) delete next[key];
      else next[key] = { project, ...s };
      return next;
    });
  }

  function loadSampleResults(project, s) {
    const key = sampleKey(project, s);
    setSampleResults((m) => ({ ...m, [key]: { ...(m[key] || {}), loading: true } }));
    fetch(`./api/projects/${encodeURIComponent(project)}/samples/${encodeURIComponent(s.sample)}/amr-results`)
      .then((r) => r.json())
      .then((data) => setSampleResults((m) => ({ ...m, [key]: { loading: false, ...data } })))
      .catch(() => setSampleResults((m) => ({ ...m, [key]: { loading: false, present: false, status: "none", files: [] } })));
  }

  function loadAmrTable(project, s) {
    const key = sampleKey(project, s);
    setAmrTables((m) => ({ ...m, [key]: { ...(m[key] || {}), loading: true } }));
    fetch(`./api/projects/${encodeURIComponent(project)}/samples/${encodeURIComponent(s.sample)}/amr-table`)
      .then((r) => r.json())
      .then((data) => setAmrTables((m) => ({ ...m, [key]: { loading: false, ...data } })))
      .catch(() => setAmrTables((m) => ({ ...m, [key]: { loading: false, present: false, rows: [] } })));
  }

  function toggleResults(project, s) {
    const key = sampleKey(project, s);
    const willOpen = !openResults[key];
    setOpenResults((m) => ({ ...m, [key]: willOpen }));
    if (willOpen) {
      setSelectedResultKey(key);
      setShowResults(true);
      if (!sampleResults[key]) loadSampleResults(project, s);
      if (!amrTables[key]) loadAmrTable(project, s);
    }
  }

  async function runSamples(list) {
    if (running || !list.length) return;
    setShowLogs(true);
    setQueueInfo({ total: list.length, done: 0 });
    for (let i = 0; i < list.length; i++) {
      await runOne(list[i]);
      setQueueInfo({ total: list.length, done: i + 1 });
    }
    setActiveRun(null);
  }

  function runSelected() {
    runSamples(Object.values(checkedKeys));
  }

  function runOne(samp) {
    return new Promise((resolve) => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      setRunning(true);
      setActiveRun({ project: samp.project, sample: samp.sample });
      setJobStatus("running");
      setLogLines([]);
      setCurrentStep("");
      const key = sampleKey(samp.project, samp);
      setSampleResults((m) => ({ ...m, [key]: { ...(m[key] || {}), status: "running" } }));
      setOpenResults((m) => ({ ...m, [key]: true }));
      setSelectedResultKey(key);

      fetch("./api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project: samp.project,
          r1: samp.r1,
          r2: samp.r2 || null,
          force_organism: forceOrganism.trim() || null,
          use_plus: usePlus,
          run_kraken: runKraken,
          run_mlst: runMlst,
          threads: threads ? parseInt(threads, 10) : null,
          kraken_db: krakenDb.trim() || null,
          amrfinder_db: amrfinderDb.trim() || null,
        }),
      })
        .then((r) => (r.ok ? r.json() : r.json().then((e) => { throw new Error(e.detail || "Run failed"); })))
        .then(({ job_id }) => {
          setJobId(job_id);
          watchJob(job_id, samp, resolve);
        })
        .catch((err) => {
          setLogLines((prev) => [...prev, `ERROR: ${err.message}`]);
          setRunning(false);
          setJobStatus("failed");
          resolve();
        });
    });
  }

  // Watch a job by POLLING a plain endpoint (no SSE/EventSource). The OOD /rnode
  // Apache proxy holds SSE connections open and corrupts concurrent sibling
  // requests (a status poll comes back with the SSE's buffered body, breaking
  // JSON parsing → runs were mislabelled "Failed" and results never refreshed).
  // /api/jobs/{id}/logtext is a normal GET returning BOTH the recorded status
  // (from the real exit code) and the current log text — proxy-safe.
  function watchJob(id, samp, done) {
    watchIdRef.current = id;   // newest run wins; stale loops below bail out
    let errors = 0;
    let finished = false;
    const finish = (status) => {
      if (finished || watchIdRef.current !== id) { done(); return; }
      finished = true;
      setRunning(false);
      setJobStatus(status);
      setCurrentStep("");
      if (samp) {
        loadSampleResults(samp.project, samp);
        loadAmrTable(samp.project, samp);
      }
      loadProjects();
      done();
    };
    const tick = () => {
      if (finished || watchIdRef.current !== id) return;
      fetch(`./api/jobs/${id}/logtext`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error("http " + r.status))))
        .then((data) => {
          errors = 0;
          if (typeof data.log === "string") {
            const lines = data.log.split("\n");
            setLogLines(lines);
            for (let i = lines.length - 1; i >= 0; i--) {
              const d = lines[i];
              if (/Step \d+:/i.test(d) || /Resolving organism/i.test(d) ||
                  /AMRFinderPlus/i.test(d) || /Pipeline completed/i.test(d)) {
                setCurrentStep(d.trim().replace(/^#+\s*/, "")); break;
              }
            }
          }
          if (!data.status || data.status === "running") { setTimeout(tick, 2000); return; }
          finish(data.status);                  // succeeded | failed from the real exit code
        })
        .catch(() => {
          errors += 1;
          if (errors < 30) setTimeout(tick, 2000);   // keep waiting through transient blips
          else finish("failed");
        });
    };
    setTimeout(tick, 1200);
  }

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
        kraken_db: settingsDraft.kraken_db,
        amrfinder_db: settingsDraft.amrfinder_db,
        projects_root: settingsDraft.projects_root,
      }),
    })
      .then((r) => r.json())
      .then(() => {
        setKrakenDb(settingsDraft.kraken_db || "");
        setAmrfinderDb(settingsDraft.amrfinder_db || "");
        loadProjects();
      })
      .catch(() => {});
  }

  const logLineClass = (line) => {
    if (line.startsWith("$ ")) return "log-line cmd";
    if (line.startsWith("ERROR") || line.startsWith("error")) return "log-line error";
    if (line === "[DONE]") return "log-line done";
    return "log-line";
  };

  const statusText = { idle: "idle", running: "running", succeeded: "succeeded", failed: "failed" }[jobStatus];

  // Results pane data for the selected sample.
  const resTable = selectedResultKey ? amrTables[selectedResultKey] : null;
  const resFiles = selectedResultKey ? sampleResults[selectedResultKey] : null;
  const resOrg = resTable?.organism || {};

  return (
    <div className="app">
      <input
        ref={uploadInputRef}
        type="file"
        multiple
        accept=".fastq.gz,.fasta,.fa,.fna,application/gzip"
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
          <img className="app-logo" src="./amr_icon.svg" alt="Antimicrobial resistance shield icon" />
          <div>
            <h1>
              AMRFinderPlus <span className="version-tag">v{APP_VERSION}</span>
            </h1>
            <p>Antimicrobial resistance, virulence &amp; stress gene detection with conservative organism resolution</p>
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
            <span className="status-label">Selected</span>
            <span className="status-value">
              {Object.keys(checkedKeys).length
                ? `${Object.keys(checkedKeys).length} sample${Object.keys(checkedKeys).length > 1 ? "s" : ""}`
                : "—"}
            </span>
          </div>
          <div className="status-item">
            <span className="status-label">Detected organism</span>
            <span className="status-value">{resOrg.organism_token || (resTable?.present ? "none / no -O" : "—")}</span>
          </div>
          <div className="status-item">
            <span className="status-label">--plus</span>
            <span className="status-value cap">{usePlus ? "on" : "off"}</span>
          </div>
          <div className="status-item">
            <span className="status-label">Job</span>
            <span className="status-value cap">
              {jobStatus === "running" ? <><span className="pulse-dot" />running</> : statusText}
            </span>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════ */}
        {/* SECTION: Settings                                        */}
        {/* ════════════════════════════════════════════════════════ */}
        <div className="row-header">
          <h2>Settings</h2>
          <button className="ghost" onClick={() => {
            if (!showSettings) {
              fetch("./api/config").then((r) => r.json()).then(setSettingsDraft).catch(() => {});
            }
            setShowSettings(!showSettings);
          }}>
            {showSettings ? "Hide" : "Show"}
          </button>
        </div>
        {showSettings && (
          <div className="row-grid row-grid-single">
            <section className="panel">
              <div className="form-section">
                <label className="form-label">AMRFinderPlus database path</label>
                <input
                  placeholder="(leave blank to use the env's default DB)"
                  value={settingsDraft.amrfinder_db || ""}
                  onChange={(e) => setSettingsDraft((d) => ({ ...d, amrfinder_db: e.target.value }))}
                />
                <div className="form-hint">
                  Optional. Blank lets amrfinder find $CONDA_PREFIX/share/amrfinderplus/data/latest.
                  {organismMeta.db_version && <> DB version detected: <code>{organismMeta.db_version}</code>.</>}
                </div>
              </div>
              <div className="form-section">
                <label className="form-label">Kraken2 database path (organism detection)</label>
                <input
                  placeholder="/srv/kapurlab/databases/kraken2/k2_standard_pluspf"
                  value={settingsDraft.kraken_db || ""}
                  onChange={(e) => setSettingsDraft((d) => ({ ...d, kraken_db: e.target.value }))}
                />
                <div className="form-hint">Directory containing hash.k2d, opts.k2d, taxo.k2d</div>
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
                    {settingsDraft.recent_projects_roots.map((r) => (
                      <option key={r} value={r}>{r}</option>
                    ))}
                  </select>
                )}
                <div className="form-hint">New projects are created under this root. Shared projects at /srv/kapurlab/projects/ are always visible. Click Save to apply.</div>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button onClick={saveSettings}>Save</button>
              </div>
            </section>
          </div>
        )}

        {/* ════════════════════════════════════════════════════════ */}
        {/* SECTION: Projects & Samples                              */}
        {/* ════════════════════════════════════════════════════════ */}
        <div className="row-header">
          <h2>Projects &amp; Samples</h2>
          <button className="ghost" onClick={() => setShowProjects(!showProjects)}>
            {showProjects ? "Hide" : "Show"}
          </button>
        </div>
        {showProjects && (
          <div className="row-grid row-grid-split">
            {/* LEFT — project / sample browser */}
            <section className="panel">
              <div className="panel-header">
                <h2>Projects</h2>
                <div className="panel-actions">
                  <button className="ghost action" onClick={loadProjects}>↻ Refresh</button>
                </div>
              </div>
              <div className="row">
                <input
                  placeholder="New project name (e.g. AMR_surveillance)"
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value.replace(/\s+/g, "_"))}
                  onKeyDown={(e) => { if (e.key === "Enter") createProject(); }}
                  disabled={creatingProject}
                  title="Spaces become underscores. Letters, digits, _ - . are allowed. Created under your personal projects and shared with the sibling GUIs."
                />
                <button onClick={createProject} disabled={creatingProject || !newProjectName.trim()}>
                  {creatingProject ? "Creating…" : "Create"}
                </button>
              </div>
              <div className="form-hint" style={{ marginTop: -4, marginBottom: 8 }}>
                Created under your personal projects root — also visible in vSNP and Kraken GUIs. Add FASTQs (or an assembly FASTA) to the project’s <code>download/</code> folder.
              </div>
              <div className="list project-list">
                {projectsLoading && <div className="loading-text">Loading projects…</div>}
                {!projectsLoading && projects.length === 0 && (
                  <div className="note">No projects found. Check Settings for the projects path.</div>
                )}
                {projects.map((proj) => (
                  <div
                    key={proj.name}
                    className={`list-item ${activeRun?.project === proj.name || activeProject === proj.name ? "active" : ""}`}
                  >
                    <div className="item-top" onClick={() => toggleProject(proj.name)}>
                      <span className="expand-icon">{expanded[proj.name] ? "▾" : "▸"}</span>
                      <div className="list-title" title={proj.name}>{proj.name}</div>
                      <span className={`scope-badge scope-${proj.scope}`}>{proj.scope}</span>
                    </div>
                    {proj.path && <div className="list-path" title={proj.path}>{proj.path}</div>}
                    <div className="list-meta">
                      {proj.fastq_count} FASTQ
                      {proj.amr_runs?.length > 0 &&
                        ` · ${proj.amr_runs.length} AMR run${proj.amr_runs.length > 1 ? "s" : ""}`}
                    </div>
                    {expanded[proj.name] && (
                      <div className="sample-list">
                        {!samples[proj.name] && <div className="loading-text">Loading samples…</div>}
                        {samples[proj.name]?.length === 0 && (
                          <div className="empty-msg" style={{ paddingLeft: 4 }}>
                            No FASTQ/assembly files yet — add some from the <strong>Inputs</strong> pane on the right.
                          </div>
                        )}
                        {samples[proj.name]?.map((s) => {
                          const key = sampleKey(proj.name, s);
                          const res = sampleResults[key];
                          const hasRun = proj.amr_runs?.includes(s.sample);
                          const status = res?.status || (hasRun ? "done" : "none");
                          const checked = !!checkedKeys[key];
                          const open = !!openResults[key];
                          const statusLabel =
                            status === "running" ? "● running" : status === "done" ? "✓ results" : "not run";
                          return (
                          <div
                            key={s.r1}
                            className={`sample-item ${isActive(proj.name, s) ? "active" : ""}`}
                          >
                            <div className="sample-name-row" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleChecked(proj.name, s)}
                                title="Select for batch run"
                              />
                              <div
                                className="sample-name"
                                title={`${s.sample} — click to show results`}
                                style={{ flex: 1, cursor: "pointer" }}
                                onClick={() => toggleResults(proj.name, s)}
                              >
                                {s.sample}
                              </div>
                              <span className={`read-badge ${s.paired ? "badge-pe" : "badge-se"}`}>
                                {s.paired ? "PE" : "SE"}
                              </span>
                              <span
                                className={`run-status run-status-${status}`}
                                title={`Run status: ${status}`}
                                style={{ fontSize: 11, whiteSpace: "nowrap" }}
                              >
                                {statusLabel}
                              </span>
                              <button
                                className="ghost"
                                style={{ fontSize: 11 }}
                                onClick={() => toggleResults(proj.name, s)}
                                title="Show/hide results for this sample"
                              >
                                {open ? "▾" : "▸"}
                              </button>
                            </div>
                            <div className="sample-files">
                              {s.paired ? (
                                <>
                                  <div className="sample-file-row">
                                    <span className="file-label">R1</span>
                                    <span className="file-name" title={s.r1_name}>{s.r1_name}</span>
                                    <span className="file-size">{fmtSize(s.r1_size)}</span>
                                  </div>
                                  <div className="sample-file-row">
                                    <span className="file-label">R2</span>
                                    <span className="file-name" title={s.r2_name}>{s.r2_name}</span>
                                    <span className="file-size">{fmtSize(s.r2_size)}</span>
                                  </div>
                                </>
                              ) : (
                                <div className="sample-file-row">
                                  <span className="file-name" title={s.r1_name}>{s.r1_name}</span>
                                  <span className="file-size">{fmtSize(s.r1_size)}</span>
                                </div>
                              )}
                            </div>
                            {open && (
                              <div className="sample-results-inline" style={{ marginTop: 6, paddingLeft: 22 }}>
                                <div style={{ display: "flex", gap: 8, marginBottom: 4 }}>
                                  <button
                                    className="ghost action"
                                    disabled={running}
                                    onClick={() => runSamples([{ project: proj.name, ...s }])}
                                  >
                                    {status === "done" ? "↻ Re-run this sample" : "▶ Run AMRFinderPlus"}
                                  </button>
                                  <button className="ghost action" onClick={() => { loadSampleResults(proj.name, s); loadAmrTable(proj.name, s); }}>
                                    ↻ Refresh
                                  </button>
                                  <button className="ghost action" onClick={() => { setSelectedResultKey(key); loadSampleResults(proj.name, s); loadAmrTable(proj.name, s); setShowResults(true); }}>
                                    View results table ↓
                                  </button>
                                </div>
                                {res?.loading ? (
                                  <div className="loading-text">Loading results…</div>
                                ) : !res || !res.present || (res.files || []).length === 0 ? (
                                  <div className="empty-msg" style={{ paddingLeft: 0 }}>
                                    {status === "running"
                                      ? "Running… results will appear here when finished."
                                      : "No AMR results yet for this sample."}
                                  </div>
                                ) : (
                                  <div className="results-list">
                                    {res.files.map((f) => {
                                      const base = `./api/projects/${encodeURIComponent(proj.name)}/file?path=${encodeURIComponent(f.path)}`;
                                      return (
                                        <div key={f.name} className="results-item">
                                          <span className="result-icon">{fileIcon(f.name)}</span>
                                          {f.openable ? (
                                            <a className="result-name result-link" href={`${base}&inline=1`}
                                               target="_blank" rel="noopener noreferrer" title={`Open ${f.name}`}>
                                              {f.label || f.name}
                                            </a>
                                          ) : (
                                            <a className="result-name result-link" href={`${base}&inline=0`}
                                               title={`Download ${f.name}`}>
                                              {f.label || f.name}
                                            </a>
                                          )}
                                          <span className="result-size">{fmtSize(f.size)}</span>
                                          <a className="result-download" href={`${base}&inline=0`} title={`Download ${f.name}`}>⬇</a>
                                        </div>
                                      );
                                    })}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>

            {/* RIGHT — Inputs + batch selection */}
            <div style={{ display: "flex", flexDirection: "column", gap: 20, minWidth: 0 }}>
              <section className="panel">
                <div className="panel-header">
                  <h2>Inputs</h2>
                  {projects.length > 0 && (
                    <select
                      value={activeProject}
                      onChange={(e) => selectProject(e.target.value)}
                      title="Project to add FASTQ / assembly files to"
                      style={{ width: "auto", maxWidth: "60%", padding: "6px 10px" }}
                    >
                      {projects.map((p) => (
                        <option key={p.name} value={p.name}>{p.name}</option>
                      ))}
                    </select>
                  )}
                </div>
                {!activeProject ? (
                  <div className="empty-msg">
                    Create a project first (top of the Projects panel), then import, upload, or download FASTQ / assembly files into it.
                  </div>
                ) : (
                  <div className="input-columns">
                    <div className="input-column">
                      <h3>Bring Your Own Reads / Assembly</h3>
                      <div className="row" style={{ margin: 0 }}>
                        <input
                          placeholder="/srv/kapurlab/… folder, .fastq.gz, or .fasta"
                          value={addPath[activeProject] || ""}
                          onChange={(e) => setAddPath((m) => ({ ...m, [activeProject]: e.target.value }))}
                          onKeyDown={(e) => { if (e.key === "Enter") linkLocal(activeProject); }}
                        />
                        <button className="ghost action" onClick={() => linkLocal(activeProject)} disabled={!(addPath[activeProject] || "").trim()}>Link</button>
                      </div>
                      <div className="form-hint">Symlinks every .fastq.gz / .fasta found — no copying.</div>

                      <div className="block">
                        <h3>Upload / Drag &amp; Drop</h3>
                        <div
                          className="dropzone"
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={(e) => { e.preventDefault(); uploadFiles(activeProject, e.dataTransfer.files); }}
                        >
                          <button type="button" onClick={() => pickFiles(activeProject)}>Choose Files</button>
                          <span className="drop-hint">Or drop FASTQ.GZ / FASTA files here</span>
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
                                <span className="file-name" title={f.name} style={{ flex: 1 }}>{f.name}</span>
                                <span className="file-size">{fmtSize(f.size)}</span>
                                <button className="ghost" style={{ fontSize: 11, padding: "2px 7px" }} title="Remove from download/" onClick={() => deleteInput(activeProject, f.name)}>✕</button>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="input-column">
                      <h3>SRA Download</h3>
                      <textarea
                        rows={6}
                        placeholder={"SRR/ERR/DRR or SRX/SRS/PRJNA accessions\n(one per line)"}
                        value={sraText[activeProject] || ""}
                        onChange={(e) => setSraText((m) => ({ ...m, [activeProject]: e.target.value }))}
                        style={{ resize: "vertical", fontFamily: "inherit" }}
                      />
                      <button
                        style={{ width: "100%" }}
                        onClick={() => sraDownload(activeProject)}
                        disabled={!parseAccessions(sraText[activeProject]).length || running}
                      >
                        Download{parseAccessions(sraText[activeProject]).length ? ` (${parseAccessions(sraText[activeProject]).length})` : ""}
                      </button>
                      <div className="form-hint">Runs in the background; progress appears in the Pipeline Log.</div>
                    </div>
                  </div>
                )}
              </section>

              <section className="panel">
                <div className="panel-header">
                  <h2>Selected for run</h2>
                  {Object.keys(checkedKeys).length > 0 && (
                    <button className="ghost action" onClick={() => setCheckedKeys({})}>Clear</button>
                  )}
                </div>
                {Object.keys(checkedKeys).length === 0 ? (
                  <div className="empty-msg">
                    Check one or more samples on the left, then run them as a batch from “Run AMRFinderPlus” below.
                    Click a sample’s name to view its results.
                  </div>
                ) : (
                  <div className="selection-box">
                    <div className="sel-title">{Object.keys(checkedKeys).length} sample(s) queued</div>
                    {Object.entries(checkedKeys).map(([key, samp]) => (
                      <div key={key} className="sel-row" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span className="sel-name" style={{ flex: 1 }}>{samp.sample}</span>
                        <span className="muted" style={{ fontSize: 11 }}>{samp.project}</span>
                        <button className="ghost" style={{ fontSize: 11 }}
                                onClick={() => toggleChecked(samp.project, samp)} title="Remove from batch">✕</button>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </div>
          </div>
        )}

        {/* ════════════════════════════════════════════════════════ */}
        {/* SECTION: Run AMRFinderPlus                               */}
        {/* ════════════════════════════════════════════════════════ */}
        <div className="row-header">
          <h2>Run AMRFinderPlus</h2>
          <button className="ghost" onClick={() => setShowRun(!showRun)}>
            {showRun ? "Hide" : "Show"}
          </button>
        </div>
        {showRun && (
          <div className="row-grid row-grid-split">
            {/* LEFT — configure & run */}
            <section className="panel">
              <h2>Configure &amp; Run</h2>

              {/* Detected organism display for the selected sample */}
              {resTable?.present && (
                <div className="form-section">
                  <label className="form-label">Detected organism (selected sample)</label>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <strong>{resOrg.organism_token || "none — runs without -O"}</strong>
                    {resOrg.confidence && (
                      <span className={confClass(resOrg.confidence)} style={{ fontSize: 11 }}>
                        {resOrg.confidence} confidence
                      </span>
                    )}
                    {resOrg.organism_source && (
                      <span className="muted" style={{ fontSize: 11 }}>source: {resOrg.organism_source}</span>
                    )}
                  </div>
                  {resOrg.dominant_species && (
                    <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                      Kraken: {resOrg.dominant_species} {resOrg.dominant_pct}%
                      {resOrg.runner_up_species ? ` · ${resOrg.runner_up_species} ${resOrg.runner_up_pct}%` : ""}
                      {resOrg.mlst_scheme ? ` · MLST ${resOrg.mlst_scheme}${resOrg.mlst_st ? ` ST${resOrg.mlst_st}` : ""}` : ""}
                    </div>
                  )}
                  {resOrg.contamination_flag && (
                    <div className="alert-banner" style={{ marginTop: 8 }}>
                      <strong>⚠ Contamination / mixture flagged.</strong> AMRFinderPlus ran without
                      <code> --organism</code> to avoid wrong point-mutation calls. Force an organism
                      below only if you are confident of the species.
                    </div>
                  )}
                </div>
              )}

              <div className="form-section">
                <label className="form-label">Force organism (overrides auto-detection)</label>
                <select
                  value={forceOrganism}
                  onChange={(e) => setForceOrganism(e.target.value)}
                  disabled={running}
                >
                  <option value="">Auto-detect (recommended)</option>
                  {organismOptions.map((o) => (
                    <option key={o} value={o}>{o}</option>
                  ))}
                </select>
                <div className="note" style={{ marginTop: 4 }}>
                  Passing the wrong <code>--organism</code> yields wrong point-mutation calls. Leave on
                  Auto-detect unless you have an independent species ID.
                  {organismMeta.source && (
                    <> Organism list source: <code>{organismMeta.source}</code>
                    {organismMeta.db_version ? <> (DB {organismMeta.db_version})</> : null}.</>
                  )}
                </div>
              </div>

              <div className="form-section">
                <label className="checkbox-label" style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                  <input type="checkbox" checked={usePlus} onChange={(e) => setUsePlus(e.target.checked)} disabled={running} />
                  <span><code>--plus</code> (virulence + stress / biocide / metal / acid resistance)</span>
                </label>
                <div className="note" style={{ marginTop: 4 }}>
                  Full One-Health characterization. For <em>Escherichia</em>, <code>-n --plus</code> also runs StxTyper.
                </div>
                <label className="checkbox-label" style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", marginTop: 10 }}>
                  <input type="checkbox" checked={runKraken} onChange={(e) => setRunKraken(e.target.checked)} disabled={running} />
                  <span>Run Kraken2 organism detection (from reads)</span>
                </label>
                <label className="checkbox-label" style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", marginTop: 10 }}>
                  <input type="checkbox" checked={runMlst} onChange={(e) => setRunMlst(e.target.checked)} disabled={running} />
                  <span>Run MLST corroboration (if available)</span>
                </label>
              </div>

              <div className="form-section">
                <label className="form-label">Threads</label>
                <input
                  type="number" min="1"
                  placeholder="(auto: half the host cores)"
                  value={threads}
                  onChange={(e) => setThreads(e.target.value)}
                  disabled={running}
                />
              </div>

              <div className="form-section">
                <label className="form-label">Kraken2 DB path</label>
                <input
                  placeholder="/srv/kapurlab/databases/kraken2/k2_standard_pluspf"
                  value={krakenDb}
                  onChange={(e) => setKrakenDb(e.target.value)}
                  disabled={running || !runKraken}
                />
              </div>

              <button
                className="run-btn"
                onClick={runSelected}
                disabled={running || Object.keys(checkedKeys).length === 0}
              >
                {running
                  ? `Running… ${queueInfo.total > 1 ? `(${queueInfo.done}/${queueInfo.total})` : ""}`
                  : `▶ Run selected${Object.keys(checkedKeys).length ? ` (${Object.keys(checkedKeys).length})` : ""}`}
              </button>
              {Object.keys(checkedKeys).length === 0 && (
                <div className="note">Check one or more samples on the left to enable the run. (Or use “Run AMRFinderPlus” under any sample.)</div>
              )}
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
                    {queueInfo.total > 1 ? ` — ${queueInfo.done}/${queueInfo.total} in batch` : ""}
                  </div>
                  <div><span className="sel-name">{activeRun.sample}</span></div>
                  <div style={{ marginTop: 2 }}>
                    <span className="muted">Project:</span> <strong>{activeRun.project}</strong>
                  </div>
                  {currentStep && <div className="muted" style={{ marginTop: 4 }}>{currentStep}</div>}
                  <div className="note" style={{ marginTop: 8 }}>
                    Files appear inline under each sample on the left; the parsed resistance table is in the Results section below.
                  </div>
                </div>
              ) : (
                <div className="empty-msg">
                  No active run. Select samples, set options, and Run. Results for any sample are shown below.
                </div>
              )}
            </section>
          </div>
        )}

        {/* ════════════════════════════════════════════════════════ */}
        {/* SECTION: Results                                         */}
        {/* ════════════════════════════════════════════════════════ */}
        <div className="row-header">
          <h2>Results</h2>
          <button className="ghost" onClick={() => setShowResults(!showResults)}>
            {showResults ? "Hide" : "Show"}
          </button>
        </div>
        {showResults && (
          <div className="row-grid row-grid-single">
            <section className="panel">
              {!resTable ? (
                <div className="empty-msg">
                  Click a sample’s name in the Projects tree to load its AMRFinderPlus results here.
                </div>
              ) : resTable.loading ? (
                <div className="loading-text">Loading results…</div>
              ) : !resTable.present ? (
                <div className="empty-msg">No AMRFinderPlus results for {selectedResultKey?.split("::")[1]} yet — run it first.</div>
              ) : (
                <>
                  <div className="panel-header">
                    <h2>{selectedResultKey?.split("::")[1]}</h2>
                    <div className="panel-actions" style={{ display: "flex", gap: 10, alignItems: "center" }}>
                      <span className="muted" style={{ fontSize: 12 }}>
                        organism: <strong>{resOrg.organism_token || "none"}</strong>
                      </span>
                      {resTable.summary?.total != null && (
                        <span className="muted" style={{ fontSize: 12 }}>
                          {resTable.summary.total} call{resTable.summary.total === 1 ? "" : "s"}
                          {resTable.summary.point_mutations ? ` · ${resTable.summary.point_mutations} point` : ""}
                          {resTable.summary.plus_count ? ` · ${resTable.summary.plus_count} plus` : ""}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Download links */}
                  {resFiles?.files?.length > 0 && (
                    <div className="results-list" style={{ marginBottom: 12 }}>
                      {resFiles.files
                        .filter((f) => ["amrfinder_tsv", "mutation_all", "run_manifest", "organism_detection", "qc", "mlst", "assembly_fasta"].includes(f.category))
                        .map((f) => {
                          const base = `./api/projects/${encodeURIComponent(selectedResultKey.split("::")[0])}/file?path=${encodeURIComponent(f.path)}`;
                          return (
                            <div key={f.name} className="results-item">
                              <span className="result-icon">{fileIcon(f.name)}</span>
                              <a className="result-name result-link" href={`${base}&inline=${f.openable ? 1 : 0}`}
                                 target={f.openable ? "_blank" : undefined} rel="noopener noreferrer">
                                {f.label || f.name}
                              </a>
                              <span className="result-size">{fmtSize(f.size)}</span>
                              <a className="result-download" href={`${base}&inline=0`} title={`Download ${f.name}`}>⬇</a>
                            </div>
                          );
                        })}
                    </div>
                  )}

                  {/* Resistance gene / point-mutation table */}
                  {(resTable.rows || []).length === 0 ? (
                    <div className="note">
                      No AMR / virulence / stress elements reported.
                      {resTable.summary?.total === 0 && " (amrfinder.tsv has no data rows.)"}
                    </div>
                  ) : (
                    <div style={{ overflowX: "auto" }}>
                      <table className="result-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                        <thead>
                          <tr style={{ textAlign: "left", borderBottom: "2px solid var(--border, #ddd)" }}>
                            <th style={{ padding: "6px 8px" }}>Element</th>
                            <th style={{ padding: "6px 8px" }}>Name</th>
                            <th style={{ padding: "6px 8px" }}>Type</th>
                            <th style={{ padding: "6px 8px" }}>Subtype</th>
                            <th style={{ padding: "6px 8px" }}>Class</th>
                            <th style={{ padding: "6px 8px" }}>Subclass</th>
                            <th style={{ padding: "6px 8px" }}>Method</th>
                            <th style={{ padding: "6px 8px", textAlign: "right" }}>% Cov</th>
                            <th style={{ padding: "6px 8px", textAlign: "right" }}>% Id</th>
                          </tr>
                        </thead>
                        <tbody>
                          {resTable.rows.map((r, i) => (
                            <tr key={i} style={{ borderBottom: "1px solid var(--border, #eee)" }}>
                              <td style={{ padding: "5px 8px", fontWeight: 600 }}>{r.element_symbol}</td>
                              <td style={{ padding: "5px 8px", maxWidth: 280 }} title={r.element_name}>{r.element_name}</td>
                              <td style={{ padding: "5px 8px" }}>{r.type}</td>
                              <td style={{ padding: "5px 8px" }}>{r.subtype}</td>
                              <td style={{ padding: "5px 8px" }}>{r.class}</td>
                              <td style={{ padding: "5px 8px" }}>{r.subclass}</td>
                              <td style={{ padding: "5px 8px" }}>
                                <span className={methodClass(r.method)} style={{ fontSize: 11 }}>{r.method}</span>
                              </td>
                              <td style={{ padding: "5px 8px", textAlign: "right" }}>{r.pct_coverage}</td>
                              <td style={{ padding: "5px 8px", textAlign: "right" }}>{r.pct_identity}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {/* Provenance / Options used disclosure */}
                  {resTable.provenance && Object.keys(resTable.provenance).length > 0 && (
                    <details style={{ marginTop: 14 }}>
                      <summary style={{ cursor: "pointer", fontWeight: 600 }}>Options used &amp; provenance</summary>
                      <div className="note" style={{ marginTop: 8 }}>
                        <div><strong>Command:</strong> <code style={{ wordBreak: "break-all" }}>{(resTable.provenance.command || []).join(" ")}</code></div>
                        {resTable.provenance.versions && (
                          <div style={{ marginTop: 6 }}>
                            <strong>Versions:</strong> amrfinder {resTable.provenance.versions.amrfinder || "?"} · DB {resTable.provenance.versions.amrfinder_db || "?"}
                          </div>
                        )}
                        {resTable.provenance.options && (
                          <div style={{ marginTop: 6 }}>
                            <strong>Thresholds:</strong> ident_min {resTable.provenance.options.ident_min} · coverage_min {resTable.provenance.options.coverage_min} · plus {String(resTable.provenance.options.plus)} · threads {resTable.provenance.options.threads}
                          </div>
                        )}
                        {resTable.provenance.qc && (
                          <div style={{ marginTop: 6 }}>
                            <strong>Assembly QC:</strong> {resTable.provenance.qc.verdict}
                            {resTable.provenance.qc.metrics?.total_length ? ` · ${Number(resTable.provenance.qc.metrics.total_length).toLocaleString()} bp` : ""}
                            {resTable.provenance.qc.metrics?.n50 ? ` · N50 ${Number(resTable.provenance.qc.metrics.n50).toLocaleString()}` : ""}
                          </div>
                        )}
                        {Array.isArray(resTable.provenance.iso_references) && (
                          <div style={{ marginTop: 6 }}>
                            <strong>Quality standards:</strong> {resTable.provenance.iso_references.map((r) => r.standard).join(", ")}
                          </div>
                        )}
                      </div>
                    </details>
                  )}
                </>
              )}
            </section>
          </div>
        )}

        {/* ════════════════════════════════════════════════════════ */}
        {/* SECTION: Pipeline Log                                    */}
        {/* ════════════════════════════════════════════════════════ */}
        <div className="row-header">
          <h2>Pipeline Log</h2>
          <button className="ghost" onClick={() => setShowLogs(!showLogs)}>
            {showLogs ? "Hide" : "Show"}
          </button>
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
                {jobStatus === "running" && currentStep && (
                  <span className="log-step" title={currentStep}>— {currentStep}</span>
                )}
              </div>
              <div className="log" ref={logRef}>
                {logLines.length === 0 ? (
                  <span className="log-placeholder">
                    {jobStatus === "idle"
                      ? "Select a sample and click Run to start."
                      : "Waiting for output…"}
                  </span>
                ) : (
                  logLines.map((line, i) => (
                    <div key={i} className={logLineClass(line)}>{line}</div>
                  ))
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
            <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border, #ddd)", fontWeight: 700 }}>
              Select a projects root
            </div>
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
