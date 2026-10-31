from django.urls import path

from . import views

app_name = "accounting"

urlpatterns = [
    path("payment-accounts/", views.PaymentAccountListView.as_view(), name="payment_accounts"),
    path("payment-accounts/new/", views.PaymentAccountCreateView.as_view(), name="payment_account_create"),
    path("payment-accounts/<int:pk>/edit/", views.PaymentAccountUpdateView.as_view(), name="payment_account_edit"),
    path("payment-accounts/<int:pk>/toggle/", views.PaymentAccountToggleView.as_view(), name="payment_account_toggle"),
    path("expenses/", views.expense_list, name="expenses"),
    path("expenses/categories/", views.expense_categories, name="expense_categories"),
    path("expenses/categories/quick/", views.expense_category_quick, name="expense_category_quick"),
    path("expenses/categories/<int:pk>/edit/", views.expense_category_edit, name="expense_category_edit"),
    path("expenses/categories/<int:pk>/toggle/", views.expense_category_toggle, name="expense_category_toggle"),
    path("expenses/<int:pk>/delete/", views.expense_delete, name="expense_delete"),
    path("income/", views.income_list, name="incomes"),
    path("income/categories/", views.income_categories, name="income_categories"),
    path("income/categories/quick/", views.income_category_quick, name="income_category_quick"),
    path("income/categories/<int:pk>/edit/", views.income_category_edit, name="income_category_edit"),
    path("income/categories/<int:pk>/toggle/", views.income_category_toggle, name="income_category_toggle"),
    path("income/<int:pk>/delete/", views.income_delete, name="income_delete"),
    path("transfers/", views.transfer_list, name="transfers"),
    path("transfers/<int:pk>/delete/", views.transfer_delete, name="transfer_delete"),
    path("chart/", views.chart, name="chart"),
    path("chart/new/", views.AccountCreateView.as_view(), name="account_create"),
    path("chart/<int:pk>/edit/", views.AccountUpdateView.as_view(), name="account_edit"),
    path("journal/", views.journal, name="journal"),
    path("journal/data/", views.journal_data, name="journal_data"),
    path("journal/new/", views.entry_create, name="entry_create"),
    path("journal/<int:pk>/", views.entry_detail, name="entry"),
    path("journal/<int:pk>/delete/", views.entry_delete, name="entry_delete"),
    path("statement/", views.statement, name="statement"),
    path("trial-balance/", views.trial_balance, name="trial_balance"),
    path("profit-and-loss/", views.profit_loss, name="profit_loss"),
    path("balance-sheet/", views.balance_sheet, name="balance_sheet"),
    path("cash-flow/", views.cash_flow, name="cash_flow"),
]
