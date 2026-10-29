import { Highlighter } from "@lobehub/ui";

/** Keep syntax highlighting out of the initial application chunk. */
export default function TaskHighlighter({ children }: { children: string }) {
  return (
    <Highlighter language="json" variant="borderless" copyable>
      {children}
    </Highlighter>
  );
}
