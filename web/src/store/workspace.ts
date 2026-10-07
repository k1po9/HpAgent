import { create } from "zustand";
import { api } from "../api/client";
import { HpApi } from "../api/resources";
import type { HpWorkspace, HpWorkspaceFilters, HpWorkspaceSearchPage } from "../api/types";

export const workspaceApi = new HpApi(api);
type Entry = { value?: unknown; error?: string; loading: boolean };
interface WorkspaceState {
  generation: number;
  revision: number;
  cache: Record<string, Entry>;
  tree: HpWorkspace | null;
  error: string;
  loading: boolean;
  search: HpWorkspaceSearchPage | null;
  filters: HpWorkspaceFilters;
  searchError: string;
  searching: boolean;
  searchStale: boolean;
  notice: string;
  searchMode: boolean;
  allFiles: boolean;
  expanded: Record<string, boolean>;
  query: <T>(key: string, load: () => Promise<T>, force?: boolean) => Promise<T>;
  loadTree: (force?: boolean) => Promise<HpWorkspace>;
  searchFiles: (filters?: HpWorkspaceFilters, more?: boolean) => Promise<void>;
  invalidate: () => void;
  invalidateQueries: () => void;
  reset: () => void;
}
const flights = new Map<string, Promise<unknown>>();
const owners = new Map<string, symbol>();
let searchToken = 0;
export const useWorkspace = create<WorkspaceState>((set, get) => ({
  generation: 0,
  revision: 0,
  cache: {},
  tree: null,
  error: "",
  loading: false,
  search: null,
  filters: {},
  searchError: "",
  searching: false,
  searchStale: false,
  searchMode: false,
  notice: "",
  allFiles: false,
  expanded: {},
  async query<T>(key: string, load: () => Promise<T>, force = false): Promise<T> {
    const generation = get().generation;
    const existing = get().cache[key];
    if (!force && flights.has(key)) return flights.get(key) as Promise<T>;
    if (!force && existing?.value !== undefined) return existing.value as T;
    const token = Symbol(key);
    owners.set(key, token);
    const promise = (async () => {
      try {
        const value = await Promise.resolve().then(load);
        if (get().generation !== generation || owners.get(key) !== token)
          throw new DOMException("Stale query", "AbortError");
        set({ cache: { ...get().cache, [key]: { value, loading: false } } });
        return value;
      } catch (error) {
        if (get().generation === generation && owners.get(key) === token)
          set({
            cache: {
              ...get().cache,
              [key]: { loading: false, error: error instanceof Error ? error.message : "查询失败" },
            },
          });
        throw error;
      } finally {
        if (owners.get(key) === token) {
          flights.delete(key);
          owners.delete(key);
        }
      }
    })();
    flights.set(key, promise);
    set({ cache: { ...get().cache, [key]: { ...existing, loading: true } } });
    return promise;
  },
  async loadTree(force = false) {
    const generation = get().generation;
    set({ loading: true, error: "" });
    try {
      const tree = await get().query("tree", () => workspaceApi.getWorkspace(), force);
      if (get().generation === generation) set({ tree, loading: false });
      return tree;
    } catch (error) {
      if (
        get().generation === generation &&
        !(error instanceof DOMException && error.name === "AbortError")
      )
        set({ error: "空间加载失败，请重试。", loading: false });
      throw error;
    }
  },
  async searchFiles(filters, more = false) {
    if (more && (get().searching || get().searchStale || !get().search?.next_after)) return;
    const token = ++searchToken;
    const generation = get().generation;
    const applied = more ? get().filters : { ...filters };
    const previous = more ? get().search : null;
    set({
      filters: applied,
      searching: true,
      searchError: "",
      ...(more ? {} : { search: null, searchStale: false }),
    });
    try {
      const page = await workspaceApi.searchWorkspace(applied, previous?.next_after);
      if (token !== searchToken || generation !== get().generation) return;
      const unique = new Map(
        [...(previous?.items ?? []), ...page.items].map((item) => [item.node_id, item]),
      );
      set({ search: { ...page, items: [...unique.values()] }, searching: false });
      if (page.items.some((item) => !get().tree?.nodes.some((n) => n.node_id === item.node_id)))
        void get()
          .loadTree(true)
          .catch(() => {});
    } catch {
      if (token === searchToken && generation === get().generation)
        set({ searchError: "搜索失败，可重试。", searching: false });
    }
  },
  invalidateQueries() {
    owners.clear();
    flights.clear();
    set({ cache: {}, revision: get().revision + 1 });
  },
  invalidate() {
    searchToken++;
    owners.clear();
    flights.clear();
    set({
      cache: {},
      revision: get().revision + 1,
      searchStale: Boolean(get().search),
      searching: false,
    });
    void get()
      .loadTree(true)
      .catch(() => {});
  },
  reset() {
    searchToken++;
    owners.clear();
    flights.clear();
    set({
      generation: get().generation + 1,
      revision: 0,
      cache: {},
      tree: null,
      error: "",
      loading: false,
      search: null,
      filters: {},
      searchError: "",
      searching: false,
      searchStale: false,
      searchMode: false,
      notice: "",
      allFiles: false,
      expanded: {},
    });
  },
}));
