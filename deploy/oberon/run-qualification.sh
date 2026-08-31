#!/bin/sh
set -eu

TENANT_NAMESPACE="gcl-oss-evalhub"
CONTROL_NAMESPACE="gcl-oss-trustyai"
QUALIFICATION_JOB="gcl-oss-evalhub-qualification"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
GCL_IMAGE=${GCL_IMAGE:?Set GCL_IMAGE to a digest-pinned image reference}
TEMP_RESPONSE=$(mktemp /tmp/gcl-oss-wrong-tenant.XXXXXX)
trap 'rm -f "$TEMP_RESPONSE"' EXIT HUP INT TERM

command -v curl >/dev/null
command -v jq >/dev/null
command -v oc >/dev/null

JOB_ID=$($SCRIPT_DIR/seed-qualification.sh)
BASE_URL="https://$(oc get route evalhub -n "$CONTROL_NAMESPACE" -o jsonpath='{.spec.host}')"
SERVICE_ACCOUNT_TOKEN=$(oc create token gcl-oss-qualifier -n "$TENANT_NAMESPACE")

WRONG_TENANT_STATUS=$(curl --silent --show-error \
  --output "$TEMP_RESPONSE" \
  --write-out '%{http_code}' \
  --header "Authorization: Bearer $SERVICE_ACCOUNT_TOKEN" \
  --header "X-Tenant: $CONTROL_NAMESPACE" \
  "$BASE_URL/api/v1/evaluations/jobs/$JOB_ID")
test "$WRONG_TENANT_STATUS" = "403"

oc delete job "$QUALIFICATION_JOB" \
  -n "$TENANT_NAMESPACE" \
  --ignore-not-found \
  --wait=true >/dev/null
oc process -f "$SCRIPT_DIR/qualification-job-template.yaml" \
  -p "GCL_IMAGE=$GCL_IMAGE" \
  -p "EVALHUB_JOB_ID=$JOB_ID" \
  | oc apply -f -

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
  and .normalized_evidence.measurement.status == "failed"
  and .normalized_evidence.extensions["io.github.eval-hub/provenance-mode"] == "oci-manifest"
  and .proposal_receipt.execution_verified == false
  and .proposal_delivery_count == 1
' >/dev/null

oc delete job,configmap \
  -n "$TENANT_NAMESPACE" \
  -l "job_id=$JOB_ID" \
  --ignore-not-found >/dev/null

printf '%s\n' "$QUALIFICATION_OUTPUT"
