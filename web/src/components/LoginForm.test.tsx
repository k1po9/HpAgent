import { render, screen } from "@testing-library/react";
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
});
