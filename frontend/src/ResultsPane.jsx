/* SHARED COMPONENT — byte-identical across the Kapur Lab tool suite.
   Source of truth: amr_plus_gui/frontend/src/ResultsPane.jsx
   Do not edit in one repo. Change it in amr_plus_gui, then re-copy to every
   sibling and re-tag. Verify with bin/check-shared-frontend.sh in the umbrella.

   The Results pane, modelled on vSNP Step 1 Results (vsnp_gui App.jsx:4943-5320):
   every completed sample in one searchable, sortable, exportable table — not just
   whichever one you last clicked. Per-tool differences arrive as props (columns,
   rowActions, labels), so the table itself stays identical everywhere. */
import React, { useState } from "react";
import { levelOf, reasonsOf, summarizeReason, fmtRunDate } from "./useResults";
import "./ResultsPane.css";

const LEVEL_TEXT = { pass: "PASS", review: "REVIEW", fail: "FAIL" };

export function StatusChip({ row }) {
  const level = levelOf(row);
  const reasons = reasonsOf(row);
  return (
    <span className={`rp-chip rp-chip-${level}`} title={reasons.join("\n") || undefined}>
      {LEVEL_TEXT[level] || level.toUpperCase()}
      {reasons.length > 0 && (
        <span className="rp-chip-note">{summarizeReason(reasons[0])}</span>
      )}
    </span>
  );
}

function fmtSize(n) {
  if (n == null) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}

/** Files + sibling-tool links for one sample. Only one row is open at a time so
    the table doesn't turn into a wall of links. */
function FilesCell({ row, project, open, onToggle }) {
  const files = row.files || [];
  const cross = row.cross_tool || [];
  const base = (f) =>
    `./api/projects/${encodeURIComponent(project)}/file?path=${encodeURIComponent(f.path)}`;
  if (!files.length && !cross.length) return <span className="rp-muted">—</span>;
  return (
    <details className="rp-files" open={open}>
      <summary
        onClick={(e) => { e.preventDefault(); onToggle(); }}
        title="Show this sample's output files"
      >
        Files ({files.length + cross.length})
      </summary>
      {open && (
        <div className="rp-files-list">
          {files.map((f) => (
            <div key={f.path || f.name} className="rp-file">
              <a href={`${base(f)}&inline=${f.openable ? 1 : 0}`}
                 target={f.openable ? "_blank" : undefined} rel="noopener noreferrer">
                {f.label || f.name}
              </a>
              <span className="rp-muted">{fmtSize(f.size)}</span>
              <a className="rp-dl" href={`${base(f)}&inline=0`} title={`Download ${f.name}`}>⬇</a>
            </div>
          ))}
          {cross.map((c) => (
            <div key={`${c.tool}-${c.kind}`} className="rp-file rp-cross">
              <a href={c.href} target="_blank" rel="noopener noreferrer">{c.label}</a>
              <span className="rp-muted">from {c.tool}</span>
            </div>
          ))}
        </div>
      )}
    </details>
  );
}

