output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "ecs_subnet_ids" {
  value = var.enable_nat_gateway ? aws_subnet.private[*].id : aws_subnet.public[*].id
}

output "ecs_assign_public_ip" {
  value = var.enable_nat_gateway ? false : true
}
