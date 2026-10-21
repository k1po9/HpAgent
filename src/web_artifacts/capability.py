"""HTML candidates use the shared invocation contract, independently of their caller."""

from run_domain.capability_contracts import CapabilityDefinition


def html_definition():
    return CapabilityDefinition(
        key="html",
        contract_version=1,
        implementation_version="html-v1",
        description="Create an immutable HTML version from text or an authorized exact ResultRef. Checks cover document shape, size and preview policy; browser interactions are not tested.",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 20000},
                "source_ref": {"type": "string", "format": "uuid"},
                "previous_ref": {"type": "string", "format": "uuid"},
                "instruction": {"type": "string", "maxLength": 4000},
            },
            "oneOf": [
                {"required": ["text"], "not": {"required": ["source_ref"]}},
                {"required": ["source_ref"], "not": {"required": ["text"]}},
            ],
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
        required_services=("html",),
        resource_operations=("read_content",),
        side_effect_policy="candidate_output",
        limits={
            "schema_version": 1,
            "max_duration_seconds": 600,
            "max_model_calls": 1,
            "max_tool_calls": 0,
        },
        retry_policy={"schema_version": 1, "max_attempts": 3},
        cancellation_policy="cooperative_bounded",
        verifier_keys=("html.basic",),
    )


def html_scope(inputs):
    return dict(
        schema_version=1,
        node_ids=[],
        file_ids=[],
        tool_names=[],
        result_ids=[inputs[k] for k in ("source_ref", "previous_ref") if k in inputs],
    )
