variable "oci_profile" {
  type    = string
  default = "DEFAULT"
}

# Always Free compute only exists in the tenancy's home region, and the home
# region is fixed at sign-up. Change this only if your home region is not
# Singapore.
variable "region" {
  type    = string
  default = "ap-singapore-1"
}

# Always Free accounts have a single root compartment, which is the tenancy.
variable "tenancy_ocid" {
  type        = string
  description = "The tenancy OCID, from the `tenancy=` line in ~/.oci/config."
}

# The Always Free A1 allowance is 2 OCPU / 12 GB in total since June 2026.
# Going over it bills a Pay As You Go account and fails on an Always Free one.
variable "ocpus" {
  type    = number
  default = 2
}

variable "memory_gb" {
  type    = number
  default = 12
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
