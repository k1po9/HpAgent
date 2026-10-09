import { useEffect, useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Surface } from "./Surface";
import { EntryPendingContext } from "../entry/EntryPendingContext";
afterEach(() => vi.restoreAllMocks());
it("keeps the same content and unsaved text through every modal mode change", () => {
  const mount = vi.fn(),
    unmount = vi.fn();
  function Content() {
    useEffect(() => {
      mount();
      return unmount;
    }, []);
    const [value, setValue] = useState("");
    return <input aria-label="草稿" value={value} onChange={(e) => setValue(e.target.value)} />;
  }
  const view = render(
    <Surface title="对象" modal={false} onClose={vi.fn()}>
      <Content />
    </Surface>,
  );
  const input = screen.getByLabelText("草稿");
  fireEvent.change(input, { target: { value: "保留修改" } });
  for (const modal of [true, false, true, false]) {
    view.rerender(
      <Surface title="对象" modal={modal} onClose={vi.fn()}>
        <Content />
      </Surface>,
    );
    expect(screen.getByLabelText("草稿")).toBe(input);
    expect(input).toHaveValue("保留修改");
  }
  expect(mount).toHaveBeenCalledTimes(1);
  expect(unmount).not.toHaveBeenCalled();
});
it("suspends a parent dialog and restores it without mounting its editor again", () => {
  const mount = vi.fn();
  function Content() {
    useEffect(mount, []);
    return <input aria-label="父层草稿" />;
  }
  function Layers({ child }: { child: boolean }) {
    return (
      <>
        <Surface title="父层" onClose={vi.fn()}>
          <Content />
        </Surface>
        {child && (
          <Surface title="保存" onClose={vi.fn()}>
            <button>保存</button>
          </Surface>
        )}
      </>
    );
  }
  const view = render(<Layers child={false} />);
  const parent = screen.getByRole("dialog", { name: "父层" });
  view.rerender(<Layers child />);
  expect(parent).toHaveAttribute("hidden");
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  view.rerender(<Layers child={false} />);
  expect(screen.getByRole("dialog", { name: "父层" })).toBe(parent);
  expect(mount).toHaveBeenCalledTimes(1);
});
it("desktop Escape is handled once and focus returns to the connected trigger", () => {
  const close = vi.fn();
  const trigger = document.createElement("button");
  document.body.append(trigger);
  trigger.focus();
  const view = render(
    <Surface title="对象" modal={false} onClose={close}>
      内容
    </Surface>,
  );
  act(() =>
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", cancelable: true })),
  );
  expect(close).toHaveBeenCalledTimes(1);
  view.unmount();
  expect(trigger).toHaveFocus();
  trigger.remove();
});

it("nested confirmation remains visible while its parent is suspended", () => {
  const view = render(
    <Surface title="父层" onClose={vi.fn()}>
      <Surface title="确认" onClose={vi.fn()}>
        确认正文
      </Surface>
    </Surface>,
  );
  expect(screen.getByRole("dialog", { name: "确认" })).toBeVisible();
  expect(screen.queryByRole("dialog", { name: "父层" })).not.toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "确认" }).closest('[aria-label="父层"]')).toBeNull();
  view.unmount();
});

it("keeps the current operation above an Inspector that becomes modal on resize", () => {
  function Layers({ narrow }: { narrow: boolean }) {
    return (
      <>
        <Surface title="Inspector" modal={narrow} onClose={vi.fn()}>
          <input aria-label="对象草稿" />
        </Surface>
        <Surface title="收件箱" onClose={vi.fn()}>
          待办
        </Surface>
      </>
    );
  }
  const view = render(<Layers narrow={false} />);
  const draft = screen.getByLabelText("对象草稿");
  fireEvent.change(draft, { target: { value: "保留" } });
  const inbox = screen.getByRole("dialog", { name: "收件箱" });
  view.rerender(<Layers narrow />);
  expect(screen.getAllByRole("dialog")).toEqual([inbox]);
  expect(draft.closest("dialog")).toHaveAttribute("hidden");
  expect(draft).toHaveValue("保留");
});

it("brings a previously inactive surface to the front when explicitly opened", () => {
  function Layers({ active }: { active: boolean }) {
    return (
      <>
        <Surface title="侧栏" active={active} onClose={vi.fn()}>
          导航
        </Surface>
        <Surface title="Inspector" onClose={vi.fn()}>
          对象
        </Surface>
      </>
    );
  }
  const view = render(<Layers active={false} />);
  const inspector = screen.getByRole("dialog", { name: "Inspector" });
  view.rerender(<Layers active />);
  expect(screen.getAllByRole("dialog")).toEqual([screen.getByRole("dialog", { name: "侧栏" })]);
  expect(inspector).toHaveAttribute("hidden");
  view.rerender(<Layers active={false} />);
  expect(screen.getByRole("dialog", { name: "Inspector" })).toBe(inspector);
});

it("R2: Tab from the final button must still reach a trailing native summary", () => {
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([
    new DOMRect(0, 0, 44, 44),
  ] as unknown as DOMRectList);
  render(
    <Surface title="账户收件箱" onClose={vi.fn()}>
      <button>刷新收件箱</button>
      <details>
        <summary>通知详情</summary>
        <pre>通知内容</pre>
      </details>
    </Surface>,
  );
  const button = screen.getByRole("button", { name: "刷新收件箱" });
  button.focus();
  expect(fireEvent.keyDown(button, { key: "Tab" })).toBe(true);
});

it("holds a restored modal outside the top layer until entry completes without remounting content", () => {
  const mount = vi.fn();
  function Content() {
    useEffect(mount, []);
    return <input aria-label="恢复的编辑器" defaultValue="保留内容" />;
  }
  const view = render(
    <EntryPendingContext value={true}>
      <Surface title="恢复的详情" onClose={vi.fn()}>
        <Content />
      </Surface>
    </EntryPendingContext>,
  );
  expect(screen.queryByRole("dialog", { name: "恢复的详情" })).not.toBeInTheDocument();
  const input = screen.getByLabelText("恢复的编辑器");
  view.rerender(
    <EntryPendingContext value={false}>
      <Surface title="恢复的详情" onClose={vi.fn()}>
        <Content />
      </Surface>
    </EntryPendingContext>,
  );
  expect(screen.getByRole("dialog", { name: "恢复的详情" })).toHaveAttribute("open");
  expect(screen.getByLabelText("恢复的编辑器")).toBe(input);
  expect(input).toHaveValue("保留内容");
  expect(mount).toHaveBeenCalledTimes(1);
});
