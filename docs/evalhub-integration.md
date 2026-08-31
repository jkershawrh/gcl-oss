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

The adapter verifies that every OCI reference ends with its declared digest. It does
not pull or independently verify registry bytes. A production host that claims artifact
verification must add registry authentication and content verification at its trust
boundary.

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

## Failure behavior

The integration fails closed on:

- non-terminal state;
- missing resource identity, tenant, timestamp, model, or required results;
- tenant mismatch;
- invalid or mismatched OCI reference and digest;
- non-finite or non-JSON source material;
- unsupported schema revision at the policy boundary;
- missing overall compliance test;
- missing OCI provenance under the default policy;
- stale evidence at the kernel boundary;
- transport, HTTP, response-size, or JSON errors.

No failure path calls a proposal consumer with an unsigned or incomplete package.

## Qualification status

On 2026-08-31, the adapter was exercised against the pinned EvalHub server running in
local mode with its in-memory SQLite store. A tenant-scoped job was created through the
real API, a terminal failed threshold result with digest-pinned OCI metadata was
reported, and `EvalHubHTTPClient` fetched the resulting
`EvaluationJobResource`. The live response normalized into failed compliance evidence
and completed the signed proposal path with `execution_verified=false`.

This qualifies the current local API shape; it is not by itself OpenShift,
registry-content, or long-running compatibility qualification. The isolated OpenShift
contract harness is documented in
[`deploy/oberon`](../deploy/oberon/README.md).

## Remaining qualification

Before the EvalHub milestone is called complete:

1. complete and record the Oberon OAuth, projected-token, and `X-Tenant` RBAC run;
2. verify OCI content against the registry rather than only verifying reference shape;
3. add an upstream-owned fixture or conformance vector;
4. confirm the mapping with EvalHub maintainers.
