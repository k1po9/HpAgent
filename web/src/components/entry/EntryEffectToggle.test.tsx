import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EntryEffectToggle } from "./EntryEffectToggle";
import { entryEffectEnabled, setEntryEffectEnabled } from "./entryEffectPreference";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setEntryEffectEnabled(true);
  localStorage.clear();
});

it("defaults on and remembers the toggle across mounts", () => {
  const first = render(<EntryEffectToggle />);
  const toggle = screen.getByRole("button", { name: "每次打开显示登录特效" });
  expect(toggle).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-pressed", "false");
  first.unmount();
  render(<EntryEffectToggle />);
  expect(screen.getByRole("button", { name: "每次打开显示登录特效" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
});

it("uses the default when storage is unavailable and can still toggle", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  render(<EntryEffectToggle />);
  expect(entryEffectEnabled()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "每次打开显示登录特效" }));
  expect(screen.getByRole("button", { name: "每次打开显示登录特效" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
});

it("still toggles when storage reads work but writes fail", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("quota exceeded");
  });
  render(<EntryEffectToggle />);
  fireEvent.click(screen.getByRole("button", { name: "每次打开显示登录特效" }));
  expect(screen.getByRole("button", { name: "每次打开显示登录特效" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
});

it("reflects a preference changed in another tab", () => {
  render(<EntryEffectToggle />);
  act(() => {
    localStorage.setItem("hpagent.entry-effect", "off");
    window.dispatchEvent(new StorageEvent("storage", { key: "hpagent.entry-effect" }));
  });
  expect(screen.getByRole("button", { name: "每次打开显示登录特效" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
});
