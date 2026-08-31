# ADR 0001: GCL is a proposal-only kernel

- Status: Accepted
- Date: 2026-08-31

## Context

Evidence systems detect risk and control systems execute changes. Combining evidence interpretation, proposal creation, authorization, execution, and outcome claims in one component creates an authority concentration and makes failures difficult to audit.

The earlier production proof implementation validated a governed decision cycle in an inference-fleet environment. The OSS project needs a portable boundary that does not import that environment's ownership assumptions.

## Decision

GCL OSS owns normalized decision synthesis through a signed, expiry-bounded proposal. It does not authorize or execute the proposal.

The architecture uses a small core with ports for evidence, deterministic policy, planning, falsification, proposal delivery, proof recording, outcome observation, keys, and telemetry.

Proposal acknowledgements cannot set `execution_verified=true`. Execution evidence enters later through an independent outcome source.

The project begins with contracts and ports rather than copying the production proof implementation. Integrations translate upstream contracts at the edge.

## Consequences

Benefits:

- integrations do not grant GCL new authority;
- DecisionPackages remain portable across proposal consumers;
- source and consumer projects retain their domain ownership;
- the kernel can run locally without a cluster or model server;
- outcomes can be evaluated independently from transport success.

Costs:

- a complete deployment needs at least one evidence source and one proposal consumer;
- authorization and execution are explicitly outside the project;
- end-to-end demos require a mock or real external authority;
- adapters must preserve provenance and failure semantics carefully.

## Rejected alternatives

### Copy the production proof repository

Rejected because it would bring fleet-specific actions, endpoints, deployment assumptions, and history into the vendor-neutral core.

### Add GCL directly to the TrustyAI Operator first

Rejected because it creates a long-lived deployment and support surface before the evidence contract has independent validation.

### Make GCL an actuator

Rejected because proposal synthesis and execution authorization require different authority and failure boundaries.
