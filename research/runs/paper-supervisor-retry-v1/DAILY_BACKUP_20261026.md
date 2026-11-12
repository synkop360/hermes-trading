# October 26 scheduled backup verification

Read-only verification at 06:56–06:57 UTC confirms the scheduled E: generation `state-2026-10-26-73a6a6a8ab53`. Its source capture is 00:01:02 UTC; the Windows copy task ran at 00:15:01 UTC, returned zero, emitted no stderr and remains enabled for the next scheduled run. The current task stdout reports `completed` and `off_host_verified=true`; its capture time, source identity and inventory hash match the independently checked receipt.

All 527 recorded files, totaling 182,952,454 bytes before receipt/inventory metadata, match their recorded lengths and SHA256 hashes. No unexpected or missing files or SQLite sidecars were found. The manifest, original October 23 opening and all eight wallet-account hashes match the original experiment identity. The opening clock was not restarted.

- [Copy/inventory/opening evidence](daily-copy-verification-20261026.json)
- [Scheduled-task evidence](daily-backup-task-observation-20261026.json)
- [Read-only checker](verify_daily_copy_20261026.py), adapted from the preserved October 25 checker with the new generation/output and additional receipt/stdout assertions

This verifies the completed local off-host copy. It is not a restore drill, a new post-repair-deployment backup, continuous-uptime proof or profitability evidence. No task was invoked or reconfigured, no network request was made, and neither the backup nor live state was modified. The snapshot repair remains undeployed and awaiting its existing approval request; the supervisor entry halt remains set. Overall goal estimate stays 65%.
