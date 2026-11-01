import { useCallback, useEffect, useRef, useState } from "react";
import { IdentityTerminal } from "../identity/IdentityTerminal";
import { IdentityPanelFile } from "../identity/IdentityPanel";
import type { IdentitySceneHandle, SceneStatus } from "../identity/IdentityScene";
import { useIdentityCanvas } from "../identity/identityLayout";
import { useClientLocation } from "../identity/useClientLocation";
import { useMotionAllowed } from "../motion/preference";

const ignoreDenied = () => {};

/** Restores the existing session: no credential form and no authentication side effects. */
export function RestoreIdentity({
  verified,
  confirmed,
  username = "",
  onReady,
}: {
  verified: boolean;
  confirmed: boolean;
  username?: string;
  onReady: () => void;
}) {
  const sceneRef = useRef<IdentitySceneHandle>(null);
  const [scene, setScene] = useState<SceneStatus>("loading");
  const finished = useRef(false);
  const motion = useMotionAllowed();
  const canvas = useIdentityCanvas();
  const location = useClientLocation(canvas);
  const phase = confirmed ? "confirmed" : "verifying";
  const finish = useCallback(() => {
    if (!verified || finished.current) return;
    finished.current = true;
    onReady();
  }, [verified, onReady]);
  useEffect(() => {
    if (!verified) return;
    if (!motion || scene === "fallback") {
      finish();
      return;
    }
    if (scene !== "ready") return;
    const timer = setTimeout(finish, 1800);
    return () => clearTimeout(timer);
  }, [verified, motion, scene, finish]);
  useEffect(() => {
    if (!verified) return;
    // A slow chunk or missing GPU frames must never strand a valid session.
    const watchdog = setTimeout(finish, 5000);
    return () => clearTimeout(watchdog);
  }, [verified, finish]);
  return (
    <div className="hp-auth-surface">
      <IdentityTerminal
        phase={phase}
        rejection={0}
        mode="login"
        typed={!!username}
        location={location}
        denied={false}
        onDenied={ignoreDenied}
        sceneRef={sceneRef}
        onSceneStatus={setScene}
      >
        <div className="hp-auth-card hp-restore-card">
          <IdentityPanelFile
            phase={phase}
            mode="login"
            username={username}
            capsLock={false}
            keyEntered={false}
            rejection={0}
            denied={false}
            location={location}
            title="HpAgent 会话恢复"
          />
          <section className="hp-identity-auth hp-restore-status" aria-label="会话恢复">
            <div className="authhead">
              <span>SESSION RESTORE</span>
            </div>
            <p role="status">{verified ? "会话已恢复，正在进入工作空间" : "正在恢复会话…"}</p>
            <p>保留登录状态，无需重新输入密码。</p>
            <button type="button" className="hp-auth-submit" disabled={!verified} onClick={finish}>
              跳过特效
            </button>
          </section>
        </div>
      </IdentityTerminal>
    </div>
  );
}
