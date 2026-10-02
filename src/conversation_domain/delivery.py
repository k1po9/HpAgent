"""Chat result notification in the same transaction as message completion."""


def enqueue_qq_result(uow, run_id):
    from delivery.service import enqueue, target_from_origin

    row = uow.execute(
        "SELECT r.*,a.message_id AS source_message_id,t.origin,COALESCE((SELECT jsonb_agg(mf.file_id::text) FROM message_files mf "
        "WHERE mf.message_id=a.message_id),'[]'::jsonb) AS files FROM runs r "
        "JOIN messages t ON t.message_id=r.trigger_message_id JOIN messages a ON a.produced_by_run_id=r.run_id "
        "WHERE r.run_id=%s AND r.status='succeeded' AND a.status='completed' "
        "AND t.origin->>'channel_type' IN ('napcat','official_qq')",
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
