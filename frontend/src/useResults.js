/* SHARED COMPONENT — byte-identical across the Kapur Lab tool suite.
   Source of truth: amr_plus_gui/frontend/src/useResults.js
   Do not edit in one repo. Change it in amr_plus_gui, then re-copy to every
   sibling and re-tag. Verify with bin/check-shared-frontend.sh in the umbrella.

   Results-pane state, modelled on vSNP's Step 1 Results (vsnp_gui App.jsx:345-415,
   1445-1520, 1819-1864). One place decides which rows are visible, so the table,
   the "showing N of M" count, the check-all and the exports can never disagree. */
import { useCallback, useEffect, useMemo, useState } from "react";

export const isoDay = (d) => {
  const x = new Date(d);
  return isNaN(x) ? "" : x.toISOString().slice(0, 10);
};

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
      const day = String(r.run_date || "").slice(0, 10);
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
