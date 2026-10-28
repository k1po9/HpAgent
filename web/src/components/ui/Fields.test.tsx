import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Field, TextField, Select, TextArea, Checkbox } from "./Fields";

it("keeps native form contracts while composing field descriptions and caller IDs", () => {
  const ref = createRef<HTMLInputElement>();
  const change = vi.fn();
  const view = render(
    <>
      <p id="external-help">外部说明</p>
      <Field label="名称" htmlFor="name" description="字段说明" error="名称重复">
        <TextField
          id="name"
          ref={ref}
          name="name"
          value="原值"
          onChange={change}
          required
          aria-describedby="external-help"
        />
      </Field>
      <Field label="类型">
        <Select defaultValue="a">
          <option value="a">A</option>
        </Select>
      </Field>
      <Field label="备注">
        <TextArea defaultValue="备注内容" />
      </Field>
      <Field>
        <Checkbox name="agree" defaultChecked />
        同意
      </Field>
    </>,
  );
  const input = screen.getByRole("textbox", { name: "名称" });
  expect(ref.current).toBe(input);
  expect(input).toHaveAccessibleDescription("外部说明 字段说明 名称重复");
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input).toBeRequired();
  fireEvent.change(input, { target: { value: "新值" } });
  expect(change).toHaveBeenCalledTimes(1);
  expect(input).toHaveValue("原值");
  expect(screen.getByRole("combobox", { name: "类型" })).toHaveValue("a");
  expect(screen.getByRole("textbox", { name: "备注" })).toHaveValue("备注内容");
  expect(screen.getByRole("checkbox", { name: "同意" })).toBeChecked();
  view.rerender(
    <Field label="名称" description="字段说明">
      <TextField value="新值" onChange={change} />
    </Field>,
  );
  const corrected = screen.getByRole("textbox", { name: "名称" });
  expect(corrected).not.toHaveAttribute("aria-invalid", "true");
  expect(corrected).toHaveAccessibleDescription("字段说明");
});
