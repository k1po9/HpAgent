import { useEffect, useRef, useState } from "react";
import type { HpWork } from "../../api/types";
import { useWorks } from "../../store/works";
import { Surface } from "../shell/Surface";
import { useTaskOperations } from "./taskOperations";
export function TaskBudgetDialog({ work, onClose }: { work: HpWork; onClose: () => void }) {
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const [baseline, setBaseline] = useState(work);
  const latest = useWorks((s) => s.items.find((w) => w.work_id === work.work_id));
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const intent = useTaskOperations((s) => s.intents[work.work_id]);
  const budget = baseline.budget!;
  return (
    <Surface title="提高任务预算" onClose={onClose}>
      <form
        className="hp-operation-form"
        onSubmit={(e) => {
          e.preventDefault();
          const limits = Object.fromEntries(
            Object.entries(values)
              .filter(([, v]) => v !== "")
              .map(([k, v]) => [k, Number(v)]),
          );
          if (
            !Object.keys(limits).length ||
            Object.entries(limits).some(
              ([k, v]) => !Number.isSafeInteger(v) || v <= budget.limits[k]!,
            )
          ) {
            setError("请选择现有维度并输入严格高于旧值的整数限额。");
            return;
          }
          setError("");
          void useWorks
            .getState()
            .increaseBudget(baseline, limits)
            .then((ok) => {
              if (ok && alive.current) onClose();
            });
        }}
      >
        <p>仅提交你明确提高的维度。已用和已预留分别计算。</p>
        {latest && latest.row_version !== baseline.row_version && (
          <p role="alert">
            任务预算或状态已更新，输入仍保留。
            <button type="button" onClick={() => setBaseline(latest)}>
              已审阅新预算，采用新基线
            </button>
          </p>
        )}
        {Object.entries(budget.limits).map(([key, old]) => (
          <label key={key}>
            {key === "model_total_tokens" ? "模型 Token 上限" : key} · 旧值 {old} · 已用{" "}
            {budget.used[key] ?? 0} · 已预留 {budget.reserved[key] ?? 0}
            <input
              type="number"
              min={old + 1}
              step="1"
              value={values[key] ?? ""}
              disabled={intent?.busy}
              onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
            />
            {values[key] && (
              <small>
                新值 {values[key]} · 增加 {Number(values[key]) - old}
              </small>
            )}
          </label>
        ))}
        {error && <p role="alert">{error}</p>}
        {intent?.error && <p role="alert">{intent.error}</p>}
        <button disabled={intent?.busy}>确认提高预算</button>
      </form>
    </Surface>
  );
}
