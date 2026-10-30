"""HTML candidates use the shared invocation contract, independently of their caller."""

from copy import deepcopy

from run_domain.capability_contracts import CapabilityDefinition


def html_definition():
    return CapabilityDefinition(
        key="html",
        contract_version=1,
        implementation_version="html-v1",
        description="将来源内容制作为可保存的交互 HTML 成果版本。返回不可变 Artifact Version / ResultRef 和基础检查；直接在聊天或最终回复中输出 HTML 不会产生这些事实。Create an immutable HTML version from text or an authorized exact ResultRef. Checks cover document shape, size and preview policy; browser interactions are not tested. source_ref supplies content only; the child does not inherit the parent conversation or Work brief. Put every requested layout, control, interaction, accessible name and revision constraint in instruction.",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 20000, "description": "Only for literal source content when source_ref is absent. Never supply nonempty text with source_ref. Put layout/change requests in instruction."},
                "source_ref": {"type": "string", "format": "uuid", "description": "准确来源 ResultRef.ref_id，例如成功研究的 Markdown 引用；服务端读取该不可变版本，无需把正文抄入 text。"},
                "previous_ref": {"type": "string", "format": "uuid", "description": "修改时固定用户选定的旧 HTML ResultRef.ref_id；跨 Run 使用前父侧须显式采用。"},
                "instruction": {"type": "string", "maxLength": 4000, "description": "完整转交用户要求的布局、交互、控件名称、内容结构及修改约束。source_ref 仅提供来源正文，子能力不会自动读取父任务或聊天；需要交互或修改时请显式填写。Forward the full requested behavior and revision constraints, not just a generic conversion request."},
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


def html_parent_input_schema(definition, *, model_facing=False):
    """The model-facing HTML tool requires an explicit delegated objective.

    Fixed/programmatic v1 callers keep their optional instruction contract.
    Parent agents must carry the requested behavior across the isolated boundary.
    """
    schema = deepcopy(definition['input_schema'])
    schema['required'] = list(dict.fromkeys([*schema.get('required', []), 'instruction']))
    schema['properties']['instruction']['minLength'] = 1
    if model_facing:
        schema.pop('oneOf', None)
        schema['required'] = list(schema['properties'])
        for name in ('text', 'source_ref', 'previous_ref'):
            schema['properties'][name]['type'] = ['string', 'null']
    return schema
