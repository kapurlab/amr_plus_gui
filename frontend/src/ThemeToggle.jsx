import { useEffect, useState } from "react";

const KEY = "bdtools-theme";
const MODES = [
  ["light", "☀", "Light"],
  ["system", "◐", "System"],
  ["dark", "☾", "Dark"],
];

function storedMode() {
  try {
    const value = localStorage.getItem(KEY);
    if (MODES.some(([mode]) => mode === value)) return value;
  } catch {}
  return "system";
}

function applyMode(mode) {
  const dark = mode === "dark"
    || (mode === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.dataset.themeMode = mode;
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}

const initialMode = storedMode();
applyMode(initialMode);

export default function ThemeToggle() {
  const [mode, setMode] = useState(initialMode);

  useEffect(() => {
    applyMode(mode);
    try { localStorage.setItem(KEY, mode); } catch {}
    const media = matchMedia("(prefers-color-scheme: dark)");
    const sync = () => mode === "system" && applyMode("system");
    const syncStorage = (event) => event.key === KEY && setMode(storedMode());
    media.addEventListener?.("change", sync);
    window.addEventListener("storage", syncStorage);
    return () => { media.removeEventListener?.("change", sync); window.removeEventListener("storage", syncStorage); };
  }, [mode]);

  return (
    <div className="theme-switch" role="group" aria-label="Appearance">
      {MODES.map(([value, icon, label]) => (
        <button key={value} type="button" title={`${label} appearance`}
          aria-label={`Use ${label.toLowerCase()} appearance`}
          aria-pressed={mode === value} onClick={() => setMode(value)}>
          <span aria-hidden="true">{icon}</span>
        </button>
      ))}
    </div>
  );
}
