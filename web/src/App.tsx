import { useCallback, useEffect, useRef, useState } from "react";
import { LoginForm } from "./components/LoginForm";
import { useAuth } from "./store/auth";
import { AppShell } from "./components/shell/AppShell";
import { RecoveryStatus } from "./components/entry/RecoveryStatus";
import { EntryTransition } from "./components/entry/EntryTransition";
import { EntryPendingContext } from "./components/entry/EntryPendingContext";
import { entryEffectEnabled } from "./components/entry/entryEffectPreference";
import { RestoreIdentity } from "./components/entry/RestoreIdentity";
import "./store/sessionLifecycle";

export function App() {
  const status = useAuth((s) => s.status);
  const accountId = useAuth((s) => s.account?.account_id);
  const pending = useAuth((s) => s.assemblyPending);
  const entrySource = useAuth((s) => s.entrySource);
  const check = useAuth((s) => s.check);
  const username = useAuth((s) => s.identities?.web?.username);
  // Capture on page mount: changing the setting never replays the current entry.
  const [entryEffect] = useState(entryEffectEnabled);
  const [introReady, setIntroReady] = useState(false);
  const finishIntro = useCallback(() => setIntroReady(true), []);
  const origin = useRef<HTMLDivElement>(null);
  const destination = useRef<HTMLDivElement>(null);
  const signedIn = status === "signedIn";
  const entering = signedIn && pending;
  const source = status === "signedOut" ? "login" : entrySource;
  const restoreIntro = entryEffect && source === "restore" && status !== "error";
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
    <div
      className="hp-entry"
      data-entering={entering}
      data-entry-source={source}
      data-intro-wait={entering && restoreIntro && !introReady}
    >
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
        <div
          key="origin"
          className="hp-entry-origin"
          data-theme="rhine"
          ref={origin}
          inert={entering && (!restoreIntro || introReady)}
        >
          {restoreIntro ? (
            <RestoreIdentity
              verified={signedIn}
              confirmed={entering && introReady}
              username={signedIn ? username : undefined}
              onReady={finishIntro}
            />
          ) : status === "error" || source === "restore" ? (
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
      {entering && (!restoreIntro || introReady) && (
        <EntryTransition
          key={accountId}
          origin={origin}
          destination={destination}
          source={restoreIntro ? "login" : source}
          onComplete={complete}
        />
      )}
    </div>
  );
}
