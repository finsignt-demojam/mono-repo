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
ARGO_NAMESPACE="${ARGO_NAMESPACE:-openshift-gitops}"
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
# Check if any OperatorGroup exists by counting items (oc get returns 0 even with no results)
OG_COUNT=$(oc get operatorgroup -n "${OPERATOR_NAMESPACE}" --no-headers 2>/dev/null | wc -l)
if [[ "${OG_COUNT}" -eq 0 ]]; then
  cat <<EOF | oc apply -f -
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: global-operators
  namespace: ${OPERATOR_NAMESPACE}
spec:
  upgradeStrategy: Default
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
echo "[INFO] Waiting for all pods in namespace '${OPERATOR_NAMESPACE}' to be ready..."
if ! oc wait pod -n "${OPERATOR_NAMESPACE}" --for=condition=Ready --all --timeout="${TIMEOUT}"; then
    echo "✗ Timeout or error waiting for pods"
    exit 1
fi
echo "✓ All pods are ready!"

echo "[INFO] Waiting for Argo CD service account to be created..."
PRIMARY_SA="openshift-gitops-argocd-application-controller"
timeout 300 bash -c '
  until oc get sa "'"${PRIMARY_SA}"'" -n "'"${ARGO_NAMESPACE}"'" >/dev/null 2>&1; do
    echo "Waiting for service account ${PRIMARY_SA} to be created..."
    sleep 5
  done
'
echo "✓ Service account ${PRIMARY_SA} found"

echo "[INFO] Granting cluster privileges to the Argo CD application-controller..."
# Grant cluster-admin role to the ArgoCD application controller
# cluster-admin includes all permissions including: creating namespaces, deployments,
# services, routes, and any other resources in any namespace
echo " - Granting cluster-admin to serviceaccount/${PRIMARY_SA}"
oc adm policy add-cluster-role-to-user cluster-admin \
  "system:serviceaccount:${ARGO_NAMESPACE}:${PRIMARY_SA}"

# echo "[INFO] Ensuring target namespace exists for ArgoCD applications..."
# TARGET_NAMESPACE="shakeout-app"
# oc get ns "${TARGET_NAMESPACE}" >/dev/null 2>&1 || oc create ns "${TARGET_NAMESPACE}"

echo "[INFO] Verifying permissions were applied correctly..."
# Check cluster role bindings
echo " - Checking cluster role bindings for ${PRIMARY_SA}..."
SA_FULL_NAME="system:serviceaccount:${ARGO_NAMESPACE}:${PRIMARY_SA}"
if oc get clusterrolebindings -o json | jq -e --arg sa "${SA_FULL_NAME}" --arg name "${PRIMARY_SA}" --arg ns "${ARGO_NAMESPACE}" '.items[] | select(.roleRef.name == "cluster-admin") | select(.subjects[]? | (.kind == "ServiceAccount" and .name == $name and .namespace == $ns) or . == $sa)' >/dev/null 2>&1; then
  echo "   ✓ Cluster role bindings found"
else
  echo "   ✗ No cluster role bindings found for ${PRIMARY_SA}"
fi

# Test specific permissions (cluster-admin should allow all operations in any namespace)
echo " - Testing cluster-admin permissions..."
# Test namespace creation (critical for CreateNamespace=true in ArgoCD apps)
if oc auth can-i create namespaces --as="system:serviceaccount:${ARGO_NAMESPACE}:${PRIMARY_SA}" >/dev/null 2>&1; then
  echo "   ✓ Can create namespaces (required for CreateNamespace=true)"
else
  echo "   ✗ Cannot create namespaces"
fi

if oc auth can-i create deployments --as="system:serviceaccount:${ARGO_NAMESPACE}:${PRIMARY_SA}" --all-namespaces >/dev/null 2>&1; then
  echo "   ✓ Can create deployments (cluster-admin verified)"
else
  echo "   ✗ Cannot create deployments"
fi

if oc auth can-i create services --as="system:serviceaccount:${ARGO_NAMESPACE}:${PRIMARY_SA}" --all-namespaces >/dev/null 2>&1; then
  echo "   ✓ Can create services (cluster-admin verified)"
else
  echo "   ✗ Cannot create services"
fi

if oc auth can-i create routes --as="system:serviceaccount:${ARGO_NAMESPACE}:${PRIMARY_SA}" --all-namespaces >/dev/null 2>&1; then
  echo "   ✓ Can create routes (cluster-admin verified)"
else
  echo "   ✗ Cannot create routes"
fi

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

echo "[INFO] Troubleshooting tips if ArgoCD sync fails:"
echo "  If you see permission errors like 'cannot create resource \"deployments\"' or 'cannot create namespace', try:"
echo "  1. Check if the service account exists:"
echo "     oc get sa ${PRIMARY_SA} -n ${ARGO_NAMESPACE}"
echo "  2. Verify cluster role bindings:"
echo "     oc get clusterrolebindings | grep ${PRIMARY_SA}"
echo "  3. Test specific permissions (should work for all resources):"
echo "     oc auth can-i create namespaces --as=system:serviceaccount:${ARGO_NAMESPACE}:${PRIMARY_SA}"
echo "     oc auth can-i create deployments --as=system:serviceaccount:${ARGO_NAMESPACE}:${PRIMARY_SA} --all-namespaces"
echo "  4. If permissions are missing, re-run the permission command:"
echo "     oc adm policy add-cluster-role-to-user cluster-admin system:serviceaccount:${ARGO_NAMESPACE}:${PRIMARY_SA}"

echo "[HINT] Console route (SSO):"
oc -n "${ARGO_NAMESPACE}" get route openshift-gitops-server -o jsonpath='{.spec.host}{"\n"}' 2>/dev/null || true
