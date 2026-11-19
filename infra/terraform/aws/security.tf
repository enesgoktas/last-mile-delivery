module "security" {
  source = "./modules/security"

  name_prefix           = local.name_prefix
  vpc_id                = module.network.vpc_id
  vpc_cidr              = var.vpc_cidr
  api_container_port    = var.api_container_port
  allowed_ingress_cidrs = var.allowed_ingress_cidrs
  tags                  = var.tags
}
