variable "name_prefix" {
  type = string
}

variable "s3_force_destroy" {
  type = bool
}

variable "tags" {
  type    = map(string)
  default = {}
}
