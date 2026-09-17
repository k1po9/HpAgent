import { useEffect, useState } from "react";
import { Button, Card, Flex, Text } from "@radix-ui/themes";
import { api as client } from "../api/client";
import { HpApi } from "../api/resources";
import type { HpFileApproval, HpPersistentFileDestination } from "../api/types";
import { newIdempotencyKey } from "../utils/idempotency";

const api = new HpApi(client);
const labels: Record<HpFileApproval["status"], string> = {
  pending: "等待你的确认",
  approved: "已批准，正在继续执行…",
  rejected: "已拒绝",
  expired: "审批已过期",
  cancelled: "操作已取消",
  consumed: "已批准并执行",
};

export function ApprovalCard({ runId }: { runId: string | null }) {
  const [approval, setApproval] = useState<HpFileApproval | null>(null);
  const [destination, setDestination] = useState<HpPersistentFileDestination | null>(null);
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => {
    if (!runId) return;
    let active = true;
    const load = () =>
      void api.listFileApprovals(runId).then((value) => {
        if (active) setApproval(value.approvals.at(-1) ?? null);
      });
    load();
    const timer = window.setInterval(load, 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [runId]);
  useEffect(() => {
    if (approval?.status !== "consumed" || !approval.logical_path) return;
    let active = true;
    void api.getPersistentFile(approval.logical_path).then((value) => {
      if (active) setDestination(value.destination);
    });
    return () => {
      active = false;
    };
  }, [approval?.logical_path, approval?.status]);
  if (!runId || !approval || approval.run_id !== runId) return null;
  const decide = async (decision: "approve" | "reject") => {
    if (submitting || approval.status !== "pending") return;
    setSubmitting(true);
    try {
      const result = await api.decideFileApproval(
        approval.approval_id,
        decision,
        newIdempotencyKey(),
      );
      setApproval(result.approval);
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <Card className="hp-approval" data-testid="approval-card">
      <Flex direction="column" gap="2">
        <Text weight="bold">需要确认</Text>
        <Text>动作：更新已有文件</Text>
        <Text>目标：{approval.logical_path ?? approval.action_summary}</Text>
        {approval.expected_revision ? (
          <Text>当前版本：revision {approval.expected_revision}</Text>
        ) : null}
        <Text color={approval.status === "pending" ? "orange" : "gray"}>
          {labels[approval.status]}
        </Text>
        {destination ? (
          <Flex gap="2" align="center">
            <Text>最新版本：revision {destination.current_revision}</Text>
            <a href={`/api/v1/files/${destination.current_file_id}/content`}>下载文件</a>
          </Flex>
        ) : null}
        {approval.status === "pending" ? (
          <Flex gap="2" justify="end">
            <Button variant="soft" disabled={submitting} onClick={() => void decide("reject")}>
              拒绝
            </Button>
            <Button disabled={submitting} onClick={() => void decide("approve")}>
              确认更新
            </Button>
          </Flex>
        ) : null}
      </Flex>
    </Card>
  );
}
