variable "name_prefix" {
  type = string
}

variable "enable_cost_budget" {
  type = bool
}

variable "monthly_cost_budget_usd" {
  type = number
}

variable "budget_time_period_start" {
  type = string
}

variable "budget_actual_threshold_percent" {
  type = number
}

variable "budget_forecast_threshold_percent" {
  type = number
}

variable "budget_notification_emails" {
  type = list(string)
}

variable "budget_notification_topic_arns" {
  type = list(string)
}
