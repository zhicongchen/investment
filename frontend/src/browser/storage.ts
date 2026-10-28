// Browser-only persistence for the GitHub Pages build. Everything lives in this
// browser's localStorage under one prefix, so "clear data" is a prefix sweep.
const PREFIX = 'stock-signals:';

export function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    throw new Error(`Could not save to browser storage (${reason}). Remove some tickers or clear stored data in Settings.`);
  }
}

export function removeKey(key: string): void {
  try {
    localStorage.removeItem(PREFIX + key);
  } catch {
    // Storage unavailable: nothing to remove.
  }
}

export function clearAll(): void {
  try {
    const keys: string[] = [];
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key?.startsWith(PREFIX)) keys.push(key);
    }
    keys.forEach((key) => localStorage.removeItem(key));
  } catch {
    // Storage unavailable: nothing to clear.
  }
}
