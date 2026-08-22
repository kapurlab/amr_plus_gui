import { useState } from "react";

// CopyLogButton — put the whole pipeline log on the clipboard in one click.
//
// SHARED FILE. Vendored byte-identically into every tool (see the suite's
// bin/check-shared-frontend.sh); source of truth is amr_plus_gui. Change it
// there, re-copy, re-tag.
//
// Why a component and not three lines inline: the clipboard is the one browser
// API here that is NOT always available. navigator.clipboard exists only in a
// secure context — https, or a localhost origin. A personal install
// (http://127.0.0.1:PORT) qualifies, but an OOD deployment reached over plain
// http on a lab hostname does NOT, and there the modern API is simply
// undefined. A log a user cannot copy from the server deployment is exactly the
// log they most need to paste into an email, so fall back to the deprecated
// execCommand path rather than fail. Both routes are wrapped: a copy that
// cannot happen says so on the button instead of throwing into the console.
//
// `text` is a FUNCTION, not a string: a running pipeline re-renders this on
// every log line, and joining thousands of lines on each of those renders is
// work nobody asked for. It is called only when the button is pressed.
export default function CopyLogButton({ text, title = "Copy the whole log to the clipboard" }) {
  const [state, setState] = useState("idle");   // idle | copied | empty | failed

  const flash = (next) => {
    setState(next);
    setTimeout(() => setState("idle"), 1600);
  };

  const legacyCopy = (payload) => {
    // Off-secure-context fallback. The textarea must be in the document and
    // focusable for execCommand to see a selection, and readOnly keeps the
    // mobile keyboard from appearing.
    const ta = document.createElement("textarea");
    ta.value = payload;
    ta.readOnly = true;
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    try {
      ta.select();
      return document.execCommand("copy");
    } finally {
      document.body.removeChild(ta);
    }
  };

  const onCopy = async (e) => {
    // The header this sits in is often a toggle; never let the click through.
    e.stopPropagation();
    let payload = "";
    try {
      payload = (typeof text === "function" ? text() : text) || "";
    } catch {
      payload = "";
    }
    if (!payload.trim()) {
      flash("empty");
      return;
    }
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(payload);
      } else if (!legacyCopy(payload)) {
        throw new Error("execCommand copy refused");
      }
      flash("copied");
    } catch {
      flash("failed");
    }
  };

  const label = {
    idle: "Copy",
    copied: "Copied ✓",
    empty: "Nothing to copy",
    failed: "Copy failed",
  }[state];

  return (
    <button
      className="ghost"
      onClick={onCopy}
      title={title}
      // Announce the outcome to screen readers, which otherwise get no signal
      // from a label that changes on its own.
      aria-live="polite"
    >
      {label}
    </button>
  );
}
