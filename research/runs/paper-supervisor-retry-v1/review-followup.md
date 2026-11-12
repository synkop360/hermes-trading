# Implementer follow-up to independent review

The independent review's two product hashes still match exactly: `supervisor_sync.py` is `d493060b2db48ccac5cee2eafa3d06d72e68e239ef9da90543f256d8bd0d8ad9`; the transition helper is `306da355b372cf0048b0192e7ec475d1c70740a5f60eb7ebe97b177d1c3b8ccf` in the reviewed Windows checkout. No material product finding required a fix.

The subsequent cached Linux run exposed a test-only receiver-clock assumption. The new transition fixture advanced its synthetic timestamp but had not patched the receiver clock while seeding failures. Windows setup happened to consume enough real time; faster Linux setup rejected the future health observation. Commit `a79fb93` controls the receiver clock explicitly while seeding those synthetic observations. It neither changes the runtime nor weakens the existing future/out-of-order checks. The failed Linux run and preceding fixture failures remain recorded.

The corrected exact package passes all 20 new checks on Windows and all 21 selected checks on Linux, including the Windows-skipped symlink case. The Linux source mount is read-only, its user unprivileged and its network disabled. All 608 package hashes remain unchanged. The final package's imported 129-module map matches the frozen candidate map.

The earlier affected Windows run executed all 140 selected cases:139 passes, one platform skip and no failures/errors/ResourceWarnings. A fresh comparison against its 376 raw source pins finds only the test file's receiver-clock correction; all 375 other source pins, including both product files, remain unchanged. This is composed evidence, not a claim that the 140-case run executed the later fixture byte version.

Full regression is active on the final frozen package under live exec handle42772; its 1,712-case catalog is unique. Its result is not inferred from the affected or new-package checks. Final source invariance and any full-run errors still require terminal inspection. The AST-only graph refresh succeeded before the last test-only correction; defer its final incremental refresh until the running timing-sensitive regression is terminal.

This follow-up is authored by the implementer, not a second independent review. The independent product review remains valid for unchanged product bytes; the later fixture correction is supported by the preserved failing Linux run followed by the corrected Windows/Linux evidence.
