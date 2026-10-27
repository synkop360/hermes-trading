# Approved local implementation ledger

- Owner approved the October 27 correction design and disabled local implementation. No deployment, live reset or entry resumption is authorized.
- Base: 6139099 in isolated `hermes-paper-operational-corrections`, branch `fix/paper-discord-reporting`.
- Plan: `docs/superpowers/plans/2026-10-27-supervisor-contention-and-halt-recovery.md`.
- Task 1: complete locally. New real-lock regression failed on the old retry, then 16 retry tests and 7 native snapshot tests passed. The only production change is the approved timing/attempt bounds.
- Tasks 2–4: pending.
- Ruling: execute inline; the delegated reviewer returned an account usage-limit error. Do not switch agents/models to bypass that limit. Keep independent review unavailable explicit in the final local evidence.
- Ruling: pending recovery rejects new health replacement under the same safety lock, preserving before/after state for checked retry or conservative abort. The existing pending-health sender retains its original request. This avoids trying to undo newer health after a crash.
- Ruling: implementation approval does not cover the eventual exact release transition/deployment or live acknowledgment; prepare those separately after local verification.
- Ruling: recovery preflight acquires shared publication/account read transactions before the safety-state lock, matching worker publication→safety lock order. Never wait for a publication lock while holding safety state.
