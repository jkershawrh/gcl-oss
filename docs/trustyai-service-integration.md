# TrustyAI Service integration

GCL OSS consumes explicit TrustyAI Service metric-compute responses as runtime
evidence. TrustyAI owns the data, metric implementation, and verdict. GCL validates
that response against the pinned source contract, binds it to the request and model,
and derives a proposal-only runtime-review constraint when the metric fails.

See the [TrustyAI runtime event workflow](diagrams/trustyai-runtime-event-workflow.md)
for the authenticated compute, normalization, policy, signing, and proposal sequence.

The implementation is pinned to TrustyAI Service source revision
`f78ca0e91cc24745fdaacb8f8ae893b059c03a0c`. Each evidence envelope links its exact
endpoint implementation at that revision rather than claiming compatibility with a
moving branch or every historical TrustyAI API.

## Implemented compute contracts

- `POST /metrics/drift/kstest` records the p-value and fails when `p_value < alpha`.
- `POST /metrics/drift/comparemeans` records the p-value and fails when
  `p_value < alpha`.
- `POST /metrics/drift/jensenshannon` records the requested distance or divergence
  statistic and fails when that statistic exceeds the threshold.
- `POST /metrics/drift/mmd` records the returned statistic and fails when it exceeds
  the threshold.
- `POST /metrics/group/fairness/spd` records statistical parity difference and fails
  when it lies outside the returned lower and upper bounds.
- `POST /metrics/group/fairness/dir` records disparate impact ratio and fails when it
  lies outside the returned lower and upper bounds.

The normalizer rejects an inconsistent upstream verdict. It does not reinterpret a
passing TrustyAI result as failing, flatten fairness ranges into one threshold, or
silently choose a different Jensen-Shannon statistic.

## Evidence and provenance

One successful compute exchange becomes one `EvidenceEnvelope`:

```text
authenticated request + TrustyAI response
  -> pinned response-shape validation
  -> request, response, and combined exchange digests
  -> model-scoped EvidenceEnvelope
  -> deterministic evidence policy
  -> hard runtime-review constraint when failed
  -> signed proposal delivered to a no-op sink
```

The envelope contains the model identity, metric kind and family, endpoint, pinned API
revision, normalized measurement, request digest, response digest, and combined
exchange digest. It intentionally does not copy feature names, protected-group
definitions, population data, or other raw request and response content into the
decision package.

This is authenticated response provenance, not immutable artifact provenance. The
exchange digest detects mutation inside the GCL record, while TLS and the caller's
bearer token protect transport. TrustyAI Service does not return a signed result,
immutable result identifier, or source timestamp at this revision, so GCL:

- leaves `assurance.artifact_uri` unset;
- labels the provenance mode `authenticated-compute-response`;
- timestamps the observation when the authenticated response is received;
- bounds validity to fifteen minutes by default; and
- never describes the exchange digest as a TrustyAI signature.

## Authority boundary

`TrustyAIServiceHTTPClient` exposes only the six compute endpoints. It has no methods
for `/data/upload`, schedule creation, schedule deletion, or `TrustyAIService` resource
changes. Demonstration data is seeded by a separate administrator-run qualification
script, not by the adapter or governance kernel.

The OpenShift reference deployment fronts TrustyAI Service with kube-rbac-proxy and
authorizes the qualifier through `get` on one named Service. That proxy authorization
is service-granular, not HTTP-method-granular; the application client is therefore the
enforced read-only boundary for GCL. A production host that needs infrastructure-level
method isolation should place an allowlisting gateway in front of TrustyAI Service.

The resulting GCL package is still only a proposal. The reference host uses a no-op
sink and requires `execution_verified=false`.

## Deployment relationship

No TrustyAI operator or custom-resource change is required for this adapter. The
integration point is the authenticated Service API, which keeps metric computation
and deployment lifecycle outside GCL.

Oberon does not currently provide the KServe `InferenceService` CRD. At the pinned
TrustyAI operator revision, enabling its TAS controller unconditionally starts an
`InferenceService` watch, so that controller cannot reconcile on Oberon without
KServe. The qualification therefore deploys the exact TrustyAI Service image directly
with the same service-certificate and kube-rbac-proxy pattern. It does not install a
placeholder KServe CRD or claim operator qualification.

That upstream deployment gap is independent of the GCL evidence contract. A useful
operator contribution would make the KServe watch discovery-based or optional when
no inference-service integration is requested.

## Integration choices

The direct compute path is the primary runtime integration because it preserves the
request parameters, response threshold, verdict, and exchange digest.

Scheduled TrustyAI metrics exposed through Prometheus can become a secondary
integration through GCL's future Prometheus adapter. That path is operationally useful
but may not preserve the full request/response contract, so it must use different
provenance and policy rules.

EvalHub remains the pre-deployment evidence source. A later joint policy can correlate
an EvalHub model-evaluation result with TrustyAI runtime drift or fairness evidence,
but only after model identity, version, tenant, and observation-window matching are
explicit. The current adapters remain independently reviewable and do not infer that
two similarly named models are the same deployment.

## Run locally

Replay the packaged, API-shaped drift fixture without network access:

```bash
gcl-oss trustyai-demo
```

Compute one metric against a live service:

```bash
gcl-oss trustyai-live \
  --base-url https://trustyai.example \
  --metric drift-kstest \
  --request request.json \
  --tenant team-a \
  --namespace models \
  --environment staging \
  --token-file /var/run/secrets/kubernetes.io/serviceaccount/token \
  --ca-file /etc/trustyai-ca/service-ca.crt
```

The client requires HTTPS by default, accepts credentials only from a file, rejects
cross-origin redirects and compressed responses, and bounds request and response
sizes.
