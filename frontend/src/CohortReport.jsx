import { useEffect, useRef, useState } from "react";

/* The cohort report for one project: a button that builds it and links to what
   it wrote. The report itself is made by bin/cohort_report.py from the
   project's per-isolate results, its metadata sheet and its newest vSNP Step 2
   tree, so this panel only starts the job and shows the outcome.

   All URLs are relative, as everywhere in this GUI, so they work behind the
   OnDemand proxy. */
export default function CohortReport({ project, sampleCount = 0 }) {
  const [state, setState] = useState(null);
  const [meta, setMeta] = useState(null);
  // What the report should show. Filled from the project's saved choices.
  const [colorBy, setColorBy] = useState("mlst");
  const [groupBy, setGroupBy] = useState([]);
  const [labelColumn, setLabelColumn] = useState("");
  const [fetching, setFetching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Sending the tree to iTOL. Nothing is sent until the form is confirmed.
  const [itolOpen, setItolOpen] = useState(false);
  const [itolProject, setItolProject] = useState("");
  const [itolName, setItolName] = useState("");
  const [itolPopup, setItolPopup] = useState(true);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(null);
  const [itolError, setItolError] = useState("");
  const timer = useRef(null);
  const base = `./api/projects/${encodeURIComponent(project || "")}/cohort-report`;
  const metaBase = `./api/projects/${encodeURIComponent(project || "")}/sample-metadata`;

  async function load() {
    if (!project) return;
    try {
      const res = await fetch(base);
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      const data = await res.json();
      setState(data);
      const saved = data.settings || {};
      setColorBy(saved.color_by ?? "mlst");
      setGroupBy(Array.isArray(saved.group_by) ? saved.group_by : []);
      setLabelColumn(saved.label_column || "");
      setItolProject((prev) => prev || data.itol?.project || data.itol?.last?.itol_project || "");
      setError("");
    } catch (e) {
      setError(e.message || String(e));
    }
    try {
      const res = await fetch(metaBase);
      if (res.ok) setMeta(await res.json());
    } catch (e) { /* the sheet's status is a convenience; the report still works */ }
  }

  useEffect(() => {
    setState(null);
    setMeta(null);
    setFetching(false);
    setBusy(false);
    setError("");
    setItolOpen(false);
    setItolProject("");
    setItolName(project || "");
    setItolPopup(true);
    setSending(false);
    setSent(null);
    setItolError("");
    load();
    return () => { if (timer.current) clearTimeout(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project]);

  function watch(jobId, done, what) {
    timer.current = setTimeout(async () => {
      try {
        const job = await (await fetch(`./api/jobs/${jobId}`)).json();
        if (job.status === "running" || job.status === "queued") return watch(jobId, done, what);
        done(false);
        if (job.status !== "succeeded") setError(`The ${what} job did not finish. See the Pipeline Log.`);
        load();
      } catch (e) {
        watch(jobId, done, what);   // a dropped poll is not a failed job
      }
    }, 2000);
  }

  // Fill the project's metadata sheet from the samples' public records.
  async function fetchMetadata() {
    setFetching(true);
    setError("");
    try {
      const res = await fetch(`${metaBase}/fetch?source=sra`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      watch(data.job_id, setFetching, "metadata");
    } catch (e) {
      setFetching(false);
      setError(e.message || String(e));
    }
  }

  async function build() {
    setBusy(true);
    setError("");
    try {
      const res = await fetch(base, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ color_by: colorBy, group_by: groupBy, label_column: labelColumn }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      watch(data.job_id, setBusy, "report");
    } catch (e) {
      setBusy(false);
      setError(e.message || String(e));
    }
  }

  // Send the tree and its annotations to the user's iTOL account.
  async function sendToItol() {
    setSending(true);
    setItolError("");
    setSent(null);
    try {
      const res = await fetch(`${base}/itol-upload`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ itol_project: itolProject.trim(), tree_name: itolName.trim() || project,
                               include_popup: itolPopup }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      setSent(data);
      setItolOpen(false);
      load();
    } catch (e) {
      setItolError(e.message || String(e));
    } finally {
      setSending(false);
    }
  }

  if (!project) return null;
  const href = (f, inline) =>
    `./api/projects/${encodeURIComponent(project)}/file?path=${encodeURIComponent(f.path)}${inline ? "&inline=1" : ""}`;
  const checks = state?.checks || {};
  const itol = state?.itol || {};
  const problems = checks.problems || [];
  const fields = state?.fields || [];
  const total = fields.length ? fields[0].isolates : 0;
  const fieldText = (f) => `${f.label} (${f.distinct} value${f.distinct === 1 ? "" : "s"}${f.filled < f.isolates ? `, ${f.filled} of ${f.isolates} filled` : ""})`;
  // A field with one value, or a different value for every isolate, makes no chart.
  const chartable = fields.filter((f) => f.distinct >= 2 && f.distinct < f.isolates);
  // A column can name the isolates only if every isolate has its own value.
  const nameable = fields.filter((f) => f.source === "metadata" && f.distinct === total && f.filled === total);
  const toggleGroup = (field) =>
    setGroupBy((prev) => (prev.includes(field) ? prev.filter((g) => g !== field) : [...prev, field]));
  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Cohort report</h2>
        <div className="panel-actions">
          <button className="ghost action" onClick={fetchMetadata} disabled={fetching || busy}
                  title="Look up public records for samples named by an SRA, ENA or DDBJ run accession and add them to this project's sample_metadata.tsv. Values already in the sheet are kept.">
            {fetching ? "Fetching…" : "Fetch public metadata"}
          </button>
          <button className="ghost action" onClick={build} disabled={busy || fetching || sampleCount === 0}
                  title="Build one report for every finished sample in this project">
            {busy ? "Building…" : state?.exists ? "↻ Rebuild" : "Build cohort report"}
          </button>
        </div>
      </div>
      <div className="note">
        One report for every finished sample in this project: serovar and lineage, resistance,
        plasmid replicons and virulence, with the vSNP Step 2 tree and SNP distances when this
        project has a Step 2 run. Display names and groups come from the project's{" "}
        <code>sample_metadata.tsv</code>.
      </div>
      <div className="note">
        {meta?.exists
          ? `Metadata sheet: ${meta.rows} sample(s), ${meta.columns.length} column(s).`
          : "No metadata sheet yet. Fetch public metadata for SRA runs, or put a sample_metadata.tsv with a sample column in the project folder."}
      </div>
      {fields.length > 0 && (
        <div className="form-section">
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="form-label">Colour isolates by</span>
              <select value={colorBy} onChange={(e) => setColorBy(e.target.value)} disabled={busy}>
                <option value="">Nothing</option>
                {fields.filter((f) => f.distinct < f.isolates || f.distinct <= 1).map((f) => (
                  <option key={f.field} value={f.field}>{fieldText(f)}</option>
                ))}
              </select>
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="form-label">Name isolates from</span>
              <select value={labelColumn} onChange={(e) => setLabelColumn(e.target.value)} disabled={busy}>
                <option value="">Automatic (the column matching the tree)</option>
                {nameable.map((f) => <option key={f.field} value={f.field}>{f.label}</option>)}
              </select>
            </label>
          </div>
          <div className="form-label" style={{ marginTop: 10 }}>Chart isolates by</div>
          <div style={{ display: "flex", gap: "6px 14px", flexWrap: "wrap" }}>
            {chartable.length === 0 && <span className="note">No field has groups to chart.</span>}
            {chartable.map((f) => (
              <label key={f.field} className="checkbox-label"
                     style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                <input type="checkbox" checked={groupBy.includes(f.field)}
                       onChange={() => toggleGroup(f.field)} disabled={busy} />
                <span>{fieldText(f)}</span>
              </label>
            ))}
          </div>
          <div className="note" style={{ marginTop: 6 }}>
            Fields come from the results and from the metadata sheet. Nothing is charted until you pick it.
            Press {state?.exists ? "Rebuild" : "Build cohort report"} to apply.
          </div>
        </div>
      )}
      {error && <div className="note" style={{ color: "var(--danger, #C46A6A)" }}>{error}</div>}
      {!state?.exists && !busy && !error && (
        <div className="empty-msg">
          {sampleCount === 0 ? "Run at least one sample first." : "No cohort report has been built for this project yet."}
        </div>
      )}
      {state?.exists && (
        <>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", margin: "8px 0" }}>
            {(state.files || []).map((f) => (
              <a key={f.name} className="ghost action" href={href(f, f.openable)}
                 target={f.openable ? "_blank" : undefined} rel="noopener noreferrer">
                {f.label}
              </a>
            ))}
          </div>
          {itol.available && (
            <div className="form-section">
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                <button className="ghost action" onClick={() => { setItolOpen((o) => !o); setItolError(""); }}
                        disabled={sending || busy || !itol.configured}
                        title="Upload this tree and its annotations to your iTOL account">
                  Send to iTOL…
                </button>
                {sent?.url && (
                  <span className="note">
                    Sent. <a href={sent.url} target="_blank" rel="noopener noreferrer">Open the tree in iTOL</a>
                  </span>
                )}
                {!sent && itol.last?.url && (
                  <span className="note">
                    Last sent {itol.last.uploaded} to {itol.last.itol_project}:{" "}
                    <a href={itol.last.url} target="_blank" rel="noopener noreferrer">open in iTOL</a>
                  </span>
                )}
              </div>
              {!itol.configured && (
                <div className="note">
                  To send trees to iTOL from here, save your iTOL API key in <code>{itol.key_file}</code> on
                  this server, readable only by you. Batch upload needs an iTOL subscription.
                </div>
              )}
              {itolOpen && itol.configured && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
                    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <span className="form-label">iTOL project (must already exist)</span>
                      <input type="text" value={itolProject} onChange={(e) => setItolProject(e.target.value)}
                             disabled={sending} />
                    </label>
                    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <span className="form-label">Tree name in iTOL</span>
                      <input type="text" value={itolName} onChange={(e) => setItolName(e.target.value)}
                             disabled={sending} />
                    </label>
                  </div>
                  <label className="checkbox-label"
                         style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", marginTop: 8 }}>
                    <input type="checkbox" checked={itolPopup} onChange={(e) => setItolPopup(e.target.checked)}
                           disabled={sending} />
                    <span>Include the details shown when a tip is clicked (every column of the metadata sheet)</span>
                  </label>
                  <div className="note" style={{ marginTop: 6 }}>
                    This sends the tree, the isolate names and the annotations to itol.embl.de, into your
                    iTOL account. Each send makes a new tree there.
                  </div>
                  <div style={{ display: "flex", gap: 10, marginTop: 6 }}>
                    <button className="action" onClick={sendToItol} disabled={sending || !itolProject.trim()}>
                      {sending ? "Sending…" : "Send"}
                    </button>
                    <button className="ghost action" onClick={() => setItolOpen(false)} disabled={sending}>
                      Cancel
                    </button>
                  </div>
                </div>
              )}
              {itolError && <div className="note" style={{ color: "var(--danger, #C46A6A)" }}>{itolError}</div>}
              {(sent?.warnings || []).length > 0 && (
                <>
                  <div className="note">iTOL accepted the tree and reported:</div>
                  <ul style={{ margin: "6px 0 6px 18px", padding: 0 }}>
                    {sent.warnings.map((w, i) => <li key={i}>{w}</li>)}
                  </ul>
                </>
              )}
            </div>
          )}
          <div className="note">
            Built {checks.generated || "—"} from {checks.isolates ?? "—"} isolate(s)
            {checks.step2_run ? `, Step 2 run ${checks.step2_run}` : ", no Step 2 tree"}.
          </div>
          {(checks.summary || []).length > 0 && (
            <ul style={{ margin: "6px 0 6px 18px", padding: 0 }}>
              {checks.summary.map((s, i) => <li key={i}>{s}</li>)}
            </ul>
          )}
          {problems.length > 0 && (
            <>
              <div className="note" style={{ color: "var(--danger, #C46A6A)", fontWeight: 600 }}>
                Check before using this report
              </div>
              <ul style={{ margin: "6px 0 6px 18px", padding: 0 }}>
                {problems.map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  );
}
