from __future__ import annotations

from fastapi import APIRouter, Request

from persistence.uow import UnitOfWork

router = APIRouter()


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(request: Request) -> dict[str, str]:
    with UnitOfWork(request.app.state.api_pool) as uow:
        uow.execute("SELECT 1")
    return {"status": "ready"}
