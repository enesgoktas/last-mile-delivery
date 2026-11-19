module "finops" {
  source = "./modules/finops"

  name_prefix                       = local.name_prefix
  enable_cost_budget                = var.enable_cost_budget
  monthly_cost_budget_usd           = var.monthly_cost_budget_usd
  budget_time_period_start          = var.budget_time_period_start
  budget_actual_threshold_percent   = var.budget_actual_threshold_percent
  budget_forecast_threshold_percent = var.budget_forecast_threshold_percent
  budget_notification_emails        = var.budget_notification_emails
  budget_notification_topic_arns    = var.budget_notification_topic_arns
}
