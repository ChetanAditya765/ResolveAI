# Demo identity maintenance

## Why an explicit maintenance operation exists

The seed command preserves existing permissions and tickets. Reseeding is therefore
not a migration for historical text. Names also appear in agent state, tool outputs,
conversation messages, evaluation snapshots, and serialized LangGraph checkpoints.
Updating only the employee directory would leave those historical displays inconsistent.

`backend/app/db/identity_correction.py` performs an explicit, transactional correction
across an allowlist of display-bearing fields. It defaults to a dry run. PostgreSQL
table locks protect the maintenance transaction; the API and worker must still be
stopped to avoid new writes around it. Pending or running agent runs and evaluation
batches block the correction. The helper rejects a missing employee, a mismatched
current name, or another employee sharing the previous name.

Checkpoint blobs are decoded and re-encoded with LangGraph's typed serializer, with
pickle fallback disabled and the application permission enum explicitly allowed.
They are not edited with byte replacement. A round-trip check verifies the decoded
content before writing. A second application makes no changes.

This is maintenance for the local demonstration dataset. A production deployment
would need a separately authorized identity-change process and an immutable record
of corrections, rather than treating retrospective display edits as ordinary audit
events.

## Run a future correction

These commands are a maintenance reference; the shipped fixture update is already
complete. Use the documented local Python environment with the backend installed,
and select the intended database through `DATABASE_URL`. If using custom database
credentials, keep them in the local environment rather than committing them.
Take a database backup before modifying a dataset that matters.

Let active work finish, then stop the application while keeping PostgreSQL available:

```powershell
docker compose -p resolveai stop frontend backend
```

From the project root, inspect the proposed changes using the exact prior display
name in place of the placeholder:

```powershell
.\.venv\Scripts\python.exe -m app.db.identity_correction EMP001 "<previous full name>" "Chetan Aditya"
```

Review the per-table counts, then commit the same correction explicitly:

```powershell
.\.venv\Scripts\python.exe -m app.db.identity_correction EMP001 "<previous full name>" "Chetan Aditya" --apply
```

Repeat the dry run and confirm every changed-row count is zero. Start the stopped
application again without deleting or replacing its named database volume:

```powershell
docker compose -p resolveai start backend frontend
```

Check `/api/ready`, `/api/employees`, `/api/demo/users`, and an existing ticket's
run and evaluation. Update source seed data for future fresh databases separately.
No permission reset is needed to correct a display name.

