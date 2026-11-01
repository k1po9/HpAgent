import { Sparkles } from "lucide-react";
import { setEntryEffectEnabled, useEntryEffectEnabled } from "./entryEffectPreference";

export function EntryEffectToggle() {
  const enabled = useEntryEffectEnabled();
  return (
    <button
      type="button"
      className="hp-entry-effect-toggle"
      aria-label="每次打开显示登录特效"
      aria-pressed={enabled}
      title={`每次打开显示登录特效：${enabled ? "开" : "关"}（下次打开生效）`}
      onClick={() => setEntryEffectEnabled(!enabled)}
    >
      <Sparkles aria-hidden="true" />
      <span>登录特效</span>
      <small>{enabled ? "开" : "关"}</small>
    </button>
  );
}
