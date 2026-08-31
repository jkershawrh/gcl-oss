# Oberon EvalHub qualification

This deployment is an isolated contract qualification for GCL OSS and EvalHub. It
does not modify or reference the `governed-cognitive-loop` namespace.

## Pinned inputs

- TrustyAI Operator manifest revision:
  `547c02508be5fef23d1c90c76772fe11b5297832`
- TrustyAI Operator image:
  `quay.io/trustyai/trustyai-service-operator@sha256:2b4adaa22f2e85356f7044fc1546d04a12160ca843ac5e19b0cf99dee5c37b9c`
- EvalHub image and API contract:
  `quay.io/evalhub/evalhub@sha256:0b9b9cb7121170eb28d7a723b487282cc6ef3640ec9cf9ff6ba50b1bf04a61a1`
  (source revision `42c09dc6aa0a9f6b1cd1e2bb1b7cacc616dcf13e`)
- kube-rbac-proxy image:
  `quay.io/opendatahub/odh-kube-rbac-proxy@sha256:c19ed97828e4e5e736334b3ea340ba92a5d0c95f7b21080c6fd7ccf6d36836af`

The latest formal TrustyAI Operator release (`v1.38.0`) predates EvalHub. This
qualification therefore pins a development manifest and image and must not be
described as a supported Red Hat deployment.

The pinned upstream manifest omits the namespace on the
`manager-auth-delegator` service-account subject. OpenShift rejects that binding.
`operator-auth-delegator.patch` adds the conventional `system` placeholder before
rendering; the installer then rewrites it to the isolated operator namespace.
The same overlay mounts `webhook-server-cert` without including its webhook Service.
`operator-prerequisites.yaml` creates that Service with the OpenShift serving-certificate
annotation, which provisions the missing secret without introducing cert-manager.
Finally, the component emits unprefixed EvalHub ClusterRoles while its controller code
and manager binding reference `trustyai-service-operator-`-prefixed names. The installer
creates exact prefixed copies after applying the upstream roles. The unprefixed copies
remain because the upstream static bindings still reference them.

## Isolation and authority

- `gcl-oss-trustyai` contains the operator and one multi-tenant EvalHub instance.
- `gcl-oss-evalhub` is the only tenant and contains the GCL qualification Job.
- `gcl-oss-qualifier` receives only `get` on the synthetic `evaluations` RBAC
  resource in its own tenant namespace.
- GCL uses a no-op proposal sink. A successful run must report
  `execution_verified=false`.

The synthetic provider sleeps instead of contacting a model. The seed script reports
one failed-threshold terminal event through EvalHub's real authenticated event API.
This qualifies transport, tenant authorization, API normalization, OCI reference
binding, policy, signing, and proposal delivery. It does not qualify model execution
or registry content verification.

## Run

From the repository root, with `oc` logged into Oberon:

```bash
deploy/oberon/install-operator.sh
deploy/oberon/apply-platform.sh
```

Build and push the repository image to a registry the tenant can pull from. Then use
its digest, not a mutable tag:

```bash
GCL_IMAGE='image-registry.openshift-image-registry.svc:5000/gcl-oss-evalhub/gcl-oss@sha256:...' \
  deploy/oberon/run-qualification.sh
```

The qualification also proves that the same service account receives HTTP 403 when
it substitutes the control-plane namespace in `X-Tenant`. It retains the terminal
EvalHub API record and completed GCL Job logs, but removes the synthetic sleeping
runtime Job and ConfigMap.
