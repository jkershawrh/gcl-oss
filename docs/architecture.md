# Architecture

## Product boundary

GCL OSS is the governed decision layer between evidence and authority. It converts scoped evidence into a reviewable proposal. It is deliberately incapable of proving execution from a proposal response.

```text
                       ┌─────────────────────────────────┐
EvidenceSource ───────>│ validation and normalization    │
PolicyCheck ──────────>│ policy checks                   │
                       │ constraint classification        │
                       │ objective interpretation         │
                       │ deterministic planner            │
                       │ falsification checks             │
                       │ DecisionPackage builder + signer │
                       └───────────────┬─────────────────┘
                                       │
                         signed, expiry-bounded proposal
                                       │
               ┌───────────────────────┼───────────────────────┐
               v                       v                       v
         ProposalSink            ProofRecorder           TelemetrySink
      external authority       evidence receipts       metrics and traces
               │
               v
         OutcomeSource
      independent observation
```

## Layers

### Contracts

Contracts are the stable interoperability surface:

- `EvidenceEnvelope` normalizes producer identity, schema, tenant scope, subject, measurement, assurance, and freshness.
- `EvidenceReference` is the compact signed manifest entry for producer, evidence ID, schema, artifact digest and URI, and canonical envelope digest.
- `PolicyResult` records an allow or deny decision and its evidence references.
- `Constraint` records a namespaced hard or soft restriction, structured expression, provenance mode, confidence, and evidence references.
- `ObjectiveSpec` records namespaced weighted terms, every governing constraint, evidence references, and whether deterministic, model-assisted, or fallback framing was used. It has no action field.
- `Candidate` describes a namespaced proposed action, consequence, rationale, parameters, a normalized value for every objective cost term, constraints covered, and evidence references. Objective weights sum to one.
- `FalsificationResult` records an attempt to disconfirm a candidate.
- `DecisionPackage` binds policy results, constraints, objective, selected candidate, alternatives, evidence, scope, proposer, falsification, and validity window.
- `SignedDecisionPackage` binds the package digest to an Ed25519 signing identity.
- `ProposalReceipt` reports delivery or admission status and structurally refuses execution claims.
- `OutcomeRecord` represents later, independently observed results.

Producer-specific details use namespaced extensions and immutable artifact references. The signed package carries a compact evidence manifest but does not mirror complete upstream response bodies.

### Governance kernel

The implemented standalone kernel owns this lifecycle:

1. Receive evidence from one or more sources.
2. Authenticate the source at the host boundary.
3. Validate the envelope schema, tenant scope, subject, and freshness.
4. Evaluate deterministic producer, schema, digest, confidence, and domain policy checks.
5. Derive evidence-bound hard and soft constraints, using deterministic fallback at any model-assisted boundary.
6. Frame an action-free objective over every current constraint.
7. Ask a deterministic planner for candidates.
8. Recompute weighted candidate costs and enforce action schema, consequence class, evidence references, objective minimization, and coverage of every hard constraint.
9. Run the required falsification checks for the selected candidate.
10. Record rejected alternatives.
11. Bound package expiry by the earliest evidence expiry, then build and sign the complete DecisionPackage.
12. Offer it to a proposal sink.
13. Correlate later outcomes from an independent source.

The kernel works in memory with a no-op proposal sink. Network services are adapters, not preconditions.

### Ports

Ports are small interfaces owned by GCL OSS:

| Port | Direction | Responsibility |
|---|---|---|
| `EvidenceSource` | inbound | Supplies normalized evidence envelopes. |
| `PolicyCheck` | internal | Returns a deterministic allow or deny result justified by evidence. |
| `ConstraintClassifier` | internal | Derives evidence-bound hard and soft constraints with deterministic fallback. |
| `ObjectiveInterpreter` | internal | Frames an action-free objective over all current constraints. |
| `Planner` | internal | Produces candidates and explicitly identifies deterministic behavior. |
| `FalsificationCheck` | internal | Deterministically attempts to disconfirm a candidate. |
| `ProposalSink` | outbound | Offers a signed package to an external authority. |
| `ProofRecorder` | outbound | Records a receipt without granting authority. |
| `OutcomeSource` | inbound | Supplies independently observed outcomes. |
| `Signer` | outbound | Signs canonical package bytes without exposing private key material to the kernel. |
| `TelemetrySink` | outbound | Emits correlated metrics, logs, and traces. |

Python protocols are the initial binding. Remote protocols will be standardized only after two independent adapters demonstrate the same need.

### Replay and concurrency

