import {
  Accordion,
  Button,
  Checkbox,
  Descriptions,
  Input,
  InputNumber,
  MotionProvider,
  Select,
  TextArea,
} from "@lobehub/ui";
import { motion } from "motion/react";
import {
  Children,
  Fragment,
  isValidElement,
  lazy,
  Suspense,
  useContext,
  useId,
  useState,
  type ButtonHTMLAttributes,
  type ComponentProps,
  type FormHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactElement,
  type ReactNode,
  type Ref,
  type TextareaHTMLAttributes,
} from "react";
import { TaskControlContext, TaskEditContext, TaskFieldContext } from "./taskControlContext";

const TaskHighlighter = lazy(() => import("./TaskHighlighter"));

/** Native-shaped field values are deliberately separate from React DOM events. */
type InputValue = { target: { value: string; checked: boolean } };
type SelectValue = { target: { value: string } };

export function TaskControlScope({ active, children }: { active: boolean; children: ReactNode }) {
  return <TaskControlContext value={active}>{children}</TaskControlContext>;
}

export function TaskButton({
  type,
  appearance,
  className = "",
  children,
  ref,
  ...props
}: Omit<ComponentProps<typeof Button>, "type" | "htmlType" | "ref"> & {
  type?: ButtonHTMLAttributes<HTMLButtonElement>["type"];
  appearance?: "default" | "primary" | "text" | "link" | "fill" | "dashed";
  ref?: Ref<HTMLButtonElement>;
}) {
  const active = useContext(TaskControlContext);
  if (!active)
    return (
      <button {...props} type={type} className={className} ref={ref}>
        {children}
      </button>
    );
  const text = typeof children === "string" ? children : "";
  const primary =
    className.includes("primary") ||
    ["提交修订", "创建工作", "确认提高预算", "确认授予所选权限"].includes(text);
  return (
    <MotionProvider motion={motion}>
      <Button
        {...props}
        ref={ref}
        className={`hp-task-control ${className}`}
        htmlType={type ?? "submit"}
        type={appearance ?? (primary ? "primary" : "default")}
      >
        {children}
      </Button>
    </MotionProvider>
  );
}

export function TaskForm({
  onDirty,
  onChange,
  children,
  ...props
}: FormHTMLAttributes<HTMLFormElement> & { onDirty?: () => void }) {
  return (
    <TaskEditContext value={onDirty}>
      <form
        {...props}
        onChange={(event) => {
          onDirty?.();
          onChange?.(event);
        }}
      >
        {children}
      </form>
    </TaskEditContext>
  );
}

/** Separate the visible label from a select's rendered value in its accessible name. */
export function TaskLabel({ children, ...props }: HTMLAttributes<HTMLLabelElement>) {
  const active = useContext(TaskControlContext);
  const id = useId();
  if (!active) return <label {...props}>{children}</label>;
  const nodes = Children.toArray(children);
  const control = nodes.find(
    (node) =>
      isValidElement(node) &&
      [TaskInput, TaskSelect, TaskTextArea].includes(node.type as typeof TaskInput),
  );
  if (!isValidElement(control)) return <label {...props}>{children}</label>;
  const checkbox =
    control.type === TaskInput &&
    (control.props as ComponentProps<typeof TaskInput>).type === "checkbox";
  const caption = nodes.filter((node) => node !== control);
  return (
    <div className={`hp-task-field${checkbox ? " hp-task-field--check" : ""}`}>
      <label {...props} htmlFor={id}>
        {caption}
      </label>
      <TaskFieldContext value={id}>{control}</TaskFieldContext>
    </div>
  );
}

type TaskInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "onChange" | "value" | "size"> & {
  value?: string | number;
  onChange?: (value: InputValue) => void;
};
export function TaskInput({ type, onChange, value, id, ...props }: TaskInputProps) {
  const active = useContext(TaskControlContext);
  const fieldId = useContext(TaskFieldContext);
  const edited = useContext(TaskEditContext);
  const inputId = id ?? fieldId;
  if (!active)
    return <input {...props} id={inputId} type={type} value={value} onChange={onChange} />;
  if (type === "checkbox")
    return (
      <Checkbox
        id={inputId}
        checked={props.checked}
        disabled={props.disabled}
        name={props.name}
        onChange={(checked) => {
          edited?.();
          onChange?.({ target: { value: String(value ?? ""), checked } });
        }}
      />
    );
  if (type === "number")
    return (
      <InputNumber
        id={inputId}
        name={props.name}
        className="hp-task-number"
        disabled={props.disabled}
        required={props.required}
        min={props.min === undefined ? undefined : Number(props.min)}
        max={props.max === undefined ? undefined : Number(props.max)}
        step={props.step === undefined ? undefined : Number(props.step)}
        value={value === "" || value === undefined ? null : Number(value)}
        onChange={(next) => {
          edited?.();
          onChange?.({
            target: {
              value: next === null ? "" : String(next),
              checked: false,
            },
          });
        }}
      />
    );
  return <Input {...props} id={inputId} type={type} value={value} onChange={onChange} />;
}

