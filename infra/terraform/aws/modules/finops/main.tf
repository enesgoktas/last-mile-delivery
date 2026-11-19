data "aws_caller_identity" "current" {}

locals {
  budget_notifications = [
    {
      notification_type = "ACTUAL"
      threshold         = var.budget_actual_threshold_percent
    },
    {
      notification_type = "FORECASTED"
      threshold         = var.budget_forecast_threshold_percent
    },
  ]

  budget_notifications_enabled = (
    length(var.budget_notification_emails) > 0 || length(var.budget_notification_topic_arns) > 0
  ) ? local.budget_notifications : []
}

resource "aws_budgets_budget" "monthly_total" {
  count = var.enable_cost_budget ? 1 : 0

  account_id        = data.aws_caller_identity.current.account_id
  name              = "${var.name_prefix}-monthly-total"
  budget_type       = "COST"
  limit_amount      = format("%.2f", var.monthly_cost_budget_usd)
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = var.budget_time_period_start

  dynamic "notification" {
    for_each = local.budget_notifications_enabled
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.notification_type
      subscriber_email_addresses = var.budget_notification_emails
      subscriber_sns_topic_arns  = var.budget_notification_topic_arns
    }
  }
}
