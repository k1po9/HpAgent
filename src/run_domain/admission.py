"""Compatibility function for Work admission; implementation lives in application."""

from application.work.admit_work_run import AdmitWorkRun, WorkAdmissionContext


def admit_work_run(uow, work, requirement, wakeup_id, database, budget_mode, agent_policy=None):
    return (
        AdmitWorkRun(database, budget_mode, agent_policy)
        .execute_in_uow(
            uow,
            WorkAdmissionContext(work, requirement, wakeup_id),
        )
        .run
    )
