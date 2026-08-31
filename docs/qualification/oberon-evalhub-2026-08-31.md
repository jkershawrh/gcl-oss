# Oberon EvalHub contract qualification — 2026-08-31

## Verdict

The isolated EvalHub-to-GCL proposal path passed on Oberon. A projected-token
workload fetched one completed, failed-threshold EvalHub evaluation from its own
tenant, normalized it, applied the pinned policy, selected the least consequential
sufficient proposal, signed the decision package, and delivered it exactly once to
the no-op proposal boundary.

This is an alpha contract qualification. It is not a Red Hat support statement, a
model-execution qualification, or evidence that any proposal was authorized or
executed.

## Qualified path

```text
authenticated EvalHub event API
  -> tenant-scoped terminal EvaluationJobResource
  -> projected-token GCL reader over service TLS
  -> normalized failed-compliance evidence
  -> promotion-review-required constraint
  -> request_review and hold alternatives
  -> deterministic cost selection and freshness falsification
  -> Ed25519-signed DecisionPackage
  -> no-op proposal receipt (execution_verified=false)
```

The run used only the isolated namespaces `gcl-oss-trustyai` and
`gcl-oss-evalhub`. The production proof namespace `governed-cognitive-loop` was not
referenced or modified.

## Environment and immutable pins

- OpenShift `4.18.16`, Kubernetes `v1.31.8`.
- GCL source revision `284034a6b9144fecd93f2f4b74d107048ccd3a99`, version
  `0.3.0a1`, amd64 image
  `sha256:aad1b3893be05e6cb029410fd9da465dcf0742e6f462e8066e1ba1b196da4472`.
- TrustyAI Operator source revision
  `a89d423ca49f70779f5b52f3c24df2efd56b7e4c`, locally built image
  `sha256:2896ac39a2c7fa66f9a986fd3de3b1e13903456ee018905f3818c7948a552ee4`.
- EvalHub source contract revision
  `42c09dc6aa0a9f6b1cd1e2bb1b7cacc616dcf13e`, multi-architecture image
  `sha256:0b9b9cb7121170eb28d7a723b487282cc6ef3640ec9cf9ff6ba50b1bf04a61a1`.
- kube-rbac-proxy image
  `sha256:c19ed97828e4e5e736334b3ea340ba92a5d0c95f7b21080c6fd7ccf6d36836af`.

The running operator reported `1/1` ready, and EvalHub reported `2/2` containers
ready with the server readiness probe set to `/api/v1/health`.

## Result evidence

- EvalHub job ID: `01ca02c2-d3aa-467a-9b36-cfef7e85a216`.
- Provider and benchmark: `gcl_contract_probe` / `contract_probe`.
- EvalHub state: `completed`; score `0.62`; threshold `0.9`; test `pass=false`.
- GCL normalized measurement: `evalhub.job.compliance`, status `failed`.
- Raw authenticated response digest:
  `sha256:ed9b14a95ef3e6012c2fa7e15eebeee037d542f6e7c8107f23332740488d9273`.
- Selected action: `io.github.jkershawrh.gcl.governance/request_review`.
- Rejected alternative: `io.github.jkershawrh.gcl.governance/hold`, because its
  weighted objective cost was higher.
- Signed package digest:
  `sha256:8d2724d880ca425ec35df76ed353ec490b84381ada5db002e1cd380c50ec8f49`.
- Proposal delivery count: `1`; receipt status: `deferred`; consumer:
  `noop://standalone`; `execution_verified=false`.
- Proof entries: `6`; the qualification Job completed successfully and remains
  available for log inspection.

The OCI fixture reference was syntactically bound to its declared digest. Registry
bytes were deliberately not fetched or verified, so the run makes no content
verification claim.

## Tenant and authority checks

The GCL Job used the `gcl-oss-qualifier` service account and its projected token. Its
namespace Role grants only `get` on EvalHub's virtual `evaluations` authorization
resource. The own-tenant request succeeded. Reusing the same token with
`X-Tenant: gcl-oss-trustyai` returned HTTP `403`.

The GCL path used an ephemeral signing key, an in-memory proof recorder, and a no-op
proposal sink. It had no deployment, model-serving, admission, or infrastructure
actuation authority. The synthetic provider's sleeping runtime Job and generated
ConfigMap were removed after the run.

## Compatibility findings

The qualification exposed integration issues worth taking upstream:

1. The latest formal TrustyAI Operator release predates EvalHub, so the run required a
   pinned development revision and cannot be presented as supported product behavior.
2. The public moving operator image lagged its source and generated the obsolete
   `/healthz` EvalHub probe. Building the pinned source produced the compatible
   `/api/v1/health` deployment.
3. The development overlay omitted a namespace on the auth-delegator subject, omitted
   the webhook Service needed for its serving certificate, and disagreed with the
   controller over prefixed EvalHub ClusterRole names. The isolated installer applies
   narrow, documented compatibility fixes.
4. The operator mounts `tenant` provider ConfigMaps under `providers/tenant`, while
   EvalHub 1.0.2 did not load that nested file. The qualification therefore installs
   its probe as an explicitly selected administrator provider and tests tenancy at the
   authenticated evaluation-record boundary.
5. OpenShift template rendering removed the Job namespace. Both deployment scripts
   now force their intended namespaces instead of inheriting the caller's current
   project.

During discovery, two namespace-less leader-election RBAC objects and one processed
qualification Job briefly inherited the active `cascade-compression` project. Each
object was identified and deleted; repeatable namespace guards were added before the
passing run. No such object remains there.

## Remaining before a stronger claim

- Verify OCI registry content, media type, and authenticated digest retrieval.
- Use a persistent EvalHub database and durable proof recorder.
- Replace the ephemeral signer with managed workload-bound key identity and rotation.
- Add NetworkPolicy and a durable, authenticated proposal consumer.
- Obtain an upstream-owned conformance fixture and maintainer review of the consumed
  EvalHub contract.
- Repeat against a released, supported operator/EvalHub combination when one exists.

The repeatable assets and exact pins are in
[`deploy/oberon`](../../deploy/oberon/README.md).
