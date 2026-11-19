# Terraform Remote State Bootstrap

This stack provisions Terraform remote state primitives:
- Encrypted/versioned S3 bucket for state
- DynamoDB lock table with PITR enabled

## Usage

```bash
cd infra/terraform/bootstrap
terraform init
terraform apply -var-file=environments/staging.tfvars.example
```

After bootstrap, initialize the main stack with backend config:

```bash
cd ../aws
terraform init -backend-config=environments/staging.backend.hcl
```
