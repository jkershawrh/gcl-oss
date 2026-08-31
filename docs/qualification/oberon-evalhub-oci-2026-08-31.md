# Oberon EvalHub OCI-content qualification — 2026-08-31

## Verdict

The isolated EvalHub-to-GCL path passed registry-content qualification on Oberon.
Before policy admission, the GCL workload authenticated to OpenShift's internal OCI
registry, fetched a digest-pinned leaf manifest, recomputed its SHA-256 digest, fetched
every config and layer descriptor, and verified each descriptor's digest and declared
size. Strict EvalHub policy admitted the resulting verifier receipt and produced one
signed proposal without execution authority.

This is an alpha integration qualification. It is not a Red Hat support statement,
model-execution qualification, semantic validation of an evaluation artifact, or
evidence that a proposal was authorized or executed.

## Qualified path

```text
authenticated EvalHub event API
  -> tenant-scoped failed-threshold EvaluationJobResource
  -> projected-token GCL reader over service TLS
  -> digest-pinned OCI artifact declaration
  -> allowlisted internal registry over service TLS
  -> pull-only Bearer authentication with projected workload token
  -> manifest, config, and layer byte verification
  -> receipt-bound normalized evidence
  -> strict EvalHub policy admission
  -> deterministic request_review selection and freshness falsification
  -> Ed25519-signed DecisionPackage
  -> no-op proposal receipt (execution_verified=false)
```

Only `gcl-oss-trustyai` and `gcl-oss-evalhub` were used. The production proof
namespace `governed-cognitive-loop` was not referenced or modified.

## Environment and immutable pins

- OpenShift `4.18.16`, Kubernetes `v1.31.8`.
- GCL source revision `3157cac13601926617425aa60b2ea29decaad1a5`, version
  `0.3.0a1`, amd64 image
  `sha256:a6cc596c2e496eefeb0baa0e42fb6ec8b188d703947b2de271c2fbe2df272aa2`.
  The image's OCI revision label matches the full source revision.
- Qualification artifact source: `deploy/oberon/oci-fixture` at the same GCL source
  revision; OCI leaf-manifest digest
  `sha256:374d42e54ebc6e918069c2a2c00045089dcb3909ea30497a8be0cadffb028cff`.
- TrustyAI Operator source revision
  `a89d423ca49f70779f5b52f3c24df2efd56b7e4c`, locally built image
  `sha256:2896ac39a2c7fa66f9a986fd3de3b1e13903456ee018905f3818c7948a552ee4`.
- EvalHub contract revision
  `42c09dc6aa0a9f6b1cd1e2bb1b7cacc616dcf13e`, image
  `sha256:0b9b9cb7121170eb28d7a723b487282cc6ef3640ec9cf9ff6ba50b1bf04a61a1`.
- kube-rbac-proxy image
  `sha256:c19ed97828e4e5e736334b3ea340ba92a5d0c95f7b21080c6fd7ccf6d36836af`.

At capture time, the TrustyAI controller reported one ready replica and the EvalHub
deployment reported one ready pod with both containers ready.

## Registry verification evidence

- EvalHub job ID: `09c861bf-a01e-49e3-9570-5bda7daf3b5e`.
- Provider and benchmark: `gcl_contract_probe` / `contract_probe`.
- EvalHub state: `completed`; score `0.62`; threshold `0.9`; test `pass=false`.
- Verifier identity:
  `https://jkershawrh.github.io/gcl-oss/verifiers/oci-distribution/v1`.
- Verification time: `2026-08-31T21:01:57.348789Z`.
- Manifest media type: `application/vnd.oci.image.manifest.v1+json`;
  manifest size: `568` bytes. The requested digest, recomputed digest, and registry
  `Docker-Content-Digest` all matched the artifact digest above.
- Config descriptor: `1,051` bytes,
  `sha256:ff046384ebaf6d2e053b444e084569faf6798a8c8ca204d2030be62d736d415b`.
- Layer descriptor: `276` bytes,
  `sha256:bac185ba90453995c255642fddfac991d326b0f0014f2176136eebb455fa0b77`.
- Both descriptor byte digests matched their manifest declarations and registry
  content-digest headers.

The implementation follows the [OCI Distribution specification](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)
and [OCI descriptor specification](https://github.com/opencontainers/image-spec/blob/main/descriptor.md)
for digest-addressed manifest/blob retrieval and descriptors. It intentionally accepts
only OCI image and Docker schema-2 leaf manifests in this alpha; index selection is
out of scope.

## Governance result

- Raw authenticated EvalHub response digest:
  `sha256:7b66e1267219f1102f1d715614cafa2a39a9cadea2b09f4b45d6ff580e5c6488`.
- Receipt-bound normalized envelope digest:
  `sha256:2df40c9dc5ecb6a9d0731a932b3739daa65f340e677230aebe5b2a02d154f773`.
- Policy result: allowed, with reason `all EvalHub evidence satisfies the pinned
  terminal-result contract with registry-verified OCI content`.
- Selected action: `io.github.jkershawrh.gcl.governance/request_review`.
- Rejected alternative: `io.github.jkershawrh.gcl.governance/hold`.
- Signed package digest:
  `sha256:4fe1abb54118c29593d2e048050e3f04be9db7678e0d27e2e5609f71d686b88a`.
- Proposal delivery count: `1`; receipt status: `deferred`; consumer:
  `noop://standalone`; `execution_verified=false`.

The qualification Job completed successfully and remains available for log
inspection. The synthetic provider runtime Job and ConfigMap were removed after the
run.

## Tenant, credential, and network boundaries

The GCL Job used the `gcl-oss-qualifier` service account. Its projected token was read
from a file, not passed as a command-line secret. The verifier exactly allowlisted
`image-registry.openshift-image-registry.svc:5000`, extended system trust with the
OpenShift service CA, requested only
`repository:gcl-oss-evalhub/gcl-oss-evalhub-fixture:pull`, rejected cross-origin
redirects, and enforced bounded
manifest, blob, and total response sizes.

The own-tenant EvalHub read succeeded. Reusing the same identity with
`X-Tenant: gcl-oss-trustyai` returned HTTP `403`. The workload had no deployment,
model-serving, admission, or infrastructure actuation authority.

## What this does not prove

- The fixture is a repository-owned OCI conformance input, not an upstream EvalHub
  evaluation-card fixture.
- Byte integrity does not prove that the artifact semantically supports the producer's
  evaluation claims.
- The signer and proof recorder remain ephemeral and in memory.
- Same-origin-only redirects may not support registries that require an external blob
  CDN; any future relaxation needs a separate explicit host and credential policy.
- The pinned TrustyAI development deployment is not a released, supported Red Hat
  combination.

The repeatable assets and commands are in
[`deploy/oberon`](../../deploy/oberon/README.md).
