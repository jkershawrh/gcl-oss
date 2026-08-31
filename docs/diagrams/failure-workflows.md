# Failure and recovery workflow

This diagram follows the implemented kernel ordering. Every gate before proposal
delivery fails closed. Ambiguous delivery is cached and requires reconciliation rather
than automatic retry.

```mermaid
flowchart TD
    START["Evidence cycle received"] --> KEY["Derive scope-bound cycle key"]
    KEY --> REPLAY{"Cached result exists?"}
    REPLAY -->|"yes"| CACHED["Return cached result<br/>replayed=true<br/>do not redeliver"]
    REPLAY -->|"no"| VALIDATE{"Scope, identity, freshness,<br/>correlation, and uniqueness valid?"}

    VALIDATE -->|"no"| EREJECT["Record evidence.rejected<br/>then decision.rejected"]
    EREJECT --> REJECTED["Cache rejected result<br/>no signed package; no delivery"]
    VALIDATE -->|"yes"| EPROOF{"Record evidence.accepted succeeds?"}
    EPROOF -->|"no"| PREFAIL["Proof failure before delivery<br/>abort; no proposal"]
    EPROOF -->|"yes"| POLICY{"All deterministic policies allow?"}

    POLICY -->|"no"| DREJECT["Record decision.rejected"]
    DREJECT --> REJECTED
    POLICY -->|"yes"| CONSTRAINTS{"At least one evidence-bound<br/>constraint produced?"}
    CONSTRAINTS -->|"no"| NOCAND["Cache no_candidate<br/>record decision.rejected"]
    CONSTRAINTS -->|"yes"| PLAN["Interpret action-free objective<br/>and build deterministic candidates"]

    PLAN --> INVARIANTS{"References, action schema, costs,<br/>minimum selection, and hard-constraint<br/>coverage are valid?"}
    INVARIANTS -->|"no"| CONTRACTERR["Contract or host configuration error<br/>raise; no delivery"]
    INVARIANTS -->|"yes"| FALSIFY{"Required deterministic<br/>falsification checks survive?"}
    FALSIFY -->|"no"| DREJECT
    FALSIFY -->|"yes"| SIGN{"Canonical package signing succeeds?"}
    SIGN -->|"no"| SIGNFAIL["Signing failure<br/>no proposal delivery"]
    SIGN -->|"yes"| DELIVER["Offer SignedDecisionPackage<br/>to ProposalSink"]

    DELIVER --> OUTCOME{"Sink returns a matching receipt?"}
    OUTCOME -->|"exception or digest mismatch"| UNKNOWN["Cache delivery_unknown<br/>record decision.delivery_unknown best effort"]
    UNKNOWN --> MANUAL["No automatic redelivery<br/>operator reconciles consumer first"]

    OUTCOME -->|"matching accepted, rejected, or deferred receipt"| CACHEOK["Cache proposed result before final proof<br/>receipt execution_verified=false"]
    CACHEOK --> FINALPROOF{"Record decision.proposed succeeds?"}
    FINALPROOF -->|"yes"| COMPLETE["Return proposed result<br/>with proof receipts"]
    FINALPROOF -->|"no"| POSTFAIL["Return cached delivery result<br/>report proof failure; do not redeliver"]

    classDef success fill:#e8f5e9,stroke:#2e7d32,color:#102a13,stroke-width:2px;
    classDef stop fill:#fce8e6,stroke:#a33a2b,color:#35100b,stroke-width:2px;
    classDef uncertain fill:#fff3e0,stroke:#a65f00,color:#321d00,stroke-width:2px;
    classDef process fill:#e8eef9,stroke:#315a9b,color:#10213d,stroke-width:1px;

    class CACHED,COMPLETE success;
    class EREJECT,REJECTED,PREFAIL,DREJECT,NOCAND,CONTRACTERR,SIGNFAIL stop;
    class UNKNOWN,MANUAL,POSTFAIL uncertain;
    class START,KEY,PLAN,DELIVER,CACHEOK process;
```

## Recovery rules

1. Exact retries return the cached result and never redeliver automatically.
2. A sink exception or digest-mismatched receipt is `delivery_unknown`; the consumer
   must be reconciled before an operator deliberately clears or supersedes the result.
3. Successful delivery is cached before the final proof write, so a proof-store outage
   after acknowledgement cannot cause duplicate consequential work.
4. The built-in cache and lock are process-local. Multi-process or multi-replica hosts
   need durable distributed idempotency before claiming the same behavior.
