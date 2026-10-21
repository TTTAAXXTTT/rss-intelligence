> 历史技术参考，已将部署域名、任务标识和个人环境替换为示例；历史验收记录不代表当前开源副本已完成在线部署。首次部署以 README 和 DEPLOYMENT.md 为准。

# RSS Intelligence V5.2 R4

R4 is the production hardening release used for the 2026-08-11 cutover.

## P48 classifier isolation

- Batch classification runs as the dedicated `rss-v52-classifier` agent.
- The classifier has no workspace access.
- Its only allowed tool is `session_status`; FreshRSS, web, file, shell, Finalizer, Publisher, cron and gateway tools are not exposed.
- Pre-isolation batch artifacts are retained only in the timestamped incident directory and are never resumed as active results.

## P49 response and accounting hardening

- Explicit final response fields and OpenClaw final-assistant metadata take precedence over progress payloads.
- Empty payloads are ignored and the latest non-empty payload is used as the compatibility fallback.
- Empty text, invalid envelope JSON and invalid model JSON have distinct error markers.
- A model preamble is tolerated only when it contains exactly one complete JSON code block that parses verbatim. Truncated, repaired or ambiguous multi-block JSON remains rejected.
- Raw and disposition counters are recomputed by the Host only after accounting rows and the exact input ID set have passed validation.
- A missing `isHighValueOpportunity` flag defaults conservatively to `false`; non-boolean values are rejected.
- Non-object accounting rows, candidates and source entries are rejected through the normal validation/fallback path.
- The immutable run copy remains compact, while the `current` handoff is formatted across lines for human and incident inspection.

## P50 deterministic Host finalization and review

- AI remains responsible for isolated, evidence-bounded batch classification. Final report assembly is now deterministic Host code and no longer depends on a model making a large terminal tool call.
- The Host revalidates the immutable candidate/run/manifest/raw-accounting/quarantine chain before staging, derives every final accounting row, and enforces Morning/Afternoon caps of 24/16 events.
- Duplicate events are merged only on byte-exact event keys or source URLs. Afternoon suppresses byte-exact Morning baseline events or sources and does not guess semantic progress from rewritten prose.
- Stage identifiers and content are deterministic and safely reusable; directories are `0700`, artifacts are `0600`, and the existing Host Publisher still performs its independent schema gate before publishing.
- Review/E2E is a deterministic read-only subscription health audit. It authenticates to FreshRSS without exposing credentials, writes no subscription changes, and no longer depends on a model tool call.
- The V5.2 model Finalizer/Review jobs remain disabled compatibility templates and are not part of production scheduling.

## Verification added

- Static fixtures cover OpenClaw envelope variants, empty/bad responses, fenced JSON, derived counters and defensive type checks.
- Host fixtures cover exact final accounting, deterministic ordering, deduplication, caps, Morning baseline suppression, empty editions, permissions, idempotency and read-only Review artifacts.
- The fixture is part of `package-selftest.sh` and the package checksum manifest.
- A real isolated Batch 1 probe verifies exact ID coverage, candidate-primary coverage and byte-for-byte in-batch source URLs before the full E2E run.

The immutable snapshot, quarantine, Host Publisher and transactional cutover architecture remain unchanged from V5.2 R3; only the unreliable model-to-stage/review terminal calls were replaced by deterministic Host steps.
