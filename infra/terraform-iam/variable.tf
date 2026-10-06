variable "region" {
  type    = string
  default = "ap-southeast-2"
}

variable "aws_profile" {
  type    = string
  default = "dat-admin"
}

# The only instance types the deployer may launch. A leaked key then cannot
# start a large instance on your credits. Keep it in step with instance_type
# in terraform-aws and node_instance_type in terraform-eks.
variable "allowed_instance_types" {
  type    = list(string)
  default = ["t4g.large", "t3.large"]
}
