import { useState } from "react";
import { Box, Button, Flex, Heading, Text, TextField } from "@radix-ui/themes";
import { api } from "../api/client";
import { useAuth } from "../store/auth";

/**
 * Same-origin credential form (hpagent-web-api-contract.md §4.2).
 *
 * The backend 303s back with a Set-Cookie; `/api/v1/me` then seeds the CSRF
 * token. Failed credentials show the server's safe message.
 */
export function LoginForm() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const check = useAuth((s) => s.check);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "register") {
        if (password !== confirmation) {
          throw new Error("两次输入的密码不一致。");
        }
        await api.register(username, password);
      } else {
        await api.login(username, password);
      }
      await check();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请重试。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Box style={{ maxWidth: 360, margin: "20vh auto 0" }}>
      <form onSubmit={onSubmit}>
        <Flex direction="column" gap="3">
          <Heading as="h1" size="5" weight="bold">
            HpAgent {mode === "login" ? "登录" : "注册"}
          </Heading>
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
              autoComplete="current-password"
              minLength={mode === "register" ? 8 : undefined}
              required
            />
          </label>
          {mode === "register" ? (
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
          ) : null}
          {error ? (
            <Text size="2" color="red">
              {error}
            </Text>
          ) : null}
          <Button type="submit" disabled={submitting}>
            {submitting
              ? mode === "login"
                ? "登录中…"
                : "注册中…"
              : mode === "login"
                ? "登录"
                : "注册"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={submitting}
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setError(null);
              setConfirmation("");
            }}
          >
            {mode === "login" ? "没有账号？注册" : "已有账号？登录"}
          </Button>
        </Flex>
      </form>
    </Box>
  );
}
