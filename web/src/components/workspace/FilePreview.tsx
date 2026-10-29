import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { HpFile } from "../../api/types";
import { workspaceApi } from "../../store/workspace";
import { canPreview } from "./workspacePresentation";
export function FilePreview({ file }: { file: HpFile }) {
  const [result, setResult] = useState<{ id: string; text?: string; error?: string } | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!canPreview(file)) return;
    const controller = new AbortController();
    let alive = true;
    void workspaceApi
      .readFileText(file.file_id, controller.signal)
      .then((text) => {
        if (alive) setResult({ id: file.file_id, text });
      })
      .catch((e: unknown) => {
        if (alive)
          setResult({
            id: file.file_id,
            error: e instanceof Error ? e.message : "正文待同步，请重试。",
          });
      });
    return () => {
      alive = false;
      controller.abort();
    };
  }, [file, attempt]);
  if (!canPreview(file))
    return (
      <p>此文件提供元数据与下载。正文预览仅支持已就绪、UTF-8 编码、已知大小不超过 1 MiB 的文本。</p>
    );
  if (result?.id !== file.file_id) return <p role="status">正在加载正文…</p>;
  if (result.error)
    return (
      <p role="alert">
        {result.error}
        <button onClick={() => setAttempt((v) => v + 1)}>重试正文</button>
      </p>
    );
  return file.content_type === "text/markdown" ? (
    <div className="hp-file-preview">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.text ?? ""}</ReactMarkdown>
    </div>
  ) : (
    <pre className="hp-file-preview">{result.text}</pre>
  );
}
