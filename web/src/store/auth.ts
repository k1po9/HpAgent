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
  check: () => Promise<void>;
  markRegistered: () => void;
  dismissRegistrationHint: () => void;
  signOut: () => Promise<void>;
  expire: () => void;
}

let generation = 0;
export const useAuth = create<AuthState>((set) => ({
  status: "checking",
  account: null,
  identities: null,
  capabilities: {},
  justRegistered: false,

  check: async () => {
    const token = ++generation;
    set((state) => (state.status === "signedIn" ? {} : { status: "checking" }));
    try {
      const me = await api.me();
      if (token !== generation) return;
      if (me === null) {
        set({
          status: "signedOut",
          account: null,
          identities: null,
          capabilities: {},
          justRegistered: false,
        });
        return;
      }
      set({
        status: "signedIn",
        account: me.account,
        identities: me.identities,
        capabilities: me.capabilities,
      });
    } catch {
      if (token !== generation) return;
      set({ status: "error", account: null, identities: null, capabilities: {} });
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
    api.reset();
    set({
      status: "signedOut",
      account: null,
      identities: null,
      capabilities: {},
      justRegistered: false,
    });
  },
}));

api.onUnauthorized = () => useAuth.getState().expire();
