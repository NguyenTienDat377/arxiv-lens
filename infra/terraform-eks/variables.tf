variable "region" {
  type    = string
  default = "ap-southeast-2"
}

variable "aws_profile" {
  type    = string
  default = "dat-admin"
}

variable "admin_cidr" {
  type        = string
  description = "Your public IP as x.x.x.x/32; the only address allowed to reach the Kubernetes API."

  validation {
    condition     = can(cidrhost(var.admin_cidr, 0)) && !startswith(var.admin_cidr, "0.0.0.0")
    error_message = "admin_cidr must be a real CIDR such as 203.0.113.7/32, never 0.0.0.0/0."
  }
}

# A string, not a number: as a number 1.30 would silently become 1.3.
# Pick one in standard support; extended support bills six times as much.
variable "kubernetes_version" {
  type    = string
  default = "1.36"
}

# x86: matches either half of the multi-arch images, and t3 is the cheapest
# 8 GB general-purpose type.
variable "node_instance_type" {
  type    = string
  default = "t3.large"
}