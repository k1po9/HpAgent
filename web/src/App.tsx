import { useEffect } from "react";
import { Flex, Spinner, Text } from "@radix-ui/themes";
import { LoginForm } from "./components/LoginForm";
import { useAuth } from "./store/auth";

/**
 * Auth gate: probe `/api/v1/me` on mount; signed-in sessions open the chat
 * workbench (E-03), signed-out sessions show the login form. A mid-session
 * `auth.expired`/401 flips the store back here and the draft survives as local
 * state (contract API-015).
 */
export function App() {
  const status = useAuth((s) => s.status);
  const check = useAuth((s) => s.check);

  useEffect(() => {
    void check();
  }, [check]);

  if (status === "checking") {
    return (
      <Flex align="center" justify="center" style={{ minHeight: "60vh" }} gap="2">
        <Spinner />
        <Text size="2" color="gray">
          正在恢复会话…
        </Text>
      </Flex>
    );
  }

  if (status === "signedOut") {
    return <LoginForm />;
  }

  return <Workbench />;
}

function Workbench() {
  return (
    <Flex align="center" justify="center" style={{ minHeight: "60vh" }}>
      <Text size="3" color="gray">
        工作台（E-03）即将就绪。
      </Text>
    </Flex>
  );
}
