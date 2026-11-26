# Grounded Conversational RAG v3 implementation plan

User supplied Phase 1 specification is the implementation contract. Work is inline without harness/delegation, commits, remote pushes, running-console restart, or device operation.

## Design and audit

Existing flow: UnifiedAgent worker → ConversationGraph docs → answer_documents → scoped/FTS/hybrid retrieval → optional quote-selection model → direct excerpts. Citation existence currently does not establish entailment. Hardware bypass and offline fallback print entire source bodies. Qt drops citation data; Web shows expanded source buttons. Message replies are applied before the generation check.

Reuse retrieval/indexing/IDs/provenance and existing worker. Add evidence selection, strict claim contracts, deterministic guards, replaceable semantic verification, conservative structured facts, and rendering from approved claims only. Default cloud remains off. No runtime downloads. Same Nemotron is a fallible optional judge, not an independent accuracy guarantee. No local free-generation model is assumed.

Alternatives considered: heavy local multilingual NLI (requires installation and Korean calibration, unsuitable as an unmeasured default); citation-only prompting (does not address requested semantic boundary); separate bounded semantic judge plus deterministic guards and structured templates (selected within requested constraints). Free-generation requires two bounded calls sharing one deadline; no repair loops. Missing judge means UNAVAILABLE, never pass.

## Tasks and tests

- [x] Evidence + verifier + renderer: tests first, red confirmed; implemented evidence.py/verifier.py/facts.py/generation.py and answer.py. Preserve citation metadata, reject stale evidence, mixed profiles, invalid IDs, numeric/entity/negation/design-state changes, raw-copy output. Tests T01–T22/T26–T30.
- [x] Integration: graph/agent metadata and stale RAG reply guard, existing execution/approval policy and background worker retained. Tests T23/T24/T29.
- [x] Web/Qt: collapsed evidence, related claims/excerpts/status and source API, actual browser and Qt offscreen checks.
- [x] Evaluation: annotated-answer interface, null for unmeasured semantic/naturalness metrics, coverage/copy metrics.
- [x] Verification and docs: full 358 passed, final focused 23 passed; last quantity binding 1 passed/10 subtests. Excluded safety source diff empty; reports written with measured results and limitations.

## Constraints

No firmware, protocol, RobotSession, motion/PID/encoder, radar, emergency-stop/approval/lease/profile policy changes. No default cloud opt-in, no user-document derivatives to either generation or verifier. Exact citations are necessary but not semantic proof. All UI verification badges derive from server verification status, not model self-confidence.
