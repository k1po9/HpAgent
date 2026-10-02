"""Artifact API admission uses a new Work; generation is covered by test_work_integration."""
from uuid import UUID, uuid4

import pytest

from conversation_domain.commands import CommandService
from persistence.uow import UnitOfWork
from web_artifacts.services import ArtifactService
from web_domain.errors import IdempotencyConflict, ResourceNotFound
from work_domain.commands import WorkCommandService

pytestmark = pytest.mark.postgres


def test_artifact_admission_replays_without_duplicate_mandates(database_url,account_id):
    chat=CommandService(database_url)
    cid=UUID(chat.create_conversation(account_id,str(uuid4())).body['conversation']['conversation_id'])
    run=UUID(chat.send_message(account_id,cid,str(uuid4()),'Source').body['run_id'])
    chat.start_run(account_id,run)
    chat.complete_run(account_id,run,'# Answer')
    with UnitOfWork(database_url) as uow:
        message=uow.execute('SELECT message_id FROM messages WHERE produced_by_run_id=%s',(run,)).fetchone()['message_id']
    service=ArtifactService(database_url)
    key=str(uuid4())
    created=service.create_artifact(account_id,message,key)
    assert service.create_artifact(account_id,message,key).replayed
    with pytest.raises(IdempotencyConflict):
        service.create_artifact(account_id,message,key,'Changed')
    assert len(WorkCommandService(database_url).list(account_id)['items'])==1
    with pytest.raises(ResourceNotFound):
        service.get_version(uuid4(),UUID(created.body['version']['artifact_version_id']))
