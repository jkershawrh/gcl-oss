# Oberon EvalHub qualification

This deployment is an isolated contract qualification for GCL OSS and EvalHub. It
does not modify or reference the `governed-cognitive-loop` namespace.

## Pinned inputs

- TrustyAI Operator manifest revision:
  `a89d423ca49f70779f5b52f3c24df2efd56b7e4c`
- TrustyAI Operator image:
  `image-registry.openshift-image-registry.svc:5000/gcl-oss-trustyai/trustyai-service-operator@sha256:2896ac39a2c7fa66f9a986fd3de3b1e13903456ee018905f3818c7948a552ee4`
- EvalHub image and API contract:
  `quay.io/evalhub/evalhub@sha256:0b9b9cb7121170eb28d7a723b487282cc6ef3640ec9cf9ff6ba50b1bf04a61a1`
  (source revision `42c09dc6aa0a9f6b1cd1e2bb1b7cacc616dcf13e`)
- kube-rbac-proxy image:
  `quay.io/opendatahub/odh-kube-rbac-proxy@sha256:c19ed97828e4e5e736334b3ea340ba92a5d0c95f7b21080c6fd7ccf6d36836af`

The latest formal TrustyAI Operator release (`v1.38.0`) predates EvalHub. This
qualification therefore pins a development manifest and image and must not be
described as a supported Red Hat deployment.

The public `main` operator image was older than its moving source manifest and still
generated `/healthz` probes, while the pinned EvalHub server exposes
`/api/v1/health`. The operator image above was built directly from the stated source
revision and pushed to Oberon's internal registry; its digest is the deployment pin.

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

The rendered leader-election Role and RoleBinding have no metadata namespace. The
installer always supplies `-n gcl-oss-trustyai` when applying the bundle so they cannot
inherit whichever project happens to be active in the caller's `oc` context.

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
