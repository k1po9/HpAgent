"""Main's account-bound Work tools share the API command boundary."""
import asyncio
import json
import re
from uuid import UUID

from langchain_core.tools import StructuredTool

from application.prompt_catalog import agent_prompt
from research_domain.capability import research_definition
from work_domain.models import Requirement

MAIN_ROLE_CONTEXT = agent_prompt("main")



def main_requirement_schema():
    """Explicit model contract for the existing Work command, validated by Requirement."""
    string = {"type": "string"}
    version = {"type": "integer", "const": 1}
    return {
        "type": "object", "additionalProperties": False,
        "description": "Complete replacement requirement. No top-level schema_version; it belongs inside spec, timing, deliverable_policy and resource_requests items.",
        "required": ["objective", "capability_key", "spec"],
        "properties": {
            "objective": {"type": "string", "minLength": 1, "maxLength": 20000},
            "capability_key": {"enum": ["generic_work", "research_report", "reminder"]},
            "spec": {"type": "object", "additionalProperties": False, "required": ["schema_version"],
                "description": "generic_work: only schema_version and optional reasoning_mode=react/plan_and_execute. source_strategy/report_format belong only to research_report; content/target_ref only to reminder.",
                "properties": {"schema_version": version, "reasoning_mode": {"enum": ["react", "plan_and_execute"]},
                    "content": string, "target_ref": string, "report_format": string,
                    "source_strategy": research_definition().input_schema["properties"]["source_strategy"]}},
            "constraints": {"type": "array", "items": string},
            "completion_mode": {"enum": ["deliverable", "ongoing"]},
            "acceptance_criteria": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["id", "required", "evidence_types"], "properties": {"id": string, "required": {"type": "boolean"},
                    "evidence_types": {"type": "array", "minItems": 1, "items": {"enum": ["research_report", "artifact_version", "operation_receipt", "delivery_receipt", "user_acceptance", "output_exists", "check_passed", "workspace_saved"]}}}}},
            "timing": {"type": "object", "additionalProperties": False, "required": ["schema_version", "kind", "timezone"],
                "properties": {"schema_version": version, "kind": {"enum": ["immediate", "once", "daily"]}, "timezone": string,
                    "due_at": string, "local_time": string, "missed_fire_policy": string}},
            "resource_requests": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["schema_version", "node_id", "operations"], "properties": {"schema_version": version, "node_id": {"type": "string", "format": "uuid"},
                    "operations": {"type": "array", "minItems": 1, "items": {"enum": ["list_metadata", "read_content", "create_child", "update_content", "delete_entry"]}}, "recursive": {"type": "boolean"}}}},
            "deliverable_policy": {"type": "object", "additionalProperties": False,
                "description": "required=true requires a nonempty directory_id UUID and a required workspace_saved criterion. Directory metadata never grants the Work access.",
                "required": ["schema_version", "required"], "properties": {"schema_version": version, "required": {"type": "boolean"},
                    "directory_id": {"type": "string", "format": "uuid"}, "entry_id": {"type": "string", "format": "uuid"},
                    "operation": string, "notification_target_ref": string}},
        },
    }

