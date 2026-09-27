variable "region" {
  type    = string
  default = "ap-southeast-2"
}

variable "aws_profile" {
  type    = string
  default = "dat-admin"
}

# Graviton (arm64). The AMI's architecture follows this automatically, so an
# x86 type such as t3.large also works without other changes.
variable "instance_type" {
  type    = string
  default = "t4g.large"
}

variable "ssh_public_key_path" {
  type    = string
  default = "~/.oci/arxiv_lens_ssh.pub"
}

variable "admin_cidr" {
  type        = string
  description = "Your public IP as x.x.x.x/32; the only address allowed to SSH in."

  validation {
    condition     = can(cidrhost(var.admin_cidr, 0)) && !startswith(var.admin_cidr, "0.0.0.0")
    error_message = "admin_cidr must be a real CIDR such as 203.0.113.7/32, never 0.0.0.0/0."
  }
}

variable "budget_email" {
  type        = string
  description = "Where budget alerts go. Set it in terraform.tfvars, which is not committed."
}

variable "monthly_budget_usd" {
  type    = number
  default = 15
}
