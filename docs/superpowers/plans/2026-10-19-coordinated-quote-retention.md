# Coordinated acknowledged quote retention

Implements approved archive tasks 4–5 in the existing dirty candidate. Disabled local API and invented correctness inputs only. No deployment, live retirement, model usage, backtest or trading activation.

1. [x] Freeze the current source baseline and demonstrate missing replay floors / cohort gates with failing tests.
2. [x] Verify the exact recorded retired archive and original eight-account cohort. Select only acknowledged `hold / position_open` outcomes with empty immutable financial projections and no referencing intents. Preserve real-bar reservations, financial records, pending exit incidents and original rowid high-water rows.
3. [x] Durably record one all-eight-account plan before mutation. Atomically delete each selected canonical bundle with monotone replay floors and a local receipt. Resume lost acknowledgements without restoring stale financial state.
4. [x] Filter current human decisions under the existing artifact lock; preserve unknown and new financial lines byte-for-byte. Record staging paths and original file identities before writes. Recover owned partial stages; retain/count unclaimed empty creation-window files with a finite retry bound, never delete unknown files.
5. [x] Gate ordinary collection, publication, capture, certification and measurement during partial transitions. Preserve explicitly admitted protective exit paths and existing lifetime/deadline limits.
6. [x] Exercise all eight accounts, lost acknowledgements, artifact failures, malformed plans, archive outages, capacity/deadline rejection, replay and protective continuation; review and fingerprint results. Frozen full Windows regression completed:1,519tests,1,516passed,3platform-related skips,0failures/errors/ResourceWarnings; all262source pins unchanged. Evidence: `research/runs/coordinated-quote-retention-v1/full-release-verification.json`. Linux remains unverified.

The storage owner must admit restoration, SQL journal/WAL and simultaneous artifact copies. SQLite free pages are reusable, not automatically returned to the filesystem. Other growing writers and a whole-state capacity measurement remain required; this transition alone does not establish capacity or profitability.
