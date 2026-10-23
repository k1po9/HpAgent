"""Shared, test-only governance installed in both API and Worker processes."""
from __future__ import annotations

import json


def configure_governance(manifest):
    from resources import work_budget

    work_budget.DEFAULT_LIMITS = {
        **work_budget.DEFAULT_LIMITS,
        'model_total_tokens': manifest['effective_governance']['work_tokens'],
        'model_input_tokens': 200000,
        'model_output_tokens': 40000,
        'model_calls': manifest['effective_governance']['work_model_calls'],
    }

    from persistence.repositories import RunBudgetRepository
    original = RunBudgetRepository.create_snapshot
    governance = manifest['effective_governance']

    def create_snapshot(self, uow, run_id, account_id, conversation_id, policy_version,
                        mode, limits, final_response_reserve_tokens):
        values = json.loads(limits)
        if policy_version == 'work-foundation-v1' and values.get('model_calls') == 40:
            values.update(model_input_tokens=governance['generic_work_run_input_tokens'],
                          model_output_tokens=governance['generic_work_run_output_tokens'],
                          model_total_tokens=governance['generic_work_run_tokens'])
            limits = json.dumps(values)
            policy_version += '-s7-1'
        return original(self, uow, run_id, account_id, conversation_id, policy_version,
                        mode, limits, final_response_reserve_tokens)

    RunBudgetRepository.create_snapshot = create_snapshot
