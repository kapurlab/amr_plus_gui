/* SHARED COMPONENT — byte-identical across the Kapur Lab tool suite.
   Source of truth: amr_plus_gui/frontend/src/ResizableTable.jsx
   Do not edit in one repo. Change it in amr_plus_gui, then re-copy to every
   sibling and re-tag. Verify with bin/check-shared-frontend.sh in the umbrella.

   Drag-to-resize column widths for any table in the suite.

   Why: every results table here is a fixed set of columns over variable-length
   biological text. A taxon name ("Mycobacterium tuberculosis variant bovis")
   wraps to three lines while Status sits half empty beside it, and the reader
   can do nothing about it. Sorting and filtering were added for the same
   reason — the table has to answer the question the reader actually has, and
   part of that is being able to see the column that carries the answer.

   Generalised from the two-column splitter ksnp_gui built for its genome-label
   editor (App.jsx, META_COL_*): the same pointer-event drag, the same window
   listeners, the same persist-on-pointerup, the same arrow-key parity — applied
   to every column of any table instead of to one hand-wired divider.

   Usage — wrap the table, drop a <Grip /> in each resizable header cell:

     import { ResizableTable, Grip } from "./ResizableTable";

     <ResizableTable id="results" className="rp-table">
       <thead><tr>
         <th>QC<Grip label="QC" /></th>
         <th>Sample<Grip label="Sample" /></th>
       </tr></thead>
       <tbody>…</tbody>
     </ResizableTable>

   A <Grip /> needs no index: it finds its own cell (and therefore its own
   column) from the DOM at drag time, so adding, removing or reordering columns
   cannot desynchronise a hand-maintained index list.

   A pane that wants its own "Reset widths" control owns the state instead:

     const widths = useColumnWidths("results");
     …
     {widths.resized && <button onClick={widths.reset}>Reset widths</button>}
     <ResizableTable widths={widths} className="rp-table">…

   Until the first drag the table renders exactly as it did before — no
   colgroup, no fixed layout, the browser's own sizing. Nothing about an
   untouched table changes. */
import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import "./ResizableTable.css";

/* Narrower than this and a column is a sliver too thin to aim at to widen it
   again — the drag would leave the table in a state the mouse cannot undo. */
const MIN_W = 40;
const STORE_PREFIX = "bdtools.colwidths";

/* Several tools are served from ONE origin under Open OnDemand, so a bare
   table id would have amr_plus_gui and mlst_gui overwrite each other's widths.
   The document title is per-tool, stable and set at build time; the OOD proxy
   path is not (it carries a per-session node and port). Read once at module
   load, before any app can retitle the page. */
const APP_KEY = (typeof document !== "undefined" && document.title) || "app";

function storageKey(id) {
  return `${STORE_PREFIX}/${APP_KEY}/${id}`;
}

function loadWidths(id) {
  if (!id) return null;
  try {
    const raw = window.localStorage.getItem(storageKey(id));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // Only an array of usable numbers counts. Anything else is a stale or
    // corrupted entry, and the browser's automatic layout is the safe answer.
    if (!Array.isArray(parsed) || !parsed.length) return null;
    if (!parsed.every((w) => Number.isFinite(w) && w >= MIN_W)) return null;
    return parsed;
  } catch {
    return null;                    // private mode / storage disabled / bad JSON
  }
}

function saveWidths(id, widths) {
  if (!id) return;
  try {
    if (widths) window.localStorage.setItem(storageKey(id), JSON.stringify(widths));
    else window.localStorage.removeItem(storageKey(id));
  } catch { /* non-fatal — the drag still worked for this session */ }
}

/** The header cells of the first header row — the columns we can size. */
function headerCells(table) {
  if (!table) return [];
  const row = table.tHead && table.tHead.rows[0];
  return row ? Array.from(row.cells) : [];
}

