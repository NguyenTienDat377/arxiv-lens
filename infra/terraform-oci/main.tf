data "oci_identity_availability_domains" "all" {
  compartment_id = var.tenancy_ocid
}

# Newest Ubuntu 24.04 build for the ARM shape; image OCIDs differ per region.
data "oci_core_images" "ubuntu" {
  compartment_id           = var.tenancy_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

resource "oci_core_vcn" "demo" {
  compartment_id = var.tenancy_ocid
  cidr_blocks    = ["10.0.0.0/16"]
  display_name   = "arxiv-lens"
  dns_label      = "arxivlens"
}

resource "oci_core_internet_gateway" "demo" {
  compartment_id = var.tenancy_ocid
  vcn_id         = oci_core_vcn.demo.id
  display_name   = "arxiv-lens"
}

resource "oci_core_route_table" "demo" {
  compartment_id = var.tenancy_ocid
  vcn_id         = oci_core_vcn.demo.id
  display_name   = "arxiv-lens"

  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.demo.id
  }
}

# The cloud-side firewall. The instance runs its own iptables as well, which
# cloud-init opens for the app port.
resource "oci_core_security_list" "demo" {
  compartment_id = var.tenancy_ocid
  vcn_id         = oci_core_vcn.demo.id
  display_name   = "arxiv-lens"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  ingress_security_rules {
    description = "ssh, admin only"
    source      = var.admin_cidr
    protocol    = "6"
    tcp_options {
      min = 22
      max = 22
    }
  }

  ingress_security_rules {
    description = "query-service UI and API"
    source      = "0.0.0.0/0"
    protocol    = "6"
    tcp_options {
      min = 8082
      max = 8082
    }
  }

  # Path MTU discovery; without it large responses can stall.
  ingress_security_rules {
    source   = "0.0.0.0/0"
    protocol = "1"
    icmp_options {
      type = 3
      code = 4
    }
  }
}

resource "oci_core_subnet" "demo" {
  compartment_id    = var.tenancy_ocid
  vcn_id            = oci_core_vcn.demo.id
  cidr_block        = "10.0.1.0/24"
  display_name      = "arxiv-lens"
  dns_label         = "public"
  route_table_id    = oci_core_route_table.demo.id
  security_list_ids = [oci_core_security_list.demo.id]
}

resource "oci_core_instance" "demo" {
  compartment_id = var.tenancy_ocid
  # Singapore has one availability domain; elsewhere the first is as good as any.
  availability_domain = data.oci_identity_availability_domains.all.availability_domains[0].name
  display_name        = "arxiv-lens-demo"
  shape               = "VM.Standard.A1.Flex"

  shape_config {
    ocpus         = var.ocpus
    memory_in_gbs = var.memory_gb
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs = 50 # Always Free covers 200 GB of block storage in total
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.demo.id
    assign_public_ip = true
    hostname_label   = "arxiv-lens"
  }

  metadata = {
    ssh_authorized_keys = trimspace(file(pathexpand(var.ssh_public_key_path)))
    # Readable from the instance metadata endpoint and stored in state: no secrets.
    user_data = base64encode(file("${path.module}/cloud-init.yaml"))
  }
}
