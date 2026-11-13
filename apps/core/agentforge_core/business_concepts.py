"""Standard business concepts - the common language between role agents and company databases.

Role agents (Treasury, Accounts Receivable ...) are written ONLY in these terms ("customer payments",
"bank statement lines"), never in one company's table names. Each connected company database has a data dictionary
that says where every concept lives in THAT company's tables and columns (services/data_sources.py):

    "customer_payment": {"table": "receipts", "columns": {"amount": "amount_inr", "date": "received_on", ...},
                         "notes": "TDS deducted by the customer is in tds_deducted_inr"}

So the same agent works on Brightline (receipts), a Tally export (VOUCHERS with VCH_TYPE='Receipt') or SAP (BSEG) -
only the dictionary changes. `fields` are the usual columns; a company maps the ones it has and explains gaps in notes.
"""

from __future__ import annotations

from typing import Any

CONCEPTS: dict[str, dict[str, Any]] = {
    # ----- parties
    "customer": {"label": "Customers", "fields": ["id", "name", "state", "tax_id", "credit_limit"],
                 "about": "Who the company sells to."},
    "vendor": {"label": "Vendors / suppliers", "fields": ["id", "name", "tax_id", "bank_account", "is_msme"],
               "about": "Who the company buys from."},
    "employee": {"label": "Employees", "fields": ["id", "name", "role", "joined_on", "exited_on"],
                 "about": "Staff, including drivers if they are employees."},
    # ----- sales side
    "invoice": {"label": "Sales invoices", "fields": ["id", "customer", "date", "due_date", "amount", "tax", "status"],
                "about": "Bills sent to customers."},
    "invoice_balance": {"label": "Open amount per invoice", "fields": ["invoice", "amount", "paid", "open_amount"],
                        "about": "What is still unpaid on each invoice (a view or a calculation)."},
    "customer_payment": {"label": "Money received from customers",
                         "fields": ["id", "date", "amount", "customer", "payer_name", "reference", "method", "bank_account"],
                         "about": "Receipts as recorded by the company (bank/UPI/cheque/cash)."},
    "payment_allocation": {"label": "Which payment paid which invoice",
                           "fields": ["payment", "invoice", "amount_applied"],
                           "about": "Links customer payments to invoices (knock-off / application)."},
    "credit_note": {"label": "Credit notes", "fields": ["id", "invoice", "customer", "date", "amount", "reason"]},
    "refund": {"label": "Refunds paid to customers", "fields": ["id", "customer", "date", "amount", "reason", "approved_by"]},
    # ----- buying side
    "vendor_invoice": {"label": "Vendor bills", "fields": ["id", "vendor", "number", "date", "due_date", "amount", "tax"]},
    "vendor_payment": {"label": "Payments made to vendors",
                       "fields": ["id", "vendor", "vendor_invoice", "date", "amount", "tds", "to_account", "approved_by"]},
    "purchase_order": {"label": "Purchase orders", "fields": ["id", "vendor", "date", "item", "quantity", "rate"]},
    "goods_receipt": {"label": "Goods received", "fields": ["id", "purchase_order", "date", "item", "quantity"]},
    # ----- cash and bank
    "bank_account": {"label": "Bank accounts", "fields": ["id", "bank", "ledger_account", "min_balance", "overdraft_limit"],
                     "about": "Each bank account and the ledger (GL) account that mirrors it in the books."},
    "bank_statement_line": {"label": "Bank statement lines",
                            "fields": ["id", "bank_account", "date", "description", "reference", "debit", "credit", "balance"],
                            "about": "What the BANK says happened (money in = credit, money out = debit)."},
    "gateway_settlement": {"label": "Payment gateway settlements",
                           "fields": ["id", "date", "gross", "fee", "tax_on_fee", "net", "bank_reference"],
                           "about": "Card/UPI gateway payouts to the bank, net of fees."},
    "gateway_settlement_item": {"label": "Payments inside a gateway settlement", "fields": ["settlement", "payment", "amount"]},
    "cash_collection": {"label": "Cash collected (e.g. cash on delivery)",
                        "fields": ["id", "collector", "date", "amount", "invoice", "deposit"]},
    "cash_deposit": {"label": "Cash deposited into the bank", "fields": ["id", "collector", "date", "amount", "bank_account"]},
    "loan": {"label": "Loans", "fields": ["id", "lender", "principal", "rate", "emi", "covenants"]},
    "loan_instalment": {"label": "Loan instalments (EMIs)",
                        "fields": ["loan", "number", "due_date", "amount", "paid_on", "penalty"]},
    # ----- books
    "ledger_entry": {"label": "Journal entries (headers)",
                     "fields": ["id", "date", "period", "type", "narration", "created_by", "approved_by", "has_document"]},
    "ledger_line": {"label": "Journal lines (debits/credits per account)",
                    "fields": ["entry", "account", "debit", "credit", "party"],
                    "about": "The books. A bank's ledger account shows the company's view of that bank."},
    "ledger_account": {"label": "Chart of accounts", "fields": ["code", "name", "type"]},
    "policy": {"label": "Company rules and limits", "fields": ["name", "value"],
               "about": "Approval limits, fees, payment terms, deadlines - if the company keeps them in a table."},
    "approval": {"label": "Approvals", "fields": ["document", "approver", "amount", "approved_at", "limit"]},
    # ----- operations
    "order": {"label": "Sales orders", "fields": ["id", "customer", "date", "status", "amount"]},
    "shipment": {"label": "Shipments / deliveries", "fields": ["id", "order", "status", "delivered_at", "damaged"]},
    "product": {"label": "Products / services", "fields": ["id", "name", "price", "stock", "reorder_level"]},
}


def concept_names() -> list[str]:
    return sorted(CONCEPTS)


def describe_concepts() -> list[dict[str, Any]]:
    """For the 'Data dictionary' editor: every concept with its usual fields."""
    return [{"name": name, **info} for name, info in sorted(CONCEPTS.items())]
