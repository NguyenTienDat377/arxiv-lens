# The half of the deployer that handles roles. Granting iam:CreateRole is
# normally as good as granting admin (create a role, attach AdministratorAccess,
# assume it), so each statement here is fenced by name, by condition, or both.
data "aws_iam_policy_document" "deployer_iam" {
  statement {
    sid       = "PassOnlyToEks"
    actions   = ["iam:PassRole"]
    resources = local.managed_roles

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["eks.amazonaws.com"]
    }
  }

  # A role can only be created with the boundary attached, so whatever is
  # attached to it later, its effective permissions can never exceed the
  # boundary.
  statement {
    sid       = "CreateRolesOnlyWithTheBoundary"
    actions   = ["iam:CreateRole"]
    resources = local.managed_roles

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.boundary_policy_arn]
    }
  }

  statement {
    sid = "ManageTheseRoles"
    actions = [
      "iam:DeleteRole", "iam:DetachRolePolicy",
      "iam:TagRole", "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = local.managed_roles
  }

  # An allow-list of what may be attached. AdministratorAccess is not on it.
  statement {
    sid       = "AttachOnlyApprovedPolicies"
    actions   = ["iam:AttachRolePolicy"]
    resources = local.managed_roles

    condition {
      test     = "ArnLike"
      variable = "iam:PolicyARN"
      values = [
        "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
        "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
        "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
        "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
        "${local.iam}:policy/EBS_CSI-*",
      ]
    }
  }

  # The EBS driver's IRSA module creates its own policy, named EBS_CSI-<hash>.
  statement {
    sid = "ManageTheEbsCsiPolicy"
    actions = [
      "iam:CreatePolicy", "iam:DeletePolicy",
      "iam:CreatePolicyVersion", "iam:DeletePolicyVersion", "iam:SetDefaultPolicyVersion",
      "iam:TagPolicy", "iam:UntagPolicy",
    ]
    resources = ["${local.iam}:policy/EBS_CSI-*"]
  }

  statement {
    sid = "ManageTheClusterOidcProvider"
    actions = [
      "iam:CreateOpenIDConnectProvider", "iam:DeleteOpenIDConnectProvider",
      "iam:TagOpenIDConnectProvider", "iam:UntagOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
      "iam:AddClientIDToOpenIDConnectProvider", "iam:RemoveClientIDFromOpenIDConnectProvider",
    ]
    resources = ["${local.iam}:oidc-provider/oidc.eks.${var.region}.amazonaws.com/id/*"]
  }

  statement {
    sid       = "ServiceLinkedRolesForEks"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["${local.iam}:role/aws-service-role/*"]

    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["eks.amazonaws.com", "eks-nodegroup.amazonaws.com"]
    }
  }

  # The allows above are already too narrow to reach these, so this Deny is a
  # second lock: it holds even if an allow is widened later by mistake.
  statement {
    sid    = "ProtectTheGuardrails"
    effect = "Deny"
    actions = [
      "iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion",
      "iam:DeletePolicy", "iam:DeletePolicyVersion",
    ]
    resources = local.protected_policy_arns
  }

  statement {
    sid       = "NeverTouchABoundary"
    effect    = "Deny"
    actions   = ["iam:PutRolePermissionsBoundary", "iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "deployer_iam" {
  name        = "arxiv-lens-deployer-iam"
  description = "Terraform deployer for the EC2 host and EKS lab: roles, fenced by a boundary"
  policy      = data.aws_iam_policy_document.deployer_iam.json
}
