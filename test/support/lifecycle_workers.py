"""Real control Activities shared by canonical lifecycle integration Workers."""
from orchestration.run_dispatcher import RunStrategyActivities


def lifecycle_control_activities(worker_database_url, controls):
    # Match the production control plane, including on replacement Workers.
    return [
        RunStrategyActivities(worker_database_url).load_strategy,
        controls.prepare_run,
        controls.load_agent_run_input,
        controls.finalize_failed,
        controls.finalize_cancelled,
    ]
