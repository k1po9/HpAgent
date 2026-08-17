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

export type AuthStatus = "checking" | "signedIn" | "signedOut";

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

export const useAuth = create<AuthState>((set) => ({
  status: "checking",
  account: null,
  identities: null,
  capabilities: {},
  justRegistered: false,

  check: async () => {
    const me = await api.me();
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
  },

  markRegistered: () => set({ justRegistered: true }),

  dismissRegistrationHint: () => set({ justRegistered: false }),

  signOut: async () => {
    await api.logout();
    api.reset();
    set({
      status: "signedOut",
      account: null,
      identities: null,
      capabilities: {},
      justRegistered: false,
    });
  },

  expire: () => {
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
