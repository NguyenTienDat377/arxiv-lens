data "aws_caller_identity" "current" {}

locals {
  account = data.aws_caller_identity.current.account_id
  iam     = "arn:aws:iam::${local.account}"
  eks     = "arn:aws:eks:${var.region}:${local.account}"

  # Built from names, not from the resources, because the deployer policies
  # have to refer to themselves and the boundary without a dependency cycle.
  boundary_policy_arn = "${local.iam}:policy/arxiv-lens-boundary"
  protected_policy_arns = [
    "${local.iam}:policy/arxiv-lens-deployer",
    "${local.iam}:policy/arxiv-lens-deployer-iam",
    local.boundary_policy_arn,
  ]

  # Names the two stacks really create, read off CloudTrail rather than guessed:
  # arxiv-lens-lab-cluster-*, arxiv-lens-lab-ebs-csi-*, default-eks-node-group-*.
  managed_roles = [
    "${local.iam}:role/arxiv-lens-*",
    "${local.iam}:role/default-eks-node-group-*",
  ]
}

# Infrastructure: everything except roles. The role-handling half is in
# iam.tf, a separate policy because a managed policy is capped at 6144
# characters and this half alone used most of that.
data "aws_iam_policy_document" "deployer" {
  statement {
    sid = "ReadForPlan"
    actions = [
      "ec2:Describe*", "ec2:GetSecurityGroupsForVpc", "ec2:GetLaunchTemplateData",
      "eks:Describe*", "eks:List*",
      "iam:Get*", "iam:List*",
      "logs:Describe*", "logs:List*",
      "ssm:GetParameter",
      "budgets:Describe*", "budgets:ViewBudget", "budgets:ListTagsForResource",
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }

  statement {
    sid = "Ec2WriteInOneRegion"
    actions = [
      "ec2:CreateVpc", "ec2:DeleteVpc", "ec2:ModifyVpcAttribute",
      "ec2:CreateSubnet", "ec2:DeleteSubnet", "ec2:ModifySubnetAttribute",
      "ec2:CreateInternetGateway", "ec2:DeleteInternetGateway",
      "ec2:AttachInternetGateway", "ec2:DetachInternetGateway",
      "ec2:CreateRouteTable", "ec2:DeleteRouteTable",
      "ec2:CreateRoute", "ec2:ReplaceRoute", "ec2:DeleteRoute",
      "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable", "ec2:ReplaceRouteTableAssociation",
      "ec2:CreateNetworkAclEntry", "ec2:ReplaceNetworkAclEntry", "ec2:DeleteNetworkAclEntry",
      "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress",
      "ec2:ModifySecurityGroupRules",
      "ec2:UpdateSecurityGroupRuleDescriptionsIngress", "ec2:UpdateSecurityGroupRuleDescriptionsEgress",
      "ec2:CreateLaunchTemplate", "ec2:CreateLaunchTemplateVersion",
      "ec2:ModifyLaunchTemplate", "ec2:DeleteLaunchTemplate", "ec2:DeleteLaunchTemplateVersions",
      "ec2:ImportKeyPair", "ec2:DeleteKeyPair",
      "ec2:AllocateAddress", "ec2:AssociateAddress", "ec2:DisassociateAddress", "ec2:ReleaseAddress",
      "ec2:RunInstances", "ec2:StartInstances", "ec2:StopInstances", "ec2:TerminateInstances",
      "ec2:ModifyInstanceAttribute", "ec2:ModifyInstanceMetadataOptions",
      "ec2:CreateTags", "ec2:DeleteTags",
    ]
    resources = ["*"]

    # EC2 creates have no existing ARN to point at, so the fence is the region.
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }

  # A Deny rather than a condition on the allow: RunInstances touches an image,
  # a subnet, a volume and more, and an instance-type condition on the allow
  # would fail on every resource that has no instance type.
  statement {
    sid       = "DenyOtherInstanceTypes"
    effect    = "Deny"
    actions   = ["ec2:RunInstances"]
    resources = ["arn:aws:ec2:*:*:instance/*"]

    condition {
      test     = "StringNotEquals"
      variable = "ec2:InstanceType"
      values   = var.allowed_instance_types
    }
  }

  # CreateCluster cannot be scoped by name: the cluster does not exist yet, so
  # the request has no ARN to match. Found with the policy simulator, which
  # allowed every other EKS action on a cluster ARN and denied this one. Left
  # fenced by region; every other EKS action below is fenced by name.
  statement {
    sid       = "EksCreateCluster"
    actions   = ["eks:CreateCluster"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }

  statement {
    sid = "EksWrite"
    actions = [
      "eks:DeleteCluster", "eks:UpdateClusterConfig", "eks:UpdateClusterVersion",
      "eks:CreateNodegroup", "eks:DeleteNodegroup", "eks:UpdateNodegroupConfig", "eks:UpdateNodegroupVersion",
      "eks:CreateAddon", "eks:DeleteAddon", "eks:UpdateAddon",
      "eks:CreateAccessEntry", "eks:DeleteAccessEntry", "eks:UpdateAccessEntry",
      "eks:AssociateAccessPolicy", "eks:DisassociateAccessPolicy",
      "eks:TagResource", "eks:UntagResource",
    ]
    resources = [
      "${local.eks}:cluster/arxiv-lens-*",
      "${local.eks}:nodegroup/arxiv-lens-*",
      "${local.eks}:addon/arxiv-lens-*",
      "${local.eks}:access-entry/arxiv-lens-*",
    ]
  }

  statement {
    sid = "LogGroupsForEks"
    actions = [
      "logs:CreateLogGroup", "logs:DeleteLogGroup",
      "logs:PutRetentionPolicy", "logs:DeleteRetentionPolicy",
      "logs:TagResource", "logs:UntagResource",
    ]
    resources = ["arn:aws:logs:${var.region}:${local.account}:log-group:/aws/eks/arxiv-lens-*"]
  }

  # Budgets is a global service, so it carries no region condition.
  statement {
    sid = "BudgetsWrite"
    actions = [
      "budgets:ModifyBudget", "budgets:TagResource", "budgets:UntagResource",
    ]
    resources = ["arn:aws:budgets::${local.account}:budget/*"]
  }
}

resource "aws_iam_policy" "deployer" {
  name        = "arxiv-lens-deployer"
  description = "Terraform deployer for the EC2 host and EKS lab: infrastructure"
  policy      = data.aws_iam_policy_document.deployer.json
}
