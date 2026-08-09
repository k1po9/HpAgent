"""
GitRepoManager —— per-account git 仓库管理。

每个 account 一个 git 仓库，session 对应一个分支。
Session 开始 → checkout -b hpagent/{session_id}
Session 结束 → squash merge 到 main + tag + 删分支
LLM 通过 Bash 工具自主执行 git add/commit/diff/log 等操作。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from workspace.isolation import WorkspaceRecoveryRequired

logger = logging.getLogger("HpAgent.GitRepoManager")


class GitRepoManager:
    """管理 per-account git 仓库的生命周期。

    Usage::

        mgr = GitRepoManager(repos_root=Path(".data/workspace"))
        await mgr.ensure_repo(account_id)
        branch = await mgr.start_session(account_id, session_id)
        # ... agent works via Bash git commands ...
        commit_hash = await mgr.end_session(account_id, session_id, task_summary)
    """

    def __init__(self, repos_root: Path) -> None:
        self._repos_root = Path(repos_root).resolve()

    def repo_path(self, account_id: str) -> Path:
        return self._repos_root / account_id / "repo"

    # ── lifecycle ──────────────────────────────────────────────────────

    async def ensure_repo(self, account_id: str) -> None:
        """初始化 git 仓库（幂等——已存在则跳过）。"""
        rp = self.repo_path(account_id)
        if (rp / ".git").exists():
            return
        rp.mkdir(parents=True, exist_ok=True)
        await self._run(account_id, "init")
        await self._run(account_id, "config", "user.name", "HpAgent")
        await self._run(account_id, "config", "user.email", "hpagent@local")
        await self._run(account_id, "commit", "--allow-empty", "-m", "root")
        logger.info("Git repo initialized: %s", rp)

    async def start_session(self, account_id: str, session_id: str) -> str:
        """为 session 创建并切换分支（幂等）。返回分支名。"""
        branch = f"hpagent/{session_id}"
        # 切回默认分支（确保不从其他 session 分支切出）
        default_branch = await self._get_default_branch(account_id)
        await self._run(account_id, "checkout", default_branch)
        # 检查分支是否已存在
        code, out, _ = await self._run(account_id, "branch", "--list", branch)
        if out.strip():
            # 分支已存在（session 复用），直接 checkout
            await self._run(account_id, "checkout", branch)
            logger.debug("Session branch already exists, checked out: %s", branch)
        else:
            code, _, err = await self._run(account_id, "checkout", "-b", branch)
            if code != 0:
                logger.warning("Failed to create branch '%s': %s", branch, err.strip())
            else:
                logger.info("Session branch created: %s", branch)
        return branch

    async def end_session(
        self,
        account_id: str,
        session_id: str,
        task_summary: str = "",
    ) -> str | None:
        """结束 session：squash merge 到 main + tag + 删分支。

        返回 commit hash（成功）或 None（冲突/失败，分支保留供排查）。
        """
        branch = f"hpagent/{session_id}"

        # 保存脏状态（LLM 可能忘记 commit）
        await self._run(account_id, "stash")

        # 切回默认分支
        default_branch = await self._get_default_branch(account_id)
        code, _, err = await self._run(account_id, "checkout", default_branch)
        if code != 0:
            logger.warning("end_session checkout %s failed for %s: %s", default_branch, session_id, err.strip())
            return None

        # squash merge
        msg = f"session:{session_id}: {task_summary}" if task_summary else f"session:{session_id}"
        code, _, err = await self._run(account_id, "merge", "--squash", branch)
        if code != 0:
            await self._run(account_id, "merge", "--abort")
            logger.warning("Merge conflict for %s, branch '%s' preserved", session_id, branch)
            return None

        code, _, err = await self._run(account_id, "commit", "-m", msg)
        if code != 0:
            logger.warning("Squash commit failed for %s: %s", session_id, err.strip())
            return None

        # tag 保留完整分支历史（即使分支已删除）
        await self._run(account_id, "tag", "-a", branch, "-m", task_summary or session_id)

        # 删除分支
        await self._run(account_id, "branch", "-d", branch)

        # 读 commit hash
        _, out, _ = await self._run(account_id, "rev-parse", "HEAD")
        commit_hash = out.strip()[:8]

        logger.info("Session %s merged to main: commit=%s", session_id, commit_hash)
        return commit_hash

    # ── web provisioning ────────────────────────────────────────────────
    # Worker-owned safe creation of missing resources for Web Runs.  These
    # methods create a repo / session branch ONLY when absence is provably
    # safe; every ambiguous state raises ``WorkspaceRecoveryRequired`` so the
    # execution fails closed instead of adopting or discarding unknown state.

    async def ensure_session_workspace(self, account_id: str, session_id: str) -> None:
        """Provision the missing per-account repo / ``hpagent/{session_id}`` branch.

        Recovery of an already-provisioned repo (verify branch + clean state,
        safe checkout) is left to ``WorkspaceRecoveryGuard``; this method only
        creates what is provably absent.  Callers must hold the Account lock.
        """
        await self.provision_repo_if_missing(account_id)
        await self.provision_session_branch_if_missing(account_id, session_id)

    async def provision_repo_if_missing(self, account_id: str) -> None:
        """Create the per-account repo only when the path is absent or empty.

        An existing non-empty directory that is not a Git repo is refused: it
        cannot be proven safe to adopt, so provisioning fails closed instead.
        """
        rp = self.repo_path(account_id)
        if (rp / ".git").exists():
            return  # already a valid worktree (dir or gitdir pointer)
        if rp.exists():
            if not rp.is_dir():
                raise WorkspaceRecoveryRequired(
                    "workspace path exists but is not a directory"
                )
            if any(rp.iterdir()):
                raise WorkspaceRecoveryRequired(
                    "workspace directory exists but is not a valid Git worktree"
                )
        rp.mkdir(parents=True, exist_ok=True)
        await self._run_checked(account_id, "init")
        await self._run_checked(account_id, "config", "user.name", "HpAgent")
        await self._run_checked(account_id, "config", "user.email", "hpagent@local")
        await self._run_checked(account_id, "commit", "--allow-empty", "-m", "root")
        logger.info("Git repo provisioned: %s", rp)

    async def provision_session_branch_if_missing(
        self, account_id: str, session_id: str
    ) -> None:
        """Create ``hpagent/{session_id}`` only from a clean, quiescent tree.

        An existing branch is left to ``WorkspaceRecoveryGuard`` (verify +
        checkout).  Creating a missing branch requires no unfinished Git
        operation, no unproven ``index.lock`` and a clean working tree;
        otherwise fail closed rather than stash/reset/force-switch.
        """
        branch = self._session_branch(session_id)
        code, out, _ = await self._run(account_id, "branch", "--list", branch)
        if code != 0:
            raise WorkspaceRecoveryRequired("branch check failed")
        if out.strip():
            return  # exists → recovery guard owns verification/checkout
        git_dir = self._git_dir(account_id)
        if any(
            (git_dir / marker).exists()
            for marker in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD")
        ):
            raise WorkspaceRecoveryRequired("unfinished Git operation")
        if (git_dir / "index.lock").exists():
            raise WorkspaceRecoveryRequired("unproven Git index.lock ownership")
        code, status_out, _ = await self._run(account_id, "status", "--porcelain")
        if code != 0 or status_out.strip():
            raise WorkspaceRecoveryRequired(
                "dirty state belongs to another or unknown Session"
            )
        default_branch = await self._get_default_branch(account_id)
        await self._run_checked(account_id, "checkout", default_branch)
        await self._run_checked(account_id, "checkout", "-b", branch)
        logger.info("Session branch provisioned: %s", branch)

    @staticmethod
    def _session_branch(session_id: str) -> str:
        return f"hpagent/{session_id}"

    def _git_dir(self, account_id: str) -> Path:
        rp = self.repo_path(account_id)
        candidate = rp / ".git"
        if candidate.is_dir():
            return candidate
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir: "):
                return (rp / text.removeprefix("gitdir: ")).resolve()
        raise WorkspaceRecoveryRequired("workspace is not a valid Git worktree")

    async def _run_checked(self, account_id: str, *args: str) -> None:
        code, _, err = await self._run(account_id, *args)
        if code != 0:
            raise WorkspaceRecoveryRequired(err.strip() or "Git command failed")

    # ── queries ────────────────────────────────────────────────────────

    async def _get_default_branch(self, account_id: str) -> str:
        """获取仓库默认分支名（动态检测，兼容 main/master 等命名）。

        通过 rev-parse 解析 HEAD 指向，避免硬编码 "main"。
        """
        code, out, _ = await self._run(account_id, "rev-parse", "--abbrev-ref", "HEAD")
        if code == 0 and out.strip():
            return out.strip()
        return "main"  # 最终兜底

    async def has_changes(self, account_id: str) -> bool:
        _, out, _ = await self._run(account_id, "status", "--porcelain")
        return bool(out.strip())

    async def get_log(self, account_id: str, limit: int = 20) -> str:
        _, out, _ = await self._run(account_id, "log", "--oneline", f"-{limit}")
        return out

    # ── internal ───────────────────────────────────────────────────────

    async def _run(self, account_id: str, *args: str) -> tuple[int, str, str]:
        """Run git command in the account's repo. Returns (exit_code, stdout, stderr)."""
        rp = self.repo_path(account_id)
        cmd = ["git", "-C", str(rp)] + list(args)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return (
            proc.returncode or 0,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )
