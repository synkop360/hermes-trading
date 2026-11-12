# Bounded version2 nonce adapter

Approved local archive implementation scope; design for the next integration step. No adapter, installed listener, migration, deployment or activation is claimed here. The existing `StateRepository.open()` enables WAL, so do not use it for the new bounded store or change its existing users.

Implement a separately purpose-bound rollback-journal `BoundedArchiveNonceStore`, with explicit initialization and existing-file reopening. Keep `verify_command` unchanged and implement its existing `add_once(nonce, *, accepted_at, expires_at) -> bool` contract. Version2's durable-store type check must explicitly admit this concrete adapter alongside the existing class; do not accept arbitrary duck typing or change V1 behavior.

Acquire and retain the storage domain owner before opening any nonce or receiver handle read/write: opening a hot SQLite journal may itself perform recovery. The prepared receiver belongs inside that same retained owner lifespan. Each request uses a fresh bounded domain admission covering the combined nonce/receiver peak, then arms the nonce adapter with that lease/deadline. `add_once` without a matching live armed lease fails closed. Complete and commit nonce SQL before entering receiver SQL, and close all prepared handles before releasing the domain owner. Other supervisor/export processes need a shared coordinator/broker, not an inverted writer-lock acquisition.

Use a dedicated exact schema with one metadata row and nonce records. Metadata binds schema/purpose, random store ID, original witness-binding/identity hashes, immutable limits and a nondecreasing accepted-time floor. Reject missing or foreign storage, unknown tables/triggers, attached databases, malformed metadata, unconfined temporary writes, unexpected WAL/SHM or a changed journal configuration; do not initialize/reset/migrate on those failures.

Require32lowercase-hex nonce characters, aware datetimes encoded as integer UTC microseconds and `expires_at == accepted_at + 300seconds`. Proposed local synthetic parameters:4KiB pages, maximum256main pages and256live records. Enforce `DELETE` journal mode, `FULL` synchronization, no cache spill, bounded value lengths and nonblocking SQL ownership. A proposed4MiB nonce-footprint envelope needs actual page/rollback/temporary peak fault verification before qualification; it is not an OS quota.

Within one admitted transaction: validate ownership/configuration/time; reject clock regression; returnFalse for an unexpired duplicate; remove only legitimately expired records (`expires_us <= accepted_us`); reject new admission if remaining live rows exhaust the bound; insert nonce and advance the time floor atomically; verify page/footprint bounds and commit before returningTrue. Capacity, deadline, corruption and ownership failures raise distinct errors rather than returningFalse. Precommit failures roll back both nonce and floor. A postcommit lost reply must preserve the accepted nonce, so recovery uses a fresh envelope nonce with the original operation ID.

This adapter depends on the preceding strict V2 freshness check (`0 <= age < 300`). Generic `verify_command` alone permits future timestamps and an inclusive300second boundary; do not present the adapter as a stronger replacement for all existing authentication. Nonce expiry must also preserve legitimate late acknowledgement of originally admitted work.

Required evidence:

- First acceptance commits once; replay rejects before and after reopening.
- Invalid MAC, future timestamp and exact freshness boundary leave the store unchanged.
- Exact expiry removes only eligible rows; clock regression never prunes.
- Row/page/shared-capacity exhaustion denies a new nonce without removing unexpired evidence.
- Foreign thread/process, replaced file, unsupported WAL/schema/binding/limits fail closed.
- Precommit interruption/disk-full rolls back nonce and time floor; postcommit lost reply remains replayed.
- An accepted nonce survives failed receiver CAS/deadline, with receiver evidence unchanged.
- Hot-journal recovery occurs only under admission; every extent is counted.
- Maximum-row churn, expiry and fault peaks stay within the declared operation envelope.

Independent older-snapshot recovery floors, coherent V2 receiver/nonce export, complete installed root inventory and all-writer participation remain required. A capacity owner or fresh nonce database supplies none of those proofs.

## SQLite temporary-write constraint

The official [temporary-file documentation](https://sqlite.org/tempfiles.html#statement_journal_files) says statement journals may be created outside the database directory, and identifies conflict-resolution forms that avoid them. Proposed adapter SQL therefore uses `INSERT OR ROLLBACK` and `UPDATE OR ROLLBACK`, with an exact trigger-free schema and no nested transactions, attached/TEMP databases, sorting, materializing subqueries or vacuuming. Any statement failure aborts the entire nonce transaction; it never continues after partial acceptance. This is a proposed way to avoid unregistered statement-journal extents, requiring verification against the actual SQLite build and fault paths. Do not claim that `temp_store=MEMORY` alone confines all temporary writes.

The [SQLite configuration reference](https://sqlite.org/c3ref/c_config_covering_index_scan.html#sqliteconfigstmtjrnlspill) describes a separate statement-journal spill threshold. Do not alter process-global SQLite configuration or its temporary directory while other databases run; no such runtime change is authorized or implemented by this design.
