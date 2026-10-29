// Browser-only regression fixture; excluded from the production entry point.
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Theme } from "@radix-ui/themes";
import "@radix-ui/themes/styles.css";
import "../src/styles.css";
import { Surface } from "../src/components/shell/Surface";
import { RunLookup } from "../src/components/run/RunLookup";
export function Harness() {
  const [open, setOpen] = useState(false),
    [child, setChild] = useState(false);
  const modal = !new URLSearchParams(location.search).has("desktop");
  return (
    <Theme>
      <button onClick={() => setOpen(true)}>打开父层</button>
      <button>背景操作</button>
      {open && (
        <Surface title="原生焦点回归" modal={modal} onClose={() => setOpen(false)}>
          <details>
            <summary>开头详情</summary>
            <input aria-label="闭合详情内输入" />
          </details>
          <fieldset disabled>
            <legend>
              <button>首个 legend 操作</button>
            </legend>
            <button>禁用 fieldset 操作</button>
          </fieldset>
          <button disabled>禁用操作</button>
          <div hidden>
            <button>隐藏操作</button>
          </div>
          <div inert>
            <button>inert 操作</button>
          </div>
          <button tabIndex={2}>正 tabindex 操作</button>
          <button tabIndex={-1}>非 Tab 操作</button>
          <iframe
            title="原生 iframe"
            srcDoc='<html><body><input aria-label="iframe 输入" /></body></html>'
          />
          <RunLookup />
          <button onClick={() => setChild(true)}>打开子操作</button>
          <details>
            <summary>末尾详情</summary>
            <button>展开详情操作</button>
          </details>
          {child && (
            <Surface title="子操作" onClose={() => setChild(false)}>
              <input aria-label="子操作草稿" />
            </Surface>
          )}
        </Surface>
      )}
    </Theme>
  );
}
createRoot(document.getElementById("root")!).render(<Harness />);
