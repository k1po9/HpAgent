import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { LoginForm } from "./LoginForm";

describe("LoginForm autocomplete", () => {
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
});
