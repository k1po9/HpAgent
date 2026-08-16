import { useEffect, useState } from "react";
import { Box, Button, Flex, Text } from "@radix-ui/themes";
import { api, type MeResponse, type QqBindingChallenge } from "../api/client";

interface QQBindingPanelProps {
  qq: MeResponse["identities"]["qq"] | undefined;
  onCompleted: () => Promise<void>;
}

export function QQBindingPanel({ qq, onCompleted }: QQBindingPanelProps) {
  const [challenge, setChallenge] = useState<QqBindingChallenge | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!challenge?.challenge_id || challenge.status === "completed") return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void api
        .getQqBindingChallenge(challenge.challenge_id)
        .then(async (next) => {
          if (cancelled) return;
          setChallenge((current) => ({ ...current, ...next }));
          if (next.status === "completed") {
            window.clearInterval(timer);
            await onCompleted();
          }
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(err instanceof Error ? err.message : "查询绑定状态失败。");
        });
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [challenge?.challenge_id, challenge?.status, onCompleted]);

  if (qq?.bound) {
    return (
      <Box className="hp-qq-binding">
        <Text size="1" color="gray">
          QQ
        </Text>
        <Text size="2">{qq.display_subject ?? "已绑定"}</Text>
      </Box>
    );
  }

  async function createChallenge() {
    setLoading(true);
    setError(null);
    try {
      setChallenge(await api.createQqBindingChallenge());
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成绑定码失败。");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Box className="hp-qq-binding">
      <Flex justify="between" align="center">
        <Text size="1" color="gray">
          QQ：未绑定
        </Text>
        <Button size="1" variant="soft" disabled={loading} onClick={() => void createChallenge()}>
          {loading ? "生成中…" : challenge ? "重新生成" : "绑定 QQ"}
        </Button>
      </Flex>
      {challenge?.code ? (
        <Box mt="2">
          <Text as="p" size="1" color="gray">
            请使用需要绑定的 QQ 发送：
          </Text>
          <Text as="p" size="2" weight="bold" className="hp-qq-binding__code">
            绑定 {challenge.code}
          </Text>
          <Text as="p" size="1" color="gray">
            绑定完成后页面会自动更新。
          </Text>
        </Box>
      ) : null}
      {challenge?.status === "expired" || challenge?.status === "cancelled" ? (
        <Text as="p" size="1" color="red">
          绑定码已失效，请重新生成。
        </Text>
      ) : null}
      {error ? (
        <Text as="p" size="1" color="red">
          {error}
        </Text>
      ) : null}
    </Box>
  );
}
