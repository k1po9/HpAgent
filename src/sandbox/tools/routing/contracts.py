"""Explicit routing contracts for every native tool exposed to the LLM."""
from __future__ import annotations

from typing import Any

from .models import ResourceScope, ToolExposure, ToolRoutingSpec

_CURRENT_RUN = ResourceScope.CURRENT_RUN
_WORKSPACE = ResourceScope.WORKSPACE
_BOTH_DIRECTIONS = frozenset({"input", "output"})
_READ_FILE_EXTENSIONS = frozenset({
    "txt", "md", "html", "csv", "json", "xml", "pdf", "docx", "xlsx", "pptx",
})


def _workspace(capability: str) -> ToolRoutingSpec:
    return ToolRoutingSpec(
        capability=capability, resource_scope=_WORKSPACE, requires_workspace=True,
    )


def _run_resource(capability: str, *extensions: str, directions=_BOTH_DIRECTIONS, **kwargs) -> ToolRoutingSpec:
    return ToolRoutingSpec(
        capability=capability, resource_scope=_CURRENT_RUN,
        requires_run_file_scope=True, accepts_extensions=frozenset(extensions),
        accepts_directions=frozenset(directions), **kwargs,
    )


def _run_context(capability: str, **kwargs) -> ToolRoutingSpec:
    return ToolRoutingSpec(
        capability=capability, resource_scope=_CURRENT_RUN,
        requires_run_file_scope=True, **kwargs,
    )


NATIVE_ROUTING_SPECS: dict[str, ToolRoutingSpec] = {
    # Workspace-bound tools.
    "fs_read": _workspace("workspace.read"),
    "fs_write": _workspace("workspace.write"),
    "fs_edit": _workspace("workspace.edit"),
    "Glob": _workspace("workspace.glob"),
    "Grep": _workspace("workspace.grep"),
    "Bash": _workspace("workspace.shell"),

    # Session services that require no file resource.
    "create_reminder": ToolRoutingSpec("reminder.create"),
    "list_reminders": ToolRoutingSpec("reminder.list"),
    "cancel_reminder": ToolRoutingSpec("reminder.cancel"),

    # Current Run file reads.
    "read_file": _run_resource(
        "file.read", *_READ_FILE_EXTENSIONS,
        exposure=ToolExposure.FRONT_DOOR,
        front_door_family="current_run_file", front_door_priority=100,
    ),
    "inspect_pdf": _run_resource("file.pdf.inspect", "pdf"),
    "read_pdf_pages": _run_resource("file.pdf.read_pages", "pdf"),
    "extract_pdf_tables": _run_resource("file.pdf.extract_tables", "pdf"),
    "inspect_docx": _run_resource("file.docx.inspect", "docx"),
    "read_docx_paragraphs": _run_resource("file.docx.read_paragraphs", "docx"),
    "extract_docx_tables": _run_resource("file.docx.extract_tables", "docx"),
    "inspect_workbook": _run_resource("file.xlsx.inspect", "xlsx"),
    "read_sheet_range": _run_resource("file.xlsx.read_range", "xlsx"),
    "inspect_presentation": _run_resource("file.pptx.inspect", "pptx"),
    "read_slide": _run_resource("file.pptx.read_slide", "pptx"),

    # Legacy bounded analysis reads scope.inputs_root exclusively.
    "inspect_file": _run_resource("file.inspect", directions={"input"}),
    "search_file": _run_resource("file.search", directions={"input"}),
    "count_matches": _run_resource("file.count_matches", directions={"input"}),
    "text_stats": _run_resource("file.text_stats", directions={"input"}),

    # Creation needs a publication context but no pre-existing resource.
    "create_docx": _run_context("file.docx.create"),
    "create_workbook": _run_context("file.xlsx.create"),
    "create_presentation": _run_context("file.pptx.create"),

    # Patches accept both authoritative inputs and outputs resolved by FileResourceResolver.
    "replace_docx_text": _run_resource("file.docx.replace_text", "docx"),
    "append_docx_section": _run_resource("file.docx.append_section", "docx"),
    "write_sheet_range": _run_resource("file.xlsx.write_range", "xlsx"),
    "replace_slide": _run_resource("file.pptx.replace_slide", "pptx"),
    "convert_file_to_pdf": _run_resource(
        "file.convert.pdf", "docx", "xlsx", "pptx",
        required_services=frozenset({"gotenberg"}),
    ),
    # PersistentWebFileService currently accepts any owned input/output Run file.
    "save_persistent_file": _run_resource("file.persist", directions=_BOTH_DIRECTIONS),
}


def routing_for(tool: Any, category: str) -> ToolRoutingSpec:
    """Resolve an explicit contract; native tools never receive a silent default."""
    name = tool.name
    metadata = dict(getattr(tool, "metadata", {}) or {})
    if category == "native":
        try:
            return NATIVE_ROUTING_SPECS[name]
        except KeyError as exc:
            raise ValueError(
                f"native tool '{name}' has no explicit routing contract"
            ) from exc
    if category == "mcp":
        server = str(metadata.get("server") or "unknown")
        return ToolRoutingSpec(
            capability=f"mcp.{server}.{name}",
            exposure=(
                ToolExposure.ALWAYS
                if metadata.get("required") else ToolExposure.SEMANTIC
            ),
        )
    if category == "skill":
        return ToolRoutingSpec(capability=f"skill.{name}")
    raise ValueError(f"unknown tool category: {category}")
