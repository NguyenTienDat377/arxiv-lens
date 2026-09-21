locals {
  admin = split("/", var.admin_cidr)
}

data "vultr_os" "ubuntu" {
  filter {
    name   = "name"
    values = ["Ubuntu 24.04 LTS x64"]
  }
}

resource "vultr_ssh_key" "demo" {
  name    = "arxiv-lens"
  ssh_key = trimspace(file(pathexpand(var.ssh_public_key_path)))
}

resource "vultr_firewall_group" "demo" {
  description = "arxiv-lens demo"
}

resource "vultr_firewall_rule" "ssh" {
  firewall_group_id = vultr_firewall_group.demo.id
  protocol          = "tcp"
  ip_type           = "v4"
  subnet            = local.admin[0]
  subnet_size       = tonumber(local.admin[1])
  port              = "22"
  notes             = "ssh, admin only"
}

resource "vultr_firewall_rule" "app" {
  firewall_group_id = vultr_firewall_group.demo.id
  protocol          = "tcp"
  ip_type           = "v4"
  subnet            = "0.0.0.0"
  subnet_size       = 0
  port              = "8082"
  notes             = "query-service UI and API"
}

resource "vultr_instance" "demo" {
  plan              = var.plan
  region            = var.region
  os_id             = data.vultr_os.ubuntu.id
  label             = "arxiv-lens-demo"
  hostname          = "arxiv-lens"
  ssh_key_ids       = [vultr_ssh_key.demo.id]
  firewall_group_id = vultr_firewall_group.demo.id
  backups           = "disabled"

  # Readable from the instance metadata endpoint and stored in state: no secrets.
  user_data = file("${path.module}/cloud-init.yaml")
}
