import { X } from "lucide-react";
import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Theme } from "@radix-ui/themes";
import { advanceForegroundRevision } from "./foreground";
import { tabOrder } from "./tabOrder";
import { EntryPendingContext } from "../entry/EntryPendingContext";
type LayerContext = { depth: number; group: object };
const SurfaceParent = createContext<LayerContext | null>(null);
let layerSequence = 0;
const ranks = new WeakMap<object, number>();
const hierarchy = new WeakMap<HTMLDialogElement, LayerContext>();

// One native top-layer dialog at a time. Suspended parents keep their React subtree.
const layers: HTMLDialogElement[] = [];
function coordinate() {
  layers.sort((a, b) => {
    const left = hierarchy.get(a)!,
      right = hierarchy.get(b)!;
    return ranks.get(left.group)! - ranks.get(right.group)! || left.depth - right.depth;
  });
  const top = layers.at(-1);
  for (const node of layers) {
    if (node === top) continue;
    if (node.open && typeof node.close === "function") node.close();
    node.hidden = true;
    node.inert = true;
  }
  if (top) {
    top.hidden = false;
    top.inert = false;
    if (!top.open) {
      if (typeof top.showModal === "function") top.showModal();
      else top.setAttribute("open", "");
    }
  }
}
export function Surface({
  title,
  headerActions,
  children,
  onClose,
  modal = true,
  className = "",
  onBack,
  active: requestedActive = true,
  role,
  dismissible = true,
}: {
  title: string;
  headerActions?: ReactNode;
  children: ReactNode;
  onClose: () => void;
  modal?: boolean;
  className?: string;
  onBack?: () => void;
  active?: boolean;
  role?: "alertdialog";
  dismissible?: boolean;
}) {
  const entering = useContext(EntryPendingContext);
  const active = requestedActive && !(modal && entering);
  const parent = useContext(SurfaceParent);
  const layer = useMemo<LayerContext>(
    () => ({
      depth: parent ? parent.depth + 1 : 0,
      group: parent?.group ?? {},
    }),
    [parent],
  );
  const lastFocus = useRef<HTMLElement | null>(null);
  const previousActive = useRef<boolean | null>(null);
  const ref = useRef<HTMLDialogElement>(null);
  const closeRef = useRef(onClose);
  const trigger = useRef<HTMLElement | null>(null);
  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  }, []);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (modal && active && previousActive.current !== true) advanceForegroundRevision();
    // A responsive mode change is not a new opening. Only a newly activated
    // surface or nested operation should move its group above existing layers.
    if (
      !ranks.has(layer.group) ||
      (active && previousActive.current === false) ||
      (parent && modal && active)
    ) {
      ranks.set(layer.group, ++layerSequence);
    }
    previousActive.current = active;
    if (modal && active) {
      hierarchy.set(node, layer);
      layers.push(node);
      coordinate();
      if (layers.at(-1) === node) {
        if (lastFocus.current?.isConnected) lastFocus.current.focus();
        else node.querySelector<HTMLElement>("h2")?.focus();
      }
    } else {
      node.hidden = !active;
      node.inert = !active;
      if (active) {
        node.setAttribute("open", "");
        if (lastFocus.current?.isConnected) lastFocus.current.focus();
      }
    }
    return () => {
      lastFocus.current =
        node.contains(document.activeElement) && document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      const index = layers.indexOf(node);
      if (index >= 0) layers.splice(index, 1);
      if (node.open && typeof node.close === "function") node.close();
      node.removeAttribute("open");
      coordinate();
    };
  }, [modal, active, layer, parent]);
  useEffect(() => {
    if (modal || !active || !dismissible) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented && !layers.length) {
        event.preventDefault();
        closeRef.current();
      }
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [modal, active, dismissible]);
  useEffect(
    () => () => {
      const target = trigger.current;
      if (target?.isConnected && !target.closest("[hidden], [inert]")) target.focus();
      else if (layers.at(-1)) layers.at(-1)?.querySelector<HTMLElement>("h2")?.focus();
      else document.getElementById("canvas-title")?.focus();
    },
    [],
  );
  const content = (
    <dialog
      ref={ref}
      className={`hp-surface ${!modal ? "hp-surface--region" : ""} ${className}`}
      role={modal ? role : "complementary"}
      aria-label={title}
      aria-modal={modal && active ? true : undefined}
      onKeyDown={(event) => {
        if (!modal || layers.at(-1) !== ref.current || event.key !== "Tab") return;
        const controls = tabOrder(event.currentTarget);
        const first = controls[0],
          last = controls.at(-1);
        const focused = document.activeElement;
        if (!first || !last) {
          event.preventDefault();
          return;
        }
        if (
          (event.shiftKey && (focused === first || focused?.tagName === "H2")) ||
          (!event.shiftKey && (focused === last || focused?.tagName === "H2"))
        ) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        }
      }}
      onCancel={(event) => {
        event.preventDefault();
        if (layers.at(-1) === ref.current) onClose();
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
        {headerActions && <div className="hp-surface-header-actions">{headerActions}</div>}
        <button onClick={onClose} aria-label={`关闭${title}`} title="关闭">
          <X size={20} aria-hidden="true" />
        </button>
      </header>
      <div className="hp-surface-body">{children}</div>
    </dialog>
  );
  return (
    <SurfaceParent.Provider value={layer}>
      {parent
        ? createPortal(
            <Theme asChild hasBackground>
              {content}
            </Theme>,
            document.body,
          )
        : content}
    </SurfaceParent.Provider>
  );
}
