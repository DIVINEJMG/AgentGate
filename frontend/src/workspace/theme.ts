import { useEffect, useState } from 'react';

export type ThemeChoice = 'system' | 'light' | 'dark';
const KEY = 'audoryn.workspace.theme';

export function storedTheme(): ThemeChoice {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === 'light' || value === 'dark' ? value : 'system';
  } catch { return 'system'; }
}

function apply(choice: ThemeChoice) {
  const root = document.documentElement;
  if (choice === 'system') root.removeAttribute('data-ws-theme'); else root.setAttribute('data-ws-theme', choice);
}

/** Workspace appearance. The pre-paint script in index.html applies the stored choice before React loads. */
export function useTheme() {
  const [theme, setTheme] = useState<ThemeChoice>(storedTheme);
  useEffect(() => { apply(theme); }, [theme]);
  function choose(next: ThemeChoice) {
    setTheme(next);
    try { if (next === 'system') window.localStorage.removeItem(KEY); else window.localStorage.setItem(KEY, next); } catch { /* The choice still applies to this session. */ }
  }
  return { theme, choose };
}
