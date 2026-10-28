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

it("associates a password mismatch with confirmation and keeps API failure at form level", async () => {
  const user = userEvent.setup();
  render(<LoginForm />);
  await user.click(screen.getByRole("button", { name: "没有账号？注册" }));
  const password = screen.getByLabelText("密码", { selector: "input" });
  const confirmation = screen.getByLabelText("确认密码");
  fireEvent.change(password, { target: { value: "password-a" } });
  fireEvent.change(confirmation, { target: { value: "password-b" } });
  fireEvent.submit(confirmation.closest("form")!);
  expect(confirmation).toHaveAttribute("aria-invalid", "true");
  expect(confirmation).toHaveAccessibleDescription("两次输入的密码不一致。");
  expect(password).not.toHaveAttribute("aria-invalid", "true");
  fireEvent.change(confirmation, { target: { value: "password-a" } });
  expect(confirmation).not.toHaveAttribute("aria-invalid", "true");
  const register = vi.spyOn(api, "register").mockRejectedValue(new Error("邀请码不可用"));
  await act(async () => fireEvent.submit(confirmation.closest("form")!));
  expect(screen.getByRole("alert")).toHaveTextContent("邀请码不可用");
  expect(confirmation.closest("form")).toHaveAccessibleDescription("邀请码不可用");
  expect(confirmation).not.toHaveAttribute("aria-invalid", "true");
  expect(password).not.toHaveAttribute("aria-invalid", "true");
  register.mockRestore();
});
