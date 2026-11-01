import { useRef, useState } from "react";
import { Box, Button, Text, TextField } from "@radix-ui/themes";
import { ArrowRight, Check } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../store/auth";
import type { IdentitySceneHandle } from "./identity/IdentityScene";
import { useIdentityCanvas } from "./identity/identityLayout";
import { IdentityLink } from "./identity/IdentityLink";
import { IdentityPanelFile } from "./identity/IdentityPanel";
import { IdentityTerminal } from "./identity/IdentityTerminal";
import { useClientLocation } from "./identity/useClientLocation";

/**
 * Same-origin credential form (hpagent-web-api-contract.md §4.2).
 *
 * The backend 303s back with a Set-Cookie; `/api/v1/me` then seeds the CSRF
 * token. Failed credentials show the server's safe message.
 */
export function LoginForm({ confirmed = false }: { confirmed?: boolean }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submission = useRef(false);
  const [submitting, setSubmitting] = useState(false);
  const [registeredPendingLogin, setRegisteredPendingLogin] = useState(false);
  // Counts credential or transport failures only; guidance messages never raise it.
  const [rejection, setRejection] = useState(0);
  const [denied, setDenied] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const scene = useRef<IdentitySceneHandle>(null);
  const check = useAuth((s) => s.check);
  const markRegistered = useAuth((s) => s.markRegistered);
  const dismissRegistrationHint = useAuth((s) => s.dismissRegistrationHint);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (submission.current || confirmed) return;
    submission.current = true;
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "register") {
        if (password !== confirmation) {
          setError("两次输入的密码不一致。");
          return;
        }
        const sessionEstablished = await api.register(username, password, inviteCode);
        if (!sessionEstablished) {
          setRegisteredPendingLogin(true);
          setMode("login");
          setConfirmation("");
          setError("注册成功，但自动登录失败。请使用刚注册的账号登录。");
          return;
        }
        setRegisteredPendingLogin(false);
        markRegistered();
      } else {
        if (!(await api.login(username, password))) {
          throw new Error("登录会话未建立，请重试。");
        }
        if (registeredPendingLogin) {
          markRegistered();
          setRegisteredPendingLogin(false);
        } else {
          dismissRegistrationHint();
        }
      }
      await check(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请重试。");
      setRejection((count) => count + 1);
    } finally {
      submission.current = false;
      setSubmitting(false);
    }
  }

  const canvas = useIdentityCanvas();
  const location = useClientLocation(canvas);
  const seal = canvas && mode === "register";
  const charges = [!!username, !!password, !!confirmation];
  const phase = confirmed ? "confirmed" : submitting ? "verifying" : "idle";
  const readCapsLock = (event: React.KeyboardEvent) =>
    setCapsLock(event.getModifierState?.("CapsLock") ?? false);
  const field = (
    label: string,
    value: string,
    onChange: (value: string) => void,
    options: { type?: "password"; autoComplete: string; minLength?: number; required?: boolean },
  ) => (
    <label className="hp-identity-field">
      <Text as="span">{label}</Text>
      <TextField.Root
        disabled={submitting || confirmed}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        {...options}
      />
    </label>
  );

  return (
    <IdentityTerminal
      phase={phase}
      rejection={rejection}
      mode={mode}
      typed={!!(username || password)}
      location={location}
      denied={denied}
      onDenied={setDenied}
      sceneRef={scene}
    >
      <Box className="hp-auth-card">
        <IdentityPanelFile
          phase={phase}
          mode={mode}
          username={username}
          capsLock={capsLock}
          keyEntered={!!password}
          rejection={rejection}
          denied={denied}
          location={location}
        />
        {canvas ? (
          <IdentityLink
            phase={phase}
            denied={denied}
            mode={mode}
            filled={
              mode === "login"
                ? [!!username, !!password]
                : [!!username, !!password, !!confirmation, true]
            }
          />
        ) : null}
        <form
          className="hp-identity-auth"
          onSubmit={onSubmit}
          onInput={() => scene.current?.pulse()}
          onKeyDown={readCapsLock}
          onKeyUp={readCapsLock}
          aria-busy={submitting}
          aria-describedby={error ? "auth-error" : undefined}
        >
          <div className="authhead" aria-hidden="true">
            <span>
              <i />
              AUTHORIZATION KEY
            </span>
          </div>
          <div className="hp-identity-slab">
            {field("用户名", username, setUsername, { autoComplete: "username", required: true })}
            {field("密码", password, setPassword, {
              type: "password",
              autoComplete: mode === "register" ? "new-password" : "current-password",
              required: true,
              ...(mode === "register" ? { minLength: 8 } : {}),
            })}
            {mode === "register" ? (
              <>
                {field("确认密码", confirmation, setConfirmation, {
                  type: "password",
                  autoComplete: "new-password",
                  minLength: 8,
                  required: true,
                })}
                {field("邀请码（可选）", inviteCode, setInviteCode, {
                  type: "password",
                  autoComplete: "off",
                })}
              </>
            ) : null}
            <Button
              className="hp-auth-submit"
              type="submit"
              disabled={submitting || confirmed}
              data-confirmed={confirmed || undefined}
              data-seal={seal || undefined}
              data-charged={(seal && charges.every(Boolean)) || undefined}
              data-busy={submitting || undefined}
            >
              {seal ? (
                <span className="hp-seal-meter" aria-hidden="true">
                  {charges.map((charged, index) => (
                    <i key={index} data-on={charged || undefined} />
                  ))}
                </span>
              ) : null}
              <span className="hp-submit-text">
                {confirmed
                  ? mode === "login"
                    ? "登录成功"
                    : "账户已创建"
                  : submitting
                    ? mode === "login"
                      ? "登录中…"
                      : "注册中…"
                    : mode === "login"
                      ? "登录"
                      : "注册"}
              </span>
              {confirmed ? (
                <Check size={16} aria-hidden="true" />
              ) : (
                <ArrowRight size={16} aria-hidden="true" />
              )}
            </Button>
            <i className="focusline" aria-hidden="true" />
          </div>
          <div className="hp-identity-under">
            <div className="msg">
              {submitting ? <span className="hp-identity-verifying" aria-hidden="true" /> : null}
              {error ? (
                <Text id="auth-error" role="alert" as="p">
                  {error}
                </Text>
              ) : capsLock ? (
                <Text as="p" role="status">
                  大写锁定已开启
                </Text>
              ) : null}
            </div>
            <Button
              type="button"
              variant="ghost"
              className="hp-identity-switch"
              disabled={submitting}
              onClick={() => {
                setMode(mode === "login" ? "register" : "login");
                setRegisteredPendingLogin(false);
                setError(null);
                setConfirmation("");
              }}
            >
              {mode === "login" ? "没有账号？注册" : "已有账号？登录"}
            </Button>
          </div>
        </form>
      </Box>
    </IdentityTerminal>
  );
}
