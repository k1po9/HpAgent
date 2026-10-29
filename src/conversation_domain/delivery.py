"""Chat result notification in the same transaction as message completion."""


def enqueue_qq_result(uow, run_id):
    from delivery.service import enqueue, target_from_origin

    row = uow.execute(
        "SELECT r.*,a.message_id AS source_message_id,t.origin,COALESCE((SELECT jsonb_agg(mf.file_id::text) FROM message_files mf "
        "WHERE mf.message_id=a.message_id),'[]'::jsonb) AS files FROM runs r "
        "JOIN messages t ON t.message_id=r.trigger_message_id JOIN messages a ON a.produced_by_run_id=r.run_id "
        "WHERE r.run_id=%s AND r.status='succeeded' AND a.status='completed' "
        "AND t.origin->>'channel_type' IN ('onebot11','official_qq')",
        (run_id,),
    ).fetchone()
    if row:
        # Chat content was composed for this surface; Work group notifications use summaries.
        target = target_from_origin(
            uow,
            row["account_id"],
            None,
            row["origin"],
            content_scope="summary" if row["origin"]["scope"] in {"group", "guild"} else "content",
        )
        enqueue(
            uow,
            row["account_id"],
            f"chat-result:{run_id}",
            {"files": row["files"]},
            run=row,
            target_id=target,
            source_message_id=row["source_message_id"],
        )


def enqueue_qq_timeout(uow, run_id, reply):
    """Freeze a configured timeout notice alongside the failed chat Run.

    No successful assistant message is required or fabricated. Delivery retains
    its normal identity checks, deduplication and uncertain-receipt handling.
    """
    from delivery.service import DeliveryTargetUnavailable, enqueue, target_from_origin

    row = uow.execute(
        "SELECT r.*,t.origin FROM runs r JOIN messages t "
        "ON t.account_id=r.account_id AND t.message_id=r.trigger_message_id "
        "WHERE r.run_id=%s AND r.source_kind='chat' AND r.status='failed' "
        "AND r.failure_code='model_read_timeout' "
        "AND t.origin->>'channel_type' IN ('onebot11','official_qq')",
        (run_id,),
    ).fetchone()
    if row is None:
        return
    try:
        # A binding may be revoked while the model is running, or between the
        # two authorization checks. Roll back only this optional notice; the
        # Run failure and its terminal event must still commit together.
        with uow.connection.transaction():
            target = target_from_origin(
                uow, row["account_id"], None, row["origin"],
                content_scope="summary" if row["origin"]["scope"] in {"group", "guild"} else "content",
            )
            enqueue(
                uow, row["account_id"], f"chat-model-timeout:{run_id}",
                {"content": reply, "summary": reply, "error_code": "model_read_timeout"},
                run=row, target_id=target,
            )
    except DeliveryTargetUnavailable:
        return
