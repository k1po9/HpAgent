import { useEffect, useId, useRef, useState } from "react";
import { runApi } from "../../store/runInspector";
import { useShell } from "../../store/shell";
import { useAuth } from "../../store/auth";
import { HpCommandError } from "../../api/types";
import { getForegroundRevision } from "../shell/foreground";

/** A verified snapshot supplies context; Work runs never enter the Chat projection. */
export function RunLookup() {
  const id = useId();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);
  const alive = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      // Invalidate diagnostic requests when this view closes.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      request.current++;
    };
  }, []);
  useEffect(
    () =>
      useShell.subscribe((state, previous) => {
        if (
          state.modal !== previous.modal ||
          state.sidebarOpen !== previous.sidebarOpen ||
          state.pendingRoute !== previous.pendingRoute ||
          state.pendingResourceChange !== previous.pendingResourceChange
        ) {
          request.current++;
          setBusy(false);
        }
      }),
    [],
  );
  return (
    <details className="hp-run-lookup">
      <summary>按执行编号查询</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const runId = value.trim();
          if (!runId) return;
          const token = ++request.current;
          const boundary = useShell.getState().requestToken;
          const account = useAuth.getState().account;
          const foreground = getForegroundRevision();
          const current = () =>
            alive.current &&
            token === request.current &&
            boundary === useShell.getState().requestToken &&
            foreground === getForegroundRevision() &&
            account === useAuth.getState().account;
          setBusy(true);
          setError(null);
          void runApi
            .getRun(runId)
            .then((snapshot) => {
              if (!current()) return;
              if (snapshot.run.run_id !== runId) {
                setError("对象不可用。");
                return;
              }
              useShell.getState().openInspector(
                {
                  kind: "run",
                  objectId: runId,
                  origin: {
                    triggerId: id,
                    ...(snapshot.source_kind === "chat"
                      ? { conversationId: snapshot.run.conversation_id }
                      : { workId: snapshot.run.work_id }),
                  },
                },
                useShell.getState().route.inspector?.kind === "task",
              );
            })
            .catch((err: unknown) => {
              if (current())
                setError(
                  err instanceof HpCommandError && [403, 404].includes(err.status)
                    ? "对象不可用。"
                    : "执行查询失败，请重试。",
                );
            })
            .finally(() => {
              if (alive.current && token === request.current) setBusy(false);
            });
        }}
      >
        <label htmlFor={id}>执行编号</label>
        <input
          id={id}
          required
          value={value}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${id}-error` : undefined}
          onChange={(e) => {
            request.current++;
            setBusy(false);
            setError(null);
            setValue(e.target.value);
          }}
        />
        <button disabled={busy}>{busy ? "查询中…" : "查询"}</button>
        {error && (
          <p id={`${id}-error`} role="alert">
            {error}
          </p>
        )}
      </form>
    </details>
  );
}
