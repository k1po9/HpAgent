"""Compatibility facade for account-scoped Artifact commands and reads."""

from uuid import UUID

from application.artifacts.commands import ArtifactCommands, legacy_artifact_sender
from application.chat.send_message import SendMessage
from persistence.command_result import CommandResult

from .queries import ArtifactQueries


class ArtifactService(ArtifactQueries):
    def __init__(self, database: object, *, sender: SendMessage | None = None):
        super().__init__(database)
        self.commands = ArtifactCommands(
            database, sender if sender is not None else legacy_artifact_sender(database)
        )

    def create_artifact(
        self,
        account_id: UUID,
        source_message_id: UUID,
        key: str,
        instruction: str | None = None,
    ) -> CommandResult:
        return self.commands.create_artifact(account_id, source_message_id, key, instruction)

    def create_version(
        self,
        account_id: UUID,
        artifact_id: UUID,
        key: str,
        instruction: str,
    ) -> CommandResult:
        return self.commands.create_version(account_id, artifact_id, key, instruction)
