"""Main's account-bound Work tools share the API command boundary."""
import asyncio
import json
import re
from uuid import UUID

from langchain_core.tools import StructuredTool

from work_domain.models import Requirement

MAIN_ROLE_CONTEXT = (
    'You are the Main Agent for this interaction. Answer simple requests directly. '
    'For explicitly delegated continuing work use accept_work and return its committed Work ID; '
    'do not wait for the background execution. A cancelled chat does not withdraw accepted Work. '
    'Use exact Work IDs or query bounded candidates; clarify ambiguous references before mutations. '
    'Before accepting on chat retry, consult committed mandate receipts and reuse their slots/IDs. '
    'Use stable mandate_slot names within the original user message, including chat retries. '
    'Requirement resource references and destinations do not grant permissions. '
    'Supported capabilities: reminder, research_report, generic_work. '
    'Timing requires an IANA timezone; once due_at requires an explicit offset, daily uses HH:MM. '
    'Reminder targets support account_inbox or current_channel (verified QQ source only); '
    'use current_channel when the user requests a reminder in this QQ interaction. '
    'Enqueue is separate from channel acceptance.'
)


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

    async def accept_work(title: str, requirement: dict, mandate_slot: str) -> str:
        """Accept one explicit mandate and return its committed identity; requires structured spec/timing."""
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', mandate_slot):
            raise ValueError('mandate_slot must be a stable short identifier')
        result = await asyncio.to_thread(commands.accept, account,
            f'main-accept:{source_message}:{mandate_slot}', title, Requirement.from_dict(requirement),
            conversation_id=conversation, source_message_id=source_message)
        return encode(result)

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
        """Submit an immutable new requirement after resolving the exact Work and version."""
        if not operation_id:
            raise ValueError('trusted operation identity required')
        return encode(await asyncio.to_thread(commands.revise, account, UUID(work_id), operation_id,
                                               row_version, Requirement.from_dict(requirement)))

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
        tools.append(StructuredTool.from_function(coroutine=function, name=function.__name__,
                                                  description=function.__doc__, metadata=metadata))
    return tools
