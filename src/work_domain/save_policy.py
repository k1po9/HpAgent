"""Generic save intent and current destination checks, separate from file publication."""
from __future__ import annotations

import json
from uuid import UUID

from work_domain.models import continuation
from work_domain.persistence import WorkRepository
from workspace.resources import ResourcePolicy

SAVE_WAIT_REASONS = frozenset({
    'workspace_save_destination_required', 'workspace_save_destination_unavailable',
    'workspace_save_permission_required', 'workspace_save_criteria_required',
})


class InvalidWorkspaceSave(ValueError):
    """A new save requirement must be corrected before its command is committed."""


def required_save_criterion(requirement):
    # Evidence types within one criterion are alternatives, not a conjunction.
    return any(c['required'] and set(c['evidence_types']) == {'workspace_saved'}
               for c in requirement['acceptance_criteria'])


def requires_workspace_save(requirement):
    return requirement['capability_key'] == 'generic_work' and (
        requirement['deliverable_policy'].get('required') or required_save_criterion(requirement))


def _has_save_evidence_criterion(requirement):
    return any(c['required'] and 'workspace_saved' in c['evidence_types']
               for c in requirement['acceptance_criteria'])


def validate_new_save_policy(requirement, *, workspace_save_requested=None):
    """Called after command replay lookup; old requirements remain readable/replayable."""
    if requirement['capability_key'] != 'generic_work':
        return
    if workspace_save_requested is False and requires_workspace_save(requirement):
        raise InvalidWorkspaceSave(
            'Ordinary published/downloadable Markdown does not require Workspace saving. '
            'Set workspace_save_requested=true only for an explicit user request to save '
            'to Workspace; otherwise use output_exists without a required workspace_saved criterion.')
    policy = requirement['deliverable_policy']
    if ((workspace_save_requested is True and not requires_workspace_save(requirement))
            or (policy.get('required') and not _has_save_evidence_criterion(requirement))):
        raise InvalidWorkspaceSave('required Generic save needs a required workspace_saved acceptance criterion')
    for field in ('directory_id', 'entry_id'):
        if policy.get(field) and UUID(policy[field]).int == 0:
            raise InvalidWorkspaceSave(
                'Workspace targets must not be zero UUID placeholders. If the user explicitly '
                'requested saving but no directory is selected, omit directory_id/entry_id, '
                'keep the required workspace_saved criterion and set deliverable_policy.required=false.')


def workspace_save_wait_reason(uow, work, requirement):
    """Check current account-owned topology/grants, including immutable legacy requirements."""
    if not requires_workspace_save(requirement):
        return None
    if not _has_save_evidence_criterion(requirement):
        return 'workspace_save_criteria_required'
    policy = requirement['deliverable_policy']
    try:
        directory = UUID(policy['directory_id']) if policy.get('directory_id') else None
        entry = UUID(policy['entry_id']) if policy.get('entry_id') else None
    except (ValueError, TypeError, AttributeError):
        return 'workspace_save_destination_required'
    if (directory is None and entry is None) or (directory is not None and directory.int == 0) or (entry is not None and entry.int == 0):
        return 'workspace_save_destination_required'
    account, wid = work['account_id'], work['work_id']
    if entry is not None:
        node = uow.execute(
            "SELECT kind,parent_id,destination_id FROM workspace_nodes WHERE account_id=%s AND node_id=%s AND deleted_at IS NULL",
            (account, entry),
        ).fetchone()
        if (not node or node['kind'] != 'file' or (directory is not None and node['parent_id'] != directory)
                or node['destination_id'] is None):
            return 'workspace_save_destination_unavailable'
        directory = node['parent_id']
    node = uow.execute(
        "SELECT kind FROM workspace_nodes WHERE account_id=%s AND node_id=%s AND deleted_at IS NULL",
        (account, directory),
    ).fetchone()
    if not node or node['kind'] != 'directory':
        return 'workspace_save_destination_unavailable'
    target, operation = (entry, 'update_content') if entry is not None else (directory, 'create_child')
    # Serialize with grant/revoke and the subsequent resource snapshot. Actual save
    # still rechecks the current grant and execution fence at commit time.
    ResourcePolicy._version(uow, account, 'work', wid)
    if not ResourcePolicy._grants(uow, account, 'work', wid, target, operation):
        return 'workspace_save_permission_required'
    return None


def defer_workspace_save(uow, work, requirement):
    """Keep unmet mandatory saves waiting without creating a Run, lease or budget reservation."""
    reason = workspace_save_wait_reason(uow, work, requirement)
    if reason is None:
        return False
    next_step = continuation('awaiting_input', reason)
    if work['continuation'] != next_step:
        current = dict(uow.execute(
            "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1,"
            "updated_at=GREATEST(now(),created_at,updated_at) WHERE account_id=%s AND work_id=%s RETURNING *",
            (json.dumps(next_step), work['account_id'], work['work_id']),
        ).fetchone())
        WorkRepository.event(uow, current, 'blocked', reason=reason)
    return True
