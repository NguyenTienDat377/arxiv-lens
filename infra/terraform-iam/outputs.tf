output "deployer_user" {
  value = aws_iam_user.deployer.name
}

output "deployer_policy_arns" {
  value = [aws_iam_policy.deployer.arn, aws_iam_policy.deployer_iam.arn]
}

# Paste this into infra/terraform-eks/terraform.tfvars; the deployer is only
# allowed to create roles that carry it.
output "permissions_boundary_arn" {
  value = aws_iam_policy.boundary.arn
}