def create_main_work_tools(context, commands):
    account = UUID(context['account_id'])
    conversation = UUID(context['conversation_id'])
    source_message = UUID(context['trigger_message_id'])
    public_audience = (context.get('scope') in {'group', 'guild'} or
                       context.get('interaction_profile') == 'qq_group' or
                       context.get('surface') == 'qq_group')

    def public_work(work):
        # Account ownership authorizes commands, not disclosure to a shared audience.
        return {**{key: work[key] for key in ('work_id', 'status', 'row_version',
                                             'current_requirement_revision')},
                'continuation': {'kind': work['continuation']['kind']}}

    def encode(value):
        value = value.body if hasattr(value, 'body') else value
        if public_audience:
            if 'work' in value:
                value = {'work': public_work(value['work'])}
            elif 'items' in value:
                value = {'items': [public_work(item) for item in value['items']],
                         'next_before': value['next_before']}
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def parse_requirement(requirement):
        allowed = {'objective', 'capability_key', 'spec', 'completion_mode', 'constraints',
                   'acceptance_criteria', 'timing', 'resource_requests', 'deliverable_policy'}
        if isinstance(requirement, dict) and set(requirement) - allowed:
            raise ValueError('requirement has unknown top-level fields: ' +
                ', '.join(sorted(set(requirement) - allowed))[:200] +
                '. There is no top-level schema_version; schema_version belongs inside spec, timing and deliverable_policy. '
                'Allowed top-level fields: ' + ', '.join(sorted(allowed)))
        policy = requirement.get('deliverable_policy') if isinstance(requirement, dict) else None
        if isinstance(policy, dict) and policy.get('required') and not policy.get('directory_id'):
            raise ValueError('deliverable_policy.required=true needs a nonempty directory_id UUID. '
                'Use the exact directory supplied by the user if available. If it is unresolved, '
                'keep a required workspace_saved acceptance criterion, set deliverable_policy.required=false '
                'and ask the user to select the directory through the task editor; never invent a UUID.')
        parsed = Requirement.from_dict(requirement)
        if parsed.capability_key == 'generic_work' and (parsed.deliverable_policy or {}).get('required') and not any(
            c['required'] and 'workspace_saved' in c['evidence_types'] for c in parsed.acceptance_criteria
        ):
            raise ValueError('required Generic save needs a required workspace_saved acceptance criterion')
        return parsed

    async def accept_work(title: str, requirement: dict, mandate_slot: str) -> str:
        """Accept an explicit continuing mandate, return committed identity immediately.

        requirement: {objective: string, capability_key: generic_work/research_report/reminder,
        spec: {schema_version: 1}, completion_mode: deliverable/ongoing (default deliverable),
        constraints?: string[], acceptance_criteria?: [{id, required: bool, evidence_types:
        [output_exists/check_passed/workspace_saved]}], timing?: {schema_version: 1,
        kind: immediate/once/daily, timezone: IANA, due_at?: offset datetime, local_time?: HH:MM},
        deliverable_policy?: {schema_version: 1, required: bool, directory_id?: UUID}}.
        Reminder spec additionally requires content and optionally target_ref.
        Daily requires ongoing. A generic_work with required Workspace saving needs a required workspace_saved acceptance criterion. Resource references are requests, never grants.
        If the save directory is unresolved, keep a required workspace_saved criterion with deliverable_policy.required=false until the user selects the destination in the task editor; never invent a directory UUID.
        Preserve the same mandate_slot and exact payload on retry of this user message.
        """
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', mandate_slot):
            raise ValueError('mandate_slot must be a stable short identifier')
        try:
            parsed = parse_requirement(requirement)
        except ValueError as exc:
            raise ValueError("委托未建立：" + str(exc)) from exc
        result = await asyncio.to_thread(commands.accept, account,
            f'main-accept:{source_message}:{mandate_slot}', title, parsed,
            conversation_id=conversation, source_message_id=source_message)
        receipt = result.body if hasattr(result, 'body') else result
        return encode({**receipt, 'handoff': {
            'work_id': receipt['work']['work_id'],
            'execution_owner': 'background_work',
            'next_action': agent_prompt('handoff'),
        }})

    async def list_works() -> str:
        """List up to 50 account-owned Work candidates for explicit disambiguation."""
        result = await asyncio.to_thread(commands.list, account)
        # Discovery only exposes bounded summaries, not private execution logs.
        return encode({'items': [{k: item[k] for k in ('work_id','title','status','row_version',
                    'current_requirement_revision','continuation')} for item in result['items']],
                       'next_before': result['next_before']})

    async def get_work(work_id: str) -> str:
        """Query an exact Work belonging to the current account."""
        result = await asyncio.to_thread(commands.get, account, UUID(work_id))
        return encode(result)

    async def revise_work(work_id: str, row_version: int, requirement: dict, operation_id: str = '') -> str:
        """Explicitly replace objective/constraints with a complete requirement (same schema as accept_work).

        First get_work for the current requirement and row_version. Preserve unchanged fields.
        Creates a new revision and cancels the old coordinator; ordinary chat never calls this.
        """
        if not operation_id:
            raise ValueError('trusted operation identity required')
        return encode(await asyncio.to_thread(commands.revise, account, UUID(work_id), operation_id,
                                               row_version, parse_requirement(requirement)))

    async def control_work(work_id: str, row_version: int, action: str, operation_id: str = '') -> str:
        """Explicitly pause, resume or stop an exact Work, independently of stopping the chat."""
        if not operation_id:
            raise ValueError('trusted operation identity required')
        return encode(await asyncio.to_thread(commands.control, account, UUID(work_id), operation_id,
                                               row_version, action))

    async def advance_work(work_id: str, row_version: int, operation_id: str = '') -> str:
        """Explicitly advance/retry an exact Work; admission uses the latest requirement."""
        if not operation_id:
            raise ValueError('trusted operation identity required')
        return encode(await asyncio.to_thread(commands.advance, account, UUID(work_id), operation_id, row_version))

    async def link_work(work_id: str, row_version: int, operation_id: str = '') -> str:
        """Link a resolved Work to this conversation without granting resources or subscribing channels."""
        if not operation_id:
            raise ValueError('trusted operation identity required')
        return encode(await asyncio.to_thread(commands.link, account, UUID(work_id), conversation, operation_id, row_version))

    tools = []
    for function in (accept_work, list_works, get_work, revise_work, control_work, advance_work, link_work):
        read_only = function in (list_works, get_work)
        metadata = {'side_effect_class': 'read_only' if read_only else 'idempotent_write'}
        if function not in (accept_work, list_works, get_work):
            metadata['idempotency_key_argument'] = 'operation_id'
        tool = StructuredTool.from_function(coroutine=function, name=function.__name__,
                                           description=function.__doc__, metadata=metadata)
        if function in (accept_work, revise_work):
            schema = tool.args_schema.model_json_schema()
            schema["properties"]["requirement"] = main_requirement_schema()
            tool.args_schema = schema
        tools.append(tool)
    return tools
