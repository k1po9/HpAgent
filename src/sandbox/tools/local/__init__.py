from .fs_read import create_fs_read_tool
from .fs_write import create_fs_write_tool
from .fs_edit import create_fs_edit_tool
from .glob_ import create_glob_tool
from .grep import create_grep_tool
from .bash import create_bash_tool
from .reminder import create_reminder_tool, create_list_reminders_tool, create_cancel_reminder_tool

LOCAL_TOOL_FACTORIES = {
    "fs_read": create_fs_read_tool,
    "fs_write": create_fs_write_tool,
    "fs_edit": create_fs_edit_tool,
    "Glob": create_glob_tool,
    "Grep": create_grep_tool,
    "Bash": create_bash_tool,
    "create_reminder": create_reminder_tool,
    "list_reminders": create_list_reminders_tool,
    "cancel_reminder": create_cancel_reminder_tool,
}

__all__ = ["LOCAL_TOOL_FACTORIES"]
