"""Transactional Chat result delivery record; presentation belongs to adapters."""


def enqueue_qq_result(uow, run_id):
    """Called inside Chat completion's transaction, after output file publication."""
    uow.execute(
        "INSERT INTO qq_deliveries(run_id,account_id,payload) "
        "SELECT r.run_id,r.account_id,jsonb_build_object('content',a.content,'origin',t.origin,"
        "'session_id',r.session_id,'files',COALESCE((SELECT jsonb_agg(mf.file_id) "
        "FROM message_files mf WHERE mf.message_id=a.message_id),'[]'::jsonb)) "
        "FROM runs r JOIN messages t ON t.message_id=r.trigger_message_id "
        "JOIN messages a ON a.produced_by_run_id=r.run_id "
        "WHERE r.run_id=%s AND r.status='completed' AND a.status='completed' "
        "AND t.origin->>'channel_type' IN ('napcat','official_qq') "
        "ON CONFLICT (run_id) DO NOTHING",
        (run_id,),
    )
