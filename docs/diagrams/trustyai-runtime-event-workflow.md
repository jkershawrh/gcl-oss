# TrustyAI runtime event workflow

This sequence shows the implemented runtime drift and fairness path. GCL calls only six
pinned compute endpoints. Data upload, schedules, and TrustyAIService resource changes
remain administrator or TrustyAI responsibilities.

```mermaid
sequenceDiagram
    autonumber
    actor Admin as Data or platform administrator
    actor Host as GCL host or qualification Job
    participant Proxy as Authenticated service boundary
    participant TAS as TrustyAI Service
    participant Adapter as TrustyAI Service adapter
    participant Policy as Runtime evidence policy pack
    participant Kernel as GovernanceKernel
    participant Proof as ProofRecorder
    participant Signer as Ed25519 Signer
    participant Sink as ProposalSink

    Admin->>TAS: Upload or maintain baseline and current data<br/>outside the GCL authority boundary
    Host->>Proxy: POST one pinned drift or fairness compute request<br/>Bearer token + trusted TLS
    Proxy->>Proxy: TokenReview and authorization

    alt Authentication or authorization fails
        Proxy-->>Host: 401 or 403
        Note over Host,Sink: No usable evidence and no proposal delivery.
    else Request is authorized
        Proxy->>TAS: Forward compute request
        TAS-->>Proxy: Metric value, threshold or range, and verdict
        Proxy-->>Host: Authenticated compute response
        Host->>Adapter: Request + response + model identity + scope
        Adapter->>Adapter: Validate pinned response shape and<br/>recompute verdict consistency

        alt Response is malformed or verdict is inconsistent
            Adapter-->>Host: Reject response
            Note over Host,Sink: Fail closed. GCL never repairs or reverses a TrustyAI verdict.
        else Response is valid
            Adapter->>Adapter: Hash request, response, and combined exchange<br/>set authenticated-compute-response provenance<br/>bound default validity to 15 minutes
            Adapter-->>Kernel: Model-scoped EvidenceEnvelope
            Kernel->>Kernel: Validate scope, freshness, correlation, and replay key
            Kernel->>Proof: evidence.accepted.v1alpha1
            Kernel->>Policy: Evaluate producer, pinned API revision,<br/>digest, confidence, and metric status
            Policy-->>Kernel: PolicyResult
            Kernel->>Proof: policy.evaluated.v1alpha1

            alt Evidence policy denies the response
                Kernel->>Proof: decision.rejected.v1alpha1
                Kernel-->>Host: rejected, no signed package
            else Failed drift or fairness result is admitted
                Kernel->>Kernel: Derive hard runtime-review constraint
                Kernel->>Proof: constraint.classified.v1alpha1
                Kernel->>Kernel: Frame objective, alternatives,<br/>minimum-cost selection, and falsification
                Kernel->>Proof: objective.interpreted.v1alpha1
                Kernel->>Proof: falsification.completed.v1alpha1

                alt Candidate fails falsification
                    Kernel->>Proof: decision.rejected.v1alpha1
                    Kernel-->>Host: rejected, no proposal delivery
                else Candidate survives
                    Kernel->>Signer: Canonical DecisionPackage bytes
                    Signer-->>Kernel: Ed25519 signature + key identity
                    Kernel->>Sink: SignedDecisionPackage
                    Sink-->>Kernel: ProposalReceipt<br/>execution_verified=false
                    Kernel->>Proof: decision.proposed.v1alpha1
                    Kernel-->>Host: proposed + signed package + receipts
                end
            end
        end
    end
```

## Provenance limit

The exchange digest detects mutation of the request and response retained by GCL. It
is not a TrustyAI signature or immutable result identifier. Until TrustyAI exposes a
source timestamp or immutable result reference, the adapter labels the provenance
honestly and keeps the evidence window short.
