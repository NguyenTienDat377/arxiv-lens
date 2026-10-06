# The ceiling on every role the deployer creates (the EKS cluster role, the
# node role and the EBS driver's role). A boundary never grants anything: a
# role's effective permissions are the overlap of its attached policies and
# this one.
#
# It is a deny-list on purpose. An allow-list of exactly what EKS needs would
# be tighter on paper, but a missing action fails silently at runtime (the EBS
# driver crash-looped for an hour on a missing credential), and the threat
# here is escalation through IAM and billing, not a node using EC2.
data "aws_iam_policy_document" "boundary" {
  statement {
    sid         = "AllowTheDataPlane"
    not_actions = ["iam:CreateServiceLinkedRole"]
    resources   = ["*"]
  }

  # Split out of the allow above: with a plain "*" a bounded role could create
  # a service-linked role for any AWS service. The cluster role does need to
  # create the load balancer and autoscaling ones on demand.
  statement {
    sid       = "AllowOnlyTheServiceLinkedRolesEksUses"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["arn:aws:iam::*:role/aws-service-role/*"]

    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values = [
        "eks.amazonaws.com", "eks-nodegroup.amazonaws.com",
        "elasticloadbalancing.amazonaws.com", "autoscaling.amazonaws.com",
      ]
    }
  }

  statement {
    sid    = "DenyChangingIdentityAndAccess"
    effect = "Deny"
    actions = [
      "iam:CreateUser", "iam:CreateGroup", "iam:AddUserToGroup",
      "iam:CreateAccessKey", "iam:UpdateAccessKey",
      "iam:CreateLoginProfile", "iam:UpdateLoginProfile",
      "iam:CreateRole", "iam:UpdateAssumeRolePolicy", "iam:PassRole",
      "iam:CreatePolicy", "iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion",
      "iam:AttachRolePolicy", "iam:AttachUserPolicy", "iam:AttachGroupPolicy",
      "iam:PutRolePolicy", "iam:PutUserPolicy", "iam:PutGroupPolicy",
      "iam:PutRolePermissionsBoundary", "iam:DeleteRolePermissionsBoundary",
      "iam:PutUserPermissionsBoundary", "iam:DeleteUserPermissionsBoundary",
      "iam:CreateOpenIDConnectProvider", "iam:CreateSAMLProvider",
      "iam:CreateInstanceProfile", "iam:AddRoleToInstanceProfile",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "DenyAccountBillingAndAuditTampering"
    effect = "Deny"
    actions = [
      "organizations:*", "account:*", "billing:*",
      "budgets:ModifyBudget",
      "cloudtrail:StopLogging", "cloudtrail:DeleteTrail", "cloudtrail:UpdateTrail",
      "sts:AssumeRole",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "boundary" {
  name        = "arxiv-lens-boundary"
  description = "Permissions boundary for roles created by the arxiv-lens deployer"
  policy      = data.aws_iam_policy_document.boundary.json
}
