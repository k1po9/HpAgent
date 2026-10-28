import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import styles from "./Markdown.module.css";
import type { ComponentProps } from "react";
const components: Components = {
  table: ({ node: _node, ...props }) => <table className={styles.table} {...props} />,
  th: ({ node: _node, ...props }) => <th className={styles.cell} {...props} />,
  td: ({ node: _node, ...props }) => <td className={styles.cell} {...props} />,
  img: ({ node: _node, ...props }) => <img className={styles.image} {...props} />,
};
export function Markdown(props: Omit<ComponentProps<typeof ReactMarkdown>, "remarkPlugins">) {
  return <ReactMarkdown components={components} {...props} remarkPlugins={[remarkGfm]} />;
}
