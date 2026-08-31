# Roadmap

This roadmap describes dependency order, not release dates.

## 0.1 alpha: contracts and ports (complete)

- Portable `EvidenceEnvelope` and `DecisionPackage` contracts.
- Ed25519 signing and verification.
- Proposal-only receipt semantics.
- Python protocols for every extension boundary.
- Architecture, integration, governance, and security specifications.
- JSON Schema export and golden fixtures.

Exit criteria: a third party can implement an adapter without importing internal modules.

## 0.2 alpha: standalone kernel (released)

- Deterministic orchestration lifecycle.
- In-memory evidence source and proof recorder.
- No-op proposal sink.
- Registered action schemas and consequence classes.
- Evidence-bound constraint classification and action-free objective interpretation.
- Policy-pack and falsification-check registration.
- Command-line demonstration with no network dependencies.
- Process-local, event-loop-local concurrent replay protection.

Deferred to 0.4: generic webhook delivery, which requires an explicit authentication, retry, and receipt contract rather than a demo-only HTTP call.

Exit criteria: a complete signed cycle runs locally using only built-in components.

Released as `v0.2.0a1` with wheel and source artifacts.

## 0.3 alpha: TrustyAI proof (in progress)

- EvalHub terminal-job and collection-result adapter (implemented against a pinned API
  v1 revision and qualified against its local server plus an authenticated, isolated
  OpenShift deployment on Oberon).
- EvalHub promotion policy pack and signed offline fixture demonstration (implemented).
- TrustyAI Service evidence adapter.
- Reference drift, fairness, safety, and promotion policy packs.
- Reproducible local and OpenShift demonstrations (EvalHub path complete; TrustyAI
  Service path remains).
- Joint contract fixtures suitable for upstream review.

Exit criteria: a TrustyAI or EvalHub result produces a signed proposal without giving GCL actuation authority.

## 0.4 alpha: operational ecosystem

- CloudEvents ingress and egress.
- Prometheus and Alertmanager evidence.
- OpenTelemetry correlation.
- OPA policy checks.
- Adapter conformance suite.
- Generic authenticated webhook proposal sink.
- Durable idempotency port for multi-process and multi-replica hosts.

## 0.5 beta: Kubernetes reference host

- Secure deployment manifests or chart.
- Workload identity, RBAC, NetworkPolicy, probes, and telemetry.
- Tenant isolation and replay tests.
- Optional configuration resource, with no actuator controller.

## 1.0: stable governance kernel

- Stable contracts and compatibility policy.
- Cross-language fixtures.
- At least two independent evidence adapters and proposal consumers.
- Threat model and independent security review.
- Multi-tenant and failure-mode qualification.
- More than one active maintainer.

## Out of scope

- Infrastructure execution and rollback.
- Reimplementing TrustyAI, EvalHub, Prometheus, or OPA.
- LLM-selected actions or LLM-enforced constraints.
- Inferring execution from proposal acknowledgement.
- A universal payload that copies every upstream schema.
