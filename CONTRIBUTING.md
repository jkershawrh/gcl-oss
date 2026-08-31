# Contributing

Read [the architecture](docs/architecture.md), [ADR 0001](docs/adr/0001-proposal-only-kernel.md), and [the security policy](SECURITY.md) before changing a contract or integration boundary.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests
```

## Workflow

1. Open an issue for an integration, contract, or architectural change.
2. Add contract tests before changing cross-system behavior.
3. Keep vendor SDKs and deployment assumptions outside the core.
4. Document identity, scope, freshness, idempotency, provenance, and failure behavior.
5. Provide external-service-free fixtures for adapters.
6. Preserve the proposal-only authority boundary.

By submitting a contribution, you agree that it is licensed under Apache License 2.0.