/**
 * Column-width state for one table, remembered per tool in localStorage.
 * Pass the result to <ResizableTable widths={…}> when the surrounding pane also
 * needs it (to offer a Reset control); otherwise let ResizableTable own it by
 * passing a plain `id`.
 */
export function useColumnWidths(id) {
  const tableRef = useRef(null);
  // null = never resized: the browser lays the table out exactly as before.
  const [widths, setWidths] = useState(() => loadWidths(id));
  // Mirrors `widths` so a drag (many events per tick) and the arrow keys read
  // the live value rather than a stale render closure — the bug ksnp_gui's
  // metaColPctRef exists to prevent, where three keypresses moved one step.
  const widthsRef = useRef(widths);

  // A set of widths is only valid for the column count it was measured at.
  // Tools gain and lose columns between releases, and some tables (irma_gui's
  // metadata editor) change column count as you switch projects — a six-entry
  // list applied to eight columns leaves the last two at zero. Checked after
  // every render, not just on mount, because that is when the count can move;
  // it costs one property read while the table is at its automatic widths.
  useEffect(() => {
    const stored = widthsRef.current;
    if (!stored) return;
    const cells = headerCells(tableRef.current);
    if (cells.length && cells.length !== stored.length) {
      widthsRef.current = null;
      setWidths(null);
      saveWidths(id, null);
    }
  });

  const apply = useCallback((next, persist = true) => {
    widthsRef.current = next;
    setWidths(next);
    // `persist` is false during a drag: writing localStorage on every
    // pointermove is pointless churn, and the pointerup commits the result.
    if (persist) saveWidths(id, next);
  }, [id]);

  const reset = useCallback(() => apply(null), [apply]);

  /* The snapshot that turns an automatically-laid-out table into a fixed one.
     Taken from what is on screen at the moment of the first drag, so the table
     does not jump: the widths it gets are the widths it already had. */
  const snapshot = useCallback(() => {
    if (widthsRef.current) return widthsRef.current;
    const cells = headerCells(tableRef.current);
    if (!cells.length) return null;
    // ceil, not round: a cell is often a fraction of a pixel wider than its
    // integer width, and rounding that away clipped content that fitted a
    // moment earlier — a PASS badge became "PAS…" the instant any column in
    // the table was dragged. Never snapshot narrower than what is on screen.
    const snap = cells.map(
      (c) => Math.max(MIN_W, Math.ceil(c.getBoundingClientRect().width)));
    widthsRef.current = snap;
    setWidths(snap);
    return snap;
  }, []);

  const setColumn = useCallback((index, px, persist) => {
    const base = widthsRef.current || snapshot();
    if (!base || index < 0 || index >= base.length) return;
    const next = base.slice();
    next[index] = Math.max(MIN_W, Math.round(px));
    apply(next, persist);
  }, [apply, snapshot]);

  return useMemo(() => ({
    tableRef, widths, widthsRef, snapshot, setColumn, reset,
    resized: Boolean(widths),
  }), [widths, snapshot, setColumn, reset]);
}

const ResizeCtx = createContext(null);

/**
 * A <table> whose columns can be dragged wider or narrower.
 *
 * id        stable key for remembering widths (ignored when `widths` is given)
 * widths    a useColumnWidths() result, when the pane owns the state
 * children  the usual thead/tbody — a <colgroup> is injected ahead of them
 * ...rest   className, style, onClick … forwarded to the <table>
 */
