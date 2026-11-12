# Cloud supervision implementation ledger

Plan: preserve the existing controlled paper experiment while moving its
supervisor into the Railway service. Source baseline: immutable 37e1be8 package.
Working copy: isolated cloud-supervision workspace; production is untouched.

Task 1 complete previously: bounded local native transport, GitHub PR 17.
Task 2 complete: local launch and finite service loop, native five-close review,
publication CAS, cost receipts, invocation budget and dedicated cloud auth home.
The Dockerfile pins the official Nous resolver; imports pass locally with that
same source revision. Docker build and Linux containment still require CI.
Task 3 partly complete: exact source transition and an interrupted native
transition/reconciliation preserve account bytes, review rows and counters.
Private PC checkpoint: 19 files, archive verified, original unchanged. It is
under ignored state/, outside this PR. Path relocation and live transfer remain.
Task 4 pending: dedicated Nous session, live activation and entry verification.

Ruling: use a dedicated cloud Nous session and the official rotation lock.
Inspection of the installed resolver established that access tokens are short
lived and refresh tokens rotate; copying the PC session would risk revocation.
The explicit environment-key mode remains available for temporary access only.

Ruling: normalize the runtime to canonical Git LF bytes for reproducible image
builds. All 127 modules without behavior changes were checked against the exact
37e1be8 Git blobs; the complete successor source map is pinned by the transition.

Review fix: cancellation initially left descendants alive. A real grandchild
test failed, then passed after Linux group / Windows suspended Job containment.
Nested-receiver cancellation also passes. New transition/build review found no
additional blocker; independent 17-test rerun passed.

Fresh validation: 104 targeted tests passed under Python 3.11.9, plus three
cloud transition tests (including interrupted native state, 94.7 seconds).
The wider combined suite was interrupted after slow SQLite fsync processing;
no full-suite pass is claimed. Linux CI includes the wider relevant suite.

Linux CI attempt 1 reached the auth installation and failed because the pinned
upstream explicitly disallows wheel builds. Use its supported editable install
from the exact detached revision, via one script shared by Docker and CI.
