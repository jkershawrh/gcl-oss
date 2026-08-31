# Oberon TrustyAI Service qualification — 2026-08-31

Status: **passed**

This record covers the first live TrustyAI Service evidence-to-proposal path for GCL
OSS. It is an isolated engineering qualification, not a supported Red Hat deployment,
product certification, or TrustyAI upstream endorsement.

## Scope and pins

- Cluster: Oberon, OpenShift `4.18.16`, Kubernetes `v1.31.8`.
- GCL source: `80e857906c9de3b6440bd7a62ea976329d30cfd2`.
- GCL image:
  `image-registry.openshift-image-registry.svc:5000/gcl-oss-evalhub/gcl-oss@sha256:fe962135e47a17b2f5705294d9d5e3e35df1cea758665ae9fa1ca86333f4e51f`.
- TrustyAI Service source:
  `f78ca0e91cc24745fdaacb8f8ae893b059c03a0c`, version `1.0.0rc0`.
- TrustyAI Service image:
  `image-registry.openshift-image-registry.svc:5000/gcl-oss-trustyai/trustyai-service@sha256:b136756d91763c2f33978565443e6d1ae5cf0daca76e09a67c722a3ffeda1a24`.
- kube-rbac-proxy image:
  `quay.io/opendatahub/odh-kube-rbac-proxy@sha256:c19ed97828e4e5e736334b3ea340ba92a5d0c95f7b21080c6fd7ccf6d36836af`.
- TrustyAI Operator source:
  `a89d423ca49f70779f5b52f3c24df2efd56b7e4c` for the existing EvalHub deployment.
- Namespaces: `gcl-oss-trustyai` and `gcl-oss-evalhub` only.

The GCL image label `org.opencontainers.image.revision` matched the source commit, and
the running Pods reported the two image digests above.

## Deployment decision

Oberon does not provide the KServe `InferenceService` CRD. Enabling the pinned
operator's TAS controller caused its mandatory `InferenceService` watch to wait for a
missing API, so no TrustyAI Service resources could reconcile. No placeholder KServe
CRD was installed.

The temporary TAS custom resource and TAS-specific operator RBAC created during that
check were removed. The final operator deployment has
`--enable-services EVALHUB`, and `trustyaiservices.trustyai.opendatahub.io` is absent.

TrustyAI Service was instead deployed directly in `gcl-oss-evalhub` from the pinned
upstream image. It uses a one-GiB PVC, OpenShift service certificate, digest-pinned
kube-rbac-proxy, service-level SubjectAccessReview, and a re-encrypt Route. This
qualifies the authenticated Service API consumed by GCL; it does not qualify the TAS
operator controller.

## Exercise

The administrator-only seed step uploaded:

- 100 tagged baseline observations with `transaction_amount` values `0` through `99`;
- 100 untagged current observations with values `1000` through `1099`; and
- model ID `gcl-oss-oberon-drift-20260831` with reference tag
  `gcl-oss-baseline-20260831`.

Repeated qualification runs append the same deterministic samples. TrustyAI's current
batch remains the latest 100 untagged observations, while all identically distributed
tagged baseline rows remain reference data.

The GCL Job called only `POST /metrics/drift/kstest` through the in-cluster TLS Service.
Its request asked for a batch of 100 and alpha `0.05`. The final qualified response
reported:

- KS statistic: `1.0`;
- p-value: `8.921184402908388e-97`;
- alpha: `0.05`; and
- `drift_detected=true`.

## GCL result

GCL normalized the response as failed runtime evidence with:

- API revision: `f78ca0e91cc24745fdaacb8f8ae893b059c03a0c`;
- schema link: the pinned upstream `kolmogorov_smirnov.py` implementation;
- provenance mode: `authenticated-compute-response`;
- request digest:
  `sha256:ae474d5bcd98426c6fd402ba30b62c1c9c11c6b4c3344636cbdc4cf9ae1fd05e`;
- response digest:
  `sha256:1640285d2fd02683ab203669822dab3c8f9559a5c64dae5fbe5a1f33596f504a`;
- exchange/evidence digest:
  `sha256:4bb519f21af12099695d2b34e83755195e584fdd53dec74d970d06445daeb4ee`;
  and
- canonical envelope digest:
  `sha256:6e1ee7cf29808c6fade9ab4123cca02e87378140f054787335ed2b70f1644392`.

The strict `trustyai-service-evidence-v1alpha1` policy admitted the evidence. The
deterministic classifier produced one hard
`io.github.jkershawrh.gcl.trustyai/runtime-review-required` constraint, and the planner
selected `io.github.jkershawrh.gcl.governance/request_review` over the more disruptive
hold alternative.

The final package:

- survived the evidence-freshness check;
- used an ephemeral Ed25519 qualification key;
- had package digest
  `sha256:8957e0c0ce78a7ff544814b239dfd5f97558df0c509ee45e5c2969698ee97374`;
- produced six in-memory proof receipts;
- was delivered once to `noop://standalone`; and
- reported `execution_verified=false` and receipt status `deferred`.

## Authentication and isolation checks

- The qualification ServiceAccount can `get` only the named
  `gcl-oss-trustyai-service`; it cannot `get` another Service in the tenant namespace.
- An authenticated default ServiceAccount received HTTP `403` from the TrustyAI
  proxy.
- An unauthenticated request received HTTP `401`.
- The live GCL command read its projected token and service CA from mounted files.
- The normalized package contains request and response digests, not the raw feature or
  population payloads.
- All namespaced objects target only `gcl-oss-trustyai` and `gcl-oss-evalhub`. The
  production proof-layer namespace was not referenced or modified by this work.

The kube-rbac-proxy check is Service-granular. Once authorized for the Service, it does
not distinguish compute endpoints from upload or schedule endpoints. The GCL client
itself exposes only the six compute methods; a production environment requiring
infrastructure-enforced method isolation needs an allowlisting gateway.

## Local and continuous checks

- Ruff: passed.
- Pytest: `107 passed`.
- Wheel build: passed for `gcl_oss-0.3.0a1-py3-none-any.whl`.
- GitHub Actions: Python `3.9`, `3.12`, and `3.13` passed in
  [CI run 33444379906](https://github.com/jkershawrh/gcl-oss/actions/runs/33444379906).

## What this does not prove

- No real model inference occurred; the data was synthetic and deliberately shifted.
- Only KS drift was exercised live. All six pinned compute response contracts are
  covered locally, but fairness, compare-means, Jensen-Shannon, and MMD still need live
  conformance fixtures.
- TrustyAI did not sign the response or return an immutable result artifact or source
  timestamp.
- The direct deployment is not TAS operator qualification.
- The no-op proposal sink is not an external authorization or execution system.
- The signing key and proof recorder are ephemeral and process-local.
- Durable multi-replica replay protection remains out of scope for this alpha.
