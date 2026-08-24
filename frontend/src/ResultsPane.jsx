/* SHARED COMPONENT — byte-identical across the Kapur Lab tool suite.
   Source of truth: amr_plus_gui/frontend/src/ResultsPane.jsx
   Do not edit in one repo. Change it in amr_plus_gui, then re-copy to every
   sibling and re-tag. Verify with bin/check-shared-frontend.sh in the umbrella.

   The Results pane, modelled on vSNP Step 1 Results (vsnp_gui App.jsx:4943-5320):
   every completed sample in one searchable, sortable, exportable table — not just
   whichever one you last clicked. Per-tool differences arrive as props (columns,
   rowActions, labels), so the table itself stays identical everywhere. */
import React, { useMemo, useState } from "react";
import { levelOf, reasonsOf, summarizeReason, fmtRunDate } from "./useResults";
import { ResizableTable, Grip, useColumnWidths } from "./ResizableTable";
import "./ResultsPane.css";

const LEVEL_TEXT = { pass: "PASS", review: "REVIEW", fail: "FAIL" };

// QC sorts by severity, not alphabetically: a sort meant to surface problems
// has to put FAIL at one end, and "fail < pass < review" is not that.
const LEVEL_RANK = { fail: 0, review: 1, pass: 2 };
const STATUS_RANK = { running: 0, done: 1, "not run": 2 };

/** The comparable value behind one cell.
 *
 * Numbers hiding in strings ("3.9%", "1,204", "12.5X") must sort as numbers —
 * lexical order puts 100 before 20 and makes a metric column useless. */
function sortValue(row, key, columns) {
  switch (key) {
    case "qc": return LEVEL_RANK[levelOf(row)] ?? 9;
    case "sample": return String(row.sample || "");
    case "status": return STATUS_RANK[String(row.status || "")] ?? 9;
    case "run_date": return row.run_date || "";
    case "files": return (row.files || []).length + (row.cross_tool || []).length;
    default: {
      const col = columns.find((c) => c.key === key);
      if (col && col.sortValue) return col.sortValue(row);
      return row.metrics ? row.metrics[key] : undefined;
    }
  }
}

