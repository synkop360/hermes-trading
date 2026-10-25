# Read-only daily-copy analysis path corrections

October25,2026. The initial analysis script stopped before verification because pathlib preserves the Windows extended prefix in resolve(), making equivalent directory objects compare unequal. It used samefile() to verify actual filesystem identity instead. The next run completed native inventory/hash validation, then stopped because sqlite3 rejects the extended-prefix as_uri() authority. The final script verifies samefile() before removing that prefix solely for the immutable read-only SQLite URI.

The two observed errors were AssertionError at the directory comparison and sqlite3.OperationalError: invalid uri authority: %3F. These are analysis harness failures, not evidence of a corrupt backup. No retry ran the copy task, network transport, snapshot creator, migration or initializer. The final receipt verifies all525 original members, opening and all eight identities. Snapshot and live state bytes were not changed.
