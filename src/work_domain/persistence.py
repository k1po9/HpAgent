"""Compatibility Work repository API; SQL and orchestration each have one owner."""

from persistence.wakeups import WakeupRepository
from persistence.work_requirements import WorkRequirementRepository
from persistence.works import WorkRepository as WorkRows
from work_domain.serialization import dto as dto


class WorkRepository(WorkRows):
    requirement = staticmethod(WorkRequirementRepository.get)
    insert_requirement = staticmethod(WorkRequirementRepository.insert)
    wakeup = staticmethod(WakeupRepository.enqueue)

    @staticmethod
    def event(uow, work, event_type, *, command_id=None, run_id=None, **payload):
        from application.work.events import record_work_event

        return record_work_event(
            uow, work, event_type, command_id=command_id, run_id=run_id, **payload
        )


def sync_schedule(uow, work, requirement):
    from application.work.schedules import sync_schedule as synchronize

    return synchronize(uow, work, requirement)
