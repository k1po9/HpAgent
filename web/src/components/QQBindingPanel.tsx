import { Button } from "./ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { Box, Text } from "@radix-ui/themes";
import { MessageSquare, Link2 } from "lucide-react";
import { api, type MeResponse, type QqBindingChallenge } from "../api/client";

interface QQBindingPanelProps {
  qq: MeResponse["identities"]["qq"] | undefined;
  onCompleted: () => Promise<void>;
  startRequested?: boolean;
}

export function QQBindingPanel({ qq, onCompleted, startRequested = false }: QQBindingPanelProps) {
  const [challenge, setChallenge] = useState<QqBindingChallenge | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const automaticStartHandled = useRef(false);

  const createChallenge = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setChallenge(await api.createQqBindingChallenge());
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成绑定码失败。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!startRequested || qq?.bound || challenge || automaticStartHandled.current) return;
    automaticStartHandled.current = true;
    void createChallenge();
  }, [challenge, createChallenge, qq?.bound, startRequested]);

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

  return (
    <Box className="hp-qq-binding" data-bound={qq?.bound === true}>
      <div className="hp-qq-binding__row">
        <div className="hp-qq-binding__identity">
          <div className="hp-qq-binding__icon">
            <MessageSquare size={22} aria-hidden="true" />
          </div>
          <div>
            <h3>QQ 关联身份</h3>
            <Text size="1" color="gray">
              {qq?.bound ? (qq.display_subject ?? "已绑定") : "QQ：未绑定"}
            </Text>
          </div>
        </div>
        {!qq?.bound && (
          <Button
            size="1"
            className="hp-qq-binding__action"
            disabled={loading}
            onClick={() => void createChallenge()}
          >
            <Link2 size={15} aria-hidden="true" />
            {loading ? "生成中…" : challenge ? "重新生成" : "绑定 QQ"}
          </Button>
        )}
      </div>
      {!qq?.bound && <p className="hp-qq-binding__description">将需要使用的 QQ 关联到当前账户。</p>}
      {challenge?.code && !qq?.bound ? (
        <Box className="hp-qq-binding__challenge">
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
      {(challenge?.status === "expired" || challenge?.status === "cancelled") && !qq?.bound ? (
        <Text as="p" size="1" color="red" role="alert">
          绑定码已失效，请重新生成。
        </Text>
      ) : null}
      {error && !qq?.bound ? (
        <Text as="p" size="1" color="red" role="alert">
          {error}
        </Text>
      ) : null}
    </Box>
  );
}
