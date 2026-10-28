import { Notice } from "../ui";
import { Button, Field, TextField } from "../ui";
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
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const intent = useTaskOperations((s) => s.intents[work.work_id]);
  const budget = baseline.budget!;
  return (
    <Surface
      className="hp-object-operation"
      motion="elevate"
      title="提高任务预算"
      onClose={onClose}
    >
      <form
        className="hp-operation-form"
        onSubmit={(e) => {
          e.preventDefault();
          const limits = Object.fromEntries(
            Object.entries(values)
              .filter(([, v]) => v !== "")
              .map(([k, v]) => [k, Number(v)]),
          );
          const invalid = Object.fromEntries(
            Object.entries(limits)
              .filter(([k, v]) => !Number.isSafeInteger(v) || v <= budget.limits[k]!)
              .map(([k]) => [k, "请输入严格高于旧值的整数限额。"]),
          );
          setFieldErrors(invalid);
          if (!Object.keys(limits).length) {
            setError("请至少提高一个预算维度。");
            return;
          }
          if (Object.keys(invalid).length) {
            setError("");
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
          <Notice role="alert">
            任务预算或状态已更新，输入仍保留。
            <Button type="button" onClick={() => setBaseline(latest)}>
              已审阅新预算，采用新基线
            </Button>
          </Notice>
        )}
        {Object.entries(budget.limits).map(([key, old]) => (
          <Field
            key={key}
            label={`${key === "model_total_tokens" ? "模型 Token 上限" : key} · 旧值 ${old} · 已用 ${budget.used[key] ?? 0} · 已预留 ${budget.reserved[key] ?? 0}`}
            description={
              values[key] ? `新值 ${values[key]} · 增加 ${Number(values[key]) - old}` : undefined
            }
            error={fieldErrors[key]}
          >
            <TextField
              type="number"
              min={old + 1}
              step="1"
              value={values[key] ?? ""}
              disabled={intent?.busy}
              onChange={(e) => {
                setValues((v) => ({ ...v, [key]: e.target.value }));
                setFieldErrors((errors) => ({ ...errors, [key]: "" }));
                setError("");
              }}
            />
          </Field>
        ))}
        {error && <Notice role="alert">{error}</Notice>}
        {intent?.error && <Notice role="alert">{intent.error}</Notice>}
        <Button variant="solid" type="submit" disabled={intent?.busy}>
          确认提高预算
        </Button>
      </form>
    </Surface>
  );
}
