# GCL OSS

GCL OSS is a proposal-only governance kernel for systems that make consequential operational decisions from machine-generated evidence.

It defines portable contracts and extension ports for turning observations, evaluations, and forecasts into constrained, falsified, signed, and expiry-bounded proposals. A separate authority decides whether to authorize and execute them.

This is a clean, vendor-neutral open source project. It does not copy the implementation or Git history of the [Governed Cognitive Loop production proof layer](https://github.com/jkershawrh/governed-cognitive-loop).

## Boundary

```text
evidence producers             GCL OSS                  external authorities

TrustyAI / EvalHub ─┐     validate envelope            human review
Prometheus / OTEL   ├───> derive constraints       ───> workflow engine
CloudEvents         │     frame objective              admission service
domain systems      ┘     plan, falsify, sign           control plane
                                |
                                v
                        proof and telemetry sinks
```

GCL OSS owns the decision record. It does not own source measurements, external authorization, infrastructure actuation, rollback, or observed outcomes.

## What exists today

The tagged `0.2.0a1` standalone alpha contains the vendor-neutral kernel. The
`0.3.0a1` development line adds the first external evidence integration:

- a versioned `EvidenceEnvelope` with producer, subject, tenant scope, freshness, confidence, digest, and namespaced extensions;
- a signed evidence manifest carrying producer, schema, artifact digest and URI, and canonical envelope digest without copying full upstream payloads;
- generalized, namespaced action candidates;
- evidence-derived hard and soft constraints with explicit source, confidence, expression, and digest references;
- an action-free `ObjectiveSpec` with interpreter and model-assistance provenance;
- a coherent `DecisionPackage` that cryptographically binds policy results, constraints, objective, candidate cost values, rejected alternatives, and falsification results;
- Ed25519 signatures with key identity, digest binding, and expiry verification;
- proposal receipts that cannot claim execution verification;
- structural Python protocols for evidence, artifact verification, policy, constraint
  classification, objective interpretation, planning, falsification, proposals, proof,
  outcomes, signing, and telemetry;
- a deterministic orchestration kernel with scope, freshness, policy, constraint, objective, registry, falsification, signing, and proposal gates;
- process-local replay handling that prevents duplicate delivery from concurrent retries on one async event loop;
- explicit `delivery_unknown` results that stop automatic redelivery when a consumer times out or returns a mismatched receipt;
- registered action schemas and required falsification checks;
- in-memory evidence and proof adapters, a demo-only signer, and a no-op proposal sink;
- committed Draft 2020-12 JSON Schemas and a golden EvalHub-style evidence fixture;
- an offline demonstration that produces a complete signed proposal without a cluster or external service;
- an EvalHub API v1 terminal-job normalizer pinned to an upstream contract revision;
- an authenticated, tenant-scoped, TLS-validating EvalHub job reader with no vendor SDK dependency;
- a live EvalHub CLI path for projected service-account tokens and private cluster CAs;
- a generic artifact-verification port, a bounded OCI Distribution verifier, and
  receipt-aware EvalHub policy;
- a TrustyAI Service adapter for six pinned drift and group-fairness compute
  contracts, with a bounded authenticated client and raw-data-minimizing provenance;
- a strict TrustyAI evidence policy and deterministic runtime-review constraint pack;
- offline and live TrustyAI CLI paths that remain proposal-only;
- a packaged failed-safety EvalHub fixture and signed end-to-end demonstration;
- an architecture and integration plan covering several ecosystems.

It is an alpha reference kernel, not a production release. The EvalHub adapter has
local API and isolated OpenShift contract qualification; its Oberon assets and
[qualification record](docs/qualification/oberon-evalhub-2026-08-31.md) are committed.
The TrustyAI Service adapter has local contract coverage and an isolated Oberon
qualification path. Durable distributed idempotency remains roadmap work.

## Install for development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests
```

Run the complete offline cycle:

```bash
gcl-oss demo
```

The demo converts a failed safety evaluation into an evidence-bound hard constraint and deterministic objective, generates `request_review` and `hold` alternatives, selects the less disruptive review proposal, runs its required freshness falsification check, signs the complete reasoning chain with an ephemeral Ed25519 key, and delivers it once to a no-op sink. The receipt always reports `execution_verified=false`.

Run the EvalHub contract demonstration:

```bash
gcl-oss evalhub-demo
```

This consumes a packaged API-v1-shaped terminal job response, validates its
digest-pinned OCI provenance declarations without registry access, derives a
promotion-review constraint, and produces the same proposal-only signed chain. See
the [EvalHub integration](docs/evalhub-integration.md).

Fetch one terminal job from a live EvalHub deployment:

```bash
gcl-oss evalhub-live \
  --base-url https://evalhub.example \
  --job-id JOB_ID \
  --tenant team-a \
  --namespace team-a \
  --environment staging \
  --token-file /var/run/secrets/kubernetes.io/serviceaccount/token \
  --ca-file /etc/evalhub-ca/service-ca.crt
```

The live command uses the current clock and therefore rejects stale evidence. It still
uses the no-op proposal sink and cannot claim execution.

Add `--verify-oci` and an exact `--registry-allow HOST[:PORT]` to fetch and verify the
manifest plus every descriptor before strict policy evaluation. Registry credentials
come from `--registry-auth-file` or the file-based username/password options; secret
values are never accepted directly on the command line.

Run the offline TrustyAI Service contract demonstration:

```bash
gcl-oss trustyai-demo
```

Or compute one metric through an authenticated live TrustyAI Service and produce a
signed, no-op proposal:

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

See the [TrustyAI Service integration](docs/trustyai-service-integration.md) for the
supported contracts, provenance limits, and deployment boundary.

Regenerate the committed contract schemas:

```bash
gcl-oss schemas --output schemas
```

## Design rules

- Evidence is untrusted until the host and policy layer validate producer identity, schema, scope, freshness, digest, and provenance.
- Every constraint and objective specification is tied to evidence in the current cycle.
- An optional model-assisted classifier or interpreter must expose the mode used and provide a deterministic fallback.
- A deterministic planner owns candidate generation and selection under hard constraints.
- The selected candidate must cover every hard constraint.
- Objective weights sum to one, every candidate supplies a normalized value for every signed cost term, and the selected candidate must minimize the weighted objective.
- The selected candidate must survive registered deterministic falsification checks.
- A proposal can never outlive the evidence used to create it.
- A signed package is a proposal, never an execution grant.
- Delivery or admission acknowledgement never proves execution.
- Producer-specific data remains behind adapters and namespaced extensions.
- The core must run without Kubernetes, a model server, or any vendor service.

## First integrations

The first target integrations are:

1. TrustyAI Service drift and fairness results.
2. EvalHub completed evaluations and collection compliance.
3. Prometheus metrics and Alertmanager transitions.
4. Generic CloudEvents evidence ingress and proposal egress.
5. OpenTelemetry correlation.
6. OPA policy checks.
7. Pluggable proof recorders and human or machine proposal consumers.

See [integration points](docs/integrations.md) for the boundary of each adapter.

## Documentation

- [Architecture](docs/architecture.md)
- [Integration points](docs/integrations.md)
- [Standalone demo](docs/standalone-demo.md)
- [EvalHub integration](docs/evalhub-integration.md)
- [TrustyAI Service integration](docs/trustyai-service-integration.md)
- [Oberon EvalHub qualification](deploy/oberon/README.md)
- [Oberon qualification record](docs/qualification/oberon-evalhub-2026-08-31.md)
- [Oberon OCI-content qualification](docs/qualification/oberon-evalhub-oci-2026-08-31.md)
- [TrustyAI and EvalHub engagement brief](docs/trustyai-engagement.md)
- [ADR 0001: proposal-only kernel](docs/adr/0001-proposal-only-kernel.md)
- [Roadmap](ROADMAP.md)
- [Governance](GOVERNANCE.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)

## License

Licensed under the [Apache License 2.0](LICENSE).
