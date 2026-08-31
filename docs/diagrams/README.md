# Architecture and event workflow diagrams

This directory is the visual architecture set for GCL OSS and its TrustyAI-family
integration points. The diagrams distinguish implemented alpha behavior from external
authority and planned host integrations so they can be used in upstream design review
without implying product adoption or execution authority.

## Diagram set

1. [TrustyAI ecosystem architecture](ecosystem-architecture.md) shows where EvalHub,
   TrustyAI Service, Guardrails, GCL OSS, external authorities, proof, telemetry, and
   independent outcomes meet.
2. [EvalHub pre-deployment event workflow](evalhub-event-workflow.md) traces a terminal
   evaluation through OCI verification and a signed promotion-review proposal.
3. [TrustyAI runtime event workflow](trustyai-runtime-event-workflow.md) traces an
   authenticated drift or fairness computation through a runtime-review proposal.
4. [Closed-loop authority and outcome workflow](closed-loop-workflow.md) separates
   proposal admission, authorization, actuation, and independently observed outcomes.
5. [Oberon deployment and trust boundaries](oberon-deployment.md) documents the
   isolated qualification topology that exists today.
6. [Failure and recovery workflow](failure-workflows.md) shows every fail-closed gate,
   ambiguous delivery handling, replay behavior, and the final-proof edge case.

## Status convention

- **Implemented** means the behavior exists in the GCL OSS alpha or has been exercised
  by the committed Oberon qualification.
- **External** means the component is deliberately outside GCL authority.
- **Planned** means a port or integration direction exists, but no production adapter
  or end-to-end authority integration is claimed.

The diagrams are architectural records, not evidence that TrustyAI or Red Hat has
endorsed GCL OSS.
