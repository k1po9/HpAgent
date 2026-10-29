import { Accordion, Select } from "@lobehub/ui";
import { useId, useState, type ReactNode } from "react";

export function WorkspaceSelectField({
  label,
  value,
  options,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const empty = useId();
  return (
    <div className="hp-workspace-field">
      <label htmlFor={id}>{label}</label>
      <Select
        id={id}
        value={value || empty}
        options={options.map((option) => ({ ...option, value: option.value || empty }))}
        disabled={disabled}
        onChange={(next) => {
          if (typeof next === "string") onChange(next === empty ? "" : next);
        }}
      />
    </div>
  );
}

export function WorkspaceDisclosure({
  title,
  children,
  className = "",
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`hp-workspace-disclosure ${className}`} data-expanded={open}>
      <Accordion
        keepMounted
        variant="borderless"
        indicatorPlacement="end"
        value={open ? ["content"] : []}
        onValueChange={(keys) => setOpen(keys.includes("content"))}
        items={[{ key: "content", title, children }]}
      />
    </div>
  );
}
