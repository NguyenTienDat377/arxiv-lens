data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ec2_instance_type" "selected" {
  instance_type = var.instance_type
}

locals {
  # Canonical names images by Debian architecture: arm64 or amd64.
  arch = contains(data.aws_ec2_instance_type.selected.supported_architectures, "arm64") ? "arm64" : "amd64"
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-${local.arch}-server-*"]
  }
}

resource "aws_vpc" "demo" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true

  tags = {
    Name = "arxiv-lens"
  }
}

resource "aws_internet_gateway" "demo" {
  vpc_id = aws_vpc.demo.id

  tags = {
    Name = "arxiv-lens"
  }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.demo.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true

  tags = {
    Name = "arxiv-lens-public"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.demo.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.demo.id
  }

  tags = {
    Name = "arxiv-lens-public"
  }
}

# Without this the subnet silently uses the VPC's main route table, which has
# no route to the internet.
resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# The only firewall in front of the host. Compose also publishes Neo4j, Kafka,
# gRPC and Redis ports, and Ubuntu on EC2 has no host firewall of its own.
resource "aws_security_group" "demo" {
  name        = "arxiv-lens"
  description = "arxiv-lens demo: ssh from admin, app from anywhere"
  vpc_id      = aws_vpc.demo.id
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.demo.id
  description       = "ssh, admin only"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_ingress_rule" "app" {
  security_group_id = aws_security_group.demo.id
  description       = "query-service UI and API"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 8082
  to_port           = 8082
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.demo.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_key_pair" "demo" {
  key_name   = "arxiv-lens"
  public_key = trimspace(file(pathexpand(var.ssh_public_key_path)))
}

resource "aws_instance" "demo" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.demo.id]
  key_name               = aws_key_pair.demo.key_name

  # Readable from the instance metadata endpoint and stored in state: no secrets.
  user_data = file("${path.module}/cloud-init.yaml")

  root_block_device {
    volume_size = 40
    volume_type = "gp3"
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  tags = {
    Name = "arxiv-lens-demo"
  }

  lifecycle {
    # A newer Ubuntu AMI would otherwise replace the instance, and the loaded
    # graph with it, on the next apply.
    ignore_changes = [ami]
  }
}

resource "aws_eip" "demo" {
  domain   = "vpc"
  instance = aws_instance.demo.id

  tags = {
    Name = "arxiv-lens"
  }

  depends_on = [aws_internet_gateway.demo]
}

resource "aws_budgets_budget" "monthly" {
  name         = "My Monthly Cost Budget"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Measure spend before credits; with credits included it reads $0 until
  # they run out, and the alert never fires.
  cost_types {
    include_credit = false
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}