export default function ResultsPane({
  project,
  results,
  selection = null,
  columns = [],
  onDetail = null,
  labels = {},
  title = "Results",
  // Opt-in row selection. When onRowSelect is given, clicking anywhere on a row
  // (outside the checkbox / Files / action controls) calls it, and the row matching
  // selectedKey is highlighted — so a tool can drive its own detail pane below the
  // table, the way irma_gui and ksnp_gui already do from their Projects lists.
  // Omitted by default, so tools that don't pass it are visually unchanged.
  onRowSelect = null,
  selectedKey = null,
}) {
  const [openFilesRow, setOpenFilesRow] = useState(null);
  const entity = labels.entity || "sample";
  const {
    rows, visibleRows, loading, error, reload,
    filter, setFilter, dateStart, setDateStart, dateEnd, setDateEnd,
    flaggedOnly, setFlaggedOnly, setRangeDays, clearDates,
    downloadCsv, downloadXlsx,
  } = results;

  return (
    <section className="panel rp-panel">
      <div className="panel-header">
        <h2>{title}</h2>
        <div className="panel-actions">
          <button className="ghost action" onClick={reload}>↻ Refresh</button>
          <button className="ghost action" onClick={downloadCsv}
                  disabled={!visibleRows.length}>Download CSV</button>
          <button className="ghost action" onClick={downloadXlsx}
                  disabled={!visibleRows.length}>Download XLSX</button>
        </div>
      </div>

      <div className="note">
        {loading ? `Loading ${entity}s…`
          : error ? `Could not load results: ${error}`
          : project ? `Showing ${visibleRows.length} of ${rows.length} ${entity}(s) for ${project}.`
          : "Select a project to see its results."}
      </div>

      <div className="rp-filters">
        <label className="rp-inline">
          <input type="checkbox" checked={flaggedOnly}
                 onChange={(e) => setFlaggedOnly(e.target.checked)} />
          Show only flagged {entity}s
        </label>
        <input type="search" className="rp-search" placeholder={`Filter ${entity}s…`}
               value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>

      <div className="rp-filters">
        <span className="rp-muted">Run date:</span>
        <input type="date" value={dateStart} onChange={(e) => setDateStart(e.target.value)} />
        <span className="rp-muted">–</span>
        <input type="date" value={dateEnd} onChange={(e) => setDateEnd(e.target.value)} />
        <button className="ghost action" onClick={() => setRangeDays(1)}>Today</button>
        <button className="ghost action" onClick={() => setRangeDays(7)}>Last 7d</button>
        <button className="ghost action" onClick={() => setRangeDays(30)}>Last 30d</button>
        <button className="ghost action" onClick={clearDates}>Clear dates</button>
      </div>

      <div className="rp-table-wrap">
        <table className="rp-table">
          <thead>
            <tr>
              {selection && (
                <th className="rp-check">
                  <input
                    type="checkbox"
                    ref={(el) => { if (el) el.indeterminate = selection.allState.indeterminate; }}
                    checked={selection.allState.checked}
                    disabled={!visibleRows.length}
                    onChange={(e) => selection.toggleAllVisible(e.target.checked)}
                    title={`Select every ${entity} currently shown (honours the filters)`}
                  />
                </th>
              )}
              <th>QC</th>
              <th>{labels.sampleHeader || "Sample"}</th>
              <th>Status</th>
              <th>{labels.dateHeader || "Run date / time"}</th>
              <th>Files</th>
              {columns.map((c) => <th key={c.key} style={{ textAlign: c.align || "left" }}>{c.label}</th>)}
              {onDetail && <th />}
            </tr>
          </thead>
          <tbody>
            {!loading && visibleRows.length === 0 && (
              <tr>
                <td className="rp-empty" colSpan={20}>
                  {rows.length
                    ? `No ${entity}s match the current filters.`
                    : `No completed ${entity}s yet — run one and it will appear here.`}
                </td>
              </tr>
            )}
            {visibleRows.map((row) => {
              const key = row.run_dir || row.sample;
              const selectable = Boolean(onRowSelect);
              return (
                <tr
                  key={key}
                  className={`rp-row rp-row-${levelOf(row)}`
                    + (selectable ? " rp-row-selectable" : "")
                    + (selectedKey && selectedKey === key ? " rp-row-selected" : "")}
                  // Row click, not cell click, so the whole row is a target — but
                  // only when the click did not land on a control. Without this
                  // guard, ticking a checkbox or opening Files would also change
                  // the selection underneath the user.
                  onClick={selectable ? (e) => {
                    if (e.target.closest("input, button, a, summary, details, label")) return;
                    onRowSelect(row);
                  } : undefined}
                  // Keyboard parity: a clickable row must be reachable and
                  // activatable without a mouse.
                  tabIndex={selectable ? 0 : undefined}
                  onKeyDown={selectable ? (e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      if (e.target.closest("input, button, a, summary, details, label")) return;
                      e.preventDefault();
                      onRowSelect(row);
                    }
                  } : undefined}
                  aria-selected={selectable ? (selectedKey === key) : undefined}
                >
                  {selection && (
                    <td className="rp-check">
                      <input type="checkbox" checked={selection.isSelected(row)}
                             onChange={() => selection.toggle(row)} />
                    </td>
                  )}
                  <td><StatusChip row={row} /></td>
                  <td className="rp-sample">{row.sample}</td>
                  <td>
                    <span className={`rp-status rp-status-${row.status}`}>
                      {row.status === "running" ? "● running"
                        : row.status === "done" ? "✓ done" : "not run"}
                    </span>
                  </td>
                  <td className="rp-date">{fmtRunDate(row.run_date)}</td>
                  <td>
                    <FilesCell row={row} project={project} open={openFilesRow === key}
                               onToggle={() => setOpenFilesRow(openFilesRow === key ? null : key)} />
                  </td>
                  {columns.map((c) => (
                    <td key={c.key} style={{ textAlign: c.align || "left" }}>
                      {c.render ? c.render(row) : (row.metrics?.[c.key] ?? "—")}
                    </td>
                  ))}
                  {onDetail && (
                    <td>
                      <button className="ghost action" onClick={() => onDetail(row)}>Detail</button>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {visibleRows.length > 8 && <div className="rp-scroll-note">Scroll for more {entity}s.</div>}
    </section>
  );
}
