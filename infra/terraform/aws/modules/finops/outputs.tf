output "budget_name" {
  value = try(aws_budgets_budget.monthly_total[0].name, null)
}
