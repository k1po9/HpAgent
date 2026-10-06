import { useEffect, useRef, type ReactNode } from "react";

/** Native modal provides focus containment and inert background; desktop regions do not. */
export function Surface({
  title,
  children,
  onClose,
  modal = true,
  className = "",
  onBack,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  modal?: boolean;
  className?: string;
  onBack?: () => void;
}) {
  const ref = useRef<HTMLDialogElement & HTMLElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const node = ref.current;
    if (modal && node) {
      if (typeof node.showModal === "function") node.showModal();
      else node.setAttribute("open", "");
    }
    node?.querySelector<HTMLElement>("h2")?.focus();
    return () => {
      if (modal && node && typeof node.close === "function") node.close();
      if (trigger?.isConnected && !trigger.closest("[hidden]")) trigger.focus();
      else document.getElementById("canvas-title")?.focus();
    };
  }, [modal]);
  useEffect(() => {
    if (modal) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.querySelector("dialog[open]")) {
        event.preventDefault();
        closeRef.current();
      }
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [modal]);
  const Tag = modal ? "dialog" : "aside";
  return (
    <Tag
      ref={ref}
      className={`hp-surface ${className}`}
      aria-label={title}
      onKeyDown={(event) => {
        if (!modal || event.key !== "Tab") return;
        const controls = Array.from(
          event.currentTarget.querySelectorAll<HTMLElement>(
            'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]',
          ),
        ).filter((element) => element.getClientRects().length > 0);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (!first || !last) {
          event.preventDefault();
          return;
        }
        if (
          event.shiftKey &&
          (document.activeElement === first || document.activeElement?.tagName === "H2")
        ) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <header className="hp-surface-header">
        {onBack && (
          <button onClick={onBack} title="返回上一对象">
            返回
          </button>
        )}
        <h2 id={className.includes("inspector") ? "inspector-title" : undefined} tabIndex={-1}>
          {title}
        </h2>
        <button onClick={onClose} aria-label={`关闭${title}`} title="关闭">
          ×
        </button>
      </header>
      <div className="hp-surface-body">{children}</div>
    </Tag>
  );
}
