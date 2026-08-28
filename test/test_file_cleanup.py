from __future__ import annotations

from uuid import uuid4

from web_domain.file_cleanup import FileCleanupService


def test_cleanup_deletes_objects_and_staging_but_retries_failures() -> None:
    account_id = uuid4()
    object_id, staging_id, failed_id = uuid4(), uuid4(), uuid4()

    class Store:
        def __init__(self):
            self.deleted: list[tuple[str, str]] = []

        def delete(self, key: str) -> None:
            if key == "accounts/fail/blob":
                raise OSError("temporary storage failure")
            self.deleted.append(("object", key))

        def delete_staging(self, file_id) -> None:
            self.deleted.append(("staging", str(file_id)))

        @staticmethod
        def storage_key(account_id, file_id) -> str:
            return f"accounts/{account_id}/objects/{file_id}/blob"

    store = Store()
    service = FileCleanupService(object(), store)  # type: ignore[arg-type]
    service._claim = lambda limit: [  # type: ignore[method-assign]
        {"file_id": object_id, "account_id": account_id, "storage_key": "accounts/ok/blob"},
        {"file_id": staging_id, "account_id": account_id, "storage_key": None},
        {"file_id": failed_id, "account_id": account_id, "storage_key": "accounts/fail/blob"},
    ]
    completed: list = []
    service._complete = completed.append  # type: ignore[method-assign]

    result = service.cleanup_once(limit=3)

    assert result.claimed == 3
    assert result.deleted == 2
    assert result.failed == 1
    assert store.deleted == [
        ("object", "accounts/ok/blob"),
        ("staging", str(staging_id)),
        ("object", f"accounts/{account_id}/objects/{staging_id}/blob"),
    ]
    assert completed == [object_id, staging_id]
