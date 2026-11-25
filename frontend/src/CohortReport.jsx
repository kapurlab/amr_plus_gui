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

  if (!project) return null;
  const href = (f, inline) =>
    `./api/projects/${encodeURIComponent(project)}/file?path=${encodeURIComponent(f.path)}${inline ? "&inline=1" : ""}`;
  const checks = state?.checks || {};
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
