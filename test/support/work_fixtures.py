"""Test setup using real Work commands and separately authorized resources."""
from dataclasses import replace
from uuid import UUID, uuid4

from research_domain.models import SourceStrategy
from work_domain.models import Requirement
from workspace.resources import ResourcePolicy


def research_requirement(objective, *, output_directory_id=None, output_required=False):
    policy = {'schema_version': 1, 'required': output_required}
    if output_directory_id:
        policy['directory_id'] = str(output_directory_id)
    return Requirement(
        objective, 'research_report', {'schema_version': 1, 'source_strategy': SourceStrategy().to_dict()},
        completion_mode='ongoing',
        acceptance_criteria=({'id': 'report', 'required': True, 'evidence_types': ['research_report']},),
        deliverable_policy=policy,
    ).to_dict()


def accept_research(commands, account_id, key, title, objective, *,
                    output_directory_id=None, output_required=False, conversation_id=None):
    result = commands.accept(account_id, key, title, Requirement.from_dict(research_requirement(
        objective, output_directory_id=output_directory_id, output_required=output_required,
    )), conversation_id=conversation_id)
    if output_directory_id:
        ResourcePolicy(commands.database).grant(
            account_id, 'work', UUID(result.body['work']['work_id']), output_directory_id,
            ['create_child', 'list_metadata', 'read_content'], True,
        )
    return result


def advance_work(commands, account_id, work_id, key):
    work = commands.get(account_id, work_id)['work']
    return commands.advance(account_id, work_id, key, work['row_version'])


def revise_output(commands, account_id, work_id, directory, required, operation='create_child', entry=None):
    work = commands.get(account_id, work_id)['work']
    value = {k: v for k, v in work['requirement'].items() if k in Requirement.__dataclass_fields__}
    policy = {'schema_version': 1, 'directory_id': str(directory), 'required': required,
              'operation': operation}
    if entry:
        policy['entry_id'] = str(entry)
    ResourcePolicy(commands.database).grant(account_id, 'work', work_id, entry or directory,
                                           [operation, 'read_content', 'list_metadata'], not bool(entry))
    return commands.revise(account_id, work_id, str(uuid4()), work['row_version'],
                           replace(Requirement.from_dict(value), deliverable_policy=policy))
