import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { LoginForm } from "./LoginForm";

describe("LoginForm autocomplete", () => {
  beforeEach(() => {
    api.reset();
    vi.unstubAllGlobals();
  });

  it("uses current-password for login and new-password for registration", async () => {
    const user = userEvent.setup();
    render(<LoginForm />);

    expect(screen.getByLabelText("密码")).toHaveAttribute("autocomplete", "current-password");
    await user.click(screen.getByRole("button", { name: "没有账号？注册" }));
    expect(screen.getByLabelText("密码", { selector: "input" })).toHaveAttribute(
      "autocomplete",
      "new-password",
    );
    expect(screen.getByLabelText("确认密码")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("邀请码（可选）")).not.toBeRequired();
  });

  it("switches to login recovery after registration succeeds without a session", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ registered: true, session_established: false }), {
            status: 201,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );
    render(<LoginForm />);

    await user.click(screen.getByRole("button", { name: "没有账号？注册" }));
    await user.type(screen.getByLabelText("用户名"), "alice");
    await user.type(screen.getByLabelText("密码", { selector: "input" }), "correct-password");
    await user.type(screen.getByLabelText("确认密码"), "correct-password");
    await user.click(screen.getByRole("button", { name: "注册" }));

    expect(await screen.findByText(/注册成功，但自动登录失败/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "HpAgent 登录" })).toBeInTheDocument();
    expect(screen.getByLabelText("用户名")).toHaveValue("alice");
    expect(screen.getByLabelText("密码")).toHaveValue("correct-password");
  });

  it("renders the server's safe invite error", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              error: {
                code: "registration_invite_invalid",
                message: "邀请码无效或不可用。",
                request_id: null,
                retryable: false,
                details: {},
              },
            }),
            { status: 403, headers: { "Content-Type": "application/json" } },
          ),
      ),
    );
    render(<LoginForm />);
    await user.click(screen.getByRole("button", { name: "没有账号？注册" }));
    await user.type(screen.getByLabelText("用户名"), "alice");
    await user.type(screen.getByLabelText("密码", { selector: "input" }), "correct-password");
    await user.type(screen.getByLabelText("确认密码"), "correct-password");
    await user.type(screen.getByLabelText("邀请码（可选）"), "bad-secret");
    await user.click(screen.getByRole("button", { name: "注册" }));
    expect(await screen.findByText("邀请码无效或不可用。")).toBeInTheDocument();
  });
});

it("deduplicates same-turn credential submissions while preserving failure retry", async () => {
  let finish!: (value: boolean) => void;
  const login = vi.spyOn(api, "login").mockImplementation(
    () =>
      new Promise<boolean>((resolve) => {
        finish = resolve;
      }),
  );
  render(<LoginForm />);
  fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "alice" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "password" } });
  const form = screen.getByLabelText("用户名").closest("form")!;
  act(() => {
    fireEvent.submit(form);
    fireEvent.submit(form);
  });
  expect(login).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("用户名")).toBeDisabled();
  expect(screen.getByLabelText("密码")).toBeDisabled();
  await act(async () => {
    finish(false);
  });
  expect(screen.getByRole("alert")).toHaveTextContent("登录会话未建立");
  expect(screen.getByRole("button", { name: "登录" })).toBeEnabled();
  login.mockRestore();
});

