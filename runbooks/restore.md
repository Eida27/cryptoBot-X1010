# Backup and isolated restore

`cbot backup --config config/paper.toml --out backups` uses SQLite's online backup API, checks integrity and writes a dated database plus SHA-256 JSON manifest. Seven daily backups are retained per mode. Service maintenance runs daily as well. Copy the private backup directory off-host with your normal authorized backup process; secrets are managed separately.

Test into a new isolated location: `cbot restore-check --backup backups/paper-YYYY-MM-DD.json --out artifacts/restore/paper.sqlite3`. Checksums and integrity must pass. Restore refuses an existing target. It preserves baseline/floor, fills, intents and halts; revokes sessions and marks reconciliation required. PAPER becomes permanently INTERRUPTED. Inspect the restored ledger; do not run both restored and original workers on one venue account.

For recovery, first stop the original worker and verify ownership/venue positions/orders. Use the verified copy with a matching mode-named path and original configuration hashes. Start paused; recover against exchange truth. Outstanding UNKNOWN submissions still occupy capacity, and protective siblings require confirmed cleanup. A restore never clears TRIAL_LOSS or automatically resumes. Keep the original damaged evidence for diagnosis.

Application logs rotate at 5 MiB with four files per day and retain 14 days. Equity snapshots are limited to one per minute and 90 days; financial fills/income/audit are retained. Check free disk before backup and service operations. Backups/reports can contain account activity and must remain private.
