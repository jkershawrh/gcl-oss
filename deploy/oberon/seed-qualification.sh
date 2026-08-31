#!/bin/sh
set -eu

CONTROL_NAMESPACE="gcl-oss-trustyai"
TENANT_NAMESPACE="gcl-oss-evalhub"
ROUTE_NAME="evalhub"
OCI_DIGEST="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
OCI_REFERENCE="quay.io/jkershawrh/gcl-oss-evalhub-fixture@$OCI_DIGEST"

command -v curl >/dev/null
command -v jq >/dev/null
command -v oc >/dev/null

BASE_URL="https://$(oc get route "$ROUTE_NAME" -n "$CONTROL_NAMESPACE" -o jsonpath='{.spec.host}')"
TOKEN=$(oc whoami -t)
NOW=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

CREATE_RESPONSE=$(curl --fail-with-body --silent --show-error \
  --request POST \
  --header "Authorization: Bearer $TOKEN" \
  --header "X-Tenant: $TENANT_NAMESPACE" \
  --header 'Content-Type: application/json' \
  --data-binary @- \
  "$BASE_URL/api/v1/evaluations/jobs" <<'JSON'
{
  "name": "gcl-oss-oberon-contract-qualification",
  "description": "Synthetic failed threshold for proposal-only GCL qualification",
  "tags": ["gcl-oss", "contract-qualification"],
  "model": {
    "url": "http://127.0.0.1:9/v1",
    "name": "oberon-qualification-model"
  },
  "benchmarks": [
    {
      "id": "contract_probe",
      "provider_id": "gcl_contract_probe",
      "weight": 1.0,
      "primary_score": {
        "metric": "score",
        "lower_is_better": false
      },
      "pass_criteria": {
        "threshold": 0.9
      }
    }
  ],
  "pass_criteria": {
    "threshold": 0.9
  }
}
JSON
)

JOB_ID=$(printf '%s' "$CREATE_RESPONSE" | jq -er '.resource.id')

curl --fail-with-body --silent --show-error \
  --request POST \
  --header "Authorization: Bearer $TOKEN" \
  --header "X-Tenant: $TENANT_NAMESPACE" \
  --header 'Content-Type: application/json' \
  --data-binary @- \
  "$BASE_URL/api/v1/evaluations/jobs/$JOB_ID/events" >/dev/null <<JSON
{
  "benchmark_status_event": {
    "id": "contract_probe",
    "provider_id": "gcl_contract_probe",
    "benchmark_index": 0,
    "status": "completed",
    "metrics": {
      "score": 0.62
    },
    "artifacts": {
      "oci_reference": "$OCI_REFERENCE",
      "oci_digest": "$OCI_DIGEST"
    },
    "started_at": "$NOW",
    "completed_at": "$NOW"
  }
}
JSON

ATTEMPT=0
while [ "$ATTEMPT" -lt 20 ]; do
  STATE=$(curl --fail-with-body --silent --show-error \
    --header "Authorization: Bearer $TOKEN" \
    --header "X-Tenant: $TENANT_NAMESPACE" \
    "$BASE_URL/api/v1/evaluations/jobs/$JOB_ID" | jq -r '.status.state')
  if [ "$STATE" = "completed" ]; then
    printf '%s\n' "$JOB_ID"
    exit 0
  fi
  ATTEMPT=$((ATTEMPT + 1))
  sleep 1
done

echo "EvalHub qualification job did not become completed" >&2
exit 1
