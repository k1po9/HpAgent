"""
Workspace —— 多用户持久化工作目录管理。

============================================================================
设计意图
============================================================================

  为每个用户提供独立的本地工作区，支持多步骤、中间结果可见的任务。
  借鉴 MLflow 的"元数据与制品分离"模式:
    - SQLite 存元数据（用户、会话、产出索引）
    - 本地文件系统存所有实体文件

  目录结构:
    users_workspace/<user_uuid>/
      ├── skills/          # 用户自定义技能（跨 session）
      ├── sessions/        # 会话目录
      │   └── <session_id>/
      │       ├── conversation/   # 对话记录（JSONL + 摘要）
      │       ├── execution/      # 工具执行计划与日志
      │       ├── workspace/      # nsjail 工作区（input/scratch/output）
      │       └── resources/      # 外部资源引用清单
      ├── persistent/      # 用户长期资产
      └── user_profile.yaml

============================================================================
与 nsjail 集成
============================================================================

  WorkspaceManager 生成 nsjail bind mount 参数:
    - workspace/ 子目录 → --bindmount (读写)
    - skills/ 子目录 → --bindmount_ro (只读)

  使用示例:
    wm = WorkspaceManager(root=Path("users_workspace"))
    session = wm.create_session(user_uuid="u1", session_id="s1")
    bind_mounts = wm.get_nsjail_mounts(user_uuid="u1", session_id="s1")
    config = NsjailConfig(bind_mounts=bind_mounts)
"""
from .models import User, Session, Artifact, SessionStatus
from .manager import WorkspaceManager

__all__ = [
    "User",
    "Session",
    "Artifact",
    "SessionStatus",
    "WorkspaceManager",
]
