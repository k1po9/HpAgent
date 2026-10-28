import type { ComponentProps } from "react";
import styles from "./Controls.module.css";
export function Notice({ role = "status", className = "", ...props }: ComponentProps<"p">) {
  return <p role={role} className={`${styles.notice} ${className}`} {...props} />;
}
export function FormActions({ className = "", ...props }: ComponentProps<"div">) {
  return <div className={`${styles.actions} ${className}`} {...props} />;
}
export function Toolbar({ className = "", ...props }: ComponentProps<"div">) {
  return <div className={`${styles.actions} ${className}`} {...props} />;
}
