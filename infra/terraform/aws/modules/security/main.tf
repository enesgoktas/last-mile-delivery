resource "aws_security_group" "alb" {
  name_prefix = "${var.name_prefix}-alb-"
  description = "ALB ingress for public HTTP/HTTPS traffic"
  vpc_id      = var.vpc_id

  #tfsec:ignore:aws-ec2-no-public-ingress-sgr Internet-facing entrypoint.
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_ingress_cidrs
  }

  #tfsec:ignore:aws-ec2-no-public-ingress-sgr Internet-facing entrypoint.
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_ingress_cidrs
  }

  egress {
    from_port   = var.api_container_port
    to_port     = var.api_container_port
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-alb"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "ecs_service" {
  name_prefix = "${var.name_prefix}-ecs-"
  description = "ECS services security group"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = var.api_container_port
    to_port         = var.api_container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  # DNS resolution through the VPC resolver.
  egress {
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # Application traffic to private data stores.
  egress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  #tfsec:ignore:aws-ec2-no-public-egress-sgr Required for AWS API endpoints without VPC endpoints.
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-ecs"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "rds" {
  name_prefix = "${var.name_prefix}-rds-"
  description = "PostgreSQL access from ECS tasks"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_service.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-rds"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "redis" {
  name_prefix = "${var.name_prefix}-redis-"
  description = "Redis access from ECS tasks"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_service.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-redis"
  })

  lifecycle {
    create_before_destroy = true
  }
}
