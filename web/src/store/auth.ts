/**
 * Session/auth store (hpagent-web-api-contract.md §4).
 *
 * The browser never holds the session secret; only the HttpOnly cookie is
 * trusted. `check` probes `GET /api/v1/me`, which also seeds the CSRF token.
 * An explicit `expire()` clears the local projection after `auth.expired` or a
 * 401 so the workbench stops mutating and the login gate reappears.
 */
import { create } from "zustand";
import { api, type MeResponse } from "../api/client";

export type AuthStatus = "checking" | "signedIn" | "signedOut" | "error";

interface AuthState {
  status: AuthStatus;
  account: MeResponse["account"] | null;
  identities: MeResponse["identities"] | null;
  capabilities: MeResponse["capabilities"];
  justRegistered: boolean;
  assemblyPending: boolean;
  entrySource: "login" | "restore";
  check: (explicit?: boolean) => Promise<void>;
  markRegistered: () => void;
  dismissRegistrationHint: () => void;
  signOut: () => Promise<void>;
  expire: () => void;
}

let generation = 0;
let sessionProbe: AbortController | null = null;
export const useAuth = create<AuthState>((set, get) => ({
  status: "checking",
  account: null,
  identities: null,
  capabilities: {},
  justRegistered: false,
  assemblyPending: false,
  entrySource: "restore",

  check: async (explicit = false) => {
    const previous = get();
    const token = ++generation;
    sessionProbe?.abort();
    const controller = new AbortController();
    sessionProbe = controller;
    const timeout = setTimeout(() => controller.abort(), 12000);
    set((state) =>
      state.status === "signedIn"
        ? {}
        : { status: "checking", entrySource: explicit ? "login" : "restore" },
    );
    try {
      const me = await api.me(controller.signal);
      if (token !== generation) return;
      if (me === null) {
        set({
          status: "signedOut",
          account: null,
          identities: null,
          capabilities: {},
          justRegistered: false,
          assemblyPending: false,
        });
        return;
      }
      set({
        status: "signedIn",
        account: me.account,
        identities: me.identities,
        capabilities: me.capabilities,
        ...(previous.status !== "signedIn" || previous.account?.account_id !== me.account.account_id
          ? {
              assemblyPending: true,
              entrySource: explicit ? ("login" as const) : ("restore" as const),
            }
          : {}),
      });
    } catch {
      if (token !== generation) return;
      set({
        status: "error",
        account: null,
        identities: null,
        capabilities: {},
        assemblyPending: false,
      });
    } finally {
      clearTimeout(timeout);
      if (sessionProbe === controller) sessionProbe = null;
    }
  },

  markRegistered: () => set({ justRegistered: true }),

  dismissRegistrationHint: () => set({ justRegistered: false }),

  signOut: async () => {
    const request = api.logout();
    useAuth.getState().expire();
    await request;
  },

  expire: () => {
    generation += 1;
    sessionProbe?.abort();
    sessionProbe = null;
    api.reset();
    set({
      status: "signedOut",
      account: null,
      identities: null,
      capabilities: {},
      justRegistered: false,
      assemblyPending: false,
    });
  },
}));

api.onUnauthorized = () => useAuth.getState().expire();
