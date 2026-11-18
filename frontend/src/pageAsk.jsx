import React, { useEffect, useRef, useState } from "react";

// Questions and notices the page draws itself, in place of window.prompt /
// confirm / alert.
//
// A browser may decline to show those dialogs. It then answers "cancelled" (or
// shows nothing) without a word to the person, and whatever was waiting on the
// answer quietly does nothing. That is how filtering a position from a SNP
// table wrote nothing and never asked for its rationale (2026-11-18). A dialog
// rendered by the page cannot be suppressed.
//
//   await askOnPage({ message })                       -> true / false
//   await askOnPage({ message, rationale: true })      -> text, or null if cancelled
//   await askOnPage({ message, input: { placeholder, defaultValue } })
//                                                      -> text, or null
//   askOnPage({ message, notice: true })               -> resolves true on OK
//   askOnPage({ message, copyText })                   -> text shown selected, to copy
//
// Questions queue: one is shown at a time, in order, so a notice raised while a
// question is open waits its turn instead of replacing it.

const queue = [];
let wake = null;

function normalize(opts) {
  const {
    title = "", message = "", rationale = false, input = null, copyText = null,
    okLabel = "", cancelLabel = "Cancel", notice = false,
  } = opts || {};
  let field = null;
  if (rationale) {
    field = { placeholder: "Rationale (required; kept in the audit log)", defaultValue: "",
              required: true, multiline: true };
  } else if (input) {
    field = { placeholder: input.placeholder || "", defaultValue: input.defaultValue || "",
              required: input.required !== false, multiline: false };
  }
  const isNotice = !!(notice || copyText !== null) && !field;
  return {
    title, message: message == null ? "" : String(message), field, copyText,
    okLabel: okLabel || "OK", cancelLabel, notice: isNotice,
  };
}

// The page's own dialogs were unavailable (no host mounted): fall back to the
// browser's, which is no worse than before.
function fallback(ask) {
  const text = [ask.title, ask.message].filter(Boolean).join("\n\n");
  if (ask.field) return window.prompt(text, ask.field.defaultValue);
  if (ask.copyText !== null) { window.prompt(text, ask.copyText); return true; }
  if (ask.notice) { window.alert(text); return true; }
  return window.confirm(text);
}

export function askOnPage(opts) {
  const ask = normalize(opts);
  if (!wake) return Promise.resolve(fallback(ask));
  return new Promise((resolve) => {
    queue.push({ ...ask, resolve });
    wake();
  });
}

export function PageAskHost() {
  const [, setTick] = useState(0);
  const [value, setValue] = useState("");
  const shown = useRef(null);
  useEffect(() => {
    wake = () => setTick((n) => n + 1);
    if (queue.length) wake();
    return () => { wake = null; };
  }, []);
  const ask = queue[0] || null;
  if (ask !== shown.current) {
    // A new question: start its field from its own default.
    shown.current = ask;
    const start = ask && ask.field ? ask.field.defaultValue : "";
    if (value !== start) setValue(start);
  }
  if (!ask) return null;

  const filled = !ask.field || !ask.field.required || value.trim() !== "";
  function answer(ok) {
    if (queue[0] !== ask) return;
    queue.shift();
    if (ask.field) ask.resolve(ok ? value.trim() : null);
    else if (ask.notice) ask.resolve(true);
    else ask.resolve(!!ok);
    setValue("");
    setTick((n) => n + 1);
  }
  function submitOnEnter(e) {
    if (e.key === "Enter" && !e.shiftKey && filled) { e.preventDefault(); answer(true); }
  }

  return (
    <div
      className="page-ask-backdrop"
      onMouseDown={(e) => { if (e.target === e.currentTarget && !ask.notice) answer(false); }}
    >
      <div
        className="page-ask"
        role={ask.notice ? "alertdialog" : "dialog"}
        aria-modal="true"
        aria-label={ask.title || "Question"}
        onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); answer(false); } }}
      >
        {ask.title ? <h3>{ask.title}</h3> : null}
        {ask.message ? <div className="page-ask-message">{ask.message}</div> : null}
        {ask.copyText !== null ? (
          <textarea
            className="page-ask-copy"
            readOnly
            autoFocus
            rows={Math.min(6, Math.max(2, String(ask.copyText).split("\n").length))}
            value={String(ask.copyText)}
            onFocus={(e) => e.target.select()}
            aria-label="Text to copy"
          />
        ) : null}
        {ask.field ? (
          ask.field.multiline ? (
            <textarea
              autoFocus
              rows={3}
              value={value}
              placeholder={ask.field.placeholder}
              aria-label={ask.field.placeholder || "Answer"}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={submitOnEnter}
            />
          ) : (
            <input
              autoFocus
              value={value}
              placeholder={ask.field.placeholder}
              aria-label={ask.field.placeholder || "Answer"}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={submitOnEnter}
            />
          )
        ) : null}
        <div className="page-ask-actions">
          <button autoFocus={!ask.field && ask.copyText === null} disabled={!filled} onClick={() => answer(true)}>
            {ask.okLabel}
          </button>
          {!ask.notice ? (
            <button className="ghost" onClick={() => answer(false)}>{ask.cancelLabel}</button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
