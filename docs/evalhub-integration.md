# EvalHub integration

The first TrustyAI-family integration converts a terminal EvalHub job resource into
portable GCL evidence, applies a separately versioned promotion policy, and produces a
signed proposal without obtaining deployment authority.

## Supported contract

The adapter targets EvalHub API v1 endpoint
`GET /api/v1/evaluations/jobs/{id}`. The parsed contract is pinned to EvalHub commit
[`42c09dc6aa0a9f6b1cd1e2bb1b7cacc616dcf13e`](https://github.com/eval-hub/eval-hub/commit/42c09dc6aa0a9f6b1cd1e2bb1b7cacc616dcf13e).

The adapter validates the security- and governance-relevant subset of
`EvaluationJobResource` while tolerating additive API fields. A changed meaning or
shape for a consumed field requires a new adapter contract revision.

Consumed fields are:

- resource ID, tenant, creation time, and update time;
- terminal job state;
- model name and optional caller-supplied model version;
- collection identity where present;
- overall score, threshold, and pass result;
- benchmark identity, provider, index, primary score, threshold, and pass result;
- OCI artifact reference and digest.

Owner identity, model endpoint URLs, arbitrary custom fields, logs, and complete raw
metrics are not copied into the evidence envelope. The canonical upstream response is
hashed so the exact fetched representation remains bound even when only a compact
summary is retained.

## Separation of responsibilities

`gcl_oss.adapters.evalhub` owns transport and normalization only. It cannot decide an
action.

`gcl_oss.policy_packs.evalhub` owns evidence admission and the deterministic mapping
from a concerning result to a promotion-review constraint. The policy pack cannot sign
or deliver a proposal.

The existing deterministic planner considers the namespaced constraint and constructs
the registered `request_review` and `hold` alternatives. The governance kernel checks
the selected action, recomputes its objective cost, runs falsification, signs the
package, and sends it to a proposal sink.

## State semantics

- `pending` and `running` jobs are rejected by the normalizer because they are not
  terminal evidence.
- `completed` jobs use the explicit overall test result. A completed compliance result
  without a test is rejected by the policy pack.
- `partially_failed` jobs become warnings even if an available partial score passes.
- `failed` jobs are represented as failed job-execution evidence, never as a failed
  model evaluation.
- `cancelled` jobs are represented as job-execution warnings, never as model failures.

A passed collection produces no promotion constraint. A failed collection produces a
hard `promotion-review-required` constraint. Incomplete or cancelled execution
produces a soft review constraint. These are governance constraints, not actuator
commands.

## Provenance

For a single benchmark with a digest-pinned OCI reference, the envelope uses that OCI
digest and reference directly. For multiple benchmarks with complete OCI provenance,
it uses a deterministic digest of the sorted OCI manifest and records each individual
reference in the compact envelope extensions. If OCI provenance is incomplete, the
normalizer falls back to a canonical authenticated-API-response digest.

The default EvalHub policy requires complete OCI provenance for evaluation results and
fails closed on the fallback. Hosts may relax that setting explicitly for development,
but must not describe response-only evidence as immutable artifact evidence.

Normalization verifies that every OCI reference ends with its declared digest but does
not perform network I/O. A host can additionally pass the evidence through the generic
`ArtifactVerifier` port. The included OCI Distribution verifier fetches the leaf
manifest by digest, recomputes its SHA-256 digest, and verifies every config and layer
descriptor's bytes, digest, declared size, and media type. The resulting positive
receipt is bound to each normalized benchmark artifact. Strict EvalHub policy accepts
only complete receipts from an explicitly trusted verifier identity.

Registry access is fail-closed: each registry host must be exactly allowlisted, HTTPS
is required by default, custom CAs are additive to system trust, and credentials are
read from a Docker auth file or a username plus password/token file. OCI Bearer token
requests are reduced to the exact repository pull scope. A token service on a different
host also requires an explicit `--registry-auth-host-allow`. Response sizes are
bounded, compressed responses and cross-origin redirects are rejected, and
index/manifest-list selection is not supported in this alpha. The verifier accepts OCI
image manifests and Docker schema-2 leaf manifests.

## Offline demonstration

Run the packaged API-v1-shaped fixture through the complete path:

```bash
gcl-oss evalhub-demo
```

Use another recorded terminal job response:

```bash
gcl-oss evalhub-demo \
  --input path/to/job.json \
  --source-base-url https://evalhub.example \
  --tenant team-a \
  --namespace models \
  --environment staging \
  --model-version v7
```

The command uses a fixture-relative clock so historical responses remain reproducible.
It is not a live polling command. The output includes the normalized evidence and the
complete signed proposal. The no-op receipt always has `execution_verified=false`.

## Live reader

The standard-library HTTP reader provides the live boundary without adding a vendor
SDK dependency to the core package:

```python
import os

from gcl_oss.adapters.evalhub import EvalHubEvidenceSource, EvalHubHTTPClient
from gcl_oss.contracts import Scope

scope = Scope(tenant="team-a", namespace="models", environment="staging")
client = EvalHubHTTPClient(
    "https://evalhub.apps.example.com",
    tenant=scope.tenant,
    bearer_token=os.environ["EVALHUB_TOKEN"],
    ca_file="/etc/pki/ca-trust/source/anchors/cluster-ca.pem",
)
source = EvalHubEvidenceSource(client, ["job-id"], scope=scope)
evidence = [item async for item in source.receive()]
```

The client sends `X-Tenant`, optionally sends a bearer token, validates TLS with the
system or configured CA, rejects cross-origin redirects, limits response size, and
escapes the job ID. Plain HTTP requires an explicit development-only override.

The client authenticates the caller to EvalHub and validates the server transport. The
host remains responsible for workload identity, token acquisition and rotation, RBAC,
secret redaction, network policy, and retention.

The packaged CLI exposes the same boundary without accepting a token on the command
line:

```bash
gcl-oss evalhub-live \
  --base-url https://evalhub.example \
  --job-id JOB_ID \
  --tenant team-a \
  --namespace team-a \
  --environment staging \
  --model-version candidate-v7 \
  --token-file /var/run/secrets/kubernetes.io/serviceaccount/token \
  --ca-file /etc/evalhub-ca/service-ca.crt
```

`--token-file` is deliberately preferred to an environment variable or command-line
token. The live command fetches once, uses the current UTC clock for freshness checks,
and sends the resulting package only to the no-op proposal sink.

Require independent registry-byte verification by adding the registry boundary:

```bash
gcl-oss evalhub-live \
  --base-url https://evalhub.example \
  --job-id JOB_ID \
  --tenant team-a \
  --namespace team-a \
  --environment staging \
  --token-file /var/run/secrets/kubernetes.io/serviceaccount/token \
  --ca-file /etc/evalhub-ca/service-ca.crt \
  --verify-oci \
  --registry-allow registry.example \
  --registry-username serviceaccount \
  --registry-password-file /var/run/secrets/kubernetes.io/serviceaccount/token \
  --registry-ca-file /etc/registry-ca/ca.crt
```

Secrets are deliberately excluded from command-line values. `oci-verify` exposes the
same standalone verification boundary for a single digest-pinned reference.

## Failure behavior

The integration fails closed on:

- non-terminal state;
- missing resource identity, tenant, timestamp, model, or required results;
- tenant mismatch;
- invalid or mismatched OCI reference and digest;
- unapproved registries, manifest or descriptor byte mismatches, unsupported manifest
  graphs, unsafe redirects, authentication failures, or response-size violations when
  registry verification is enabled;
- non-finite or non-JSON source material;
- unsupported schema revision at the policy boundary;
- missing overall compliance test;
- missing OCI provenance under the default policy;
- stale evidence at the kernel boundary;
- transport, HTTP, response-size, or JSON errors.

No failure path calls a proposal consumer with an unsigned or incomplete package.

## Qualification status

On 2026-08-31, the adapter passed a local API-shape exercise, an isolated OpenShift
contract qualification, and a second OCI-content qualification on Oberon. The latter
used projected service-account tokens, service TLS, EvalHub's `X-Tenant` boundary, the
real terminal-job and event APIs, and digest-pinned GCL and fixture images. It fetched
and verified the OCI manifest, config, and layer before strict policy admission. The
own-tenant read succeeded, the same identity received HTTP 403 for a different tenant,
and the signed proposal path completed once with `execution_verified=false`.

The detailed [qualification record](qualification/oberon-evalhub-2026-08-31.md)
contains the exact image and source pins, observed decision-package digests,
compatibility findings, and exclusions. The repeatable cluster harness is documented
in [`deploy/oberon`](../deploy/oberon/README.md).

The separate [OCI-content qualification record](qualification/oberon-evalhub-oci-2026-08-31.md)
captures the verifier receipt, descriptor digests, stricter policy result, and updated
immutable pins.

## Remaining qualification

Before the EvalHub milestone is called complete:

1. add an upstream-owned fixture or conformance vector;
2. confirm the mapping with EvalHub maintainers.
