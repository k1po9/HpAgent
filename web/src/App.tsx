import { useEffect } from "react";
import { Button } from "@radix-ui/themes";
import { LoginForm } from "./components/LoginForm";
import { useAuth } from "./store/auth";
import { AppShell } from "./components/shell/AppShell";
import "./store/sessionLifecycle";

export function App() {
  const status = useAuth((s) => s.status);
  const accountId = useAuth((s) => s.account?.account_id);
  const check = useAuth((s) => s.check);
  useEffect(() => {
    void check();
  }, [check]);
  if (status === "signedOut")
    return (
      <div className="hp-auth-surface">
        <LoginForm />
      </div>
    );
  if (status === "checking")
    return (
      <main className="hp-auth-surface">
        <div>
          <h1>HpAgent</h1>
          <p role="status">正在恢复会话…</p>
        </div>
      </main>
    );
  if (status === "error")
    return (
      <main className="hp-auth-surface">
        <div>
          <h1>HpAgent</h1>
          <p role="alert">无法连接 HpAgent API</p>
          <Button onClick={() => void check()}>重试</Button>
        </div>
      </main>
    );
  return <AppShell key={accountId} />;
}
