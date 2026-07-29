# Commercial Product Upgrade Status

This status was refreshed for the 0.18.0 release on 2026-07-26 from the repository audit dated 2026-07-24. It distinguishes implemented repository work from human acceptance, production release, and measured operation. The authoritative machine-readable cards are in `docs/requirements/COMMERCIAL_REQUIREMENTS.json`.

The 0.18.4 dependency-security, Docker/GPU and media-compatibility patch updates the current release pointer and repository verification without changing requirement acceptance or production blockers. Its upgrade, rollback and verification boundaries are documented in `docs/releases/0.18.4.md`.

The 0.18.2 Console Next consistency patch updates the current release pointer and repository verification without changing requirement acceptance or production blockers. Its table, pagination and responsive-layout changes are documented in `docs/releases/0.18.2.md`.

The 0.18.1 platform optimization patch updates the current release pointer and repository verification, but does not change any requirement acceptance status or close any production blocker listed below. Its performance, configuration and delivery changes are documented in `docs/releases/0.18.1.md`.

The Python `0.18.1` wheel was built and installed without dependencies in a clean virtual environment; the installed SDK and isolated `0.18.1` service passed live health and compatibility checks. Structural traceability remains valid and the release decision remains `block` because the external evidence below is still unavailable.

## Current status

| Requirement | Repository outcome | Current status | Acceptance blocker |
| --- | --- | --- | --- |
| `MOD-P0-01` | Runtime adapters, governance, cutover, and fail-closed checks exist | `blocked` | Five lawful production model artifacts and held-out evaluations are unavailable |
| `OPS-P0-02` | Production backends, migration, consistency, drill schema, and release gate exist | `blocked` | Target-topology fault, recovery, backup, upgrade, rollback, and alert drills are not observed |
| `PERF-P0-03` | Seven-scenario, three-topology capacity protocol and validator exist | `blocked` | Real GPU and production-backend observations are unavailable |
| `SCH-P1-01` | Dynamic batching, fairness, timeout, cancellation, and degradation exist | `blocked` | Required production-model A/B capacity result depends on `PERF-P0-03` |
| `DEP-P1-02` | Kubernetes workloads plus a 27-resource immutable stable/canary release materializer, policies, autoscaling, and migration job exist | `blocked` | Target-cluster HA, scale, canary, and node-failure drills are unavailable |
| `MOD-P1-03` | Trusted hot reload, fingerprint, prewarm, alias CAS, and rollback exist | `verification` | Automated/staging verification and owner sign-off remain |
| `REG-P1-04` | Registry, provenance, evaluation, approvals, release states, and rollback exist | `verification` | Governance workflow sign-off remains |
| `DATA-P1-05` | Feedback pool, annotation exchange, lineage, immutable analysis evidence, read-only threshold suggestions, model comparison, and release candidate decisions exist | `verification` | Privacy and connector acceptance remain |
| `CUS-P2-01` | Scheduled lifecycle and entitlement rollback, license, HA concurrency, immutable metering/reversals, versioned cost attribution, quota forecasts, support, closure, and step-up authentication exist | `verification` | Product/privacy E2E acceptance remains |
| `SLA-P2-02` | Versioned SLA reports, incidents, support, and service-quality console exist | `verification` | Operational acceptance and 30-day observation remain |
| `SDK-P2-03` | Product Python SDK, reference clients, retry/idempotency/upload/webhook contracts, and repeatable clean-environment live smoke exist | `verification` | Customer integration acceptance remains |
| `VID-P2-04` | Resumable video, checkpoints, priority queues, consecutive-failure reconnect budgets, streams, callbacks, and timeline exist | `verification` | Long-duration failure/recovery E2E remains |
| `COM-P2-05` | COM-001 through COM-012 enforce control-specific semantics; COM-006 gates all commercial exports; rights, six-backend proof, and signed evidence tooling exist | `blocked` | Legal applicability, impact assessment, control approvals, and real delivery package are unavailable |
| `TPL-P3-01` | Five immutable versioned templates have digest-bound acceptance manifests, preview, history, fingerprints, fail-closed validation, and rollback | `verification` | Product/delivery, customer-template, and target-capacity acceptance remain |

## Repository verification snapshot

The 0.18.0 repository verification completed with the following observed results:

- Python regression: `746 passed, 6 skipped`.
- Console unit, type, lint, and production-build checks: passed (`44` Vitest cases).
- Browser E2E: `40 passed` across Chromium desktop/tablet/mobile, Firefox desktop, and WebKit desktop, including commercial operations, webhook debugging, guarded dialogs, and all product routes.
- Repository Ruff and strict Python type gates: passed (`215` typed sources).
- Static deployment gate, platform-only strict readiness gate, support-matrix validation, and `git diff --check`: passed.
- OpenAPI compatibility: passed with `154` current paths versus `147` baseline paths and `0` breaking changes.
- Kubernetes release materialization and validation: passed for `27` rendered resources using distinct immutable stable/canary digests; no target cluster was available for behavioral drills.
- The Python `0.18.0` wheel was built, installed without dependencies in a new virtual environment, imported from that environment, and passed live health plus API compatibility checks against an isolated service.
- Recent-authentication gates passed for local password re-entry, forced OIDC reauthentication (`prompt=login`, `max_age=0`, recent `auth_time`), high-risk route enforcement, and non-interactive credential rejection.
- Commercial operations gates passed for scheduled status/entitlement activation, cancellation and rollback, immutable usage-event idempotency, reversal-based adjustment, day/month timezone aggregation, cost attribution, budget status, and quota forecasts.
- Compliance gates passed for control-specific COM-001 through COM-012 semantics and COM-006 enforcement across rights, feedback, and administrator exports.
- Template gates passed for all five acceptance manifests, digest-bound previews/applications, and fail-closed behavior when evidence is missing.
- Full strict production readiness remains intentionally blocked by the five declared fallback/placeholder model capabilities: appearance, face detection, face embedding, gait, and pose.
- Traceability: all `14` requirement cards are structurally valid; the release decision remains `block` because external observations and approvals are absent.

## Gate behavior

Run structural traceability without claiming release:

```powershell
python tools/portrait_upgrade_traceability.py
```

Run the commercial release decision. This must remain blocked until every requirement is at least `accepted` and all required approval records are present:

```powershell
python tools/portrait_upgrade_traceability.py --release
```

The repository must not be described as a complete commercial production release while any P0 requirement is blocked. Empty `approval_records` are intentional: no human signatures or target-environment observations have been fabricated.
