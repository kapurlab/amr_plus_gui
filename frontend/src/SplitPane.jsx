/* SHARED COMPONENT — byte-identical across the Kapur Lab tool suite.
   Source of truth: amr_plus_gui/frontend/src/SplitPane.jsx
   Do not edit in one repo. Change it in amr_plus_gui, then re-copy to every
   sibling and re-tag. Verify with bin/check-shared-frontend.sh in the umbrella.

   Drag-to-resize the boundary between two side-by-side panes — every such pair
   in the tool, from one line of wiring.

   Why: the pane proportions are a guess made once, at design time, about which
   half of the screen carries the answer. "Configure & Run" beside "Kraken
   Results" is right while you are setting up a run and wrong the moment you are
   reading a taxon name that wraps to three lines in a column that cannot grow —
   the same reason the tables became resizable. The defaults are good; what was
   missing was leaning the layout one way for a minute.

   The sibling of ResizableTable.jsx, built from the same parts: pointer-event
   drag with window listeners, persist-on-pointerup, arrow keys with a Shift
   coarse step, double-click to reset, one localStorage key per tool per pane
   pair. Anything learned about one applies to the other.

   Usage — ONE element, once, anywhere inside the app's root:

     import { PaneSplitters } from "./SplitPane";
     …
     <PaneSplitters />

   It then finds every `.row-grid` row on the page and gives each one a divider,
   including rows that mount later (a Results pane appearing after a run) and
   rows added in a future release. Deliberately not a per-row wrapper: these
   apps are single 1,000–8,000-line App.jsx files whose rows nest at the same
   indentation, and rewriting ~30 of those by hand across nine repos is how a
   layout change becomes a broken build. Nothing in the JSX has to know.

   A row is split only when it really is two panes side by side: exactly two
   element children AND two grid tracks. That skips the single-column rows, the
   2x2 card grids, and vsnp_gui's Step 1/Step 2 rows that hold seven and
   thirteen children — at runtime, from the DOM, so no list of exceptions can go
   stale.

   Until the first drag NOTHING changes: no inline columns, no extra grid track,
   the stylesheet's own `grid-template-columns` exactly as before. The handle is
   invisible until its row is hovered or it takes focus. */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./SplitPane.css";

/* Ratio bounds. A pane narrower than ~15% of the row is a sliver whose own
   min-width fights the grid, and the drag would leave a layout the pointer
   cannot comfortably undo (the MIN_W reasoning from ResizableTable, expressed
   as a fraction because these tracks are fr units, not pixels). */
const MIN_RATIO = 0.15;
const MAX_RATIO = 0.85;

/* Arrow-key steps: fine for placing a boundary, coarse for crossing the row. */
const STEP = 0.02;
const COARSE_STEP = 0.08;

const STORE_PREFIX = "bdtools.panesplit";

/* Several tools are served from ONE origin under Open OnDemand, so a bare pane
   id would have amr_plus_gui and mlst_gui overwrite each other's layouts. The
   document title is per-tool, stable and set at build time; the OOD proxy path
   is not (it carries a per-session node and port). Read once at module load,
   before any app can retitle the page. Identical rule to ResizableTable. */
const APP_KEY = (typeof document !== "undefined" && document.title) || "app";

const clamp = (r) => Math.min(MAX_RATIO, Math.max(MIN_RATIO, r));

function storageKey(id) {
  return `${STORE_PREFIX}/${APP_KEY}/${id}`;
}

function loadRatio(id) {
  if (!id) return null;
  try {
    const raw = window.localStorage.getItem(storageKey(id));
    if (!raw) return null;
    const parsed = Number.parseFloat(raw);
    // Only a usable fraction counts. Anything else is a stale or corrupted
    // entry, and the stylesheet's own proportions are the safe answer.
    if (!Number.isFinite(parsed) || parsed < MIN_RATIO || parsed > MAX_RATIO) return null;
    return parsed;
  } catch {
    return null;                    // private mode / storage disabled / bad value
  }
}

function saveRatio(id, ratio) {
  if (!id) return;
  try {
    if (ratio) window.localStorage.setItem(storageKey(id), String(ratio));
    else window.localStorage.removeItem(storageKey(id));
  } catch { /* non-fatal — the drag still worked for this session */ }
}

/** Element children, ignoring the divider this module itself adds. */
function panesOf(row) {
  return Array.from(row.children).filter((c) => !c.classList.contains("sp-handle"));
}

/** The row's live geometry, read from the DOM rather than assumed: the gutter,
    the padding and the stacked/side-by-side state all come from the host
    stylesheet, which differs between App.css and vsnp_gui's styles.css. */
function geometry(row) {
  if (!row) return null;
  const cs = window.getComputedStyle(row);
  const tracks = (cs.gridTemplateColumns || "").split(" ").filter(Boolean);
  const gap = Number.parseFloat(cs.columnGap || cs.gap || "0") || 0;
  const padLeft = Number.parseFloat(cs.paddingLeft) || 0;
  const padRight = Number.parseFloat(cs.paddingRight) || 0;
  const box = row.getBoundingClientRect();
  return {
    // One track means the row has stacked (every row in the suite collapses to
    // `1fr` at 900px); two means the divider is a real boundary.
    stacked: tracks.length !== 2,
    gap,
    left: box.left + padLeft,
    usable: box.width - padLeft - padRight - gap,
  };
}

/** Is this row two panes side by side, right now? */
function splittable(row) {
  if (panesOf(row).length !== 2) return false;
  const geo = geometry(row);
  return Boolean(geo) && !geo.stacked && geo.usable > 0;
}

/* A stable name for the row, so a remembered layout survives a reload and a
   release. Taken from the heading of its first pane ("Configure & Run" ->
   configure-run), which is the one thing about a row that is both unique on the
   page and unlikely to change; falls back to the row's position among its
   siblings when a pane has no heading. */
function rowId(row) {
  const head = row.querySelector("h1, h2, h3");
  const text = head && head.textContent ? head.textContent : "";
  const slug = text.toLowerCase().replace(/&/g, " ").replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "").slice(0, 48);
  if (slug) return slug;
  const rows = Array.from(document.querySelectorAll(".row-grid"));
  return `row-${rows.indexOf(row) + 1}`;
}

/**
 * The divider for one row: measures the boundary, owns that row's ratio, and
 * writes the proportions onto the row as custom properties.
 */
function Divider({ row }) {
  const id = useRef(rowId(row)).current;
  const [ratio, setRatio] = useState(() => loadRatio(id));
  // Mirrors `ratio` so a drag (many events per tick) and the arrow keys read the
  // live value rather than a stale render closure — the bug ResizableTable's
  // widthsRef exists to prevent, where three keypresses moved one step.
  const ratioRef = useRef(ratio);
  const [x, setX] = useState(null);

  /* The proportions live on the ROW, not here: the row is the grid, and its
     stylesheet only reads them once `data-sp-resized` is set (see the CSS).
     Written imperatively because the row belongs to the host's JSX — and
     re-written after every render, so a host re-render that resets the style
     attribute cannot leave a resized row at its default columns. */
  useEffect(() => {
    row.classList.add("sp-split");
    if (ratio) {
      row.style.setProperty("--sp-a", `${ratio}fr`);
      row.style.setProperty("--sp-b", `${1 - ratio}fr`);
      row.dataset.spResized = "true";
    } else {
      row.style.removeProperty("--sp-a");
      row.style.removeProperty("--sp-b");
      delete row.dataset.spResized;
    }
  });

  /* Leave the row exactly as it was found if this divider goes away — an
     unmount must not leave a class, an attribute or a custom property behind. */
  useEffect(() => () => {
    row.classList.remove("sp-split");
    row.style.removeProperty("--sp-a");
    row.style.removeProperty("--sp-b");
    delete row.dataset.spResized;
  }, [row]);

  /* Keep the handle on the boundary as the row, the window or the content
     resizes. Re-measured on every ratio change too, since the boundary is what
     the drag just moved. */
  useEffect(() => {
    const measure = () => {
      const geo = geometry(row);
      const [first] = panesOf(row);
      if (!geo || geo.stacked || !first) { setX(null); return; }
      setX(first.getBoundingClientRect().width + geo.gap / 2);
    };
    measure();
    // A window resize is the guaranteed signal; ResizeObserver is the precise
    // one (a reflow inside a pane that leaves the row's own box alone). Both,
    // because neither is sufficient: RO is absent in older browsers and,
    // found while testing this, delivers nothing at all in some embedded
    // webviews — a handle stuck at a boundary that has moved is worse than one
    // that only catches up on resize.
    window.addEventListener("resize", measure);
    let ro;
    if (typeof ResizeObserver !== "undefined") {
      // Observe the row AND its first pane: the row's width answers a window
      // resize, the pane's answers a reflow inside it that leaves the row alone.
      ro = new ResizeObserver(measure);
      ro.observe(row);
      const [first] = panesOf(row);
      if (first) ro.observe(first);
    }
    return () => {
      window.removeEventListener("resize", measure);
      if (ro) ro.disconnect();
    };
  }, [row, ratio]);

  const apply = useCallback((next, persist) => {
    const value = clamp(next);
    ratioRef.current = value;
    setRatio(value);
    // `persist` is false during a drag: writing localStorage on every
    // pointermove is pointless churn, and the pointerup commits the result.
    if (persist) saveRatio(id, value);
  }, [id]);

  const reset = useCallback(() => {
    ratioRef.current = null;
    setRatio(null);
    saveRatio(id, null);
  }, [id]);

  const startDrag = (e) => {
    // Primary button / contact only: a right-click on the divider should open
    // the context menu, not start a drag no pointerup will ever end.
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const geo = geometry(row);
    if (!geo || geo.stacked || geo.usable <= 0) return;
    // Listen on the window, not on the 14px handle: the pointer leaves the
    // handle immediately and every move after that would otherwise be lost.
    const onMove = (ev) => apply((ev.clientX - geo.left) / geo.usable, false);
    const onUp = (ev) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      document.body.classList.remove("sp-dragging");
      apply((ev.clientX - geo.left) / geo.usable, true);
    };
    // While dragging, the whole page shows the resize cursor and stops
    // selecting text — otherwise a drag across a pane highlights its contents.
    document.body.classList.add("sp-dragging");
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  };

  /* The keyboard needs a starting number even on a row never dragged: measure
     what the stylesheet is giving the first pane right now. */
  const currentRatio = () => {
    if (ratioRef.current) return ratioRef.current;
    const geo = geometry(row);
    const [first] = panesOf(row);
    if (!geo || geo.stacked || !first || geo.usable <= 0) return null;
    return clamp(first.getBoundingClientRect().width / geo.usable);
  };

  const onKeyDown = (e) => {
    const step = e.shiftKey ? COARSE_STEP : STEP;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      const base = currentRatio();
      if (base == null) return;
      e.preventDefault();
      apply(base + (e.key === "ArrowLeft" ? -step : step), true);
    } else if (e.key === "Home" || e.key === "Escape") {
      e.preventDefault();
      reset();
    }
  };

  if (x == null) return null;
  return (
    <div
      className="sp-handle"
      style={{ left: `${x}px` }}
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize these panes"
      aria-valuemin={Math.round(MIN_RATIO * 100)}
      aria-valuemax={Math.round(MAX_RATIO * 100)}
      aria-valuenow={Math.round((ratio || 0.5) * 100)}
      tabIndex={0}
      title="Drag to resize • ← → to nudge • double-click to reset"
      onPointerDown={startDrag}
      onDoubleClick={(e) => { e.preventDefault(); e.stopPropagation(); reset(); }}
      onKeyDown={onKeyDown}
    >
      <span className="sp-handle-line" aria-hidden="true" />
    </div>
  );
}

