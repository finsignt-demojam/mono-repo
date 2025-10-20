#! /bin/bash

# This script is used to bootstrap a new ROSA cluster.

# The script assumes that you are authenticate with the cluster with cluster admin privilages.
# It will configure oauth for GutHub authentication of the existing cluster
# It will then install the OpenShift GitOps operator and configure GitOps to have cluster privileges for deploying applications

#!/usr/bin/env bash
# Bootstrap OpenShift GitOps (Argo CD) and grant cluster privileges to the controller for deploying applications.
# Requires: oc logged in as a user with cluster-admin.
set -euo pipefail

# ---- Configurable bits (override via env if you like) ----
OPERATOR_NAMESPACE="${OPERATOR_NAMESPACE:-openshift-gitops-operator}"
ARGO_NAMESPACE="${ARGO_NAMESPACE:-openshift-gitops-operator}"
CATALOG_SOURCE="${CATALOG_SOURCE:-redhat-operators}"
CATALOG_NAMESPACE="${CATALOG_NAMESPACE:-openshift-marketplace}"
OPERATOR_PACKAGE="${OPERATOR_PACKAGE:-openshift-gitops-operator}"
# Choose a channel available to your cluster; "latest" is commonly available on recent OCP.
OPERATOR_CHANNEL="${OPERATOR_CHANNEL:-latest}"

echo "[INFO] Verifying 'oc' login and permissions..."
oc whoami >/dev/null
# Basic permission check: try to list clusteroperators
oc get clusteroperators >/dev/null

echo "[INFO] Ensuring operator namespace: ${OPERATOR_NAMESPACE}"
oc get ns "${OPERATOR_NAMESPACE}" >/dev/null 2>&1 || oc create ns "${OPERATOR_NAMESPACE}"

echo "[INFO] Creating/ensuring a global OperatorGroup in ${OPERATOR_NAMESPACE}..."
# A global OperatorGroup in openshift-operators usually exists; create if missing.
if ! oc get operatorgroup -n "${OPERATOR_NAMESPACE}" >/dev/null 2>&1; then
  cat <<EOF | oc apply -f -
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: global-operators
  namespace: ${OPERATOR_NAMESPACE}
spec:
  upgradeStrategy: Default
  targetNamespaces:
  - ${OPERATOR_NAMESPACE}
EOF
else
  echo "[INFO] OperatorGroup already present in ${OPERATOR_NAMESPACE}"
fi

echo "[INFO] Creating Subscription for ${OPERATOR_PACKAGE} on channel ${OPERATOR_CHANNEL}..."
cat <<EOF | oc apply -f -
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: ${OPERATOR_PACKAGE}
  namespace: ${OPERATOR_NAMESPACE}
spec:
  channel: latest 
  installPlanApproval: Automatic
  name: ${OPERATOR_PACKAGE} 
  source: ${CATALOG_SOURCE}
  sourceNamespace: ${CATALOG_NAMESPACE}
EOF

echo "[INFO] Waiting for the Operator CSV to succeed..."
# Find the CSV name that matches the package, then wait until Succeeded
# We poll because CSV name includes a version.
timeout 600 bash -c '
  while true; do
    # Get all CSVs and find one that contains "gitops" in the name
    CSV=$(oc get csv -n "'"${OPERATOR_NAMESPACE}"'" -o name | grep -i gitops | head -1 | cut -d/ -f2 || true)
    if [[ -n "$CSV" ]]; then
      PHASE=$(oc get csv "$CSV" -n "'"${OPERATOR_NAMESPACE}"'" -o jsonpath="{.status.phase}" 2>/dev/null || echo "Unknown")
      echo "CSV: $CSV, Phase: $PHASE"
      [[ "$PHASE" == "Succeeded" ]] && exit 0
    else
      echo "No GitOps CSV found yet, waiting..."
    fi
    sleep 10
  done
'
echo "[INFO] OpenShift GitOps Operator is installed."

echo "[INFO] Waiting for the default Argo CD namespace (${ARGO_NAMESPACE}) to appear..."
timeout 300 bash -c '
  until oc get ns "'"${ARGO_NAMESPACE}"'" >/dev/null 2>&1; do
    sleep 3
  done
'
TIMEOUT="300s"
echo "Waiting for all pods in namespace '${OPERATOR_NAMESPACE}' to be ready..."
if oc wait pod -n "${OPERATOR_NAMESPACE}" --for=condition=Ready --all --timeout="${TIMEOUT}"; then
    echo "✓ All pods are ready!"
    exit 0
else
    echo "✗ Timeout or error waiting for pods"
    exit 1
fi

echo "[INFO] Granting cluster privileges to the Argo CD application-controller..."
# Primary, current SA name:
PRIMARY_SA="openshift-gitops-argocd-application-controller"
# Legacy/alternate names to cover version differences:
ALT_SAS=("openshift-gitops-app-controller" "openshift-gitops-argocd-application-controller-sa")

grant_privileges() {
  local sa="$1"
  if oc -n "${ARGO_NAMESPACE}" get sa "${sa}" >/dev/null 2>&1; then
    echo " - Granting cluster-admin to serviceaccount/${sa}"
    oc adm policy add-cluster-role-to-user cluster-admin "system:serviceaccount:${ARGO_NAMESPACE}:${sa}"
    
    echo " - Granting edit role to serviceaccount/${sa} (for granular permissions)"
    oc adm policy add-cluster-role-to-user edit "system:serviceaccount:${ARGO_NAMESPACE}:${sa}"
  else
    echo "   [INFO] SA ${sa} not present; skipping."
  fi
}

grant_privileges "${PRIMARY_SA}"
for s in "${ALT_SAS[@]}"; do grant_privileges "$s"; done

echo "[INFO] Verifying effective access by listing namespaces using the controller SA token..."
# (Best-effort sanity check)
if oc -n "${ARGO_NAMESPACE}" get sa "${PRIMARY_SA}" >/dev/null 2>&1; then
  TOKEN=$(oc -n "${ARGO_NAMESPACE}" create token "${PRIMARY_SA}" --duration=10m 2>/dev/null || true)
  if [[ -n "${TOKEN:-}" ]]; then
    oc --token="${TOKEN}" --server="$(oc whoami --show-server)" get ns >/dev/null && \
      echo " - Access check OK (controller can list namespaces)." || \
      echo " - Access check WARN: could not verify with token (non-fatal)."
  else
    echo " - Could not mint a token for SA ${PRIMARY_SA} (version/policy difference)."
  fi
fi

echo "[DONE] OpenShift GitOps is installed and granted cluster privileges for deploying applications."
echo "[HINT] Console route (SSO):"
oc -n "${ARGO_NAMESPACE}" get route openshift-gitops-server -o jsonpath='{.spec.host}{"\n"}' 2>/dev/null || true
