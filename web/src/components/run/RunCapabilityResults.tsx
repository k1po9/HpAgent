import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useShell } from "../../store/shell";
import { useTaskQuery } from "../tasks/useTaskQuery";

type CapabilityResult = {
  invocation_id: string;
  capability_key: string;
  payload: {
    outcome: string;
    summary: string;
    outputs: Array<{ ref_id: string; type: string; object_id: string; version: string }>;
    checks: Array<{
      verifier_key: string;
      verifier_version: string;
      verdict: string;
      subject_ref: string;
    }>;
    gaps: unknown[];
  };
};
export function RunCapabilityResults({
  runId,
  revision,
}: {
  runId: string;
  revision?: string | number;
}) {
  const [opened, setOpened] = useState(false);
  const query = useTaskQuery<{ items: CapabilityResult[]; truncated: boolean }>(
    opened ? `capability-results:${runId}` : null,
    `/api/v1/runs/${runId}/capability-results`,
    revision,
  );
  return (
    <section aria-label="能力成果">
      <h4>能力成果</h4>
      <button disabled={query.loading} onClick={() => (opened ? query.retry() : setOpened(true))}>
        {opened ? "刷新能力成果" : "读取能力成果"}
      </button>
      {query.error && <p role="alert">能力成果暂不可用。</p>}
      {query.data?.items.map((item) => (
        <CapabilityOutput key={item.invocation_id} item={item} runId={runId} />
      ))}
      {query.data?.truncated && <p>仅展示最近 100 次能力结果。</p>}
    </section>
  );
}
function CapabilityOutput({ item, runId }: { item: CapabilityResult; runId: string }) {
  const [read, setRead] = useState(false);
  const report = useTaskQuery<{ report: { report_markdown: string } }>(
    read ? `capability-report:${runId}:${item.invocation_id}` : null,
    `/api/v1/runs/${runId}/research/report?invocation_id=${encodeURIComponent(item.invocation_id)}`,
  );
  return (
    <article>
      <p>
        {item.capability_key} · {item.payload.outcome}
      </p>
      <p>{item.payload.summary}</p>
      {item.payload.checks.map((check, i) => (
        <p key={i}>
          {check.verifier_key} / {check.verifier_version} · {check.verdict} · {check.subject_ref}
        </p>
      ))}
      {item.payload.gaps.length > 0 && (
        <details>
          <summary>已知缺口</summary>
          <pre>{JSON.stringify(item.payload.gaps, null, 2)}</pre>
        </details>
      )}
      {item.payload.outputs
        .filter((ref) => ref.type === "artifact_version")
        .map((ref) => (
          <button
            key={ref.ref_id}
            onClick={() =>
              useShell.getState().openInspector(
                {
                  kind: "artifact",
                  objectId: ref.object_id,
                  versionId: ref.version,
                  origin: { runId },
                },
                true,
              )
            }
          >
            查看 HTML 成果版本
          </button>
        ))}
      {item.capability_key === "research" && (
        <button onClick={() => setRead(true)}>读取此研究报告</button>
      )}
      {report.error && (
        <p role="alert">
          报告暂不可用。<button onClick={report.retry}>重试报告</button>
        </p>
      )}
      {report.data && (
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {report.data.report.report_markdown}
        </ReactMarkdown>
      )}
    </article>
  );
}
