# Security policy

## Supported versions

GCL OSS is pre-1.0. Security fixes apply to the latest release and the default branch.

## Reporting

Use GitHub private vulnerability reporting at:

`https://github.com/jkershawrh/gcl-oss/security/advisories/new`

Do not disclose suspected vulnerabilities in a public issue.

## Security invariants

- GCL OSS emits proposals, not authorization grants or actuator commands.
- Proposal acknowledgement cannot prove execution.
- Package verification includes digest, signature, key identity, scope, and expiry.
- The signed package binds policy results, constraints, objective, candidates, alternatives, and falsification results.
- Model-assisted output cannot choose the committed action or bypass hard constraints, and every such boundary requires deterministic fallback.
- Evidence remains untrusted until producer, schema, scope, freshness, and provenance are validated.
- Optional integrations cannot silently widen authority when unavailable.
- Production key material comes from external key or secret management.

Violations of these invariants are security issues even when they do not enable conventional code execution.
