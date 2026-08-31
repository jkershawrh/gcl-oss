# Standalone demonstration

The offline demonstration proves the complete proposal-only path without Kubernetes, a model server, TrustyAI, or an execution system.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
gcl-oss demo
```

## Scenario

The built-in evidence envelope describes a model safety collection with a score of `0.62` against a required threshold of `0.90`.

```text
failed safety measurement
  -> tenant and freshness validation
  -> minimum-confidence policy
  -> evidence-derived hard review constraint
  -> deterministic, action-free risk-reduction objective
  -> deterministic alternatives
       selected: io.github.jkershawrh.gcl.governance/request_review
       rejected: io.github.jkershawrh.gcl.governance/hold
  -> evidence-freshness falsification check
  -> DecisionPackage
  -> ephemeral Ed25519 signature
  -> no-op ProposalSink
```

The no-op sink retains the package for inspection and reports `deferred`. It does not authorize or execute either candidate. Its receipt is structurally prohibited from setting `execution_verified=true`.

## Inspect

The JSON output includes:

- the cycle key used for idempotency;
- the selected and rejected candidates;
- accepted policy checks, constraints, objective, and evidence references;
- the signed producer, schema, artifact, and normalized-envelope evidence manifest;
- candidate cost values, weighted selection, constraint coverage, and falsification results;
- package digest, signature algorithm, and key ID;
- the proposal receipt;
- proof receipt identifiers;
- proposal delivery and proof-entry counts.

The demo key is generated in memory for each invocation. It is not a production key configuration.

## Schema export

Versioned Draft 2020-12 JSON Schemas are committed under `schemas/`. Regenerate them with:

```bash
gcl-oss schemas --output schemas
```

CI verifies that the committed files exactly match the Pydantic contract models.

## Retry behavior

Exact retries return the cached result without delivering a second proposal. Concurrent duplicate calls on one kernel instance and async event loop are serialized around the replay cache. A transport timeout becomes `delivery_unknown` and also remains cached, because retrying an ambiguous consequential request automatically could duplicate work at the consumer.
