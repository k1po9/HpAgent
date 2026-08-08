"""ConversationService —— 会话启动、复用和本地资源准备。

该服务承接原 worker.handle_message 中的会话业务逻辑：
  - account_id 解析
  - Temporal Workflow start/signal
  - session/workspace/git repo 初始化
  - session sandbox 创建或重建
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Type

from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from common.types import UnifiedMessage
from session.workspace import init_session, init_user

logger = logging.getLogger("HpAgent.ConversationService")


class UnboundIdentity(Exception):
    """发送者在 PostgreSQL 中没有活跃身份绑定。

    Phase F 决策：绝不回落到 accounts.json，也绝不自动创建 Account /
    IdentityBinding。消息不会进入 Temporal / Agent / Session / Hindsight，
    由 MessageIngressService 捕获后向 QQ 发送固定 "账号尚未绑定" 回复。
    """

    def __init__(self, *, channel_type: str, sender_id: str):
        super().__init__(
            f"unbound {channel_type} identity: sender_id={sender_id}"
        )
        self.channel_type = channel_type
        self.sender_id = sender_id


class ConversationService:
    """对话会话应用服务。"""

    def __init__(
        self,
        *,
        temporal_client: Any,
        workflow_cls: Type[Any],
        task_queue: str,
        idle_timeout_minutes: int,
        activity_timeout: int,
        account_service: Any,
        workspace_root: Path,
        file_store: Any,
        workspace_db: Any,
        git_repo_manager: Any,
        sandbox_manager: Any,
    ):
        self._client = temporal_client
        self._workflow = workflow_cls
        self._task_queue = task_queue
        self._idle_timeout_minutes = idle_timeout_minutes
        self._activity_timeout = activity_timeout
        self._account_service = account_service
        self._workspace_root = workspace_root
        self._file_store = file_store
        self._workspace_db = workspace_db
        self._git_repo_manager = git_repo_manager
        self._sandbox_manager = sandbox_manager

    async def start_or_signal(self, message: UnifiedMessage, channel_type: str) -> None:
        account_id = await self._account_service.resolve(channel_type, message.sender_id)
        if not account_id:
            raise UnboundIdentity(channel_type=channel_type, sender_id=message.sender_id)
        workflow_id = f"hpagent-{account_id}"

        session_context = {
            "account_id": account_id,
            "sender_id": message.sender_id,
            "channel_type": channel_type,
            "metadata": message.metadata,
        }

        session_id = f"session-{account_id}-{uuid.uuid4().hex[:8]}"
        user_message = self._build_user_message(
            message=message,
            channel_type=channel_type,
            session_id=session_id,
            account_id=account_id,
        )

        try:
            await self._client.start_workflow(
                self._workflow.run,
                user_message,
                id=workflow_id,
                task_queue=self._task_queue,
            )

            await self._prepare_session_resources(
                account_id=account_id,
                session_id=session_id,
                task_summary=message.content[:100],
                session_context=session_context,
            )
            logger.info("Started new session %s (account=%s)", session_id, account_id)

        except WorkflowAlreadyStartedError:
            await self._signal_existing_or_replace(
                workflow_id=workflow_id,
                account_id=account_id,
                message=message,
                user_message=user_message,
                session_context=session_context,
            )
        except Exception as e:
            logger.exception("Failed to start or signal session %s: %s", session_id, e)

    async def _signal_existing_or_replace(
        self,
        *,
        workflow_id: str,
        account_id: str,
        message: UnifiedMessage,
        user_message: dict,
        session_context: dict,
    ) -> None:
        handle = self._client.get_workflow_handle(workflow_id)
        signaled = False
        session_id = user_message["session_id"]

        try:
            status = await handle.query(self._workflow.get_status)
            session_id = status.get("session_id", f"session-{account_id}")

            if not self._has_session_sandbox(session_id):
                try:
                    await self._prepare_session_resources(
                        account_id=account_id,
                        session_id=session_id,
                        task_summary="",
                        session_context=session_context,
                    )
                    logger.info("Sandbox recreated for signaled session %s", session_id)
                except Exception as e:
                    logger.warning("Sandbox recreation failed for %s: %s", session_id, e)

            user_message["session_id"] = session_id
            await handle.signal(self._workflow.new_message, user_message)
            logger.info("Signaled existing session %s", session_id)
            signaled = True
        except Exception as signal_err:
            logger.info(
                "Workflow %s signal failed (%s), starting replacement session",
                workflow_id, signal_err,
            )

        if not signaled:
            await self._start_replacement_session(
                workflow_id=workflow_id,
                account_id=account_id,
                message=message,
                user_message=user_message,
                session_context=session_context,
            )

    async def _start_replacement_session(
        self,
        *,
        workflow_id: str,
        account_id: str,
        message: UnifiedMessage,
        user_message: dict,
        session_context: dict,
    ) -> None:
        session_id = f"session-{account_id}-{uuid.uuid4().hex[:8]}"
        user_message["session_id"] = session_id

        await self._prepare_session_resources(
            account_id=account_id,
            session_id=session_id,
            task_summary=user_message["content"][:100],
            session_context=session_context,
        )

        await self._client.start_workflow(
            self._workflow.run,
            user_message,
            id=workflow_id,
            task_queue=self._task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
        logger.info("Started replacement session %s (account=%s)", session_id, account_id)

    async def _prepare_session_resources(
        self,
        *,
        account_id: str,
        session_id: str,
        task_summary: str,
        session_context: dict,
    ) -> None:
        repo_path = str(self._workspace_root / account_id / "repo")
        init_user(self._file_store, self._workspace_db, account_id)
        await self._git_repo_manager.ensure_repo(account_id)
        await self._git_repo_manager.start_session(account_id, session_id)
        init_session(
            self._file_store,
            self._workspace_db,
            user_uuid=account_id,
            session_id=session_id,
            task_summary=task_summary,
        )
        self._sandbox_manager.create_session_sandbox(
            session_id=session_id,
            workspace_path=repo_path,
            user_uuid=account_id,
            session_context=session_context,
        )

    def _has_session_sandbox(self, session_id: str) -> bool:
        try:
            self._sandbox_manager.get_sandbox_for_session(session_id)
            return True
        except Exception:
            return False

    def _build_user_message(
        self,
        *,
        message: UnifiedMessage,
        channel_type: str,
        session_id: str,
        account_id: str,
    ) -> dict:
        return {
            "message_id": message.message_id,
            "content": message.content,
            "sender_id": message.sender_id,
            "channel_type": channel_type,
            "session_id": session_id,
            "account_id": account_id,
            "metadata": message.metadata,
            "timestamp": message.timestamp,
            "idle_timeout_minutes": self._idle_timeout_minutes,
            "activity_timeout": self._activity_timeout,
        }
