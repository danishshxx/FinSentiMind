# Security and Operations

Status: **PERSONAL / LOCAL WORKSTATION**

This is not a public multi-user service. Do not expose the development server
directly to the internet.

## 1. Secret management

- `.env` is ignored by Git.
- Supabase/Telegram/Stockbit secrets are read from environment variables.
- `Settings.__repr__` masks secret values.
- Secrets must never be written to logs, screenshots, reports, or commits.
- Rotate any credential that has been exposed.

## 2. Local access

Default command:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- No public authentication or multi-user isolation is implemented.
- No CORS middleware is enabled; browser access is same-origin.
- Do not bind to `0.0.0.0` without a trusted reverse proxy, authentication, and
  network controls.
- Local-only access is a deliberate security boundary.

## 3. Application hardening

- Security headers: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Permissions-Policy`, `Cross-Origin-Opener-Policy`.
- Unhandled errors return a generic `Internal server error`; tracebacks stay in
  server logs.
- Ticker, portfolio ID, and risk-lab ticker inputs are validated.
- Manual import schemas validate dates, keys, units, currencies, revisions, and
  availability timestamps.
- The API is read-only; there are no write endpoints for holdings, notes, or
  fundamentals.

## 4. Optional audit logging

```powershell
$env:FINSENTIMIND_AUDIT_LOG = "1"
```

When enabled, method/path/status/duration/client are appended to
`ml/logs/api_audit.jsonl`. Query strings, bodies, cookies, and credentials are
not recorded. `ml/logs/` is ignored by Git.

## 5. Backup and recovery

Create a checksummed local archive:

```powershell
python -m ml.ops.backup
python -m ml.ops.backup --output D:\backups\finsentimind.zip
python -m ml.ops.backup --verify D:\backups\finsentimind.zip
```

- Staging directories are excluded.
- The archive contains `BACKUP_MANIFEST.json` with SHA-256 per file.
- Verification recomputes every checksum.
- Backups are local files and must not be committed.
- Recommended: keep multiple dated copies on a separate local drive.

Recovery:

1. Verify the archive.
2. Stop the API server.
3. Extract to a temporary directory.
4. Compare the intended dataset/generation and `active.json`.
5. Restore only verified files.
6. Restart uvicorn and run `/api/data/health`.

## 6. Data reliability

- Immutable generations with SHA-256 manifests.
- Atomic `active.json` pointer switch.
- Staging retained on failed refresh.
- `refresh_latest.json` records failures; `refresh_last_success.json` records
  the last successful run.
- Rollback: `python -m ml.preprocessing.refresh_pipeline --rollback <run_id>`.
- Data freshness is shown in Data Health; stale data is never relabeled as
  fresh.
- Pending news/quarantine reports are auditable.

## 7. Dependency review

- `python -m pip check` passes in the current environment.
- `pip-audit` is not installed; no automated CVE scan was performed.
- The dependency set is broad and includes unused packages inherited from the
  project environment. Before public deployment, reduce the runtime surface,
  pin reviewed versions, run `pip-audit`/SCA in CI, and monitor advisories.
- This document does not claim zero vulnerabilities.

## 8. Operational limitations

- No high-availability deployment.
- No remote monitoring or alerting.
- No automatic backup schedule.
- No production authentication.
- No guarantee of provider availability.
- Manual fundamental/flow/macro imports require owner discipline and provenance.
