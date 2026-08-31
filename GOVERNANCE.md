# Governance

GCL OSS is maintained in the open under `jkershawrh`.

## Roles

- Users run or integrate GCL OSS.
- Contributors submit issues, designs, tests, documentation, or code.
- Maintainers review changes, manage releases, and protect architectural boundaries.

The repository owner is the initial maintainer. Maintainer membership will expand through sustained, constructive contribution.

## Decisions

While the project has one active maintainer, validated routine changes may be committed
directly to `main` after local checks and required hosted CI. Personal pull requests are
not used as a substitute for independent review. Once another active maintainer or an
upstream/downstream reviewer participates, reviewable changes use GitHub pull requests.

Changes to stable contracts, signing, tenancy, authority boundaries, deterministic
action ownership, extension interfaces, licensing, or governance require a public
design issue or ADR regardless of the delivery workflow.

Maintainers document the decision and meaningful dissent. When consensus cannot be reached, maintainers record the final rationale publicly.

## Protected boundary

GCL OSS emits proposals. It does not grant execution authority and it does not prove execution from a proposal response. Changes that blur these meanings require explicit rejection or a superseding ADR.

## Releases

Releases use semantic versioning. Alpha contracts may change between minor versions. Stable versioned contracts are never changed in place.
