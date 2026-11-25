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
      setState(await res.json());
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
      const res = await fetch(base, { method: "POST" });
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
