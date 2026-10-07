import { useEffect } from "react";
import { useWorkspace } from "../../store/workspace";
/** Every mounted consumer observes the same keyed, account-scoped cache entry. */
export function useWorkspaceQuery<T>(key: string | null, load: () => Promise<T>) {
  const revision = useWorkspace((s) => s.revision);
  const generation = useWorkspace((s) => s.generation);
  const entry = useWorkspace((s) => (key ? s.cache[key] : undefined));
  useEffect(() => {
    if (!key) return;
    // Joining a flight is ordinary subscription, including StrictMode and A → B → A.
    // Replaced requests must not publish their AbortError into a surviving view.
    void useWorkspace
      .getState()
      .query(key, load)
      .catch(() => {});
    // The explicit key defines the load closure's identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, revision, generation]);
  return {
    data: entry?.value as T | undefined,
    error: entry?.error,
    loading: Boolean(key && (!entry || entry.loading)),
    retry: () => {
      if (key)
        void useWorkspace
          .getState()
          .query(key, load, true)
          .catch(() => {});
    },
  };
}
