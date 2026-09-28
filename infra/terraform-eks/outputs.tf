output "cluster_name" {
  value = module.eks.cluster_name
}

output "kubeconfig" {
  value = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region} --profile ${var.aws_profile}"
}

output "port_forward" {
  value = "kubectl port-forward -n arxiv-lens svc/query-service 8082:8082"
}
