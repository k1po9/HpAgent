import { create } from "zustand";
import { useWorkbench } from "./workbench";
import {
  approveInspectorIntent,
  captureInspectorIntent,
  type InspectorIntent,
} from "../components/spatial/inspectorIntent";
type NavigationOptions = { stack: Inspector[]; intent?: InspectorIntent | null };
let pendingNavigation: { route: Route; options: NavigationOptions } | null = null;

export type Screen = "ai" | "workspace" | "tasks";
export type Inspector = {
  kind: "run" | "artifact" | "file" | "task";
  objectId: string;
  versionId?: string;
  tab?:
    | "overview"
    | "outputs"
    | "resources"
    | "advanced"
    | "preview"
    | "details"
    | "versions"
    | "usage";
  origin?: {
    conversationId?: string;
    messageId?: string;
    workId?: string;
    runId?: string;
    originalVersionId?: string;
    sourceRequirementRevision?: number;
    triggerId?: string;
    directoryId?: string;
  };
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
    route.type =
      params.get("type") === "artifact_build"
        ? "general"
        : types.includes(params.get("type") ?? "")
          ? params.get("type")!
          : "all";
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
  const allowedTabs: Record<string, string[]> = {
    task: ["overview", "outputs", "resources", "advanced"],
    run: ["overview", "resources", "advanced"],
    file: ["preview", "details", "versions", "usage"],
    artifact: ["preview", "versions", "details"],
  };
  const tab = params.get("tab");
  if (route.inspector && tab && allowedTabs[route.inspector.kind]?.includes(tab))
    route.inspector.tab = tab as Inspector["tab"];
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
    if (route.inspector.tab) params.set("tab", route.inspector.tab);
  }
  const path = `#/${route.screen}${route.screen === "ai" && route.conversationId ? `/${encodeURIComponent(route.conversationId)}` : ""}`;
  return `${path}${params.size ? `?${params}` : ""}`;
}
interface ShellState {
  pendingRoute: Route | null;
  dirtyResourceEditor: string | null;
  dirtyTaskEditor: string | null;
  dirtyDirectoryEditor: (() => void) | null;
  pendingDirectoryChange: (() => void) | null;
  pendingResourceChange: (() => void) | null;
  requestResourceChange: (action: () => void) => void;
  pendingRouteReason: "attachments" | "resources" | "tasks" | "directory";
  route: Route;
  backStack: Inspector[];
  pages: Partial<Record<Screen, Route>>;
  modal: "account" | "resources" | "artifact-create" | "task-create" | "task-inbox" | null;
  sidebarOpen: boolean;
  expanded: boolean;
  requestToken: number;
  navigate: (route: Route, replace?: boolean, options?: NavigationOptions) => boolean;
  restore: (hash: string, context?: LegacyContext) => void;
  openInspector: (inspector: Inspector, child?: boolean, source?: InspectorIntent) => void;
  setInspectorTab: (tab: Inspector["tab"]) => void;
  closeInspector: () => void;
  back: () => void;
  reset: () => void;
}
export const useShell = create<ShellState>((set, get) => ({
  pendingRoute: null,
  dirtyResourceEditor: null,
  dirtyTaskEditor: null,
  dirtyDirectoryEditor: null,
  pendingDirectoryChange: null,
  pendingResourceChange: null,
  requestResourceChange(action) {
    if (get().dirtyResourceEditor) set({ pendingResourceChange: action });
    else if (get().dirtyDirectoryEditor) set({ pendingDirectoryChange: action });
    else action();
  },
  pendingRouteReason: "attachments",
  route: { screen: "ai" },
  backStack: [],
  pages: {},
  modal: null,
  sidebarOpen: false,
  expanded: false,
  requestToken: 0,
  navigate(route, replace = false, options) {
    options ??= pendingNavigation?.route === route ? pendingNavigation.options : undefined;
    pendingNavigation = options ? { route, options } : null;
    if (get().dirtyTaskEditor) {
      window.history.replaceState(null, "", serializeRoute(get().route));
      set({ pendingRoute: route, pendingRouteReason: "tasks", sidebarOpen: false });
      return false;
    }
    if (get().dirtyResourceEditor) {
      window.history.replaceState(null, "", serializeRoute(get().route));
      set({ pendingRoute: route, pendingRouteReason: "resources", sidebarOpen: false });
      return false;
    }
    if (get().dirtyDirectoryEditor) {
      window.history.replaceState(null, "", serializeRoute(get().route));
      set({ pendingRoute: route, pendingRouteReason: "directory", sidebarOpen: false });
      return false;
    }
    const workbench = useWorkbench.getState();
    if (
      route.screen === "ai" &&
      (route.conversationId ?? null) !== workbench.activeConversationId &&
      workbench.attachments.length
    ) {
      window.history.replaceState(null, "", serializeRoute(get().route));
      set({
        pendingRoute: route,
        pendingRouteReason: "attachments",
        modal: null,
        sidebarOpen: false,
      });
      return false;
    }
    if (workbench.creatingConversation && !workbench.activeConversationId)
      workbench.leaveConversation();
    const hash = serializeRoute(route);
    if (window.location.hash !== hash)
      window.history[replace ? "replaceState" : "pushState"](null, "", hash);
    approveInspectorIntent(options?.intent ?? null);
    pendingNavigation = null;
    set({
      route,
      pendingRoute: null,
      pages: {
        ...get().pages,
        [get().route.screen]: { ...get().route, inspector: undefined, workId: undefined },
      },
      backStack:
        options?.stack ??
        (route.inspector?.kind === get().route.inspector?.kind &&
        route.inspector?.objectId === get().route.inspector?.objectId
          ? get().backStack
          : []),
      sidebarOpen: false,
      modal: null,
      requestToken: get().requestToken + 1,
    });
    return true;
  },
  restore(hash, context) {
    // A guarded popstate restores the visible URL. The paired hashchange must not
    // replace its pending destination with that restored current route.
    if (get().pendingRoute && hash === serializeRoute(get().route)) return;
    const parsed = parseRoute(hash, context);
    get().navigate(parsed.route, true);
    if (get().pendingRoute) return;
    set({ modal: parsed.modal ?? null });
  },
  openInspector(inspector, child = false, source) {
    if (get().dirtyResourceEditor) {
      const captured = source ?? captureInspectorIntent({ ...get().route, inspector }, inspector);
      get().requestResourceChange(() => get().openInspector(inspector, child, captured));
      return;
    }
    const { route, backStack } = get();
    if (JSON.stringify(route.inspector) === JSON.stringify(inspector)) {
      document.getElementById("inspector-title")?.focus();
      return;
    }
    const stack = child && route.inspector ? [...backStack, route.inspector].slice(-5) : [];
    const next = {
      ...route,
      ...(inspector.kind === "task" && route.screen === "tasks"
        ? { workId: inspector.objectId }
        : {}),
      inspector,
    };
    get().navigate(next, false, {
      stack,
      intent: source ? { ...source, route: next } : captureInspectorIntent(next, inspector),
    });
  },
  setInspectorTab(tab) {
    const { route } = get();
    if (!route.inspector || !tab) return;
    const normalized = parseRoute(
      serializeRoute({ ...route, inspector: { ...route.inspector, tab } }),
    ).route.inspector?.tab;
    if (normalized !== tab) return;
    get().navigate({ ...route, inspector: { ...route.inspector, tab } }, true);
  },
  closeInspector() {
    const inspector = get().route.inspector;
    get().navigate({ ...get().route, inspector: undefined, workId: undefined });
    if (get().pendingRoute || !inspector) return;
    const token = get().requestToken;
    queueMicrotask(() => {
      if (get().route.inspector || get().requestToken !== token) return;
      const trigger =
        inspector?.kind === "artifact"
          ? document.getElementById(
              inspector.origin?.triggerId ?? `artifact-open-${inspector.objectId}`,
            )
          : inspector?.kind === "file"
            ? document.getElementById(
                inspector.origin?.triggerId ?? `workspace-node-${inspector.objectId}`,
              )
            : inspector?.kind === "task"
              ? document.getElementById(inspector.origin?.triggerId ?? `task-${inspector.objectId}`)
              : document.getElementById(
                  inspector?.origin?.triggerId ?? `run-open-${inspector?.objectId}`,
                );
      if (trigger?.isConnected && !trigger.closest("[hidden], [inert]"))
        trigger.focus({ preventScroll: true });
      else document.getElementById("canvas-title")?.focus();
    });
  },
  back() {
    const stack = [...get().backStack];
    const inspector = stack.pop();
    if (!inspector) {
      get().closeInspector();
      return;
    }
    get().navigate({ ...get().route, inspector }, false, { stack });
  },
  reset() {
    pendingNavigation = null;
    approveInspectorIntent(null);
    set({
      pendingRoute: null,
      dirtyResourceEditor: null,
      dirtyTaskEditor: null,
      dirtyDirectoryEditor: null,
      pendingDirectoryChange: null,
      pendingResourceChange: null,
      pendingRouteReason: "attachments",
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