export function TaskTextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const active = useContext(TaskControlContext);
  const id = useContext(TaskFieldContext);
  return active ? (
    <TextArea {...props} id={props.id ?? id} autoSize={{ minRows: 3, maxRows: 8 }} />
  ) : (
    <textarea {...props} />
  );
}

type Option = { value: string; label: ReactNode; disabled?: boolean };
function optionsOf(children: ReactNode): Option[] {
  return Children.toArray(children).flatMap((child): Option[] => {
    if (!isValidElement(child)) return [];
    if (child.type === Fragment)
      return optionsOf((child.props as { children?: ReactNode }).children);
    if (child.type !== "option") return [];
    const option = child.props as ComponentProps<"option">;
    return [
      { value: String(option.value ?? ""), label: option.children, disabled: option.disabled },
    ];
  });
}
type TaskSelectProps = {
  children: ReactNode;
  value: string;
  onChange?: (value: SelectValue) => void;
  disabled?: boolean;
  required?: boolean;
  name?: string;
  id?: string;
  className?: string;
};
export function TaskSelect({ children, value, onChange, id, ...props }: TaskSelectProps) {
  const active = useContext(TaskControlContext);
  const fieldId = useContext(TaskFieldContext);
  const edited = useContext(TaskEditContext);
  const emptyValue = useId();
  if (!active)
    return (
      <select {...props} id={id ?? fieldId} value={value} onChange={onChange}>
        {children}
      </select>
    );
  return (
    <Select
      {...props}
      id={id ?? fieldId}
      className={`hp-task-select ${props.className ?? ""}`}
      value={value === "" ? emptyValue : value}
      options={optionsOf(children).map((option) => ({
        ...option,
        value: option.value === "" ? emptyValue : option.value,
      }))}
      onChange={(next) => {
        if (typeof next !== "string") return;
        edited?.();
        onChange?.({ target: { value: next === emptyValue ? "" : next } });
      }}
    />
  );
}

export function TaskDetails({
  children,
  open,
  className = "",
}: {
  children: ReactNode;
  open?: boolean;
  className?: string;
}) {
  const active = useContext(TaskControlContext);
  const id = useId();
  const [expanded, setExpanded] = useState(Boolean(open));
  const nodes = Children.toArray(children);
  const summary = nodes.find((node) => isValidElement(node) && node.type === "summary") as
    ReactElement<{ children?: ReactNode }> | undefined;
  if (!active)
    return (
      <details open={open} className={className}>
        {children}
      </details>
    );
  return (
    <Accordion
      className={`hp-task-disclosure ${className}`}
      variant="borderless"
      indicatorPlacement="end"
      keepMounted
      value={(open ?? expanded) ? [id] : []}
      onValueChange={(keys) => setExpanded(keys.includes(id))}
      items={[
        {
          key: id,
          title: summary?.props.children,
          children: nodes.filter((node) => node !== summary),
        },
      ]}
    />
  );
}

export function TaskDescriptions({ children }: { children: ReactNode }) {
  const nodes = Children.toArray(children) as ReactElement<{ children?: ReactNode }>[];
  return (
    <Descriptions
      className="hp-task-descriptions"
      colon={false}
      items={nodes.flatMap((node, i) =>
        i % 2 === 0
          ? [{ key: String(i), label: node.props.children, children: nodes[i + 1]?.props.children }]
          : [],
      )}
    />
  );
}

export function TaskCode({ children }: { children: string }) {
  const active = useContext(TaskControlContext);
  if (!active) return <pre>{children}</pre>;
  return (
    <MotionProvider motion={motion}>
      <Suspense fallback={<pre>{children}</pre>}>
        <TaskHighlighter>{children}</TaskHighlighter>
      </Suspense>
    </MotionProvider>
  );
}
