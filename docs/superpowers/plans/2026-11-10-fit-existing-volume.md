# Existing 500 MB paper volume implementation plan

Goal: reduce persistent and transient storage without buying capacity.
User explicitly authorized compressed historical backups and fewer wallets if
necessary. Prefer exact compression; changing the preregistered eight profiles
would alter comparisons and is a fallback only if measured savings require it.

Measured allocation: legacy root logs 43.8 MB, root databases 57 MB, primary
adaptive wallet 94.4 MB, seven other wallets 13.5-14.2 MB each. Native state
307.9 MB; filesystem free 134.2 MB, just below the unchanged safety reserve.

- [ ] Archive only fixed obsolete root-level legacy logs under the existing
  worker ownership lease. Stream gzip with bounded scratch usage. Verify exact
  decoded SHA256 and durable archive/receipt before unlinking an unchanged
  original. Never touch current account databases, snapshots, audit rows or logs.
  Test interrupted archive/delete, tampering, links and ownership contention.
- [x] In the explicit volume-owned migration context, preserve full preimages
  of only the six fixed mutable source-binding components while all native
  SQLite handles are exclusively held and sidecar-free. Untouched financial
  history remains in place and every file stays in the full frozen protected
  map, verified before/during/reconcile/rollback. Ordinary callers retain their
  existing full-copy behavior. Native intent seals compact mode and the whole
  protected map. Reconciliation after completion also verifies both maps.
  Test actual native interrupted transition and rollback, corrupt archives and
  unchanged account/economic state.
- [ ] Measure actual compressed peak, keep the original state budget/reserve,
  publish/review/CI the exact candidate and deploy only after capacity checks.
- [ ] Inspect the durable storage halt. Resume only through an audited native
  recovery with original clocks/counters/history preserved; no blind deletion.
  Actual closed preflight confirms all positions closed, no pending batch and
  last settled packet not an operational storage stop. A predecessor-pinned
  one-shot recovery may clear only storage state under real worker/publication
  leases after fresh admission, preserve exact original halt bytes and complete
  lifecycle preimage, and leave every counter/deadline/economic field unchanged.
- [ ] Complete retained supervisor transfer and actual Nous login/entry checks.

Review focus: changing file while compressing; interrupted durable publication;
corrupt compressed evidence; migration resume after allocation already exists;
live worker contention. Logs contain fixed status/byte counts only.

Production result: deployment01a2c0fb completed verified compression of seven
obsolete root logs and reclaimed40,111,226bytes. User explicitly approved
replacement after automatic review initially blocked original retirement.
All eight current wallets remain present. No source transition/checkpoint upload
or storage halt reset has occurred. Native10-test follow-up passes; complete
candidate Linux CI will include the new compaction tests.
