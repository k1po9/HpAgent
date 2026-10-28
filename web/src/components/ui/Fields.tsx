import { createContext, useContext, useId, type ComponentProps, type ReactNode } from "react";
import styles from "./Controls.module.css";

const FieldContext = createContext<{
  id: string;
  describedBy?: string;
  invalid: boolean;
} | null>(null);

function useFieldProps<
  T extends ComponentProps<"input"> | ComponentProps<"select"> | ComponentProps<"textarea">,
>(props: T): T {
  const field = useContext(FieldContext);
  if (!field) return props;
  return {
    ...props,
    id: props.id ?? field.id,
    "aria-describedby":
      [props["aria-describedby"], field.describedBy].filter(Boolean).join(" ") || undefined,
    "aria-invalid": field.invalid ? true : props["aria-invalid"],
  };
}

// Native controls preserve form attributes, controlled values, refs and events.
export function TextField({ className = "", ...props }: ComponentProps<"input">) {
  return <input className={`${styles.field} ${className}`} {...useFieldProps(props)} />;
}
export function Select({ className = "", ...props }: ComponentProps<"select">) {
  return <select className={`${styles.field} ${className}`} {...useFieldProps(props)} />;
}
export function Checkbox({ className = "", type = "checkbox", ...props }: ComponentProps<"input">) {
  return (
    <input type={type} className={`${styles.checkbox} ${className}`} {...useFieldProps(props)} />
  );
}
export function TextArea({ className = "", ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      className={`${styles.field} ${styles.textarea} ${className}`}
      {...useFieldProps(props)}
    />
  );
}

type FieldProps = ComponentProps<"label"> & {
  label?: ReactNode;
  description?: ReactNode;
  error?: ReactNode;
};

// Existing implicit labels remain supported. Use label for fields with help/errors,
// keeping that text outside the accessible name. htmlFor can match an explicit input id.
export function Field({
  className = "",
  label,
  description,
  error,
  children,
  htmlFor,
  ...props
}: FieldProps) {
  const generatedId = useId();
  const id = htmlFor ?? generatedId;
  const descriptionId = description ? `${id}-description` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  return (
    <FieldContext
      value={{
        id,
        describedBy: [descriptionId, errorId].filter(Boolean).join(" ") || undefined,
        invalid: Boolean(error),
      }}
    >
      {label === undefined && !description && !error ? (
        <label className={`${styles.label} ${className}`} htmlFor={htmlFor} {...props}>
          {children}
        </label>
      ) : (
        <div className={`${styles.label} ${className}`}>
          <label htmlFor={label === undefined ? htmlFor : id} {...props}>
            {label === undefined ? children : label}
          </label>
          {label !== undefined && children}
          {description && (
            <small id={descriptionId} className={styles.description}>
              {description}
            </small>
          )}
          {error && (
            <small id={errorId} className={styles.error} role="alert">
              {error}
            </small>
          )}
        </div>
      )}
    </FieldContext>
  );
}
