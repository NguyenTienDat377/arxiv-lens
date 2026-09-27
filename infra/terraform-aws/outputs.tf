output "instance_id" {
  value = aws_instance.demo.id
}

output "public_ip" {
  value = aws_eip.demo.public_ip
}

output "demo_url" {
  value = "http://${aws_eip.demo.public_ip}:8082"
}

output "ssh" {
  value = "ssh -i ${pathexpand(trimsuffix(var.ssh_public_key_path, ".pub"))} ubuntu@${aws_eip.demo.public_ip}"
}

output "stop" {
  value = "aws ec2 stop-instances --instance-ids ${aws_instance.demo.id} --region ${var.region} --profile ${var.aws_profile}"
}

output "start" {
  value = "aws ec2 start-instances --instance-ids ${aws_instance.demo.id} --region ${var.region} --profile ${var.aws_profile}"
}
