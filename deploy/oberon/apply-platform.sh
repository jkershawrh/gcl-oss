#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

command -v oc >/dev/null

oc apply -f "$SCRIPT_DIR/platform.yaml"
oc wait evalhub/evalhub \
  -n gcl-oss-trustyai \
  --for=condition=Ready \
  --timeout=300s
oc rollout status deployment/evalhub \
  -n gcl-oss-trustyai \
  --timeout=300s
