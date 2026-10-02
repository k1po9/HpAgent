"""Server-owned, versioned strategies. Unsupported capabilities never fall back."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class StrategySnapshot:
    strategy_kind: str
    executor_key: str
    executor_version: int = 1
    strategy_policy_version: int = 1

    def to_dict(self):
        return asdict(self)


class StrategyRegistry:
    _strategies = {
        'reminder': StrategySnapshot('deterministic', 'reminder'),
        'research_report': StrategySnapshot('fixed_workflow', 'research_report'),
        'generic_work': StrategySnapshot('generic_agent', 'work_agent'),
    }

    def plan(self, requirement):
        capability = requirement['capability_key']
        if capability not in self._strategies:
            raise ValueError('unsupported capability')
        return self._strategies[capability]

    def validate_run(self, run):
        expected = (StrategySnapshot('generic_agent', 'chat_agent') if run['source_kind'] == 'chat'
                    else next((s for s in self._strategies.values()
                               if s.executor_key == run['executor_key']), None))
        if expected is None or any(run[k] != v for k, v in expected.to_dict().items()):
            raise ValueError('unsupported frozen execution strategy')
        return expected


class WorkExecutionPlanner:
    def __init__(self, registry=None):
        self.registry = registry or StrategyRegistry()

    def plan(self, requirement):
        # Spec validation belongs to Requirement; this boundary chooses only registered executors.
        return self.registry.plan(requirement)
