import { useMemo, useState } from "react";
import { Button, Flex, Text } from "@radix-ui/themes";
import { useArtifacts } from "../store/artifacts";
import { ArtifactPreview } from "./ArtifactPreview";

export function ArtifactPanel() {
  const artifactId = useArtifacts((s) => s.openArtifactId);
  const versionId = useArtifacts((s) => s.openVersionId);
  const artifact = useArtifacts((s) => (artifactId ? s.artifactsById[artifactId] : undefined));
  const versions = useArtifacts((s) =>
    artifactId ? (s.versionsByArtifactId[artifactId] ?? []) : [],
  );
  const selectVersion = useArtifacts((s) => s.selectVersion);
  const createVersion = useArtifacts((s) => s.createVersion);
  const close = useArtifacts((s) => s.closeArtifact);
  const [instruction, setInstruction] = useState("");
  const version = useMemo(
    () =>
      versions.find((v) => v.artifact_version_id === versionId) ?? versions[versions.length - 1],
    [versions, versionId],
  );
  if (!artifactId) return null;

  const index = version ? versions.indexOf(version) : -1;
  return (
    <aside className="hp-artifact-panel" aria-label="Artifact 面板">
      <Flex className="hp-artifact-header" direction="column" gap="2">
        <Flex align="center" justify="between" gap="2">
          <div>
            <Text weight="bold">{artifact?.title ?? "Artifact"}</Text>
            {version ? (
              <Text size="1" color="gray">
                {" "}
                · v{version.version}
              </Text>
            ) : null}
          </div>
          <Button size="1" variant="soft" onClick={close}>
            关闭
          </Button>
        </Flex>
        <Flex gap="2">
          <Button
            size="1"
            variant="soft"
            disabled={index <= 0}
            onClick={() => selectVersion(versions[index - 1]!.artifact_version_id)}
          >
            上一版
          </Button>
          <Button
            size="1"
            variant="soft"
            disabled={index < 0 || index >= versions.length - 1}
            onClick={() => selectVersion(versions[index + 1]!.artifact_version_id)}
          >
            下一版
          </Button>
        </Flex>
      </Flex>
      <div className="hp-artifact-body">
        {!version || version.status === "queued" || version.status === "running" ? (
          <div className="hp-artifact-state">正在生成 Artifact…</div>
        ) : version.status === "failed" ? (
          <div className="hp-artifact-state hp-artifact-state--failed" role="alert">
            <strong>Artifact 生成失败</strong>
            <p>{version.failure?.message}</p>
          </div>
        ) : version.html ? (
          <ArtifactPreview html={version.html} />
        ) : null}
      </div>
      <form
        className="hp-artifact-revision"
        onSubmit={(event) => {
          event.preventDefault();
          if (!artifactId || !instruction.trim()) return;
          void createVersion(artifactId, instruction);
          setInstruction("");
        }}
      >
        <input
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
          placeholder="告诉我如何修改这个 Artifact…"
        />
        <Button type="submit" size="1">
          生成新版本
        </Button>
      </form>
    </aside>
  );
}
