# EvalHub pre-deployment event workflow

This sequence shows the implemented failed-safety path. OCI byte verification is an
optional strict host mode; digest-pinned OCI provenance is required by the default
EvalHub policy even when the host does not fetch the bytes.

```mermaid
sequenceDiagram
    autonumber
    actor Host as GCL host or qualification Job
    participant EH as EvalHub API v1
    participant EA as EvalHub adapter
    participant OCI as OCI registry
    participant Policy as EvalHub policy pack
    participant Kernel as GovernanceKernel
    participant Proof as ProofRecorder
    participant Signer as Ed25519 Signer
    participant Sink as ProposalSink

    Host->>EH: GET /api/v1/evaluations/jobs/{id}<br/>Bearer token + X-Tenant
    EH-->>Host: EvaluationJobResource
    Host->>EA: Normalize response with expected scope and model version
    EA->>EA: Validate pinned fields, tenant, terminal state,<br/>timestamps, explicit test result, OCI references

    alt Transport, tenant, schema, state, or provenance is invalid
        EA-->>Host: Reject evidence
        Note over Host,Sink: Fail closed. No kernel cycle and no proposal delivery.
    else Terminal result is valid
        opt Strict artifact verification enabled
            EA->>OCI: GET digest-pinned manifest and descriptors<br/>exact registry allowlist + pull-only credentials
            OCI-->>EA: Manifest, config, and layer bytes
            EA->>EA: Recompute digests and bind ArtifactVerificationReceipt
        end

        EA-->>Kernel: EvidenceEnvelope<br/>authenticated result digest + optional verified artifacts
        Kernel->>Kernel: Validate scope, freshness, correlation, and replay key
        Kernel->>Proof: evidence.accepted.v1alpha1
        Kernel->>Policy: Evaluate producer, API revision,<br/>confidence, result semantics, and OCI provenance
        Policy-->>Kernel: PolicyResult
        Kernel->>Proof: policy.evaluated.v1alpha1

        alt Policy denies the evidence
            Kernel->>Proof: decision.rejected.v1alpha1
            Kernel-->>Host: rejected, no signed package
        else Failed safety collection is admitted
            Kernel->>Kernel: Derive hard promotion-review constraint
            Kernel->>Proof: constraint.classified.v1alpha1
            Kernel->>Kernel: Frame action-free objective<br/>and request_review / hold alternatives
            Kernel->>Proof: objective.interpreted.v1alpha1
            Kernel->>Kernel: Select minimum-cost candidate<br/>and run required falsification checks
            Kernel->>Proof: falsification.completed.v1alpha1

            alt Candidate fails falsification
                Kernel->>Proof: decision.rejected.v1alpha1
                Kernel-->>Host: rejected, no proposal delivery
            else Candidate survives
                Kernel->>Signer: Canonical DecisionPackage bytes
                Signer-->>Kernel: Ed25519 signature + key identity
                Kernel->>Sink: SignedDecisionPackage
                Sink-->>Kernel: ProposalReceipt<br/>accepted, rejected, or deferred<br/>execution_verified=false
                Kernel->>Proof: decision.proposed.v1alpha1
                Kernel-->>Host: proposed + signed package + receipts
            end
        end
    end
```

## Contract boundary

EvalHub owns job execution, benchmark results, collection semantics, and result
artifacts. GCL's adapter owns only transport and normalization. The policy pack owns
the mapping from admitted evidence to a promotion-review constraint, and the external
proposal consumer owns every authorization or deployment decision.
