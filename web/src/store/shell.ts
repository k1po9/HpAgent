import { create } from "zustand";

export type Screen = "ai" | "workspace" | "tasks";
export type Inspector = {
  kind: "run" | "artifact" | "file" | "task";
  objectId: string;
  versionId?: string;
};
export type Route = {
  screen: Screen;
  conversationId?: string;
  directoryId?: string;
  bucket?: "attention" | "active" | "waiting" | "ended";
  type?: string;
  workId?: string;
  inspector?: Inspector;
};
export type LegacyContext = { conversationId?: string; artifactId?: string; runId?: string };
const buckets = ["attention", "active", "waiting", "ended"];
const types = ["all", "research", "artifact_build", "reminder", "general"];
const id = (value: string | null) =>
  value && /^[a-zA-Z0-9_~-][a-zA-Z0-9._~-]{0,255}$/.test(value) ? value : undefined;

export function parseRoute(
  hash: string,
  context: LegacyContext = {},
): { route: Route; modal?: "account" | "resources" } {
  const legacy = hash.replace(/^#/, "");
  if (!legacy.startsWith("/")) {
    if (legacy === "files") return { route: { screen: "workspace" } };
    if (legacy === "works" || legacy === "research")
      return {
        route: {
          screen: "tasks",
          bucket: "attention",
          type: legacy === "research" ? "research" : "all",
        },
      };
    const route: Route = { screen: "ai", conversationId: context.conversationId };
    if (legacy === "artifacts" && context.artifactId)
      route.inspector = { kind: "artifact", objectId: context.artifactId };
    if (legacy === "diagnostics" && context.runId)
      route.inspector = { kind: "run", objectId: context.runId };
    return {
      route,
      modal:
        legacy === "account"
          ? "account"
          : legacy === "authority" && context.conversationId
            ? "resources"
            : undefined,
    };
  }
  const [path, query] = legacy.split("?");
  const params = new URLSearchParams(query);
  const parts = path!.split("/");
  const screen: Screen = parts[1] === "workspace" || parts[1] === "tasks" ? parts[1] : "ai";
  const route: Route = { screen };
  if (parts[1] === "ai" && parts[2]) {
    try {
      route.conversationId = id(decodeURIComponent(parts[2]));
    } catch {
      /* normalize malformed paths */
    }
  }
  if (screen === "workspace") route.directoryId = id(params.get("dir"));
  if (screen === "tasks") {
    route.bucket = (
      buckets.includes(params.get("bucket") ?? "") ? params.get("bucket") : "attention"
    ) as Route["bucket"];
    route.type = types.includes(params.get("type") ?? "") ? params.get("type")! : "all";
    route.workId = id(params.get("work"));
  }
  const inspect = params.get("inspect") ?? "";
  const colon = inspect.indexOf(":");
  const kind = inspect.slice(0, colon);
  const objectId = id(inspect.slice(colon + 1));
  if (["run", "artifact", "file", "task"].includes(kind) && objectId) {
    route.inspector = { kind: kind as Inspector["kind"], objectId };
    if (kind === "artifact") route.inspector.versionId = id(params.get("version"));
  }
  if (route.workId && !route.inspector) route.inspector = { kind: "task", objectId: route.workId };
  return { route };
}

export function serializeRoute(route: Route): string {
  const params = new URLSearchParams();
  if (route.screen === "workspace" && route.directoryId) params.set("dir", route.directoryId);
  if (route.screen === "tasks") {
    params.set("bucket", route.bucket ?? "attention");
    params.set("type", route.type ?? "all");
    if (route.workId) params.set("work", route.workId);
  }
  if (route.inspector) {
    params.set("inspect", `${route.inspector.kind}:${route.inspector.objectId}`);
    if (route.inspector.versionId) params.set("version", route.inspector.versionId);
  }
  const path = `#/${route.screen}${route.screen === "ai" && route.conversationId ? `/${encodeURIComponent(route.conversationId)}` : ""}`;
  return `${path}${params.size ? `?${params}` : ""}`;
}
interface ShellState {
  route: Route;
  backStack: Inspector[];
  pages: Partial<Record<Screen, Route>>;
  modal: "account" | "resources" | "artifact-create" | null;
  sidebarOpen: boolean;
  expanded: boolean;
  requestToken: number;
  navigate: (route: Route, replace?: boolean) => void;
  restore: (hash: string, context?: LegacyContext) => void;
  openInspector: (inspector: Inspector, child?: boolean) => void;
  closeInspector: () => void;
  back: () => void;
  reset: () => void;
}
export const useShell = create<ShellState>((set, get) => ({
  route: { screen: "ai" },
  backStack: [],
  pages: {},
  modal: null,
  sidebarOpen: false,
  expanded: false,
  requestToken: 0,
  navigate(route, replace = false) {
    const hash = serializeRoute(route);
    if (window.location.hash !== hash)
      window.history[replace ? "replaceState" : "pushState"](null, "", hash);
    set({
      route,
      pages: {
        ...get().pages,
        [get().route.screen]: { ...get().route, inspector: undefined, workId: undefined },
      },
      backStack: [],
      sidebarOpen: false,
      modal: null,
      requestToken: get().requestToken + 1,
    });
  },
  restore(hash, context) {
    const parsed = parseRoute(hash, context);
    get().navigate(parsed.route, true);
    set({ modal: parsed.modal ?? null });
  },
  openInspector(inspector, child = false) {
    const { route, backStack } = get();
    if (JSON.stringify(route.inspector) === JSON.stringify(inspector)) {
      document.getElementById("inspector-title")?.focus();
      return;
    }
    const stack = child && route.inspector ? [...backStack, route.inspector].slice(-5) : [];
    get().navigate({ ...route, inspector });
    set({ backStack: stack });
  },
  closeInspector() {
    get().navigate({ ...get().route, inspector: undefined, workId: undefined });
  },
  back() {
    const stack = [...get().backStack];
    const inspector = stack.pop();
    if (!inspector) {
      get().closeInspector();
      return;
    }
    get().navigate({ ...get().route, inspector });
    set({ backStack: stack });
  },
  reset() {
    set({
      route: { screen: "ai" },
      pages: {},
      backStack: [],
      modal: null,
      sidebarOpen: false,
      expanded: false,
      requestToken: get().requestToken + 1,
    });
  },
}));
