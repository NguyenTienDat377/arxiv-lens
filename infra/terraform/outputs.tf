output "public_ip" {
  value = vultr_instance.demo.main_ip
}

output "demo_url" {
  value = "http://${vultr_instance.demo.main_ip}:8082"
}

output "ssh" {
  value = "ssh -i ~/.oci/arxiv_lens_ssh root@${vultr_instance.demo.main_ip}"
}
