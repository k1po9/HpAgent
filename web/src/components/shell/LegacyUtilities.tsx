import { useCallback, useEffect, useState } from "react";
import { Button, Flex, Text } from "@radix-ui/themes";
import { useWorkbench } from "../../store/workbench";
import { api } from "../../api/client";
import { commandError } from "../../utils/commands";
export function EmptySelection() {
  const error = useWorkbench((s) => s.error);
  const clearError = useWorkbench((s) => s.clearError);
  return (
    <Flex direction="column" gap="3" align="center" justify="center" style={{ height: "100%" }}>
      {error ? (
        <Flex gap="2" align="center">
          <Text color="red" role="alert">
            {error}
          </Text>
          <Button size="1" variant="soft" onClick={clearError}>
            关闭提示
          </Button>
        </Flex>
      ) : null}
      <Text size="3" color="gray">
        选择或新建一个对话开始。
      </Text>
    </Flex>
  );
}

export function NotificationInbox() {
  const [items, setItems] = useState<Array<{ notification_id: string; content: unknown }>>([]);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      const page = await api.request<{ items: typeof items }>({
        method: "GET",
        path: "/api/v1/notifications",
      });
      setItems(page.items);
      setError(null);
    } catch (e) {
      setError(commandError(e));
    }
  }, []);
  useEffect(() => {
    let current = true;
    void Promise.resolve().then(() => {
      if (current) return refresh();
    });
    return () => {
      current = false;
    };
  }, [refresh]);
  return (
    <section className="hp-operation-form">
      <h2>账户收件箱</h2>
      <button onClick={() => void refresh()}>刷新收件箱</button>
      {!items.length && <p>暂无已送达提醒。</p>}
      {items.map((item) => (
        <pre key={item.notification_id}>
          {typeof item.content === "string" ? item.content : JSON.stringify(item.content, null, 2)}
        </pre>
      ))}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
