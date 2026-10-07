import { useEffect } from "react";
import { create } from "zustand";
import { api } from "../../api/client";
import { HpCommandError } from "../../api/types";
import { useWorks } from "../../store/works";
import { commandError } from "../../utils/commands";
type Revision = string | number | undefined;
type Entry = { value?: unknown; error?: string; loading: boolean; revision?: Revision };
const queries = create<{ entries: Record<string, Entry> }>(() => ({ entries: {} }));
const pins = new Map<string, number>();
let activeReads = 0;
const readQueue: Array<() => void> = [];
function boundedGet(path: string, signal: AbortSignal) {
  return new Promise<unknown>((resolve, reject) => {
    const start = () => {
      if (signal.aborted) {
        reject(new DOMException("Cancelled", "AbortError"));
        readQueue.shift()?.();
        return;
      }
      activeReads++;
      void api
        .request({ method: "GET", path, signal })
        .then(resolve, reject)
        .finally(() => {
          activeReads--;
          readQueue.shift()?.();
        });
    };
    if (activeReads < 3) start();
    else readQueue.push(start);
  });
}
const flights = new Map<
  string,
  { promise: Promise<void>; abort: AbortController; revision: Revision }
>();
export function resetTaskQueries() {
  flights.forEach((f) => f.abort.abort());
  flights.clear();
  queries.setState({ entries: {} });
}
async function query(key: string, path: string, revision: Revision, force = false) {
  const previous = flights.get(key);
  if (previous && previous.revision === revision) return previous.promise;
  // A newer snapshot supersedes this read, including a request still in the queue.
  if (previous) {
    previous.abort.abort();
    flights.delete(key);
  }
  const cached = queries.getState().entries[key];
  if (!force && cached?.value !== undefined && cached.revision === revision) return;
  const generation = useWorks.getState().generation;
  const abort = new AbortController();
  const valid = () =>
    generation === useWorks.getState().generation && flights.get(key)?.abort === abort;
  queries.setState((s) => ({
    entries: { ...s.entries, [key]: { ...s.entries[key], loading: true, error: undefined } },
  }));
  const promise = (async () => {
    try {
      const value = await boundedGet(path, abort.signal);
      if (valid())
        queries.setState((s) => {
          const entries = { ...s.entries, [key]: { value, loading: false, revision } };
          for (const old of Object.keys(entries).slice(
            0,
            Math.max(0, Object.keys(entries).length - 80),
          ))
            if (old !== key && !flights.has(old) && !pins.has(old)) delete entries[old];
          return { entries };
        });
    } catch (e) {
      if (valid())
        queries.setState((s) => ({
          entries: {
            ...s.entries,
            [key]: {
              ...(e instanceof HpCommandError && [403, 404].includes(e.status)
                ? {}
                : s.entries[key]),
              error:
                e instanceof HpCommandError && [403, 404].includes(e.status)
                  ? "对象不可用。"
                  : commandError(e),
              loading: false,
            },
          },
        }));
    } finally {
      if (valid()) flights.delete(key);
    }
  })();
  flights.set(key, { promise, abort, revision });
  return promise;
}
export function useTaskQuery<T>(key: string | null, path: string, revision?: Revision) {
  const generation = useWorks((s) => s.generation);
  const entry = queries((s) => (key ? s.entries[key] : undefined));
  useEffect(() => {
    if (!key) return;
    pins.set(key, (pins.get(key) ?? 0) + 1);
    void query(key, path, revision);
    return () => {
      const count = (pins.get(key) ?? 1) - 1;
      if (count) pins.set(key, count);
      else pins.delete(key);
    };
  }, [key, path, generation, revision]);
  return {
    data: entry?.value as T | undefined,
    error: entry?.error,
    loading: Boolean(key && (!entry || entry.loading)),
    retry: () => {
      if (key) void query(key, path, revision, true);
    },
  };
}