function compareValues(a, b) {
  const blankA = a === null || a === undefined || a === "" || a === "—";
  const blankB = b === null || b === undefined || b === "" || b === "—";
  // Missing values sink to the bottom in BOTH directions — a column of blanks
  // at the top is never what someone sorting wanted to see.
  if (blankA && blankB) return 0;
  if (blankA) return 1;
  if (blankB) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  const na = Number(String(a).replace(/[,%\s]/g, "").replace(/[Xx×]$/, ""));
  const nb = Number(String(b).replace(/[,%\s]/g, "").replace(/[Xx×]$/, ""));
  if (!Number.isNaN(na) && !Number.isNaN(nb)) return na - nb;
  // Natural order, so SRR2 comes before SRR10.
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

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

/* Module scope, NOT redefined inside ResultsPane on every render.
   A component declared in the render body is a NEW type each time, so React
   unmounts and remounts the whole header row on every keystroke in the filter
   box — which threw away keyboard focus on a resize grip after a single nudge
   (and re-created nine header cells for nothing). Taking the sort state as
   props keeps the type stable and the DOM in place. */
function SortHeader({ sortKey, children, align, sort, onSort }) {
  return (
    <th style={{ textAlign: align || "left" }}
        className={`rp-sortable ${sort.key === sortKey ? "rp-sorted" : ""}`}>
      <button type="button" className="rp-sort-btn" onClick={() => onSort(sortKey)}
              title={`Sort by ${typeof children === "string" ? children : sortKey}`}>
        {/* The label is wrapped so a narrowed column truncates it with an
            ellipsis. A bare text node inside a flex row cannot be given one,
            and the label would spill across the next column instead. */}
        <span className="rt-th-label">{children}</span>
        <span className="rp-sort-arrow" aria-hidden="true">
          {sort.key === sortKey ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
      <Grip label={typeof children === "string" ? children : sortKey} />
    </th>
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
  // Rendered under the empty-state message when this project has no rows.
  // The table is scoped to ONE project, and that scoping has misread as data
  // loss: a finished run "vanished" because the page reloaded with a different
  // project selected and the empty state said nothing about where results DO
  // exist. The hosting App knows the other projects' run counts, so it supplies
  // the hint (typically buttons that switch projects); the table stays generic.
  emptyHint = null,
}) {
  const [openFilesRow, setOpenFilesRow] = useState(null);
  /* Column widths, remembered per tool. Keyed by the pane title rather than a
     bare "results": mhc_gui and ncbi_submit_gui each render two of these panes,
     and one shared key would have the second overwrite the first's layout. */
  const colWidths = useColumnWidths(`results:${title}`);
  // null key = the server's own order (newest run first), which is the right
  // default; clicking a header takes over from there.
  const [sort, setSort] = useState({ key: null, dir: "asc" });
  const entity = labels.entity || "sample";
  const {
    rows, visibleRows, loading, error, reload,
    filter, setFilter, dateStart, setDateStart, dateEnd, setDateEnd,
    flaggedOnly, setFlaggedOnly, setRangeDays, clearDates,
    downloadCsv, downloadXlsx,
  } = results;

  const sortedRows = useMemo(() => {
    if (!sort.key) return visibleRows;
    const factor = sort.dir === "desc" ? -1 : 1;
    // Copy first: visibleRows belongs to the hook, and sorting in place would
    // mutate what every other consumer sees.
    return [...visibleRows].sort(
      (a, b) => factor * compareValues(sortValue(a, sort.key, columns),
                                       sortValue(b, sort.key, columns)));
  }, [visibleRows, sort, columns]);

  /* Click cycles asc -> desc -> back to the server's order. The third state
     matters: once you have sorted, "newest first" is otherwise unreachable
     without reloading the pane. */
  function toggleSort(key) {
    setSort((s) => {
      if (s.key !== key) return { key, dir: "asc" };
      if (s.dir === "asc") return { key, dir: "desc" };
      return { key: null, dir: "asc" };
    });
  }

  // Passed to every SortHeader. The component is at module scope (see the note
  // there) and takes the sort state as props rather than closing over it.
  const sortProps = { sort, onSort: toggleSort };

  return (
    <section className="panel rp-panel">
      <div className="panel-header">
        <h2>{title}</h2>
        <div className="panel-actions">
          {/* Only once there is something to undo — an always-present Reset
              invites the question "reset to what?" of a table nobody has
              touched. */}
          {colWidths.resized && (
            <button className="ghost action rt-reset" onClick={colWidths.reset}
                    title="Put every column back to its automatic width">
              Reset widths
            </button>
          )}
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
        <ResizableTable className="rp-table" widths={colWidths}>
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
              <SortHeader {...sortProps} sortKey="qc">QC</SortHeader>
              <SortHeader {...sortProps} sortKey="sample">{labels.sampleHeader || "Sample"}</SortHeader>
              <SortHeader {...sortProps} sortKey="status">Status</SortHeader>
              <SortHeader {...sortProps} sortKey="run_date">{labels.dateHeader || "Run date / time"}</SortHeader>
              <SortHeader {...sortProps} sortKey="files">Files</SortHeader>
              {columns.map((c) => (
                // A column with nothing comparable behind it (a links cell, say)
                // opts out with sortable:false rather than offering a control
                // that does nothing.
                c.sortable === false
                  ? <th key={c.key} style={{ textAlign: c.align || "left" }}>
                      <span className="rt-th-label">{c.label}</span>
                      <Grip label={c.label} />
                    </th>
                  : <SortHeader {...sortProps} key={c.key} sortKey={c.key} align={c.align}>{c.label}</SortHeader>
              ))}
              {onDetail && <th />}
            </tr>
          </thead>
          <tbody>
            {!loading && sortedRows.length === 0 && (
              <tr>
                <td className="rp-empty" colSpan={20}>
                  {rows.length
                    ? `No ${entity}s match the current filters.`
                    : <>
                        {/* "in this project" is load-bearing: without it, a user
                            whose selection reset on reload reads this as "your
                            run is gone" when it is one project switch away. */}
                        {`No completed ${entity}s in this project yet — run one and it will appear here.`}
                        {emptyHint}
                      </>}
                </td>
              </tr>
            )}
            {sortedRows.map((row) => {
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
        </ResizableTable>
      </div>
      {visibleRows.length > 8 && <div className="rp-scroll-note">Scroll for more {entity}s.</div>}
    </section>
  );
}
