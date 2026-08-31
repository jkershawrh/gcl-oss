#!/bin/sh
set -eu

OPERATOR_NAMESPACE="gcl-oss-trustyai"
OPERATOR_REPOSITORY="https://github.com/trustyai-explainability/trustyai-service-operator.git"
OPERATOR_REVISION="547c02508be5fef23d1c90c76772fe11b5297832"
OPERATOR_IMAGE="quay.io/trustyai/trustyai-service-operator@sha256:2b4adaa22f2e85356f7044fc1546d04a12160ca843ac5e19b0cf99dee5c37b9c"
EVALHUB_IMAGE="quay.io/evalhub/evalhub@sha256:0b9b9cb7121170eb28d7a723b487282cc6ef3640ec9cf9ff6ba50b1bf04a61a1"
RBAC_PROXY_IMAGE="quay.io/opendatahub/odh-kube-rbac-proxy@sha256:c19ed97828e4e5e736334b3ea340ba92a5d0c95f7b21080c6fd7ccf6d36836af"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE_DIR=$(mktemp -d /tmp/gcl-oss-trustyai-operator.XXXXXX)
RENDERED_MANIFEST="$SOURCE_DIR/evalhub-operator.yaml"
trap 'rm -rf "$SOURCE_DIR"' EXIT HUP INT TERM

command -v git >/dev/null
command -v oc >/dev/null

oc apply -f "$SCRIPT_DIR/namespaces.yaml"

git -C "$SOURCE_DIR" init --quiet
git -C "$SOURCE_DIR" remote add origin "$OPERATOR_REPOSITORY"
git -C "$SOURCE_DIR" fetch --quiet --depth 1 origin "$OPERATOR_REVISION"
git -C "$SOURCE_DIR" checkout --quiet --detach FETCH_HEAD
test "$(git -C "$SOURCE_DIR" rev-parse HEAD)" = "$OPERATOR_REVISION"
git -C "$SOURCE_DIR" apply "$SCRIPT_DIR/operator-auth-delegator.patch"

oc kustomize "$SOURCE_DIR/config/overlays/evalhub-only" \
  | sed \
      -e "s|namespace: system|namespace: $OPERATOR_NAMESPACE|g" \
      -e "s|quay.io/trustyai/trustyai-service-operator:latest|$OPERATOR_IMAGE|g" \
      -e "s|quay.io/evalhub/evalhub:latest|$EVALHUB_IMAGE|g" \
      -e "s|quay.io/opendatahub/odh-kube-rbac-proxy:odh-stable|$RBAC_PROXY_IMAGE|g" \
  >"$RENDERED_MANIFEST"

grep -F "$OPERATOR_IMAGE" "$RENDERED_MANIFEST" >/dev/null
grep -F "$EVALHUB_IMAGE" "$RENDERED_MANIFEST" >/dev/null
grep -F "$RBAC_PROXY_IMAGE" "$RENDERED_MANIFEST" >/dev/null

oc apply -f "$RENDERED_MANIFEST"
oc rollout status deployment/controller-manager \
  -n "$OPERATOR_NAMESPACE" \
  --timeout=300s
