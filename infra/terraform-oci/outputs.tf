output "public_ip" {
  value = oci_core_instance.demo.public_ip
}

output "demo_url" {
  value = "http://${oci_core_instance.demo.public_ip}:8082"
}

output "ssh" {
  value = "ssh -i ~/.oci/arxiv_lens_ssh ubuntu@${oci_core_instance.demo.public_ip}"
}
