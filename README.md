# GCL OSS

GCL OSS is a proposal-only governance kernel for systems that make consequential operational decisions from machine-generated evidence.

It defines portable contracts and extension ports for turning observations, evaluations, and forecasts into constrained, falsified, signed, and expiry-bounded proposals. A separate authority decides whether to authorize and execute them.

This is a clean, vendor-neutral open source project. It does not copy the implementation or Git history of the [Governed Cognitive Loop production proof layer](https://github.com/jkershawrh/governed-cognitive-loop).

## Boundary

```text
evidence producers             GCL OSS                  external authorities

TrustyAI / EvalHub ─┐     validate provenance          human review
Prometheus / OTEL   ├───> apply policy             ───> workflow engine
CloudEvents         │     generate alternatives        admission service
domain systems      ┘     falsify, sign, expire        control plane
                                |
                                v
                        proof and telemetry sinks
```

GCL OSS owns the decision record. It does not own source measurements, external authorization, infrastructure actuation, rollback, or observed outcomes.

## What exists today

The `0.1.0a1` foundation contains:

- a versioned `EvidenceEnvelope` with producer, subject, tenant scope, freshness, confidence, digest, and namespaced extensions;
- generalized, namespaced action candidates;
- a coherent `DecisionPackage` that requires evidence-bound candidates and a surviving falsification result;
- Ed25519 signatures with key identity, digest binding, and expiry verification;
- proposal receipts that cannot claim execution verification;
- structural Python protocols for evidence, policy, planning, falsification, proposals, proof, outcomes, keys, and telemetry;
- an architecture and integration plan covering several ecosystems.

It is an architecture foundation, not a production release. The orchestration kernel and external adapters are roadmap work.

## Install for development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests
```

## Design rules

- Evidence is untrusted until producer, schema, scope, freshness, and provenance are validated.
- An optional LLM may frame an objective, but it cannot choose the committed action.
- A deterministic planner owns candidate generation and hard constraints.
- The selected candidate must survive registered falsification checks.
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
- [ADR 0001: proposal-only kernel](docs/adr/0001-proposal-only-kernel.md)
- [Roadmap](ROADMAP.md)
- [Governance](GOVERNANCE.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)

## License

Licensed under the [Apache License 2.0](LICENSE).