describe("LoginForm identity feedback", () => {
  const layout = () => screen.getByLabelText("用户名").closest(".hp-auth-layout")!;

  beforeEach(() => {
    api.reset();
    vi.unstubAllGlobals();
  });

  async function register(user: ReturnType<typeof userEvent.setup>, confirmation: string) {
    await user.click(screen.getByRole("button", { name: "没有账号？注册" }));
    await user.type(screen.getByLabelText("用户名"), "alice");
    await user.type(screen.getByLabelText("密码", { selector: "input" }), "correct-password");
    await user.type(screen.getByLabelText("确认密码"), confirmation);
    await user.click(screen.getByRole("button", { name: "注册" }));
  }

  it("treats a server rejection as denied and keeps the same fields", async () => {
    const login = vi.spyOn(api, "login").mockRejectedValue(new Error("用户名或密码错误。"));
    const user = userEvent.setup();
    render(<LoginForm />);
    const field = screen.getByLabelText("用户名");
    await user.type(field, "alice");
    await user.type(screen.getByLabelText("密码"), "password");
    await user.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("用户名或密码错误。");
    expect(layout()).toHaveAttribute("data-denied", "true");
    expect(screen.getByLabelText("用户名")).toBe(field);
    login.mockRestore();
  });

  it("does not treat a local password mismatch as denied", async () => {
    const register_ = vi.spyOn(api, "register");
    const user = userEvent.setup();
    render(<LoginForm />);
    await register(user, "different-password");
    expect(await screen.findByRole("alert")).toHaveTextContent("两次输入的密码不一致。");
    expect(layout()).not.toHaveAttribute("data-denied");
    expect(register_).not.toHaveBeenCalled();
    register_.mockRestore();
  });

  it("does not treat a successful registration without a session as denied", async () => {
    const register_ = vi.spyOn(api, "register").mockResolvedValue(false);
    const user = userEvent.setup();
    render(<LoginForm />);
    await register(user, "correct-password");
    expect(await screen.findByText(/注册成功，但自动登录失败/)).toBeInTheDocument();
    expect(layout()).not.toHaveAttribute("data-denied");
    register_.mockRestore();
  });

  it("shows the username but never the password on the file, and counts real attempts", async () => {
    const login = vi.spyOn(api, "login").mockRejectedValue(new Error("用户名或密码错误。"));
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText("用户名"), "alice");
    await user.type(screen.getByLabelText("密码"), "secret-value");
    const card = screen.getByLabelText("用户名").closest(".hp-auth-card")!;
    expect(screen.getByRole("heading", { name: "HpAgent 登录" })).toHaveTextContent("ALICE");
    expect(card.textContent).not.toMatch(/secret-value/i);
    expect(card.textContent).toContain("ATTEMPT01");
    await user.click(screen.getByRole("button", { name: "登录" }));
    await screen.findByRole("alert");
    expect(card.textContent).toContain("ATTEMPT02");
    login.mockRestore();
  });

  it("never lets the parsing link see field content", async () => {
    const width = vi.spyOn(window, "innerWidth", "get").mockReturnValue(1600);
    const height = vi.spyOn(window, "innerHeight", "get").mockReturnValue(900);
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})),
    );
    const user = userEvent.setup();
    const { container } = render(<LoginForm />);
    const label = () => container.querySelector(".hp-identity-link-label");
    expect(label()).toHaveTextContent("AWAITING · 用户名");
    await user.type(screen.getByLabelText("密码"), "secret-value");
    expect(label()).toHaveTextContent("PARSING · 密码");
    expect(container.querySelector(".hp-identity-link")?.outerHTML).not.toContain("secret");
    expect(label()?.textContent).not.toContain("secret");
    width.mockRestore();
    height.mockRestore();
  });

  it("charges the registration seal from filled state alone", async () => {
    const width = vi.spyOn(window, "innerWidth", "get").mockReturnValue(1600);
    const height = vi.spyOn(window, "innerHeight", "get").mockReturnValue(900);
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})),
    );
    const user = userEvent.setup();
    const { container } = render(<LoginForm />);
    await user.click(screen.getByRole("button", { name: "没有账号？注册" }));
    const lit = () => container.querySelectorAll(".hp-seal-meter i[data-on]").length;
    const seal = screen.getByRole("button", { name: "注册" });
    expect(lit()).toBe(0);
    await user.type(screen.getByLabelText("用户名"), "alice");
    expect(lit()).toBe(1);
    await user.type(screen.getByLabelText("密码", { selector: "input" }), "correct-password");
    await user.type(screen.getByLabelText("确认密码"), "x");
    expect(lit()).toBe(3);
    expect(seal).toHaveAttribute("data-charged");
    expect(seal).toBeEnabled();
    width.mockRestore();
    height.mockRestore();
  });

  it("reflects the real Caps Lock state", () => {
    render(<LoginForm />);
    const field = screen.getByLabelText("用户名");
    fireEvent.keyDown(field, { key: "A", modifierCapsLock: true });
    expect(screen.getByRole("status")).toHaveTextContent("大写锁定已开启");
    fireEvent.keyUp(field, { key: "A", modifierCapsLock: false });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("falls back to the CSS backdrop without WebGL", () => {
    render(<LoginForm />);
    expect(layout()).toHaveAttribute("data-scene", "fallback");
  });
});
