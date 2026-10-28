import { DropdownMenu } from "@radix-ui/themes";
import { useRef, useState } from "react";
import { Button } from "./Button";
import styles from "./Controls.module.css";

/** Keep Radix portals inside the native dialog's top layer and inert boundary. */
export function ActionMenu({
  id,
  label = "更多",
  items,
}: {
  id: string;
  label?: string;
  items: { id: string; label: string; disabled?: boolean; onSelect: () => void }[];
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [container, setContainer] = useState<HTMLElement | null>(null);
  return (
    <DropdownMenu.Root
      modal={false}
      open={open}
      onOpenChange={(next) => {
        if (next) setContainer(trigger.current?.closest("dialog") ?? document.body);
        setOpen(next);
      }}
    >
      <DropdownMenu.Trigger>
        <Button id={id} ref={trigger} variant="ghost">
          {label}
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content
        container={container}
        className={styles.menu}
        onEscapeKeyDown={(event) => {
          event.preventDefault();
          event.stopPropagation();
          setOpen(false);
        }}
        onKeyDown={(event) => {
          if (event.key === "Tab") event.stopPropagation();
        }}
      >
        {items.map((item) => (
          <DropdownMenu.Item
            key={item.id}
            id={item.id}
            disabled={item.disabled}
            onSelect={() => {
              // A local Surface must capture the permanent trigger, not an item about to unmount.
              trigger.current?.focus({ preventScroll: true });
              item.onSelect();
            }}
            className={styles.menuItem}
          >
            {item.label}
          </DropdownMenu.Item>
        ))}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
