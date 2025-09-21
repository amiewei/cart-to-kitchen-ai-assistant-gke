#!/bin/bash
# minikube_cleanup.sh - Remove microservices-demo deployment and namespace from Minikube
set -e

CHART_NAME="microservices-demo"
NAMESPACE="online-shop"

# Uninstall Helm release from the namespace
if helm status $CHART_NAME -n $NAMESPACE >/dev/null 2>&1; then
  echo "Uninstalling Helm release $CHART_NAME from namespace $NAMESPACE..."
  helm uninstall $CHART_NAME -n $NAMESPACE
else
  echo "Helm release $CHART_NAME not found in namespace $NAMESPACE."
fi

# Delete the namespace
if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  echo "Deleting namespace $NAMESPACE..."
  kubectl delete namespace "$NAMESPACE"
else
  echo "Namespace $NAMESPACE does not exist."
fi

# Optionally, stop minikube (uncomment if desired)
# echo "Stopping minikube..."
# minikube stop

echo "Cleanup complete."
