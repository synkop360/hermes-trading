# Paired packet storage implementation plan

Reduce the measured repeated replay-input footprint without dropping causal/audit evidence or changing financial decisions. User authorized independent recovery work; no paid capacity, new model call or activation is part of this stage.

Implement a bounded lossless SQLite packet codec in paired_packet_storage.py. Content-address unchanged release/archive/strategy/binding material and encoded indicator history. Store compressed object bytes plus original byte length under SHA256; compress an envelope containing inline per-batch facts and explicit path references. Preserve the original full packet input hash and reconstruct exactly before replay or worker observation. Do not reinterpret user dictionaries as reference markers. Preserve plain JSON legacy packets.

Write objects and packet in the same existing exclusive financial-packet transaction. Missing/corrupt objects, bad lengths, trailing/incomplete streams, invalid paths and decompression limits must fail closed. Read-only worker stats must decode without creating tables or mutating evidence. No history deletion, automatic migration or paid-volume expansion.

Tests first: exact round trip/hash, unchanged-history reuse after reopen, distinct quote/time facts, legacy JSON compatibility, corruption/missing/trailing/oversize rejection, rollback of object insertion. Integrate only packet persistence/read paths and update direct packet inspection tests. Verify paired crash/replay and worker tests, independent review and full suite. Measure codec growth on representative public inputs; do not claim total-volume feasibility from packet compression alone. Account journals/settlements and sustained capacity admission remain separate required work.

## Verified local checkpoint

Implemented and integrated.11codec/22paired/15worker checks and845full-suite tests pass in98.669s; codec tests include128generated nested-value roundtrips. Final independent scoped review clear. Regressions reproduced and fixed completed-worker hash validation and writer/reader aggregate-size admission mismatch. Read-only reopen and legacy packets remain supported. AST graph updated without model use. Evidence research/runs/paired-packet-storage-v1.

Representative180-template storage benchmark yields about1.297%compressed envelope/object payload versus raw, with all exact roundtrips/hashes checked. This is packet-only efficiency, not total-volume feasibility or profitability. Wallet/cost journals, settlement/completed results, sustained whole-state growth and capacity admission remain next; no paid capacity, deployment, historical deletion or experiment activation occurred.
