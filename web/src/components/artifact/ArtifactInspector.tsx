import { InspectorTabs } from "../shell/InspectorTabs";
import { useEffect } from "react";
import { useArtifacts, defaultVersion, latestSuccess } from "../../store/artifacts";
import { useShell, type Inspector } from "../../store/shell";
import { useWorks } from "../../store/works";
import { ArtifactPreview } from "../ArtifactPreview";
import { ArtifactComposer } from "./ArtifactComposer";
import { artifactFilename, downloadArtifact, clearArtifactDownloads } from "./artifactDownloads";
import type { ArtifactSaveSource } from "../workspace/workspaceOperations";
import { artifactStatus } from "./artifactPresentation";
const emptyVersions: never[] = [];
async function refreshArtifact(id: string, force = false, active = () => true) {
  const token = useShell.getState().requestToken;
  const items = await useArtifacts.getState().loadArtifact(id, force);
  const shell = useShell.getState();
  const inspector = shell.route.inspector;
  if (
    !active() ||
    !items ||
    shell.requestToken !== token ||
    inspector?.kind !== "artifact" ||
    inspector.objectId !== id ||
    inspector.versionId
  )
    return;
  const selected = defaultVersion(items);
  if (selected)
    shell.navigate(
      { ...shell.route, inspector: { ...inspector, versionId: selected.artifact_version_id } },
      true,
    );
}
export function ArtifactInspector({
  inspector,
  onSaveHtml,
}: {
  inspector: Inspector;
  onSaveHtml?: (source: ArtifactSaveSource) => void;
}) {
  const id = inspector.objectId;
  const artifact = useArtifacts((s) => s.artifactsById[id]);
  const versions = useArtifacts((s) => s.versionsByArtifactId[id] ?? emptyVersions);
  const query = useArtifacts((s) => s.queries[id]);
  const intent = useArtifacts((s) => s.intents[`version:${id}`]);
  const route = useShell((s) => s.route);
  const works = useWorks((s) => s.items);
  const workId = inspector.origin?.workId ?? (route.screen === "tasks" ? route.workId : undefined);
  const work = works.find((w) => w.work_id === workId);
  const references = work?.artifacts.filter((a) => a.artifact_id === id) ?? [];
  const reference = inspector.origin?.originalVersionId
    ? references.find((a) => a.artifact_version_id === inspector.origin!.originalVersionId)
    : references.length === 1
      ? references[0]
      : undefined;
  useEffect(() => {
    let active = true;
    void refreshArtifact(id, false, () => active);
    return () => {
      active = false;
      clearArtifactDownloads();
    };
  }, [id]);
  useEffect(() => {
    if (workId && !work)
      void useWorks
        .getState()
        .refresh(workId)
        .catch(() => {});
  }, [workId, work]);
  const version = inspector.versionId
    ? versions.find((v) => v.artifact_version_id === inspector.versionId)
    : defaultVersion(versions);
  const parent = latestSuccess(versions);
  const newest = [...versions].sort((a, b) => b.version - a.version)[0];
  const originalVersion = versions.find(
    (v) => v.artifact_version_id === reference?.artifact_version_id,
  );
  const change = (patch: Partial<Inspector>) => {
    const shell = useShell.getState();
    shell.navigate({ ...shell.route, inspector: { ...inspector, ...patch } }, true);
  };
  const select = (versionId: string) => change({ versionId });
  const tab = inspector.tab ?? "preview";
  if (query?.status === 403 || query?.status === 404)
    return (
      <div>
        <p role="alert">对象不可用。</p>
        <button onClick={() => refreshArtifact(id, true)}>重新核实</button>
      </div>
    );
  if (!artifact)
    return (
      <div>
        <p role={query?.error ? "alert" : "status"}>{query?.error ?? "正在加载 HTML 成果…"}</p>
        {query?.error && <button onClick={() => refreshArtifact(id, true)}>重试</button>}
      </div>
    );
  return (
    <div className="hp-artifact-inspector" aria-label="HTML Artifact">
      <header className="hp-artifact-heading">
        <h3>{artifact.title}</h3>
        <p>
          HTML ·{" "}
          {version
            ? `正在查看 v${version.version} · ${artifactStatus[version.status]}`
            : "版本不可用"}
        </p>
      </header>
      {workId && (
        <div className="hp-artifact-origin">
          <p>
            {reference
              ? `任务原引用 · r${reference.source_requirement_revision} · ${originalVersion ? `v${originalVersion.version}` : reference.artifact_version_id}`
              : "任务来源待核实"}
          </p>
          {reference && reference.artifact_version_id !== version?.artifact_version_id && (
            <p>
              {version && originalVersion && version.version > originalVersion.version
                ? "此版本由后续修改生成，不自动替代任务交付。"
                : "正在查看其他版本，不自动替代任务交付。"}
            </p>
          )}
          <button
            onClick={() =>
              useShell.getState().openInspector({ kind: "task", objectId: workId, tab: "outputs" })
            }
          >
            返回任务成果与验收
          </button>
        </div>
      )}
      {newest && newest.artifact_version_id !== inspector.versionId && (
        <p role="status">
          v{newest.version} · {artifactStatus[newest.status]}{" "}
          <button onClick={() => select(newest.artifact_version_id)}>查看 v{newest.version}</button>
        </p>
      )}
      {query?.error && (
        <p role="alert">
          同步中断：{query.error}{" "}
          <button onClick={() => refreshArtifact(id, true)}>重试同步</button>
        </p>
      )}
      <InspectorTabs
        prefix="artifact"
        label="HTML 成果内容"
        tabs={[
          { id: "preview", label: "预览" },
          { id: "versions", label: "版本历史" },
          { id: "details", label: "详情" },
        ]}
        selected={tab}
      />
      {!version && (
        <p role="alert">
          {versions.length ? "指定版本不可用，未替换为其他版本。" : "暂无版本内容。"}{" "}
          {defaultVersion(versions) && (
            <button onClick={() => select(defaultVersion(versions)!.artifact_version_id)}>
              查看可用版本
            </button>
          )}
        </p>
      )}
      <section
        id={`artifact-panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`artifact-tab-${tab}`}
        tabIndex={0}
      >
        {tab === "preview" && version && (
          <>
            {version.status === "completed" && version.html ? (
              <>
                <div className="hp-artifact-toolbar">
                  <button
                    onClick={() =>
                      downloadArtifact(
                        version.html!,
                        artifactFilename(artifact.title, version.version),
                      )
                    }
                  >
                    下载 HTML
                  </button>
                  {onSaveHtml && (
                    <button
                      onClick={() =>
                        onSaveHtml({
                          html: version.html!,
                          file_name: `${artifactFilename(artifact.title, version.version)}.txt`,
                          artifactId: id,
                          versionId: version.artifact_version_id,
                          version: version.version,
                          title: artifact.title,
                        })
                      }
                    >
                      保存源码副本到空间
                    </button>
                  )}
                </div>
                <ArtifactPreview key={version.artifact_version_id} html={version.html} />
              </>
            ) : (
              <div
                className="hp-artifact-state"
                role={version.status === "failed" ? "alert" : "status"}
              >
                <p>
                  v{version.version} · {artifactStatus[version.status]}
                </p>
                {version.failure && <p>{version.failure.message}</p>}
                {parent && (
                  <button onClick={() => select(parent.artifact_version_id)}>
                    查看最近成功版本 v{parent.version}
                  </button>
                )}
              </div>
            )}
          </>
        )}
        {tab === "versions" && (
          <ol className="hp-artifact-history">
            {[...versions]
              .sort((a, b) => b.version - a.version)
              .map((v) => (
                <li key={v.artifact_version_id}>
                  <button
                    aria-current={
                      v.artifact_version_id === inspector.versionId ? "true" : undefined
                    }
                    onClick={() => select(v.artifact_version_id)}
                  >
                    查看 v{v.version}
                  </button>
                  <p>
                    {artifactStatus[v.status]} · {v.created_at}
                  </p>
                  <p>
                    基准：
                    {v.parent_version_id
                      ? `v${versions.find((p) => p.artifact_version_id === v.parent_version_id)?.version ?? "未知"}`
                      : "首次生成，无继承版本"}
                  </p>
                  <p>{v.instruction ?? "初始生成"}</p>
                  {v.failure && <p role="alert">{v.failure.message}</p>}
                </li>
              ))}
          </ol>
        )}
        {tab === "details" && (
          <dl className="hp-artifact-details">
            <dt>成果标识</dt>
            <dd>{id}</dd>
            <dt>来源</dt>
            <dd>
              {workId
                ? `任务 ${workId}`
                : artifact.conversation_id
                  ? `对话 ${artifact.conversation_id}`
                  : "来源待核实"}
            </dd>
            <dt>创建时间</dt>
            <dd>{artifact.created_at}</dd>
            <dt>版本标识</dt>
            <dd>{version?.artifact_version_id ?? "不可用"}</dd>
            <dt>版本完成时间</dt>
            <dd>{version?.completed_at ?? "尚未完成"}</dd>
            <dt>产生执行</dt>
            <dd>
              {version?.producing_run_id ? (
                <button
                  onClick={() =>
                    useShell
                      .getState()
                      .openInspector({ kind: "run", objectId: version.producing_run_id! }, true)
                  }
                >
                  {version.producing_run_id}
                </button>
              ) : (
                "未提供"
              )}
            </dd>
            {version?.failure && (
              <>
                <dt>失败代码</dt>
                <dd>{version.failure.code}</dd>
              </>
            )}
            {intent?.result?.code && (
              <>
                <dt>请求错误</dt>
                <dd>
                  {intent.result.code} · {intent.result.requestId}
                </dd>
              </>
            )}
          </dl>
        )}
      </section>
      <ArtifactComposer artifactId={id} versions={versions} onSelect={select} />
    </div>
  );
}
