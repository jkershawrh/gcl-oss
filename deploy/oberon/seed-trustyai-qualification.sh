#!/bin/sh
set -eu

TENANT_NAMESPACE="gcl-oss-evalhub"
ROUTE_NAME="gcl-oss-trustyai-service"
MODEL_ID="gcl-oss-oberon-drift-20260831"
BASELINE_TAG="gcl-oss-baseline-20260831"

command -v curl >/dev/null
command -v jq >/dev/null
command -v oc >/dev/null

BASE_URL="https://$(oc get route "$ROUTE_NAME" -n "$TENANT_NAMESPACE" -o jsonpath='{.spec.host}')"
TOKEN=$(oc whoami -t)

REFERENCE_PAYLOAD=$(jq -nc \
  --arg model "$MODEL_ID" \
  --arg tag "$BASELINE_TAG" \
  '{
    model_name: $model,
    data_tag: $tag,
    is_ground_truth: false,
    request: {
      inputs: [{
        name: "transaction_amount",
        shape: [100],
        datatype: "FP64",
        data: [range(0; 100)]
      }]
    },
    response: {
      model_name: $model,
      outputs: [{
        name: "fraud_score",
        shape: [100],
        datatype: "FP64",
        data: [range(0; 100) | 0]
      }]
    }
  }')

CURRENT_PAYLOAD=$(jq -nc \
  --arg model "$MODEL_ID" \
  '{
    model_name: $model,
    is_ground_truth: false,
    request: {
      inputs: [{
        name: "transaction_amount",
        shape: [100],
        datatype: "FP64",
        data: [range(1000; 1100)]
      }]
    },
    response: {
      model_name: $model,
      outputs: [{
        name: "fraud_score",
        shape: [100],
        datatype: "FP64",
        data: [range(0; 100) | 1]
      }]
    }
  }')

for PAYLOAD in "$REFERENCE_PAYLOAD" "$CURRENT_PAYLOAD"; do
  curl --fail-with-body --silent --show-error \
    --request POST \
    --header "Authorization: Bearer $TOKEN" \
    --header 'Content-Type: application/json' \
    --data "$PAYLOAD" \
    "$BASE_URL/data/upload" \
    | jq -e '.status == "success"' >/dev/null
done

METRIC_REQUEST=$(jq -nc \
  --arg model "$MODEL_ID" \
  --arg tag "$BASELINE_TAG" \
  '{
    modelId: $model,
    metricName: "KSTest",
    batchSize: 100,
    thresholdDelta: 0.05,
    referenceTag: $tag
  }')

curl --fail-with-body --silent --show-error \
  --request POST \
  --header "Authorization: Bearer $TOKEN" \
  --header 'Content-Type: application/json' \
  --data "$METRIC_REQUEST" \
  "$BASE_URL/metrics/drift/kstest" \
  | jq -e '
      .status == "success"
      and .drift_detected == true
      and .p_value < .alpha
    '
