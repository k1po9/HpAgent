import { useSyncExternalStore } from "react";

const key = "hpagent.entry-effect";
const change = "hpagent-entry-effect-change";
let fallback = true;
let storageWriteFailed = false;

/** Browser-local presentation preference; never changes the authenticated session. */
export function entryEffectEnabled(): boolean {
  if (storageWriteFailed) return fallback;
  try {
    return localStorage.getItem(key) !== "off";
  } catch {
    return fallback;
  }
}

export function setEntryEffectEnabled(enabled: boolean) {
  fallback = enabled;
  try {
    localStorage.setItem(key, enabled ? "on" : "off");
    storageWriteFailed = false;
  } catch {
    // Keep the choice for this page when storage is restricted.
    storageWriteFailed = true;
  }
  window.dispatchEvent(new Event(change));
}

function subscribe(listener: () => void) {
  const storage = (event: StorageEvent) => {
    if (event.key === key || event.key === null) {
      storageWriteFailed = false;
      listener();
    }
  };
  window.addEventListener(change, listener);
  window.addEventListener("storage", storage);
  return () => {
    window.removeEventListener(change, listener);
    window.removeEventListener("storage", storage);
  };
}

export function useEntryEffectEnabled() {
  return useSyncExternalStore(subscribe, entryEffectEnabled, () => true);
}
