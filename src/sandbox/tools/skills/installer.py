"""
Skill 安装器 —— 从开放技能生态系统安装 skills。

支持:
  - npx skills add: 调用 skills CLI 从 skills.sh 生态安装
  - 直接 GitHub 克隆: 从 GitHub repo 获取 SKILL.md
  - 本地目录复制: 将已有 SKILL.md 目录复制到 skills_path

安装后的 skill 目录结构:
  tools/skills/<skill-name>/
    SKILL.md
    scripts/      (optional)
    references/   (optional)
    assets/       (optional)
"""
import os
import shutil
import subprocess
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("HpAgent.SkillInstaller")


def install_via_cli(package: str, skills_path: str, global_install: bool = True) -> bool:
    """通过 npx skills CLI 安装 skill。

    Args:
        package: 包名，格式 owner/repo 或 owner/repo@skill-name
        skills_path: 目标安装目录 (tools/skills/)
        global_install: 是否全局安装（用户级别）

    Returns:
        True 表示安装成功
    """
    cmd = ["npx", "skills", "add", package]
    if global_install:
        cmd.append("-g")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "SKILLS_PATH": skills_path},
        )
        if result.returncode == 0:
            logger.info("Skill installed via CLI: %s", package)
            return True
        else:
            logger.warning("CLI install failed for %s: %s", package, result.stderr.strip())
            return False
    except FileNotFoundError:
        logger.warning("npx not found, cannot install skills via CLI")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("CLI install timed out for %s", package)
        return False


def install_from_dir(source_dir: str, skills_path: str) -> Optional[str]:
    """从本地目录安装一个 SKILL.md skill。

    将整个 skill 目录复制到 skills_path 下。

    Args:
        source_dir: 源 skill 目录（包含 SKILL.md）
        skills_path: 目标 skills 目录 (tools/skills/)

    Returns:
        安装后的 skill 名称，失败返回 None
    """
    src = Path(source_dir).resolve()
    dest_dir = Path(skills_path).resolve()

    skillmd = src / "SKILL.md"
    if not skillmd.exists():
        logger.warning("No SKILL.md found in %s", src)
        return None

    # 读取 name 字段确定目录名
    try:
        from .skillmd import parse_skillmd
        fm, _ = parse_skillmd(skillmd)
        skill_name = fm["name"]
    except Exception as e:
        logger.warning("Cannot parse SKILL.md: %s", e)
        return None

    target = dest_dir / skill_name
    if target.exists():
        logger.info("Skill already installed: %s", skill_name)
        return skill_name

    try:
        shutil.copytree(src, target, dirs_exist_ok=True)
        logger.info("Skill installed from dir: %s → %s", src, target)
        return skill_name
    except Exception as e:
        logger.warning("Failed to install skill from %s: %s", src, e)
        return None


def discover_installed_skills(skills_path: str) -> list[str]:
    """列出已安装的 SKILL.md skills。

    扫描 skills_path 下所有子目录中的 SKILL.md 文件。
    """
    root = Path(skills_path)
    found = []
    for skillmd in root.glob("*/SKILL.md"):
        try:
            from .skillmd import parse_skillmd
            fm, _ = parse_skillmd(skillmd)
            found.append(fm["name"])
        except Exception:
            continue
    return found


def list_installed(skills_path: str) -> list[dict]:
    """列出已安装 skills 的详细信息。"""
    root = Path(skills_path)
    result = []
    for skillmd in sorted(root.glob("*/SKILL.md")):
        try:
            from .skillmd import parse_skillmd
            fm, _ = parse_skillmd(skillmd)
            result.append({
                "name": fm.get("name", "unknown"),
                "description": fm.get("description", ""),
                "license": fm.get("license"),
                "path": str(skillmd.parent),
            })
        except Exception:
            continue
    return result
