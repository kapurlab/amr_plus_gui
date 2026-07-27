/* SHARED COMPONENT — byte-identical across the Kapur Lab tool suite.
   Source of truth: amr_plus_gui/frontend/src/useResults.js
   Do not edit in one repo. Change it in amr_plus_gui, then re-copy to every
   sibling and re-tag. Verify with bin/check-shared-frontend.sh in the umbrella.

   Results-pane state, modelled on vSNP's Step 1 Results (vsnp_gui App.jsx:345-415,
   1445-1520, 1819-1864). One place decides which rows are visible, so the table,
   the "showing N of M" count, the check-all and the exports can never disagree. */
import { useCallback, useEffect, useMemo, useState } from "react";

/** Local calendar day of a Date, as YYYY-MM-DD.
 *
 * NOT toISOString().slice(0,10) — that is the UTC day, so "Today" meant the UTC
 * today. West of Greenwich an evening run already belonged to tomorrow's filter.
 * The table shows local time (see fmtRunDate), so the filter must agree or a run
 * displays one date and is filtered under another. */
export const isoDay = (d) => {
  const x = new Date(d);
  if (isNaN(x)) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${x.getFullYear()}-${p(x.getMonth() + 1)}-${p(x.getDate())}`;
};

/** Backend timestamp -> Date, or null when there is no usable time.
 *
 * A naive string ("2026-07-27T10:36:40") is what JS already treats as local, which
 * is what the backends mean. A string carrying Z or an offset is converted to local
 * so the table shows the wall-clock time the run actually happened for whoever is
 * reading it — matching the local timestamp in the run's own folder name.
 *
 * A DATE-ONLY value returns null on purpose: `new Date("2026-07-27")` parses as UTC
 * midnight, which west of Greenwich renders as 18:00 the PREVIOUS DAY. Showing the
 * wrong date is worse than showing no time. */
export function parseRunDate(value) {
  if (!value) return null;
  const s = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return null;
  const d = new Date(s.includes("T") ? s : s.replace(" ", "T"));
  return isNaN(d.getTime()) ? null : d;
}

/** The local day a row belongs to, for date filtering. Falls back to the leading
 *  10 characters when there is no parseable time (date-only, or unrecognised). */
export function runDay(value) {
  const d = parseRunDate(value);
  return d ? isoDay(d) : String(value || "").trim().slice(0, 10);
}

/** Run timestamp for display: "2026-07-27_07-10-52".
 *
 * Deliberately the same shape the pipelines already stamp into run labels and
 * output filenames (ksnp_20260727_074433,
 * <label>_2026-07-27_10-45-27_stats.xlsx), so a row in this table can be matched
 * to a folder or a spreadsheet by eye. Seconds are kept for the same reason —
 * two runs of one set can land in the same minute.
 *
 * The time was previously discarded by slicing to 10 characters, which made
 * several runs of the same set on one day indistinguishable — precisely when you
 * need to tell them apart. Date-only values render as the bare date rather than
 * inventing 00-00-00. */
export function fmtRunDate(value) {
  const d = parseRunDate(value);
  if (!d) return String(value || "").trim().slice(0, 10) || "—";
  const p = (n) => String(n).padStart(2, "0");
  return `${isoDay(d)}_${p(d.getHours())}-${p(d.getMinutes())}-${p(d.getSeconds())}`;
}

export const levelOf = (row) => (row?.flags?.level || "pass").toLowerCase();
export const reasonsOf = (row) => row?.flags?.reasons || [];
export const isFlagged = (row) => levelOf(row) !== "pass";

/** Trim a QC note to something that fits a chip without hiding the number. */
export function summarizeReason(reason) {
  const r = String(reason || "").trim();
  if (!r) return "";
  return r.length <= 48 ? r : r.slice(0, 45).replace(/[\s,;:.]+$/, "") + "…";
}

export function useResults(project, { path = "results", auto = true } = {}) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [dateStart, setDateStart] = useState("");
  const [dateEnd, setDateEnd] = useState("");
  const [flaggedOnly, setFlaggedOnly] = useState(false);

  const reload = useCallback(() => {
    if (!project) { setRows([]); return Promise.resolve(); }
    setLoading(true);
    setError("");
    return fetch(`./api/projects/${encodeURIComponent(project)}/${path}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => setRows(Array.isArray(d.rows) ? d.rows : []))
      .catch((e) => { setRows([]); setError(String(e.message || e)); })
      .finally(() => setLoading(false));
  }, [project, path]);

  useEffect(() => { if (auto) reload(); }, [auto, reload]);

  /* Every filter is applied HERE and nowhere else. vSNP learned this the hard
     way: a second filter applied at render time let the check-all act on rows
     the user could not see. */
  const visibleRows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return rows.filter((r) => {
      if (q && !String(r.sample || "").toLowerCase().includes(q)) return false;
      if (flaggedOnly && !isFlagged(r)) return false;
      const day = runDay(r.run_date);
      if (dateStart && (!day || day < dateStart)) return false;
      if (dateEnd && (!day || day > dateEnd)) return false;
      return true;
    });
  }, [rows, filter, flaggedOnly, dateStart, dateEnd]);

  const setRangeDays = useCallback((days) => {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - (days - 1));
    setDateStart(isoDay(start));
    setDateEnd(isoDay(end));
  }, []);

  const clearDates = useCallback(() => { setDateStart(""); setDateEnd(""); }, []);

  /* Exports carry the active filters, so a downloaded file matches the screen
     rather than silently containing everything. */
  const exportQuery = useCallback(() => {
    const p = new URLSearchParams();
    if (dateStart) p.set("start", dateStart);
    if (dateEnd) p.set("end", dateEnd);
    if (filter.trim()) p.set("q", filter.trim());
    const s = p.toString();
    return s ? `?${s}` : "";
  }, [dateStart, dateEnd, filter]);

  const download = useCallback((ext) => {
    if (!project) return;
    window.open(
      `./api/projects/${encodeURIComponent(project)}/${path}.${ext}${exportQuery()}`,
      "_blank"
    );
  }, [project, path, exportQuery]);

  return {
    rows, visibleRows, loading, error, reload,
    filter, setFilter, dateStart, setDateStart, dateEnd, setDateEnd,
    flaggedOnly, setFlaggedOnly, setRangeDays, clearDates, exportQuery,
    downloadCsv: () => download("csv"),
    downloadXlsx: () => download("xlsx"),
  };
}

