# Integration points

GCL OSS integrates at evidence, policy, proposal, proof, telemetry, and outcome boundaries. It does not replace the connected system.

## Priority map

| Integration | GCL port | Purpose | Priority |
|---|---|---|---|
| TrustyAI Service | `EvidenceSource`, `ConstraintClassifier` | Drift, fairness, and model-monitoring evidence plus reference governance constraints. | P0 |
| EvalHub | `EvidenceSource`, `ArtifactVerifier`, `ConstraintClassifier` | Terminal evaluation and collection-compliance evidence, verified artifacts, and reference promotion constraints. | P0 |
| No-op consumer | `ProposalSink` | Standalone testing without external authority. | P0 |
| Generic CloudEvents | `EvidenceSource`, `ProposalSink` | Portable asynchronous transport. | P1 |
| Authenticated webhook | `ProposalSink` | Generic external admission with explicit retry semantics. | P1 |
| Prometheus | `EvidenceSource` | Range-query operational evidence. | P1 |
| Alertmanager | `EvidenceSource` | Alert state transitions as evidence. | P1 |
| OpenTelemetry | `TelemetrySink` | Correlation across evaluation, proposal, authority, and outcome. | P1 |
| OPA/Rego | `PolicyCheck` | Deterministic evidence admission and candidate policy. | P1 |
| Guardrails Orchestrator | `EvidenceSource` | Aggregated detector rates and severity changes. | P1 |
| Append-only and OCI stores | `ProofRecorder` | Evidence and package receipts or artifacts. | P1 |
| Human review systems | `ProposalSink`, `OutcomeSource` | Approval workflow and observed disposition. | P1 |
| Kubernetes controllers | `ProposalSink`, `OutcomeSource` | Independent admission, execution, and observed state. | P2 |

## TrustyAI Service

The implemented adapter consumes explicit TrustyAI-computed drift and group-fairness
responses. It maps:

- producer service identity;
- OpenShift namespace and tenant;
- model or inference endpoint identity;
- metric name and evaluation window;
- observed value, threshold, and status;
- result confidence where available;
- source response or artifact digest;
- correlation identifiers.

TrustyAI owns the metric computation. GCL OSS must not reproduce the algorithm or change a passing result to failing. A policy pack decides how a valid result constrains proposals.

The integration is intentionally split in two. The evidence adapter only normalizes a TrustyAI result. A separately versioned policy pack derives namespaced, evidence-bound constraints such as review required or promotion blocked. This keeps a source-schema change from silently changing governance policy and lets TrustyAI contributors review the adapter without adopting GCL's proposal semantics.

The first contract pins six current compute endpoints: KS test, compare means,
Jensen-Shannon, MMD, statistical parity difference, and disparate impact ratio. The
client does not expose TrustyAI data upload or metric-schedule mutation. See the
[TrustyAI Service integration specification](trustyai-service-integration.md).

## EvalHub

EvalHub is the preferred first pre-deployment integration because it already presents a unified API across evaluation frameworks and collections.

The implemented API v1 adapter consumes terminal job state, provider, benchmark or
collection identity, compliance status, scores, OCI artifact references, tenant, and a
canonical result digest. It is pinned to an explicit upstream OpenAPI revision.
Non-terminal jobs are rejected rather than treated as success or failure. Failed job
execution is kept distinct from a failed model test.

Transport and normalization live in `gcl_oss.adapters.evalhub`. Evidence admission and
promotion constraints live in `gcl_oss.policy_packs.evalhub`. The default policy
requires complete digest-pinned OCI provenance for evaluation results. A stricter host
mode runs every declared artifact through `ArtifactVerifier` and requires trusted,
evidence-bound receipts before policy admission. See the [EvalHub integration
specification](evalhub-integration.md).

The first end-to-end demo should be:

```text
failed safety collection
  -> EvalHub result envelope
  -> deterministic, evidence-bound promotion constraint
  -> action-free objective over the current constraints
  -> io.github.jkershawrh.gcl.governance/request_review or hold candidate
  -> falsification checks
  -> signed DecisionPackage
  -> no-op or human-review ProposalSink
```

No operator change is required for this demo. Operator integration should follow demonstrated demand.

## Guardrails

GCL OSS is not in the synchronous token path. The adapter consumes aggregate detector behavior over a defined window, such as severe-event count, detector rate, model identity, and population scope. These signals may support slower operational proposals such as review, quarantine, rerouting, or isolation.

## Prometheus and Alertmanager

The Prometheus adapter records the query, time range, labels, result, source, and response digest. Alertmanager transitions are normalized as evidence, never commands. Resolved alerts remain useful outcome evidence when correlated to a package.

## OpenTelemetry

Trace context links the upstream observation, GCL cycle, package, external authorization, and outcome. OpenTelemetry provides correlation and operational telemetry. It is not the immutable proof store.

## OPA and Rego

OPA can implement evidence-admission and candidate policy checks. Each result records the policy package, bundle revision, query, decision, and relevant evidence. A policy response cannot sign a DecisionPackage unless OPA is separately operated as the external proposal authority.

## CloudEvents

CloudEvents is the preferred asynchronous transport envelope. Event type and schema version select a producer-specific normalizer. A generic endpoint must not treat arbitrary JSON as trusted evidence.

Initial event families should include:

- `io.github.jkershawrh.gcl.evidence.accepted.v1alpha1`;
- `io.github.jkershawrh.gcl.evidence.rejected.v1alpha1`;
- `io.github.jkershawrh.gcl.policy.evaluated.v1alpha1`;
- `io.github.jkershawrh.gcl.constraint.classified.v1alpha1`;
- `io.github.jkershawrh.gcl.objective.interpreted.v1alpha1`;
- `io.github.jkershawrh.gcl.falsification.completed.v1alpha1`;
- `io.github.jkershawrh.gcl.decision.proposed.v1alpha1`;
- `io.github.jkershawrh.gcl.decision.rejected.v1alpha1`;
- `io.github.jkershawrh.gcl.decision.delivery_unknown.v1alpha1`;
- `io.github.jkershawrh.gcl.outcome.observed.v1alpha1`.

## Proof recorders

The port should support append-only ledgers, OCI result artifacts, transparency logs, and signed object storage. The recorder returns a receipt identifier. The standalone kernel fails closed on a recorder error before delivery; if the final write fails after delivery, it preserves the acknowledged delivery and reports the proof failure without redelivering. A receipt never grants execution authority.

## Proposal consumers

Proposal sinks can target human review, generic webhooks, workflow engines, Kubernetes admission services, or fleet control planes. Every consumer owns:

- proposer authentication;
- signature, key identity, expiry, and scope verification;
- authorization;
- desired and observed state;
- execution and rollback;
- outcome reporting.

GCL OSS records `accepted`, `rejected`, or `deferred`. It does not infer execution from any of them.

## Packaging policy

P0 adapters live in the main repository until the conformance surface stabilizes. A vendor-specific adapter with a large SDK dependency should become a separate distribution that depends on `gcl-oss`, not a dependency of the core package.
