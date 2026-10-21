# Coordinated hypothesis history repair

User continuation authorizes recovery of the existing paper learning loop. No paid model/service calls, trading orders, target/risk changes or deployment in this operation.

1. Block new external watcher runs with a tested local maintenance marker. Wait for already-running watcher/reflect processes to finish, without terminating unrelated processes.
2. Test pure repair: only the known malformed line hash may be quarantined; preserve raw bytes, all valid records and order, and never add a reflection timestamp. Test compare-and-swap abort and retained backup.
3. Implement standalone standard-library repair helper. In the remote paper container, verify training/paper mode, identify only actual worker module processes from /proc, pause them briefly, and refuse repair if any paused process holds the hypothesis log open. Verify current hash, retain fsynced backup, atomically replace, fsync metadata/directory, and resume every paused process in finally. A caller must not kill the helper on a short timeout. Unexpected record/hash/mode/process state aborts.
4. Independent review before remote execution. Capture a fresh expected source hash and apply only if unchanged. Preserve helper/stdout/manifests locally. Read remote heartbeat/log hashes afterward and verify watcher dry-run readability with model calls still blocked.
5. Keep the external watcher maintenance gate until strategy-writer ownership and model cost/access are resolved. This gate is operational maintenance, not a pause of the research goal or shutdown of the paper worker.