export function ResizableTable({ id, widths: controlled, children, className = "", style, ...rest }) {
  // Hooks cannot be conditional, so the internal state is always created; with
  // a controller passed in it simply goes unused (and an undefined id reads and
  // writes nothing).
  const own = useColumnWidths(controlled ? undefined : id);
  const ctx = controlled || own;
  const { widths } = ctx;

  /* Fixed layout is what makes a column width mean something: under the
     automatic algorithm a width is a suggestion the browser may redistribute.
     The table is then sized to the sum of its columns, so widening past the
     pane scrolls it (every host wrapper already scrolls) instead of squeezing
     the other columns to compensate for the one being read. */
  const tableStyle = widths
    ? { ...style, tableLayout: "fixed", width: widths.reduce((a, b) => a + b, 0) }
    : style;

  return (
    <ResizeCtx.Provider value={ctx}>
      <table
        ref={ctx.tableRef}
        className={`rt-table${widths ? " rt-sized" : ""}${className ? ` ${className}` : ""}`}
        style={tableStyle}
        {...rest}
      >
        {widths && (
          <colgroup>
            {widths.map((w, i) => <col key={i} style={{ width: `${w}px` }} />)}
          </colgroup>
        )}
        {children}
      </table>
    </ResizeCtx.Provider>
  );
}

/**
 * The drag handle for the column its header cell belongs to.
 * Renders nothing outside a <ResizableTable>, so a grip dropped into a table
 * that was never wrapped is inert rather than broken.
 */
export function Grip({ label }) {
  const ctx = useContext(ResizeCtx);
  const ref = useRef(null);
  if (!ctx) return null;

  /* The grip's own column, read from the DOM. Nothing here is hand-numbered,
     so a column inserted to the left cannot silently resize the wrong one. */
  const columnIndex = () => {
    const th = ref.current && ref.current.closest("th, td");
    if (!th || !th.parentNode) return -1;
    return Array.prototype.indexOf.call(th.parentNode.children, th);
  };

  const startDrag = (e) => {
    // Primary button / contact only: a right-click on the grip should open the
    // context menu, not start a drag that no pointerup will ever end.
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();             // never let the drag also trigger a sort
    const th = ref.current && ref.current.closest("th, td");
    const index = columnIndex();
    if (!th || index < 0) return;
    ctx.snapshot();
    const startX = e.clientX;
    const startW = th.getBoundingClientRect().width;
    // Listen on the window, not on the 9px handle: the pointer leaves the
    // handle immediately and every move after that would otherwise be lost.
    const onMove = (ev) => ctx.setColumn(index, startW + (ev.clientX - startX), false);
    const onUp = (ev) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      document.body.classList.remove("rt-dragging");
      ctx.setColumn(index, startW + (ev.clientX - startX), true);
    };
    // While dragging, the whole page shows the resize cursor and stops
    // selecting text — otherwise a drag across the table highlights every row.
    document.body.classList.add("rt-dragging");
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  };

  const nudge = (delta) => {
    const index = columnIndex();
    if (index < 0) return;
    const base = ctx.widthsRef.current || ctx.snapshot();
    if (!base) return;
    ctx.setColumn(index, base[index] + delta, true);
  };

  const onKeyDown = (e) => {
    // Shift for a coarse step: 8px is precise, 40px crosses a column quickly.
    const step = e.shiftKey ? 40 : 8;
    if (e.key === "ArrowLeft") { e.preventDefault(); nudge(-step); }
    else if (e.key === "ArrowRight") { e.preventDefault(); nudge(step); }
    else if (e.key === "Home" || e.key === "Escape") { e.preventDefault(); ctx.reset(); }
  };

  return (
    <span
      ref={ref}
      className="rt-grip"
      role="separator"
      aria-orientation="vertical"
      aria-label={label ? `Resize the ${label} column` : "Resize this column"}
      tabIndex={0}
      title="Drag to resize • ← → to nudge • double-click to reset every column"
      onPointerDown={startDrag}
      // A grip inside a sortable header sits on top of the sort button; without
      // these the click falls through and re-sorts the table on every drag.
      onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}
      onDoubleClick={(e) => { e.preventDefault(); e.stopPropagation(); ctx.reset(); }}
      onKeyDown={onKeyDown}
    >
      <span className="rt-grip-line" aria-hidden="true" />
    </span>
  );
}

export default ResizableTable;
