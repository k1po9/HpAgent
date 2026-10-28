import { Button as RadixButton, IconButton as RadixIconButton } from "@radix-ui/themes";
import type { ComponentProps } from "react";
import styles from "./Controls.module.css";

type ButtonProps = ComponentProps<typeof RadixButton>;
/** Project controls never infer submission from DOM placement. */
export function Button({
  type = "button",
  variant = "outline",
  className = "",
  color = variant === "solid" ? undefined : "gray",
  ...props
}: ButtonProps) {
  return (
    <RadixButton
      type={type}
      variant={variant}
      color={color}
      className={`${styles.button} ${className}`}
      {...props}
    />
  );
}
export function IconButton({
  type = "button",
  variant = "ghost",
  className = "",
  ...props
}: ComponentProps<typeof RadixIconButton>) {
  return (
    <RadixIconButton
      type={type}
      variant={variant}
      className={`${styles.button} ${styles.icon} ${className}`}
      {...props}
    />
  );
}
