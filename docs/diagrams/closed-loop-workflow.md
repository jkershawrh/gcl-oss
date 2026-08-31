# Closed-loop authority and outcome workflow

This reference workflow shows how GCL can participate in a governed loop without
becoming the authority or actuator. The GCL alpha implements the evidence-to-proposal
path and the `OutcomeSource` contract. A production proposal consumer, actuator, and
outcome adapter are host integrations, not current end-to-end claims.

```mermaid
sequenceDiagram
    autonumber
    participant Evidence as EvalHub, TrustyAI, or other EvidenceSource
    participant GCL as GCL OSS kernel
    participant Proof as ProofRecorder
    participant Authority as External proposal authority
    participant Target as Governed target or actuator
    participant Outcome as Independent OutcomeSource

    Evidence->>GCL: EvidenceEnvelope<br/>producer + scope + subject + freshness + provenance
    GCL->>Proof: Evidence, policy, constraint,<br/>objective, and falsification records
    GCL->>GCL: Build and sign expiry-bounded DecisionPackage
    GCL->>Authority: SignedDecisionPackage<br/>proposal only
    Authority-->>GCL: ProposalReceipt<br/>accepted, rejected, or deferred<br/>execution_verified=false
    GCL->>Proof: decision.proposed.v1alpha1 + ProposalReceipt

    alt Proposal is rejected or deferred
        Note over Authority,Target: No actuation is inferred. Review or retry policy belongs to the authority and host.
    else Proposal is admitted
        Authority->>Authority: Independently authenticate proposer,<br/>verify signature and expiry, apply authorization policy

        alt Authorization is denied
            Authority-->>Outcome: Optional independently observed disposition
        else Authorization succeeds
            Authority->>Target: Authorized command or desired state
            Target-->>Authority: Operation status<br/>not trusted by GCL as a proposal receipt
            Target-->>Outcome: Actual state and effect telemetry
            Outcome-->>GCL: OutcomeRecord<br/>package digest + observation time + measurements
            GCL->>Proof: outcome.observed.v1alpha1<br/>host integration
            GCL->>GCL: Correlate outcome with package<br/>new evidence may begin a later cycle
        end
    end
```

## Non-equivalence rule

These records answer different questions:

- `ProposalReceipt` says whether a consumer received or admitted a proposal.
- An authority's operation record says what it attempted after separate authorization.
- `OutcomeRecord` says what an independent source later observed.

None may be silently substituted for another, and only the independent observation
can support an execution or effect claim.
