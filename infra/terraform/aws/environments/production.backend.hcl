# Terraform remote state backend config for production.
# Populate with real values in your environment before running terraform init.
bucket         = "last-mile-delivery-terraform-state"
key            = "production/aws/terraform.tfstate"
region         = "us-east-1"
dynamodb_table = "last-mile-delivery-terraform-locks"
encrypt        = true
