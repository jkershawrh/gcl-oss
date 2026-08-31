# TrustyAI and EvalHub engagement brief

This brief defines the upstream conversation now that the EvalHub and TrustyAI Service
proofs are demonstrable.
It is not an adoption claim or a request for TrustyAI to transfer execution authority
to GCL.

Use the [TrustyAI ecosystem architecture](diagrams/ecosystem-architecture.md) and the
[architecture and event workflow set](diagrams/README.md) during the design review.

## Positioning

GCL OSS consumes trustworthy evaluation or monitoring results and turns them into a
signed, expiry-bounded governance proposal. TrustyAI and EvalHub continue to own metric
and evaluation computation. An independent human or machine authority continues to own
authorization, execution, rollback, and outcome reporting.

The integration value is the inspectable chain between evidence and a proposed
operational response:

```text
EvalHub or TrustyAI result
  -> source-specific normalization
  -> deterministic evidence policy
  -> evidence-bound constraints
  -> objective and alternatives
  -> falsification
  -> signed proposal
  -> independent authority
```

## First maintainers to approach

Use project ownership rather than presumed employer reporting lines. As of 2026-08-31,
the TrustyAI Operator CODEOWNERS file identifies:

- `@julpayne` and `@ruivieira` for EvalHub controller and API work;
- `@ruivieira`, `@RobGeada`, and `@SudipSinha` for TrustyAI Service work;
- `@RobGeada`, `@christinaexyou`, and `@m-misiura` for Guardrails work later.

The source of truth is the current
[TrustyAI Operator CODEOWNERS](https://github.com/trustyai-explainability/trustyai-service-operator/blob/main/.github/CODEOWNERS).
The TrustyAI community repository publishes its
[community meeting](https://github.com/trustyai-explainability/community#community-meetings)
and ADR process.

## First request

Ask for contract review, not product endorsement. For EvalHub:

1. Is `EvaluationJobResource` the intended downstream result boundary?
2. Are `completed`, `partially_failed`, `failed`, and `cancelled` mapped correctly?
3. Is the overall `results.test` field the right collection-compliance signal?
4. Are `oci_reference` and `oci_digest` stable provenance fields for downstream use?
5. Which tenant and model identifiers should remain mandatory?
6. Where should a jointly maintained conformance fixture live?
7. Would a short ADR or GitHub discussion be the preferred architectural record?

For TrustyAI Service:

1. Are the six synchronous compute endpoints intended as a supported downstream
   evidence boundary?
2. Are p-value, threshold, and fairness-range verdict semantics mapped correctly?
3. Should compute responses gain a source timestamp, result identifier, model version,
   or signed/immutable result reference?
4. Is there a preferred stable OpenAPI or compatibility marker beyond a source-revision
   pin during the current release-candidate phase?
5. Should model namespace and tenant be explicit in the service response rather than
   supplied by the authenticated host?
6. Is service-level kube-rbac-proxy authorization sufficient, or is method/path-level
   authorization planned for compute-only consumers?
7. Should the operator's KServe `InferenceService` watch be optional when KServe is
   absent and no inference-service integration is requested?

Do not ask for an operator integration, new custom resource, or downstream product
commitment in the first conversation.

## Demonstration agenda

Keep the first session to twenty minutes:

1. Two minutes: state the evidence-to-proposal gap.
2. Three minutes: show the authority boundary; GCL cannot execute.
3. Five minutes: run `gcl-oss evalhub-demo` and `gcl-oss trustyai-demo`.
4. Five minutes: inspect evidence provenance, constraint, alternatives, signature, and
   `execution_verified=false`.
5. Five minutes: ask the seven contract questions above and agree on one next review.

## Evidence to bring

- the tagged standalone `v0.2.0a1` baseline;
- the EvalHub adapter and pinned API revision;
- tests for terminal state, tenant separation, provenance, stale data, and no-actuation;
- the packaged failed-safety fixture;
- the signed demo output and the
  [Oberon qualification record](qualification/oberon-evalhub-2026-08-31.md), including
  the projected-token success, cross-tenant HTTP 403, and upstream compatibility gaps;
- the TrustyAI Service integration specification, six-contract test coverage, and
  [Oberon live-compute qualification evidence](qualification/oberon-trustyai-service-2026-08-31.md);
- the observed operator/KServe dependency clearly separated from the GCL API contract.

## Success criteria

The first conversation succeeds if maintainers confirm or correct the consumed contract
and identify a review path. It does not require a TrustyAI merge, an OpenShift AI product
commitment, or agreement that GCL belongs inside the TrustyAI Operator.
