# Architecture

## Product boundary

GCL OSS is the governed decision layer between evidence and authority. It converts scoped evidence into a reviewable proposal. It is deliberately incapable of proving execution from a proposal response.

```text
                       ┌─────────────────────────────────┐
EvidenceSource ───────>│ validation and normalization    │
ContextProvider ──────>│ policy checks                   │
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
- `Candidate` describes a namespaced proposed action, consequence, rationale, parameters, and evidence references.
- `FalsificationResult` records an attempt to disconfirm a candidate.
- `DecisionPackage` binds the selected candidate, alternatives, evidence, scope, proposer, falsification, and validity window.
- `SignedDecisionPackage` binds the package digest to an Ed25519 signing identity.
- `ProposalReceipt` reports delivery or admission status and structurally refuses execution claims.
- `OutcomeRecord` represents later, independently observed results.

Producer-specific details use namespaced extensions and immutable artifact references. The contracts do not mirror complete upstream response bodies.

### Governance kernel

The target kernel owns this lifecycle:

1. Receive evidence from one or more sources.
2. Authenticate the source at the host boundary.
3. Validate schema, tenant scope, subject, freshness, and provenance.
4. Evaluate deterministic policy checks.
5. Frame an objective, optionally using an LLM with a deterministic fallback.
6. Ask a deterministic planner for candidates.
7. Enforce action schema, consequence class, and hard constraints.
8. Run the required falsification checks for the selected candidate.
9. Record rejected alternatives.
10. Build and sign the DecisionPackage.
11. Offer it to a proposal sink.
12. Correlate later outcomes from an independent source.

The kernel must work in memory with a no-op proposal sink. Network services are adapters, not preconditions.

### Ports

Ports are small interfaces owned by GCL OSS:

| Port | Direction | Responsibility |
|---|---|---|
| `EvidenceSource` | inbound | Supplies normalized evidence envelopes. |
| `PolicyCheck` | internal | Returns a deterministic allow or deny result justified by evidence. |
| `Planner` | internal | Produces candidates and explicitly identifies deterministic behavior. |
| `FalsificationCheck` | internal | Attempts to disconfirm a candidate. |
| `ProposalSink` | outbound | Offers a signed package to an external authority. |
| `ProofRecorder` | outbound | Records a receipt without granting authority. |
| `OutcomeSource` | inbound | Supplies independently observed outcomes. |
| `KeyProvider` | inbound | Supplies signing and verification keys or handles. |
| `TelemetrySink` | outbound | Emits correlated metrics, logs, and traces. |

Python protocols are the initial binding. Remote protocols will be standardized only after two independent adapters demonstrate the same need.

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

Evidence is an assertion by a producer. A digest binds the normalized envelope to an artifact, but does not make the producer correct. Policy decides which producer identities, schemas, confidence floors, and age limits are acceptable.

### LLM honesty boundary

An LLM can summarize context or frame objective terms. It cannot emit an action candidate, mark a constraint satisfied, suppress a failed falsification check, sign a package, or call a proposal sink.

### Proposal boundary

The proposal consumer independently authenticates the proposer, verifies signature and expiry, authorizes the requested action, and owns execution. `accepted` means only that the proposal entered that consumer's process.

### Proof boundary

A proof recorder establishes that content was recorded under a receipt. It does not establish that the content is correct and it grants no authorization.

### Outcome boundary

Outcome observations must come from a source independent of the proposal acknowledgement. The target system, telemetry, or an auditor may report execution and effects.

## Contract evolution

The current contracts are `gcl.io/v1alpha1`. Alpha contracts may change between minor releases. Before beta:

1. publish JSON Schema fixtures;
2. add cross-language golden tests;
3. implement at least two evidence adapters and two proposal sinks;
4. specify extension naming and action registration;
5. write a migration policy.

Stable versions are never changed in place.

## Repository structure

```text
src/gcl_oss/
  contracts.py       portable versioned models and signatures
  ports.py           structural extension interfaces
tests/               contract and boundary tests
docs/                architecture, ADRs, and integration specifications
```

The orchestration kernel, built-in adapters, and conformance fixtures will be added without importing the production proof implementation.