The standalone kernel derives a cycle key from producer identity, evidence identity, producer-supplied artifact digest, canonical normalized-envelope digest, correlation, and scope. An exact retry returns the cached result and does not deliver the proposal again. Each kernel instance serializes cycles within one async event loop so concurrent duplicate retries cannot race past the cache.

If the proposal consumer times out, raises an error, or returns a receipt for a different package digest, the result is `delivery_unknown`. The signed package and uncertainty are cached, and an exact retry is not delivered automatically. An operator must reconcile the consumer before deliberately clearing or superseding that result. This avoids converting ambiguous transport failure into duplicate consequential work.

If final proof recording fails after successful proposal delivery, the delivery result is cached first. The kernel reports the proof failure explicitly and does not redeliver on replay.

This cache is process-local and its concurrency guard is event-loop-local. A multi-thread, multi-process, or multi-replica host must implement durable distributed idempotency before it can claim the same guarantee across execution contexts.

### Canonical signatures

The alpha signer receives the UTF-8 bytes of the validated package's JSON representation with null fields omitted, object keys sorted, compact separators, finite JSON numbers only, and non-ASCII characters escaped. The package digest and Ed25519 signature cover exactly those bytes. Cross-language golden vectors and a final canonicalization standard are beta prerequisites; consumers should pin the alpha package version rather than assume a future stable byte format.

### Adapters

An adapter translates one external contract into one port. It must document:

- supported upstream versions;
- authentication and workload identity;
- tenant and subject mapping;
- maximum evidence age and clock skew;
- replay and idempotency rules;
- digest or artifact verification;
- retry and timeout behavior;
- failure-open, failure-closed, or degraded semantics;
- data retention and redaction;
- a test fixture that does not require the external service.

Adapters cannot widen GCL authority. A proposal adapter may transport a package to a controller, but GCL does not become that controller.

### Hosts

The same kernel can run as:

- an embedded Python library;
- a local command-line or HTTP service;
- an asynchronous CloudEvents consumer;
- a multi-tenant service behind an authenticating proxy;
- a Kubernetes deployment.

A future Kubernetes resource may configure a GCL deployment. It must not make GCL an actuator.

## Trust boundaries

### Evidence boundary

Evidence is an assertion by a producer. The assurance digest identifies the source artifact, while the cycle key also binds the canonical normalized envelope. Neither makes the producer correct or proves the artifact relationship. The host authenticates transport identity, and policy decides which producer identities, schemas, digests, confidence floors, and age limits are acceptable.

### LLM honesty boundary

A model can assist with classifying ambiguous evidence or framing objective terms. Its output is schema-limited, evidence-bound, mode-labelled, and behind a deterministic fallback. The objective must retain every current constraint. The deterministic kernel recomputes candidate costs from the signed terms and rejects a selection that is not minimal or does not cover every hard constraint. Model-assisted code cannot emit an action candidate, mark a constraint satisfied, suppress a failed falsification check, sign a package, or call a proposal sink.

### Proposal boundary

The proposal consumer independently authenticates the proposer, verifies signature and expiry, authorizes the requested action, and owns execution. `accepted` means only that the proposal entered that consumer's process.

### Proof boundary

A proof recorder establishes that content was recorded under a receipt. It does not establish that the content is correct and it grants no authorization.

### Outcome boundary

Outcome observations must come from a source independent of the proposal acknowledgement. The target system, telemetry, or an auditor may report execution and effects.

## Contract evolution

The current contracts are `io.github.jkershawrh.gcl/v1alpha1`. Schemas, action names, extension keys, and CloudEvents use the conventional reverse-DNS namespace for the `jkershawrh` GitHub account. Alpha contracts may change between minor releases. Before beta:

1. publish JSON Schema fixtures;
2. add cross-language golden tests;
3. implement at least two evidence adapters and two proposal sinks;
4. specify extension naming and action registration;
5. write a migration policy.

Stable versions are never changed in place.

## Repository structure

```text
src/gcl_oss/
  adapters/          source-specific transport and evidence normalization
  contracts.py       portable versioned models and signatures
  ports.py           structural extension interfaces
  policy_packs/      separately versioned deterministic evidence policies
  registry.py        namespaced action definitions and parameter schemas
  kernel.py          deterministic proposal-only orchestration
  builtin.py         offline reference components
  schemas.py         deterministic JSON Schema export
tests/               contract and boundary tests
docs/                architecture, ADRs, and integration specifications
```

The orchestration kernel, built-in in-memory adapters, no-op proposal sink, schema
fixtures, and offline demonstrations are implemented without importing the production
proof implementation. The first EvalHub adapter and policy pack are present; live
environment qualification, the TrustyAI Service adapter, and the broader conformance
kit remain roadmap work.
