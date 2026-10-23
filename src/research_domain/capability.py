"""Trusted Research v1 definition. It creates candidates, never saves or notifies."""

from run_domain.capability_contracts import CapabilityDefinition


def research_definition():
    return CapabilityDefinition(
        key="research",
        contract_version=1,
        implementation_version="research-web-v2",
        description="Research an objective and return immutable Markdown and scoped citation checks.",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["objective"],
            "properties": {
                "objective": {"type": "string", "minLength": 1, "maxLength": 20000},
                "source_strategy": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        **{
                            k: {"type": "boolean"}
                            for k in ("public_web", "official_sources", "github", "rss")
                        },
                        "uploaded_files": {"const": False},
                        "freshness_days": {"type": "integer", "minimum": 0, "default": 0, "description": "Omitted or 0 means no date restriction. Positive days require a confirmable source date within that interval; unknown dates cannot satisfy it."},
                        "preferred_domains": {
                            "type": "array",
                            "maxItems": 30,
                            "items": {"type": "string"},
                        },
                        "allowed_domains": {"type": "array", "maxItems": 30, "items": {"type": "string"}},
                        "excluded_domains": {"type": "array", "maxItems": 30, "items": {"type": "string"}},
                        "rss_feeds": {"type": "array", "maxItems": 30, "items": {"type": "string"}},
                    },
                },
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                k: {}
                for k in (
                    "schema_version",
                    "outcome",
                    "summary",
                    "outputs",
                    "checks",
                    "gaps",
                    "error",
                    "provenance",
                )
            },
            "required": ["schema_version", "outcome", "outputs", "checks"],
        },
        executor_kind="fixed_workflow",
        execution_mode="isolated_execution",
        allowed_surfaces=("chat", "work"),
        required_services=("research",),
        resource_operations=("read_content",),
        side_effect_policy="candidate_output",
        limits={
            "schema_version": 1,
            "max_duration_seconds": 3600,
            "max_model_calls": 1,
            "max_tool_calls": 23,
        },
        retry_policy={"schema_version": 1, "max_attempts": 4},
        cancellation_policy="cooperative_bounded",
        verifier_keys=("research.basic",),
    )
