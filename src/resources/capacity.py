"""Cross-worker resource tickets, account rotation and reserved interactive capacity."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from persistence.uow import UnitOfWork


class CapacityUnavailable(RuntimeError):
    pass


class CapacityService:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def admission(uow, work):
        # A short global lock serializes counts, never held during external execution.
        uow.execute("SELECT pg_advisory_xact_lock(4830020401)")
        limit = int(os.getenv("WORK_ACCOUNT_COORDINATORS", "4"))
        active = uow.execute(
            "SELECT count(*) AS n FROM runs WHERE account_id=%s AND source_kind='work' "
            "AND status IN ('queued','running','cancelling')",
            (work["account_id"],),
        ).fetchone()["n"]
        global_active = uow.execute(
            "SELECT count(*) AS n FROM runs WHERE source_kind='work' AND status IN ('queued','running','cancelling')"
        ).fetchone()["n"]
        return active < limit and global_active < int(os.getenv("WORK_GLOBAL_COORDINATORS", "32"))

    def acquire(self, run_id, resource, ticket_id):
        with UnitOfWork(self.database) as uow:
            uow.execute("SELECT pg_advisory_xact_lock(4830020402)")
            run = uow.execute(
                "SELECT r.*,a.status AS account_status FROM runs r JOIN accounts a USING(account_id) "
                "WHERE r.run_id=%s",
                (run_id,),
            ).fetchone()
            if (
                not run
                or run["status"] not in {"queued", "running"}
                or run["account_status"] != "active"
            ):
                raise CapacityUnavailable("Run is not active")
            if run["work_id"]:
                from run_domain.lifecycle import RunLifecycleService

                RunLifecycleService.check_work(uow, run)
            lane = "background" if run["work_id"] else "interactive"
            uow.execute(
                "UPDATE capacity_queue SET state='released' WHERE state<>'released' AND lease_until<now()"
            )
            uow.execute(
                "INSERT INTO capacity_queue(ticket_id,account_id,run_id,resource,lane,lease_until) "
                "VALUES (%s,%s,%s,%s,%s,now()+interval '90 seconds') ON CONFLICT(ticket_id) "
                "DO UPDATE SET lease_until=EXCLUDED.lease_until WHERE capacity_queue.state<>'released'",
                (ticket_id, run["account_id"], run_id, resource, lane),
            )
            uow.execute(
                "INSERT INTO capacity_turns(resource,lane,account_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (resource, lane, run["account_id"]),
            )
            total = int(os.getenv(f"CAPACITY_{resource.upper()}_GLOBAL", "12"))
            interactive = int(os.getenv(f"CAPACITY_{resource.upper()}_INTERACTIVE_RESERVED", "4"))
            per_account = int(os.getenv(f"CAPACITY_{resource.upper()}_ACCOUNT", "3"))
            counts = uow.execute(
                "SELECT count(*) AS total,count(*) FILTER(WHERE lane='background') AS background,"
                "count(*) FILTER(WHERE account_id=%s) AS account,"
                "count(*) FILTER(WHERE account_id=%s AND lane='background') AS account_background FROM capacity_queue "
                "WHERE resource=%s AND state='held'",
                (run["account_id"], run["account_id"], resource),
            ).fetchone()
            background_account_limit = max(0, per_account - int(interactive > 0))
            if (
                counts["total"] >= total
                or counts["account"] >= per_account
                or lane == "background"
                and (
                    counts["background"] >= max(0, total - interactive)
                    or counts["account_background"] >= background_account_limit
                )
            ):
                return False
            # Each lane rotates eligible accounts; saturated accounts do not block others.
            winner = uow.execute(
                "SELECT q.ticket_id FROM capacity_queue q JOIN capacity_turns t "
                "USING(resource,lane,account_id) WHERE q.resource=%s AND q.lane=%s AND q.state='waiting' "
                "AND (SELECT count(*) FROM capacity_queue h WHERE h.resource=q.resource "
                "AND h.account_id=q.account_id AND h.state='held')<%s "
                "AND (q.lane<>'background' OR (SELECT count(*) FROM capacity_queue h WHERE h.resource=q.resource "
                "AND h.account_id=q.account_id AND h.lane='background' AND h.state='held')<%s) "
                "ORDER BY t.last_admitted_at,q.created_at,q.ticket_id LIMIT 1",
                (resource, lane, per_account, background_account_limit),
            ).fetchone()
            if not winner or winner["ticket_id"] != ticket_id:
                return False
            uow.execute("UPDATE capacity_queue SET state='held' WHERE ticket_id=%s", (ticket_id,))
            uow.execute(
                "UPDATE capacity_turns SET last_admitted_at=now() WHERE resource=%s AND lane=%s AND account_id=%s",
                (resource, lane, run["account_id"]),
            )
            return True

    def release(self, ticket_id):
        with UnitOfWork(self.database) as uow:
            uow.execute(
                "UPDATE capacity_queue SET state='released' WHERE ticket_id=%s AND state<>'released'",
                (ticket_id,),
            )

    def renew(self, ticket_id):
        with UnitOfWork(self.database) as uow:
            return bool(
                uow.execute(
                    "UPDATE capacity_queue SET lease_until=now()+interval '90 seconds' "
                    "WHERE ticket_id=%s AND state='held' AND lease_until>now() RETURNING ticket_id",
                    (ticket_id,),
                ).fetchone()
            )

    @asynccontextmanager
    async def slot(self, run_id, resource):
        ticket = uuid4()
        task = None
        owner = asyncio.current_task()

        async def heartbeat():
            while True:
                await asyncio.sleep(20)
                if not await asyncio.to_thread(self.renew, ticket):
                    owner.cancel()
                    return

        try:
            while not await asyncio.to_thread(self.acquire, run_id, resource, ticket):
                await asyncio.sleep(0.5)
            task = asyncio.create_task(heartbeat())
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await asyncio.shield(asyncio.to_thread(self.release, ticket))
