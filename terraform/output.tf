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

output "gke_cluster_location" {
  description = "Location of the cluster"
  value       = resource.google_container_cluster.my_cluster.location
}

output "gke_cluster_name" {
  description = "Name of the cluster"
  value       = resource.google_container_cluster.my_cluster.name
}

output "gcp_project_id" {
  description = "The GCP project ID."
  value       = var.gcp_project_id
}

output "artifact_registry_repository_url" {
  description = "The URL of the Artifact Registry repository."
  value       = "${google_artifact_registry_repository.primary.location}-docker.pkg.dev/${var.gcp_project_id}/${google_artifact_registry_repository.primary.repository_id}"
}