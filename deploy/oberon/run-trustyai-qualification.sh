#!/bin/sh
set -eu

TENANT_NAMESPACE="gcl-oss-evalhub"
QUALIFICATION_JOB="gcl-oss-trustyai-qualification"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
GCL_IMAGE=${GCL_IMAGE:?Set GCL_IMAGE to a digest-pinned image reference}

case "$GCL_IMAGE" in
  *@sha256:*) ;;
  *)
    echo "GCL_IMAGE must be pinned with a sha256 digest" >&2
    exit 1
    ;;
esac

command -v jq >/dev/null
command -v oc >/dev/null

"$SCRIPT_DIR/seed-trustyai-qualification.sh" >/dev/null

oc delete job "$QUALIFICATION_JOB" \
  -n "$TENANT_NAMESPACE" \
  --ignore-not-found \
  --wait=true >/dev/null
oc process -f "$SCRIPT_DIR/trustyai-qualification-job-template.yaml" \
  -p "GCL_IMAGE=$GCL_IMAGE" \
  | oc apply -n "$TENANT_NAMESPACE" -f -

if ! oc wait job/$QUALIFICATION_JOB \
  -n "$TENANT_NAMESPACE" \
  --for=condition=complete \
  --timeout=180s; then
  oc logs job/$QUALIFICATION_JOB -n "$TENANT_NAMESPACE" || true
  exit 1
fi

QUALIFICATION_OUTPUT=$(oc logs job/$QUALIFICATION_JOB -n "$TENANT_NAMESPACE")
printf '%s\n' "$QUALIFICATION_OUTPUT" | jq -e '
  .status == "proposed"
  and .normalized_evidence.scope.tenant == "gcl-oss-evalhub"
  and .normalized_evidence.scope.namespace == "gcl-oss-evalhub"
  and .normalized_evidence.subject.id == "gcl-oss-oberon-drift-20260831"
  and .normalized_evidence.measurement.name == "trustyai.drift.kstest.p_value"
  and .normalized_evidence.measurement.status == "failed"
  and .normalized_evidence.measurement.value < .normalized_evidence.measurement.threshold
  and .normalized_evidence.extensions["org.trustyai.service/api-revision"] == "f78ca0e91cc24745fdaacb8f8ae893b059c03a0c"
  and .normalized_evidence.extensions["org.trustyai.service/provenance-mode"] == "authenticated-compute-response"
  and (.normalized_evidence.extensions["org.trustyai.service/request-digest"] | test("^sha256:[0-9a-f]{64}$"))
  and (.normalized_evidence.extensions["org.trustyai.service/response-digest"] | test("^sha256:[0-9a-f]{64}$"))
  and any(
    .signed_package.package.policy_results[];
    .check_id == "trustyai-service-evidence-v1alpha1"
    and .allowed == true
  )
  and any(
    .signed_package.package.constraints[];
    .name == "io.github.jkershawrh.gcl.trustyai/runtime-review-required"
    and .hard == true
    and .expression.effect == "require-runtime-review"
  )
  and .proposal_receipt.execution_verified == false
  and .proposal_delivery_count == 1
' >/dev/null

printf '%s\n' "$QUALIFICATION_OUTPUT"
