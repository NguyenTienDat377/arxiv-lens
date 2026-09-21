variable "region" {
  type    = string
  default = "sgp"
}

variable "plan" {
  type    = string
  default = "vc2-2c-4gb"
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
