#!/bin/bash
# minikube_deploy.sh - Deploy microservices-demo Helm chart to Minikube
set -e

CHART_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$CHART_DIR")"

CHART_NAME="microservices-demo"
NAMESPACE="online-shop"

# Start minikube if not running
if ! minikube status >/dev/null 2>&1; then
  echo "Starting minikube..."
  minikube start
else
  echo "Minikube is already running."
fi

echo "Changing to Helm chart directory: $CHART_DIR"
cd "$CHART_DIR"

# Set kubectl context to minikube
kubectl config use-context minikube

# Create namespace if it doesn't exist
if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  echo "Creating namespace $NAMESPACE..."
  kubectl create namespace "$NAMESPACE"
else
  echo "Namespace $NAMESPACE already exists."
fi

# Install Helm if not present
if ! command -v helm >/dev/null 2>&1; then
  echo "Helm not found. Please install Helm before running this script."
  exit 1
fi

# Deploy the Helm chart into the namespace
if helm status $CHART_NAME -n $NAMESPACE >/dev/null 2>&1; then
  echo "Helm release $CHART_NAME already exists in namespace $NAMESPACE. Upgrading..."
  helm upgrade $CHART_NAME . -n $NAMESPACE
else
  echo "Installing Helm release $CHART_NAME into namespace $NAMESPACE..."
  helm install $CHART_NAME . -n $NAMESPACE
fi

# Wait for all pods to be ready in the namespace
kubectl wait --for=condition=Ready pods --all --timeout=300s -n $NAMESPACE

# Port-forward frontend service in the namespace
FRONTEND_PORT=8080
FRONTEND_SVC="frontend"
echo "Port-forwarding $FRONTEND_SVC service to localhost:$FRONTEND_PORT in namespace $NAMESPACE"
echo "You can access the app at http://localhost:$FRONTEND_PORT"
kubectl port-forward svc/$FRONTEND_SVC $FRONTEND_PORT:80 -n $NAMESPACE
