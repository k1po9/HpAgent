import { useLayoutEffect, useRef, useState } from "react";
import type { HpMessage, HpRun } from "../../api/types";
import { isTerminalRunStatus } from "../../store/workbench";

/** Presentation policy only. Unknown/future statuses fail closed; phase is never authority. */
export function readingFoldBlock(run: HpRun | null, uncertain: boolean): string | null {
  if (uncertain) return "状态待确认，暂不能收纳阅读区";
  if (run && !isTerminalRunStatus(run.status)) return "执行尚未结束，暂不能收纳阅读区";
  return null;
}

interface ReadingPosition {
  scope: string;
  top: number;
  bottom: boolean;
  anchor?: HTMLElement;
  offset: number;
}

/** No persistence, business commands, subscriptions, observers, timers or animation loop. */
export function useReadingFold(scope: string, messages: HpMessage[], blocked: string | null) {
  const viewport = useRef<HTMLDivElement>(null);
  const region = useRef<HTMLDivElement>(null);
  const control = useRef<HTMLButtonElement>(null);
  const position = useRef<ReadingPosition | null>(null);
  const [state, setState] = useState({ scope, messages, folded: false });
  // Reset in render so invalidated content is accessible in this very commit.
  // Never remount the thread/runtime/composer to reset this local presentation bit.
  if (state.scope !== scope || (state.folded && (blocked || state.messages !== messages))) {
    setState({ scope, messages, folded: false });
  }
  const folded = state.scope === scope && state.folded && !blocked && state.messages === messages;
  useLayoutEffect(() => {
    if (folded) return;
    const saved = position.current;
    position.current = null;
    const el = viewport.current;
    if (!saved || saved.scope !== scope || !el) return;
    if (saved.bottom) el.scrollTop = el.scrollHeight;
    else if (saved.anchor?.isConnected && el.contains(saved.anchor))
      el.scrollTop +=
        saved.anchor.getBoundingClientRect().top - el.getBoundingClientRect().top - saved.offset;
    else el.scrollTop = saved.top;
  }, [folded, scope]);
  const reveal = () => setState({ scope, messages, folded: false });
  const toggle = (allowOwnPresentationInert = false) => {
    if (folded) {
      reveal();
      return;
    }
    const el = viewport.current;
    if (blocked || !el || el.closest("[hidden]") || document.hidden) return;
    const inert = el.closest("[inert]");
    if (inert && !(allowOwnPresentationInert && inert === region.current)) return;
    const top = el.getBoundingClientRect().top;
    const anchor = Array.from(el.querySelectorAll<HTMLElement>("[data-message-id]")).find(
      (node) => node.getBoundingClientRect().bottom > top,
    );
    position.current = {
      scope,
      top: el.scrollTop,
      bottom: el.scrollHeight - el.scrollTop - el.clientHeight < 48,
      anchor,
      offset: anchor ? anchor.getBoundingClientRect().top - top : 0,
    };
    // Programmatic/assistive activation must not strand focus in the hidden subtree.
    if (region.current?.contains(document.activeElement))
      control.current?.focus({ preventScroll: true });
    setState({ scope, messages, folded: true });
  };
  return { viewport, region, control, folded, reveal, toggle };
}