/**
 * Tri-state selection over whatever is CURRENTLY VISIBLE.
 *
 * Shared with the Projects pane's check-all on purpose: the invariant that
 * "select all" only ever touches rows the user can actually see is the whole
 * point, and duplicating it is how the two drift apart. A filtered check-all
 * that quietly queues hidden samples is a data-loss-shaped bug, not a UI nit.
 */
export function useVisibleSelection(visibleItems, keyOf) {
  const [selected, setSelected] = useState({});

  const isSelected = useCallback((item) => !!selected[keyOf(item)], [selected, keyOf]);

  const toggle = useCallback((item) => {
    const k = keyOf(item);
    setSelected((m) => {
      const next = { ...m };
      if (next[k]) delete next[k];
      else next[k] = item;
      return next;
    });
  }, [keyOf]);

  const allState = useMemo(() => {
    const total = visibleItems.length;
    const on = visibleItems.filter((it) => selected[keyOf(it)]).length;
    return { total, on, checked: total > 0 && on === total, indeterminate: on > 0 && on < total };
  }, [visibleItems, selected, keyOf]);

  const toggleAllVisible = useCallback((checked) => {
    setSelected((m) => {
      const next = { ...m };
      visibleItems.forEach((it) => {
        const k = keyOf(it);
        if (checked) next[k] = it;
        else delete next[k];
      });
      return next;
    });
  }, [visibleItems, keyOf]);

  return { selected, setSelected, isSelected, toggle, allState, toggleAllVisible };
}
