import { useCallback, useEffect, useRef } from "react";
import { LoginForm } from "./components/LoginForm";
import { useAuth } from "./store/auth";
import { AppShell } from "./components/shell/AppShell";
import { RecoveryStatus } from "./components/entry/RecoveryStatus";
import { EntryTransition } from "./components/entry/EntryTransition";
import { EntryPendingContext } from "./components/entry/EntryPendingContext";
import "./store/sessionLifecycle";

export function App() {
  const status = useAuth((s) => s.status);
  const accountId = useAuth((s) => s.account?.account_id);
  const pending = useAuth((s) => s.assemblyPending);
  const entrySource = useAuth((s) => s.entrySource);
  const check = useAuth((s) => s.check);
  const origin = useRef<HTMLDivElement>(null);
  const destination = useRef<HTMLDivElement>(null);
  const signedIn = status === "signedIn";
  const entering = signedIn && pending;
  const source = status === "signedOut" ? "login" : entrySource;
  const complete = useCallback(() => {
    if (useAuth.getState().account?.account_id === accountId)
      useAuth.setState({ assemblyPending: false });
  }, [accountId]);
  useEffect(() => {
    void check();
  }, [check]);
  useEffect(() => {
    if (!signedIn || entering) return;
    const frame = requestAnimationFrame(() => {
      // A restored modal owns focus; otherwise focus the current page heading.
      if (!document.querySelector('dialog[open][aria-modal="true"]'))
        document.getElementById("canvas-title")?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [signedIn, entering, accountId]);
  return (
    <div className="hp-entry" data-entering={entering} data-entry-source={source}>
      {signedIn && (
        <div
          key="destination"
          className="hp-entry-destination"
          ref={destination}
          inert={entering}
          aria-hidden={entering || undefined}
        >
          <EntryPendingContext value={entering}>
            <AppShell key={accountId} />
          </EntryPendingContext>
        </div>
      )}
      {(!signedIn || entering) && (
        <div key="origin" className="hp-entry-origin" ref={origin} inert={entering}>
          {status === "error" || source === "restore" ? (
            <RecoveryStatus
              key={status === "error" ? "error" : "recovery"}
              state={status === "error" ? "error" : signedIn ? "success" : "checking"}
              onRetry={() => void check()}
            />
          ) : (
            <div className="hp-auth-surface">
              <LoginForm confirmed={entering} />
            </div>
          )}
        </div>
      )}
      {entering && (
        <span className="hp-entry-announcement" role="status">
          {source === "restore" ? "会话已恢复，正在进入工作空间" : "身份已确认，正在进入工作空间"}
        </span>
      )}
      {entering && (
        <EntryTransition
          key={accountId}
          origin={origin}
          destination={destination}
          source={source}
          onComplete={complete}
        />
      )}
    </div>
  );
}
