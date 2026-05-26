variable "cluster_name" {
  description = "Target Kubernetes cluster name"
  type        = string
  default     = "autonomous-ai-prod"
}

variable "replica_count" {
  description = "Backend replica count"
  type        = number
  default     = 2
}
