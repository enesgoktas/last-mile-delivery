module "storage" {
  source = "./modules/storage"

  name_prefix      = local.name_prefix
  s3_force_destroy = var.s3_force_destroy
  tags             = var.tags
}
