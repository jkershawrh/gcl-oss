# Oberon deployment and trust boundaries

This diagram documents the isolated qualification topology used on Oberon. It is not a
supported Red Hat deployment or a production GCL topology. All runtime proposals end
at a no-op sink with `execution_verified=false`.

```mermaid
flowchart TB
    ADMIN["Qualification administrator<br/>installs pinned manifests and seeds synthetic data"]

    subgraph OBERON["Oberon OpenShift cluster"]
        API["OpenShift API<br/>TokenReview, SubjectAccessReview, RBAC"]
        REG["Internal image registry<br/>digest-pinned GCL and fixture images"]
        KSERVE["KServe InferenceService CRD absent"]

        subgraph CONTROL["Namespace: gcl-oss-trustyai"]
            OP["Pinned TrustyAI Operator<br/>EVALHUB controller only"]
            CR["EvalHub custom resource"]
            EH["EvalHub service<br/>multi-tenant API"]
            PROVIDER["Synthetic contract-probe provider"]
            IMG["Pinned TrustyAI Service image<br/>stored in namespace registry"]
        end

        subgraph TENANT["Namespace: gcl-oss-evalhub"]
            SA["ServiceAccount: gcl-oss-qualifier<br/>projected short-lived token"]
            EJOB["EvalHub qualification Job<br/>GCL OSS image"]
            TJOB["TrustyAI qualification Job<br/>GCL OSS image"]
            SEED["Administrator-run seed Jobs"]
            RBAC["Namespace RBAC<br/>EvalHub read + one named Service get"]

            subgraph TASPOD["Standalone TrustyAI Service Deployment"]
                PROXY["kube-rbac-proxy<br/>service TLS + auth delegation"]
                APP["TrustyAI Service<br/>loopback compute API"]
                PVC["PVC on ocpv-tenants"]
                PROXY -->|"allowlisted upstream"| APP
                APP --> PVC
            end

            NOOP["No-op ProposalSink<br/>deferred, execution_verified=false"]
        end
    end

    ADMIN --> OP
    ADMIN --> SEED
    OP -->|"reconciles"| CR
    CR --> EH
    PROVIDER --> EH
    SA --> EJOB
    SA --> TJOB
    RBAC --> SA

    EJOB -->|"HTTPS GET terminal job<br/>Bearer token + X-Tenant"| EH
    EJOB -->|"pull-only OCI verification"| REG
    TJOB -->|"HTTPS POST metric compute<br/>Bearer token"| PROXY
    PROXY -->|"TokenReview and SubjectAccessReview"| API
    OP --> API
    SEED -->|"data upload outside GCL"| APP
    IMG --> APP

    EJOB --> NOOP
    TJOB --> NOOP
    KSERVE -.->|"TAS controller deliberately disabled<br/>no placeholder CRD installed"| OP

    classDef qualified fill:#e8f5e9,stroke:#2e7d32,color:#102a13,stroke-width:2px;
    classDef cluster fill:#e8eef9,stroke:#315a9b,color:#10213d,stroke-width:2px;
    classDef boundary fill:#fff3e0,stroke:#a65f00,color:#321d00,stroke-width:2px;
    classDef limitation fill:#fce8e6,stroke:#a33a2b,color:#35100b,stroke-width:2px,stroke-dasharray:5 5;

    class OP,CR,EH,PROVIDER,SA,EJOB,TJOB,SEED,RBAC,PROXY,APP,PVC,NOOP qualified;
    class API,REG,IMG cluster;
    class ADMIN boundary;
    class KSERVE limitation;
```

## What the topology qualifies

The EvalHub path qualifies tenant-scoped authenticated reads, normalization,
digest-pinned OCI manifest and descriptor verification, policy, signing, and no-op
proposal delivery. The TrustyAI path qualifies authenticated compute, pinned response
normalization, runtime policy, signing, and no-op proposal delivery. It does not
qualify model execution, a production authority, or operator-managed TrustyAI Service
on a KServe-enabled cluster.
