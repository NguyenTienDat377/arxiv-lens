data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = "arxiv-lens-lab"
  # EKS refuses a cluster whose subnets span fewer than two availability zones.
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.0"

  name = local.name
  cidr = "10.1.0.0/16"
  azs  = local.azs

  public_subnets          = ["10.1.1.0/24", "10.1.2.0/24"]
  map_public_ip_on_launch = true

  # Nodes sit in public subnets instead, pulling images through the internet
  # gateway; a NAT gateway would cost more per month than a lab session.
  enable_nat_gateway = false

  public_subnet_tags = {
    "kubernetes.io/role/elb" = 1
  }
}

# The EBS CSI driver's controller pods authenticate as this role via OIDC
# (IRSA), not as the node. The node-role policy below is not enough on its
# own: these pods refuse to fall back to the node's IMDS credentials at all.
module "ebs_csi_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts"
  version = "~> 6.0"

  name                  = "${local.name}-ebs-csi"
  attach_ebs_csi_policy = true

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"]
    }
  }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.0"

  name               = local.name
  kubernetes_version = var.kubernetes_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.public_subnets

  # kubectl reaches the API from your IP only. Nodes use the private endpoint
  # inside the VPC; their public IPs are not in admin_cidr, so without it they
  # could never join the cluster.
  endpoint_public_access       = true
  endpoint_public_access_cidrs = [var.admin_cidr]
  endpoint_private_access      = true

  # A new cluster's access list is empty; without this even its creator is
  # refused by kubectl.
  enable_cluster_creator_admin_permissions = true

  # EKS already encrypts Secrets with an AWS-owned key. A customer-managed key
  # adds a monthly charge per lab session and a 30-day deletion window.
  create_kms_key    = false
  encryption_config = null

  addons = {
    vpc-cni = {
      before_compute = true
    }
    kube-proxy = {}
    coredns    = {}
    aws-ebs-csi-driver = {
      service_account_role_arn = module.ebs_csi_irsa.arn
    }
  }

  eks_managed_node_groups = {
    default = {
      ami_type       = "AL2023_x86_64_STANDARD"
      instance_types = [var.node_instance_type]

      min_size     = 1
      max_size     = 1
      desired_size = 1

    }
  }
}
