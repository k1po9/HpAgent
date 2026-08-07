/**
 * API client (hpagent-web-api-contract.md §3, §6–§8, §10, §11).
 *
 * - Same-origin HttpOnly Cookie; the CSRF token comes from `GET /api/v1/me`.
 * - Every modifying request carries `X-CSRF-Token` and an `Idempotency-Key`
 *   (one key per user intent; network retries reuse it).
 * - Recovery by stable error code: `csrf_invalid` refreshes the token exactly
 *   once via `GET /me` then retries; `conversation_busy` surfaces the active
 *   Run; `idempotency_conflict` never overwrites. The client never submits an
 *   `account_id` and never auto-rotates a caller-supplied Idempotency-Key.
 */
import { HpCommandError, type HpErrorDetail } from "./types";

export interface MeResponse {
  account: {
    account_id: string;
    status: string;
    created_at: string;
  };
  session: {
    expires_at: string;
    idle_expires_at: string;
  };
  csrf_token: string;
  capabilities: Record<string, boolean>;
}

export interface ApiRequestInit {
  method: "GET" | "POST" | "PATCH" | "DELETE";
  path: string;
  body?: unknown;
  idempotencyKey?: string;
  signal?: AbortSignal;
  /** Extra headers (e.g. `If-Match` for conditional writes). */
  headers?: Record<string, string>;
  /** When true, a 401/403 no longer triggers token refresh (auth is gone). */
  sensitive?: boolean;
}

export interface HpApiTransport {
  fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response>;
}

const CSRF_ERROR_CODES = new Set(["csrf_invalid"]);

export class ApiClient {
  private csrfToken: string | null = null;
  private readonly fetchImpl: HpApiTransport["fetch"];

  constructor(fetchImpl?: HpApiTransport["fetch"]) {
    // Resolve `globalThis.fetch` at call time (not construction) so tests and
    // a Vite HMR reload can swap it; an arrow call preserves the browser's
    // same-origin `this` without binding a stale function.
    this.fetchImpl =
      fetchImpl ?? ((input, init) => globalThis.fetch(input as RequestInfo | URL, init));
  }

  /** Reset the cached CSRF token (logout, session expiry). */
  reset(): void {
    this.csrfToken = null;
  }

  async me(signal?: AbortSignal): Promise<MeResponse | null> {
    const response = await this.fetchImpl("/api/v1/me", {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      signal,
    });
    if (response.status === 401) {
      this.csrfToken = null;
      return null;
    }
    if (!response.ok) {
      throw await this.toError(response);
    }
    const body = (await response.json()) as MeResponse;
    this.csrfToken = body.csrf_token;
    return body;
  }

  async login(username: string, password: string): Promise<boolean> {
    const response = await this.fetchImpl("/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ username, password, return_to: "/" }),
      redirect: "manual",
    });
    if (response.status !== 303) {
      // A 401 carries the stable JSON error envelope; surface it as a typed
      // error so the login form can show the message.
      if (response.status === 401 || response.status === 422) {
        throw await this.toError(response);
      }
      return false;
    }
    // The 303 Set-Cookie has been stored by the browser; /me seeds the CSRF
    // token and confirms the session.
    return (await this.me()) !== null;
  }

  async logout(): Promise<void> {
    const response = await this.fetchImpl("/api/v1/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: this.mutationHeaders(),
    });
    this.csrfToken = null;
    if (response.status !== 204) {
      throw await this.toError(response);
    }
  }

  async request<T>(init: ApiRequestInit): Promise<T> {
    const headers = this.mutationHeaders();
    if (init.body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    for (const [name, value] of Object.entries(init.headers ?? {})) {
      headers[name] = value;
    }
    const response = await this.fetchImpl(init.path, {
      method: init.method,
      credentials: "same-origin",
      headers,
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: init.signal,
    });
    return this.resolve<T>(response, init, 0);
  }

  private mutationHeaders(): Record<string, string> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (this.csrfToken) {
      headers["X-CSRF-Token"] = this.csrfToken;
    }
    return headers;
  }

  private async resolve<T>(
    response: Response,
    init: ApiRequestInit,
    csrfRetryCount: number,
  ): Promise<T> {
    if (response.ok) {
      const idempotencyReplayed = response.headers.get("idempotency-replayed") === "true";
      if (response.status === 204) {
        return undefined as T;
      }
      const body = (await response.json()) as T;
      return { ...body, __idempotencyReplayed: idempotencyReplayed } as T;
    }

    // CSRF token rotation: refresh from /me exactly once, then replay the same
    // request with the same Idempotency-Key (the intent is unchanged).
    if (csrfRetryCount === 0 && !init.sensitive && (await this.isCsrfInvalid(response))) {
      const me = await this.me();
      if (me !== null) {
        const headers = this.mutationHeaders();
        if (init.body !== undefined) {
          headers["Content-Type"] = "application/json";
        }
        for (const [name, value] of Object.entries(init.headers ?? {})) {
          headers[name] = value;
        }
        const retried = await this.fetchImpl(init.path, {
          method: init.method,
          credentials: "same-origin",
          headers,
          body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
          signal: init.signal,
        });
        return this.resolve<T>(retried, init, 1);
      }
    }

    throw await this.toError(response);
  }

  private async isCsrfInvalid(response: Response): Promise<boolean> {
    if (response.status !== 403) {
      return false;
    }
    try {
      const body = (await response.clone().json()) as { error?: HpErrorDetail };
      return CSRF_ERROR_CODES.has(body?.error?.code ?? "");
    } catch {
      return false;
    }
  }

  private async toError(response: Response): Promise<HpCommandError> {
    let error: HpErrorDetail = {
      code: "service_unavailable",
      message: "服务暂时不可用，请稍后重试。",
      request_id: null,
      retryable: true,
      details: {},
    };
    try {
      const body = (await response.json()) as { error?: HpErrorDetail };
      if (body?.error) {
        error = body.error;
      }
    } catch {
      // Non-JSON error body: keep the safe default.
    }
    return new HpCommandError(
      response.status,
      error,
      response.headers.get("idempotency-replayed") === "true",
    );
  }
}

export const api = new ApiClient();
