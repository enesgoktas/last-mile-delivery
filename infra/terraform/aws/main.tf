terraform {
  required_version = ">= 1.6.0"

  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(
      {
        Project     = var.project_name
        Environment = var.environment
        ManagedBy   = "terraform"
      },
      var.tags,
    )
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name_prefix = "${var.project_name}-${var.environment}"
  azs         = length(var.availability_zones) > 0 ? var.availability_zones : data.aws_availability_zones.available.names
}
