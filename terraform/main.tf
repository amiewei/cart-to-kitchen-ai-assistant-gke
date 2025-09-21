# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Definition of local variables
locals {
  base_apis = [
    "serviceusage.googleapis.com",
    "container.googleapis.com",
    "monitoring.googleapis.com",
    "cloudtrace.googleapis.com",
    "cloudprofiler.googleapis.com",
    "artifactregistry.googleapis.com",
  ]
  ai_apis = [
    "aiplatform.googleapis.com",         # Vertex AI API
    "generativelanguage.googleapis.com", # Gemini API
    "contentwarehouse.googleapis.com",   # Content Warehouse API (for Imagen)
  ]
  memorystore_apis = ["redis.googleapis.com"]
  cluster_name     = google_container_cluster.my_cluster.name
}

# Enable Google Cloud APIs
module "enable_google_apis" {
  source  = "terraform-google-modules/project-factory/google//modules/project_services"
  version = "~> 18.0"

  project_id                  = var.gcp_project_id
  disable_services_on_destroy = false

  # activate_apis is the set of base_apis, ai_apis, and the APIs required by user-configured deployment options
  activate_apis = concat(
    local.base_apis,
    local.ai_apis,
    var.memorystore ? local.memorystore_apis : []
  )
}

# Create GKE cluster
resource "google_container_cluster" "my_cluster" {

  name     = var.name
  location = var.region

  # Enable autopilot for this cluster
  enable_autopilot = true

  # Set an empty ip_allocation_policy to allow autopilot cluster to spin up correctly
  ip_allocation_policy {
  }

  # Avoid setting deletion_protection to false
  # until you're ready (and certain you want) to destroy the cluster.
  # deletion_protection = false

  depends_on = [
    module.enable_google_apis
  ]
}

# Create a repository in Google Artifact Registry to store custom-built images.
resource "google_artifact_registry_repository" "primary" {
  provider      = google
  location      = var.region
  repository_id = "online-boutique-images" # You can name this whatever you like
  description   = "Docker repository for Online Boutique custom images"
  format        = "DOCKER"
}
