import { useEffect, useRef, useState } from "react";

export function ArtifactPreview({ html }: { html: string }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [loadKey, setLoadKey] = useState(0);

  useEffect(() => {
    const listener = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow) return;
      if (event.data?.type === "hpagent-artifact-runtime-error") {
        setRuntimeError(String(event.data.message || "Artifact 运行错误"));
      }
    };
    window.addEventListener("message", listener);
    return () => window.removeEventListener("message", listener);
  }, [loadKey]);

  return (
    <div className="hp-artifact-preview">
      {runtimeError ? (
        <div className="hp-artifact-runtime-error" role="alert">
          预览内脚本出错：{runtimeError}
          <button
            type="button"
            onClick={() => {
              setRuntimeError(null);
              setLoadKey((v) => v + 1);
            }}
          >
            重新加载
          </button>
        </div>
      ) : null}
      <iframe
        key={loadKey}
        ref={frame}
        title="Artifact 预览"
        sandbox="allow-scripts"
        srcDoc={html}
      />
    </div>
  );
}