/**
 * Give every two-pane row in this tool a draggable divider. Render once.
 *
 * selector  which rows to consider (default: the suite's `.row-grid` convention)
 */
export function PaneSplitters({ selector = ".row-grid" }) {
  const [rows, setRows] = useState([]);

  useEffect(() => {
    let frame = 0;
    let observed = [];
    const same = (a, b) => a.length === b.length && a.every((el, i) => el === b[i]);
    /* Whether a row is two panes side by side is a question about its BOX, and a
       box can change without a DOM mutation and without a window `resize`
       event: a zoom, a container query, a sidebar collapsing — and, found while
       testing this, a viewport change driven by the devtools protocol, which
       resizes the page without dispatching `resize` at all. So the rows
       themselves are observed; the window listener is kept only as a cheap
       belt. Without this a row that stacked on a narrow window stayed
       divider-less after the window was widened again. */
    const ro = typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(() => schedule()) : null;
    const scan = () => {
      frame = 0;
      const candidates = Array.from(document.querySelectorAll(selector));
      // Re-observe only when the candidate set really changed: ResizeObserver
      // notifies on first observation, so re-observing the same elements every
      // pass would keep scheduling itself forever.
      if (ro && !same(candidates, observed)) {
        ro.disconnect();
        candidates.forEach((el) => ro.observe(el));
        observed = candidates;
      }
      const found = candidates.filter(splittable);
      // Replace the list only when it really changed: a new array on every
      // mutation would remount every divider (and lose the drag in progress).
      setRows((prev) => (same(prev, found) ? prev : found));
    };
    /* Coalesce the bursts (a batch of samples arriving mutates the DOM many
       times per tick, and querySelectorAll per mutation is waste) — with a
       TIMER, not requestAnimationFrame. rAF does not run while the page is
       hidden, so a latch holding an un-fired frame id blocks every later scan
       for as long as the tab stays in the background: resize the window with
       the tool in another tab, come back, and the dividers are gone until the
       app remounts. Found in an embedded webview that never runs frames at
       all; a background tab in any browser is the same shape. */
    const schedule = () => { if (!frame) frame = window.setTimeout(scan, 16); };
    scan();
    const mo = new MutationObserver(schedule);
    mo.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("resize", schedule);
    return () => {
      if (frame) window.clearTimeout(frame);
      if (ro) ro.disconnect();
      mo.disconnect();
      window.removeEventListener("resize", schedule);
    };
  }, [selector]);

  /* One portal per row: the divider is React's to manage, but it lives inside a
     container the host's JSX owns. A portal is how those two facts coexist —
     appending the node by hand would leave it outside the reconciler, to be
     dropped the next time React touched the row's children. */
  return rows.map((row, i) => (
    <React.Fragment key={rowId(row) || i}>{createPortal(<Divider row={row} />, row)}</React.Fragment>
  ));
}

export default PaneSplitters;
