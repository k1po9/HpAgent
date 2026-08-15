from __future__ import annotations

from uuid import UUID

from persistence.uow import UnitOfWork

from .generator import ArtifactGenerationError, WebArtifactGenerator


class ArtifactBuildService:
    def __init__(self, database: object, generator: WebArtifactGenerator):
        self.database = database
        self.generator = generator

    async def execute(self, version_id: UUID) -> dict[str, str]:
        inputs = self._prepare(version_id)
        if inputs["status"] == "completed":
            return {"artifact_version_id": str(version_id), "status": "completed"}
        try:
            html = await self.generator.generate(
                source_markdown=inputs["source_markdown"],
                instruction=inputs["instruction"],
                previous_html=inputs["previous_html"],
            )
            self._complete(version_id, html)
            return {"artifact_version_id": str(version_id), "status": "completed"}
        except ArtifactGenerationError as exc:
            self._fail(version_id, exc.code, exc.safe_message)
            return {"artifact_version_id": str(version_id), "status": "failed"}
        except Exception:
            self._fail(version_id, "artifact_build_failed", "Artifact 生成失败。")
            return {"artifact_version_id": str(version_id), "status": "failed"}

    def _prepare(self, version_id: UUID) -> dict[str, str | None]:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT v.*,m.content AS source_markdown,p.html AS previous_html "
                "FROM artifact_versions v JOIN artifacts a ON a.account_id=v.account_id "
                "AND a.artifact_id=v.artifact_id JOIN messages m ON m.account_id=a.account_id "
                "AND m.message_id=a.source_message_id LEFT JOIN artifact_versions p "
                "ON p.account_id=v.account_id AND p.artifact_version_id=v.parent_version_id "
                "WHERE v.artifact_version_id=%s FOR UPDATE OF v", (version_id,),
            ).fetchone()
            if not row:
                raise ValueError("artifact version not found")
            if row["status"] == "completed":
                return {"status": "completed", "source_markdown": "", "instruction": None,
                        "previous_html": None}
            uow.execute(
                "UPDATE artifact_versions SET status='running',started_at=COALESCE(started_at,now()),"
                "completed_at=NULL,html=NULL,failure_code=NULL,failure_message=NULL,updated_at=now() "
                "WHERE artifact_version_id=%s", (version_id,),
            )
            return {"status": "running", "source_markdown": str(row["source_markdown"]),
                    "instruction": row["instruction"], "previous_html": row["previous_html"]}

    def _complete(self, version_id: UUID, html: str) -> None:
        with UnitOfWork(self.database) as uow:
            uow.execute(
                "UPDATE artifact_versions SET status='completed',html=%s,failure_code=NULL,"
                "failure_message=NULL,completed_at=now(),updated_at=now() "
                "WHERE artifact_version_id=%s AND status='running'", (html, version_id),
            )
            uow.execute(
                "UPDATE artifacts SET updated_at=now() WHERE artifact_id=(SELECT artifact_id "
                "FROM artifact_versions WHERE artifact_version_id=%s)", (version_id,),
            )

    def _fail(self, version_id: UUID, code: str, message: str) -> None:
        with UnitOfWork(self.database) as uow:
            uow.execute(
                "UPDATE artifact_versions SET status='failed',html=NULL,failure_code=%s,"
                "failure_message=%s,started_at=COALESCE(started_at,now()),completed_at=now(),"
                "updated_at=now() WHERE artifact_version_id=%s AND status<>'completed'",
                (code, message[:1000], version_id),
            )
