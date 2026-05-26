# Starter Terraform for Autonomous AI Operator enterprise deployment

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.23"
    }
  }
}

variable "namespace" {
  type    = string
  default = "autonomous-ai"
}

variable "enable_runtime_mesh" {
  type    = bool
  default = false
}

variable "enable_policy_engine" {
  type    = bool
  default = false
}

resource "kubernetes_namespace" "operator" {
  metadata {
    name = var.namespace
  }
}

resource "kubernetes_config_map" "runtime_config" {
  metadata {
    name      = "runtime-config"
    namespace = kubernetes_namespace.operator.metadata[0].name
  }
  data = {
    ENABLE_RUNTIME_MESH     = tostring(var.enable_runtime_mesh)
    ENABLE_POLICY_ENGINE    = tostring(var.enable_policy_engine)
    ENABLE_COST_METERING    = "false"
    ENABLE_INFRA_INTELLIGENCE = "false"
    COMPLIANCE_MODE         = "enterprise"
  }
}
