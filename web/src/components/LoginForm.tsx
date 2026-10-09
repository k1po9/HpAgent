import { useState } from "react";
import { Box, Button, Flex, Heading, Text, TextField } from "@radix-ui/themes";
import { MessageSquare, Folder, Activity, ArrowRight, Check } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../store/auth";

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
  const [submitting, setSubmitting] = useState(false);
  const [registeredPendingLogin, setRegisteredPendingLogin] = useState(false);
  const check = useAuth((s) => s.check);
  const markRegistered = useAuth((s) => s.markRegistered);
  const dismissRegistrationHint = useAuth((s) => s.dismissRegistrationHint);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "register") {
        if (password !== confirmation) {
          throw new Error("两次输入的密码不一致。");
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
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="hp-auth-layout">
      <section className="hp-auth-brand" aria-label="HpAgent 工作空间">
        <div className="hp-auth-wordmark">
          <span>Hp</span>Agent
        </div>
        <p className="hp-auth-tagline">你的个人 AI 工作空间</p>
        <ul>
          <li>
            <MessageSquare aria-hidden="true" />与 AI 协作，推进想法
          </li>
          <li>
            <Folder aria-hidden="true" />
            管理文件与对话资料
          </li>
          <li>
            <Activity aria-hidden="true" />
            跟进任务，查看执行与成果
          </li>
        </ul>
      </section>
      <Box className="hp-auth-card">
        <form onSubmit={onSubmit} aria-describedby={error ? "auth-error" : undefined}>
          <Flex direction="column" gap="3">
            <div className="hp-auth-card-heading">
              <Heading
                as="h1"
                size="5"
                weight="bold"
                aria-label={`HpAgent ${mode === "login" ? "登录" : "注册"}`}
              >
                {mode === "login" ? "欢迎回来" : "创建新账户"}
              </Heading>
              <Text as="p" size="2" color="gray">
                {mode === "login" ? "登录你的 HpAgent 账户" : "加入 HpAgent，开启你的 AI 工作空间"}
              </Text>
            </div>
            <label>
              <Text as="span" size="2" color="gray">
                用户名
              </Text>
              <TextField.Root
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
              />
            </label>
            <label>
              <Text as="span" size="2" color="gray">
                密码
              </Text>
              <TextField.Root
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "register" ? "new-password" : "current-password"}
                minLength={mode === "register" ? 8 : undefined}
                required
              />
            </label>
            {mode === "register" ? (
              <>
                <label>
                  <Text as="span" size="2" color="gray">
                    确认密码
                  </Text>
                  <TextField.Root
                    type="password"
                    value={confirmation}
                    onChange={(e) => setConfirmation(e.target.value)}
                    autoComplete="new-password"
                    minLength={8}
                    required
                  />
                </label>
                <label>
                  <Text as="span" size="2" color="gray">
                    邀请码（可选）
                  </Text>
                  <TextField.Root
                    type="password"
                    value={inviteCode}
                    onChange={(e) => setInviteCode(e.target.value)}
                    autoComplete="off"
                  />
                </label>
              </>
            ) : null}
            {error ? (
              <Text id="auth-error" role="alert" size="2" color="red">
                {error}
              </Text>
            ) : null}
            <Button
              className="hp-auth-submit"
              type="submit"
              disabled={submitting || confirmed}
              data-confirmed={confirmed || undefined}
            >
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
              {confirmed ? (
                <Check size={16} aria-hidden="true" />
              ) : (
                <ArrowRight size={16} aria-hidden="true" />
              )}
            </Button>
            <Button
              type="button"
              variant="ghost"
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
          </Flex>
        </form>
      </Box>
    </div>
  );
}
