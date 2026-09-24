terraform {
  required_version = ">= 1.6"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 6.0"
    }
  }
}

# Reads ~/.oci/config, the file `oci setup config` writes. Nothing secret here.
provider "oci" {
  config_file_profile = var.oci_profile
  region              = var.region
}
