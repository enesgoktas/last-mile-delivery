output "alb_dns_name" {
  value = aws_lb.api.dns_name
}

output "alb_arn_suffix" {
  value = aws_lb.api.arn_suffix
}

output "api_target_group_arn_suffix" {
  value = aws_lb_target_group.api_blue.arn_suffix
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "api_service_name" {
  value = aws_ecs_service.api.name
}

output "worker_service_name" {
  value = aws_ecs_service.worker.name
}

output "api_codedeploy_app_name" {
  value = try(aws_codedeploy_app.api[0].name, null)
}

output "api_codedeploy_deployment_group_name" {
  value = try(aws_codedeploy_deployment_group.api[0].deployment_group_name, null)
}

output "api_task_definition_family" {
  value = aws_ecs_task_definition.api.family
}

output "worker_task_definition_family" {
  value = aws_ecs_task_definition.worker.family
}
