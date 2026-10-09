import { useEffect, useState } from "react";

export function RecoveryStatus({
  state,
  onRetry,
}: {
  state: "checking" | "success" | "error";
  onRetry: () => void;
}) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (state !== "checking") return;
    const timer = setTimeout(() => setSlow(true), 4000);
    return () => clearTimeout(timer);
  }, [state]);
  return (
    <main className="hp-recovery" data-state={state}>
      <div className="hp-recovery-orbit">
        <svg viewBox="0 0 240 240" aria-hidden="true" className="hp-recovery-ring">
          <circle className="hp-recovery-track" cx="120" cy="120" r="112" />
          <circle className="hp-recovery-arc" cx="120" cy="120" r="112" />
        </svg>
        <h1>HpAgent</h1>
        {state === "error" ? (
          <p role="alert">无法连接 HpAgent API</p>
        ) : (
          <p role="status">{state === "success" ? "会话已恢复 ✓" : "正在恢复会话…"}</p>
        )}
      </div>
      <div className="hp-recovery-detail">
        {state === "error" ? (
          <>
            <p>请检查连接后重试，你的工作内容不会因此丢失。</p>
            <button onClick={onRetry}>重试</button>
          </>
        ) : (
          <p>
            {state === "success"
              ? "正在准备工作空间"
              : slow
                ? "连接耗时较长，仍在验证会话…"
                : "正在验证身份，准备你的工作空间"}
          </p>
        )}
      </div>
    </main>
  );
}
