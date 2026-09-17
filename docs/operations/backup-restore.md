# Backup and Restore

## Backup

With the Compose services running:

```bash
./scripts/operations/backup.sh
BACKUP_DIR=/secure/location ./scripts/operations/backup.sh
```

The script creates timestamped custom-format dumps for application PostgreSQL and Hindsight PostgreSQL plus a compressed `.data/workspace` archive. Redis is transient and is not backed up. File-store volumes and Temporal PostgreSQL are not included by the script; include them in deployment-level volume snapshots when uploaded/generated blobs or in-flight workflow history must be recoverable.

Keep the backup set, repository revision, `.env`/secret references, image versions, and Compose volume inventory together. Protect dumps as production data.

## Restore

Restore into an isolated environment first:

1. Stop API and workers so no writes occur.
2. Restore the application dump with `pg_restore` into the `hpagent` database as the migration owner.
3. Restore the Hindsight dump into the Hindsight database.
4. Restore the workspace archive at `.data/workspace` with original ownership and permissions.
5. Restore file-store and Temporal volume snapshots from the same recovery point when included.
6. Start dependencies, run `hpagent-migrate`, then start API/workers.
7. Verify `/health/ready`, inspect worker pollers/logs, and test one read-only account/conversation lookup before accepting traffic.

Do not combine application PostgreSQL from one point in time with unrelated file-store or Temporal snapshots without a reconciliation plan.
