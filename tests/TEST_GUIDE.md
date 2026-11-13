# Brightline Logistics: finance test database

A **fictional** logistics company in Bengaluru with complete finance data for testing AI agents across 12 finance roles. All people, companies, GSTINs, PANs and bank numbers are made up.

- **Period:** 1-Jul-2026 to **12-Nov-2026**. Treat 2026-11-12 as "today" for aging, due dates and alerts.
- **Fiscal year:** 2026-27 (Apr-Mar). Amounts in INR.
- **Size:** 73 tables + 2 views, about 8,300 rows, **120 planted problems** with expected answers in the `test_scenarios` table.

## Load it

```bash
createdb brightline_finance
psql -d brightline_finance -f brightline_finance_db.sql
```

The file drops and recreates its own tables, so load it into its **own database** rather than your platform database. Then point a `data_sources` entry at it.

This file replaces `company_business_db.sql`. It contains the same customers, orders, shipments and tickets, but `invoices` has a richer layout (GST split, IRN, contract invoices).

## What's consistent (so agents can cross-check)

- **Double-entry books:** every invoice, receipt, bill, payment, payroll, EMI and tax payment has a journal entry, and debits = credits. `v_trial_balance` balances.
- **AR ledger ties out:** GL account 1200 = open invoice balances (`v_invoice_balances`).
- **Bank statement vs books:** differences between `bank_transactions` and the bank GL accounts (1101, 1102, 2301, 1104) are **exactly** the planted reconciliation items.
- **Linked records:** payslips match attendance, attendance matches driver logs, fuel bills match driver logs (except the planted week), and GST returns match invoices and bills.

**Simplifications:** all services are taxed at 18% GST; receivables and payables start at zero on 1-Jul; income tax on salary uses a simplified new-regime calculation.

## Key ids

| Role | Employee | Name |
|---|---|---|
| CFO / Finance Head | E002 | Ananya Rao |
| Financial Controller | E003 | Rohan Gupta |
| Treasury / Transaction Manager | E004 | Vikram Shetty |
| Accounts Receivable / Collections | E005 | Neha Sharma |
| Accounts Payable | E006 | Arjun Iyer |
| Billing / Invoicing | E007 | Sneha Kulkarni |
| General Ledger Accountant | E008 | Rahul Mehta |
| Tax & Compliance | E009 | Meera Joshi |
| FP&A Analyst | E010 | Kavya Menon |
| Internal Auditor / Risk & Controls | E011 | Farhan Khan |
| Payroll & Expenses | E012 | Lakshmi Pillai |
| Credit Controller | E013 | Dhruv Hegde |

Bank accounts: 1 HDFC (collections, GL 1101) · 2 ICICI (payments, GL 1102) · 3 SBI overdraft (GL 2301) · 4 Kotak FD (GL 1104). Business customers are ids 1-12; contract customers are 1-11. Rules and limits live in `finance_policies` and `approval_matrix`.

## Tables by role

### CFO / Finance Head
Cash position, KPIs, approvals above limits, ad-hoc questions. **Planted problems:** 1

**Tables:** `bank_transactions` (289), `bank_accounts` (4), `v_trial_balance`, `budgets` (348), `invoices` (160), `approvals` (105), `approval_matrix` (17), `refunds` (13), `loans` (2)

**Tasks you can test:**
- Daily cash position across 4 bank accounts
- Weekly KPI summary: DSO, collection rate, overdue %
- Monthly P&L / balance sheet from v_trial_balance
- Budget vs actual
- Profitability by customer / route
- Approvals above limit (refunds > 5,000, payments > 5 lakh)
- Ad-hoc questions ("revenue from Pune in Q2?")

### Financial Controller
Month-end close, journals, accruals, depreciation, controls. **Planted problems:** 10

**Tables:** `journal_entries` (610), `journal_lines` (1885), `accounting_periods` (5), `month_end_close_tasks` (76), `fixed_assets` (24), `asset_depreciation` (89), `goods_receipts` (8), `vendor_invoices` (84), `bank_accounts` (4)

**Tasks you can test:**
- Review journals and flag odd ones
- Close checklist for Oct-2026 (period still 'closing')
- Accruals: GRNI, electricity, FD interest
- Prepaid insurance amortisation
- Depreciation check incl. disposed asset
- Back-dated entries into closed periods
- Physical verification of fixed assets

### Treasury / Transaction Manager
Bank statements, payment matching, gateway & COD, loans, cash forecast. **Planted problems:** 18

**Tables:** `bank_transactions` (289), `receipts` (126), `receipt_allocations` (120), `invoices` (160), `gateway_settlements` (52), `gateway_settlement_items` (69), `cod_collections` (9), `cod_deposits` (8), `loans` (2), `loan_schedule` (144), `bank_accounts` (4), `finance_policies` (30)

**Tasks you can test:**
- Daily transaction & collections summary
- Match incoming payments to invoices (typo refs, lump sums, no ref, unknown payer)
- Duplicate / partial / over-payments
- Gateway settlements: arrived in full? fee = 1.8%?
- COD collected vs deposited per driver
- Bank reconciliation for each account (book = journal_lines on the bank GL code)
- Low-balance and large-payment alerts
- 13-week cash forecast from open AR, open AP, payroll, EMIs, GST/TDS dues
- Loan EMI tracking and covenant check

### Accounts Receivable / Collections
Overdue tracking, reminders, promises, statements. **Planted problems:** 9

**Tables:** `invoices` (160), `v_invoice_balances`, `receipts` (126), `receipt_allocations` (120), `payment_promises` (6), `collection_activities` (49), `credit_notes` (6), `customers` (60), `form16a_received` (8)

**Tasks you can test:**
- Aging report 0-30 / 31-60 / 61-90 / 90+ (today = 2026-11-12)
- Collections priority list
- Reminder emails by lateness level
- Broken payment promises
- Unapplied cash
- Statement of account per customer
- Disputed invoices
- DSO and collection rate

### Accounts Payable
Vendor bills, 3-way match, payment runs, vendor master. **Planted problems:** 12

**Tables:** `vendors` (17), `vendor_invoices` (84), `vendor_invoice_lines` (110), `purchase_orders` (8), `po_lines` (15), `goods_receipts` (8), `grn_lines` (15), `vendor_payments` (71), `vendor_bank_changes` (2), `vendor_statements` (31), `kyc_records` (29), `tds_certificates_issued` (8)

**Tasks you can test:**
- 3-way match PO / GRN / invoice (price and quantity)
- Duplicate invoice detection
- Weekly payment run list (due by Friday)
- AP aging
- MSME 45-day rule
- Vendor statement reconciliation
- New vendor and bank-change checks
- TDS on each payment

### Billing / Invoicing
Invoices from orders and contracts, GST, IRN, e-way bills, credit notes. **Planted problems:** 7

**Tables:** `orders` (120), `order_items` (239), `shipments` (115), `contracts` (11), `invoices` (160), `invoice_lines` (334), `credit_notes` (6), `shipment_finance` (115), `eway_bills` (22)

**Tasks you can test:**
- Every delivered order has an invoice
- Every contract billed every month at contract rate
- Invoice numbering gaps
- GST type by place of supply
- IRN present and within 30 days
- E-way bill where goods value > 50,000
- Credit notes for cancellations/disputes

### General Ledger Accountant
Journals, reconciliations, suspense, trial balance. **Planted problems:** 5

**Tables:** `journal_entries` (610), `journal_lines` (1885), `chart_of_accounts` (77), `cost_centres` (8), `v_trial_balance`

**Tasks you can test:**
- Trial balance (must balance)
- Clearing accounts at zero: 1150 gateway, 1160 COD, 1199 suspense, 2240 unapplied
- Tax accounts vs returns/challans
- Fixed asset register vs GL 1500-1590
- Recurring journals present each month

### Tax & Compliance
GST, TDS, e-invoicing, 26AS, notices, compliance calendar. **Planted problems:** 16

**Tables:** `customers` (60), `invoices` (160), `vendor_invoices` (84), `gstr2b_entries` (47), `gst_returns` (8), `tds_challans` (15), `tds_returns` (4), `form26as_entries` (8), `compliance_calendar` (14), `tax_notices` (2), `eway_bills` (22), `vendor_payments` (71)

**Tasks you can test:**
- GSTIN validation (check digit + state code)
- GST calculation per invoice
- GSTR-2B vs purchase register
- GSTR-1 / 3B status and due dates
- TDS section/rate per payment, deposit by the 7th
- Form 26AS vs TDS deducted by customers
- Notices with reply due

### FP&A Analyst
Revenue/profit analysis, budget vs actual, cost per delivery. **Planted problems:** 4

**Tables:** `journal_lines` (1885), `journal_entries` (610), `budgets` (348), `invoices` (160), `invoice_lines` (334), `shipment_finance` (115), `driver_daily_logs` (1481), `contracts` (11), `vendor_invoices` (84)

**Tasks you can test:**
- Revenue by region / customer / product / month
- Top customers and products
- Budget vs actual by account and month
- Profit per order and route
- Cost per delivery / per km / per driver
- Fuel billed vs fuel logged

### Internal Auditor / Risk & Controls
Fraud checks, approval limits, segregation of duties, access. **Planted problems:** 23

**Tables:** `approvals` (105), `approval_matrix` (17), `vendor_payments` (71), `vendor_bank_changes` (2), `refunds` (13), `journal_entries` (610), `payslips` (156), `employees` (40), `expense_claims` (93), `erp_access_log` (268), `insurance_claims` (6), `risk_register` (10), `audit_findings` (6), `kyc_records` (29), `cod_collections` (9)

**Tasks you can test:**
- Approval above authority / retro approval
- Self-approval (creator = approver)
- Split payments under a limit
- Weekend, late-night, round-amount transactions
- Duplicate payments
- Ghost employees
- Unusual refunds
- Suspicious logins
- KYC and risk register reviews

### Payroll & Expenses
Attendance, payslips, statutory deductions, claims, incentives. **Planted problems:** 9

**Tables:** `employees` (40), `attendance_monthly` (193), `payroll_runs` (4), `payslips` (156), `expense_claims` (93), `employee_advances` (3), `driver_daily_logs` (1481), `finance_policies` (30), `tds_returns` (4)

**Tasks you can test:**
- Recompute payslips from attendance + policies
- Driver incentives from driver_daily_logs
- Expense claims vs policy (limits, 60-day rule, duplicates)
- Claims vs driver logs
- Full & final for exited employees
- November payroll inputs (attendance_monthly month 2026-11)

### Credit Controller
Credit limits, risk scoring, holds, new applications. **Planted problems:** 6

**Tables:** `customer_credit_profiles` (12), `v_invoice_balances`, `customer_financials` (5), `credit_applications` (3), `payment_promises` (6), `kyc_records` (29)

**Tasks you can test:**
- Exposure vs limit per customer
- Credit-risk score from payment history + financials
- Overdue credit reviews
- New credit applications decision
- Hold / release recommendations

## Example test prompts

- "Give me the AR aging report as of today with the top 5 customers to chase."
- "Match all receipts from the last 30 days to invoices and list anything that doesn't match."
- "Do the HDFC bank reconciliation for October and explain every difference."
- "Check all vendor payments in October for fraud red flags."
- "Which delivered orders have not been invoiced?"
- "Reconcile GSTR-2B with our purchase register for Q2."
- "Recompute September payslips from attendance and policy and show differences."
- "Which customers are over their credit limit, and what do you recommend?"
- "What's left on the October close checklist, and who owns it?"

## How to grade an agent

Each row in `test_scenarios` names the role, task, table, record and **expected result**. Run the agent on a task, then check whether it found every scenario for that task. Results it reports that aren't in the table are either noise or real issues the generator created by chance; check those by hand.

```sql
SELECT role, task, record_ref, expected_result FROM test_scenarios WHERE role = 'Treasury / Transaction Manager';
```

## All planted problems

### CFO / Finance Head (1)

| # | Task | Where | What's wrong | Expected |
|---|---|---|---|---|
| 66 | Approvals above a limit (refunds over INR 5,000) | `refunds` 12 | Refund INR 61,948.82 approved by Support Head (limit INR 5,000). | Flag approval above authority; needs CFO. |

### Financial Controller (10)

| # | Task | Where | What's wrong | Expected |
|---|---|---|---|---|
| 3 | Physical check of fixed assets | `fixed_assets` FA-0023 | Asset marked 'not_found' at last physical verification but still on the books. | Flag for write-off or investigation. |
| 4 | Physical check of fixed assets | `fixed_assets` FA-0018, FA-0021 | Two assets last verified in March 2024 (over 2 years ago). | Flag as overdue for verification. |
| 40 | Book accruals (goods received not invoiced) | `goods_receipts` GRN for PO-2608 | Packaging received on 28-Oct but no vendor invoice yet. | Accrue INR 31,350 (+GST) to Goods Received Not Invoiced for October close. |
| 41 | Book accruals | `vendor_invoices` Bangalore Power Supply Co / Oct-2026 | October electricity bill not received by month-end close. | Accrue estimated electricity (~INR 70,000) for October. |
| 72 | Book accruals | `bank_accounts` 4 | FD interest accrued Sep-Oct (~INR 29,600) not booked. | Accrue FD interest to 1260 / 4201. |
| 74 | Calculate and post depreciation | `asset_depreciation` FA-0001 | Van sold on 15-Sep-2026 still has an October depreciation charge. | Remove October depreciation for disposed asset. |
| 75 | Book accruals and prepaid expenses | `journal_entries` Oct-2026 insurance amortisation | October insurance amortisation (INR 60,000) not posted. | Post amortisation before closing October. |
| 95 | Review journal entries and flag odd ones | `journal_entries` 608 | Self-approved manual journal. | Flag. |
| 96 | Final close sign-off | `journal_entries` 609 | Journal dated 31-Aug (closed period) was created on 15-Oct. | Flag back-dated entry into a closed period. |
| 99 | Run the close checklist | `month_end_close_tasks` 2026-10 | October close has overdue tasks (ICICI recon, prepaid amortisation, accruals, suspense, TB review...). | List pending/overdue close tasks with owners. |

### Treasury / Transaction Manager (18)

| # | Task | Where | What's wrong | Expected |
|---|---|---|---|---|
| 17 | Match incoming payments to invoices | `receipts` 23 | Remark has a typo (7O1O) so exact matching fails; real invoice is INV-7010. | Fuzzy-match to INV-7010; invoice currently shows unpaid but is actually paid. |
| 18 | Spot duplicate, partial or overpayments | `receipts` 26 | Customer paid INR 1,000 more than INV-7012. | Flag overpayment of 1,000 (sitting in Unapplied Receipts). |
| 19 | Find payments with no invoice reference | `receipts` 28 | Payment remark just says 'PAYMENT'. | Suggest INV-7016 (exact amount match for customer 4). |
| 21 | Spot duplicate payments | `receipts` 32, 33 | Customer 9 paid INV-7021 twice (3 days apart, different UTR). | Flag duplicate; refund or adjust against next invoice. |
| 22 | Bank reconciliation | `bank_transactions` CHQ2001522286977 | Cheque from customer 10 for INV-7022 bounced; books still show invoice paid. | Reverse receipt; invoice back to overdue; recover 590 charges. |
| 23 | Match incoming payments to invoices | `receipts` 40 | One payment covers two invoices (INV-7013 and INV-7025). | Match one receipt to both invoices. |
| 27 | Find payments with no invoice reference | `receipts` 48 | INR 47,200 from 'MAA DURGA ENTERPRISES', not a known customer; parked in Suspense. | Flag unidentified receipt; investigate and clear suspense. |
| 28 | Check gateway settlements arrived in full | `gateway_settlements` setl_20260903 | Gateway reports this settlement as paid but no credit reached HDFC. | Flag missing settlement; raise with gateway. Gateway clearing account (1150) will not be zero. |
| 29 | Check payment gateway charges against agreed rates | `gateway_settlements` setl_20261005 | Gateway charged 2.4% instead of the contracted 1.8%. | Flag excess fee of INR 143.00 (+GST) and claim refund. |
| 30 | Track cash-on-delivery money collected vs deposited | `cod_deposits` driver 9 on 2026-08-10 | Driver deposited INR 1,500 less than collected. | Flag short deposit of 1,500. |
| 31 | Track cash-on-delivery money collected vs deposited | `cod_collections` 7 | Driver 7 collected INR 4,836.82 COD cash but never deposited it. | Flag undeposited COD cash (> 3 day policy); follow up with driver. |
| 52 | Bank reconciliation | `vendor_payments` 65 | Cheque issued to Ganesh Roadlines, recorded in books, not yet presented to bank. | Show as uncleared cheque (legit reconciling item). |
| 71 | Track loan EMIs and interest dates | `loan_schedule` 101 | SBI vehicle loan EMI debited 3 days late with INR 590 penal charge; penal charge not booked in books. | Book penal charge; fix mandate/balance. |
| 73 | Bank reconciliation | `bank_transactions` Oct-2026 bank charges (HDFC 1,416 & ICICI 2,124) | October bank charges are in the bank statements but not booked. | Book INR 3,540 bank charges. |
| 92 | Alert on low balance | `bank_transactions` ICICI week of 21-Sep-2026 | Funding transfer was short; ICICI balance fell below the INR 5 lakh minimum. | Low-balance alert for ICICI. |
| 93 | Alert on large payments | `bank_transactions` Karnataka Motors RTGS 16-Oct; SafeHaul 03-Jul | Debits above INR 5 lakh. | Large-payment alerts. |
| 98 | Bank reconciliation | `journal_entries` 610 | Book-only credit to HDFC of 5,00,000 with no matching bank transaction. | Reconciling item; reverse journal. |
| 115 | Bank covenant compliance check | `loans` 1 | Compute DSCR / current ratio / debt-equity from the trial balance vs HDFC covenants. | Report covenant headroom for Q2. |

### Accounts Receivable / Collections (9)

| # | Task | Where | What's wrong | Expected |
|---|---|---|---|---|
| 20 | Report unapplied cash | `receipts` 30 | Recent receipt (within last 5 days) not yet applied; matches INV-7019. | Show as unapplied cash and apply it. |
| 24 | Apply received payments | `receipts` 42 | Short payment of INR 236 on INV-7032 (bank charges deducted by customer). | Invoice stays partially paid by 236; small-balance write-off decision. |
| 25 | Report unapplied cash | `receipts` 43 | Recent receipt (within last 5 days) not yet applied; matches INV-7034. | Show as unapplied cash and apply it. |
| 26 | Report unapplied cash | `receipts` 47 | Recent receipt (within last 5 days) not yet applied; matches INV-5532. | Show as unapplied cash and apply it. |
| 68 | Resolve invoice disputes | `invoices` INV-7031 | Customer 8 disputes Sep invoice (claims 2 damaged consignments); unpaid. | Track dispute; propose credit note; exclude from collection reminders. |
| 87 | TDS certificate (Form 16A) collection from customers | `form16a_received` customers 2, 7 | Q2 Form 16A not yet received. | Send reminders. |
| 89 | Alert when a promised payment date passes | `payment_promises` customer 7 | Customer 7 broke an earlier promise and the latest promise date (10-Nov) has passed without payment. | Alert AR; escalate; keep credit hold. |
| 90 | Aging report / collections priority list | `invoices` customer 5 | Customer 5 has paid nothing since its July invoice; every later invoice is overdue. | Top of collections priority; consider bad debt provision. |
| 91 | Apply payments / partial payments | `invoices` customer 11 | Customer 11 paid only 50% of Aug and Sep contract invoices. | Show as partially paid; chase balance. |

### Accounts Payable (12)

| # | Task | Where | What's wrong | Expected |
|---|---|---|---|---|
| 33 | Change a vendor's bank details | `vendor_bank_changes` 1 | Metro Packaging bank account changed on email request, no approval, no call-back verification; payments then went to the new account (domain looks spoofed). | High fraud risk: freeze payments, verify with vendor by phone. |
| 37 | 3-way match | `vendor_invoices` 21 | Tyre invoice has no PO although the vendor requires a PO. | Hold invoice; ask for PO or retrospective approval. |
| 38 | 3-way match / flag price mismatches | `vendor_invoices` 23 | Invoice rate INR 19,400 vs PO rate INR 18,500 for truck tyres. | Flag price variance INR 7,200 (+GST); hold payment. |
| 39 | 3-way match / flag quantity mismatches | `vendor_invoices` 26 | Billed 450 boxes but GRN shows only 400 received. | Flag 50-box quantity mismatch; ask for credit note (INR 2,100 + GST). |
| 42 | MSME vendors paid within 45 days | `vendor_invoices` Shield Security Services Aug-2026 | MSME vendor's August invoice on hold and unpaid for more than 45 days. | Flag MSME breach; resolve hold and pay (interest liability under MSMED Act). |
| 43 | Duplicate vendor invoice detection | `vendor_invoices` 77, 78 | Same vendor, same date, same amount; invoice numbers differ only by '/' vs '-'. | Flag as likely duplicate before payment. |
| 44 | Duplicate vendor invoice detection | `vendor_invoices` 79, 80 | Exact same invoice number entered twice; both were paid. | Flag duplicate payment; recover INR 21,995 from vendor. |
| 46 | Set up a new vendor (PAN, GSTIN, bank check) | `vendors` 16 | Vendor 'Sai Logistics Solutions' created 23-Oct, no GSTIN, no KYC, invoice next day, paid on a Saturday night. | Flag new-vendor risk; block until KYC done. |
| 49 | Change a vendor's bank details | `vendor_payments` 49 | Payment sent to Metro Packaging's NEW (unverified) bank account. | Recall/verify payment. |
| 84 | Issue TDS certificates (Form 16A) to vendors | `tds_certificates_issued` Q2 FY26-27 | Q2 Form 16A not yet issued (due 15-Nov-2026). | Remind / generate Q2 certificates. |
| 111 | Reconcile vendor statements with our books | `vendor_statements` vendor 4 | Vendor statement shows invoice AC/1029 (INR 41,300) not in our books. | Obtain copy and book (or dispute). |
| 112 | Reconcile vendor statements with our books | `vendor_statements` vendor 2 | Vendor statement does not show our September payment. | Send payment proof (UTR) to vendor. |

### Billing / Invoicing (7)

| # | Task | Where | What's wrong | Expected |
|---|---|---|---|---|
| 8 | Check every delivered order has an invoice | `orders` ORD-10057 | Order delivered but no invoice was raised. | List as delivered-not-invoiced; raise invoice. |
| 9 | Check every delivered order has an invoice | `orders` ORD-10010 | Order delivered but no invoice was raised. | List as delivered-not-invoiced; raise invoice. |
| 10 | Check every delivered order has an invoice | `orders` ORD-10051 | Order delivered but no invoice was raised. | List as delivered-not-invoiced; raise invoice. |
| 12 | Monthly invoices for contract customers | `contracts` 6 | Contract customer 6 was not billed for October 2026 (invoice due 31-Oct). | Flag missing contract invoice for Oct-2026. |
| 13 | Check invoices match contract rates | `invoices` INV-7016 | Billed pallets at INR 1600 instead of contract rate INR 1800. | Flag under-billing; raise supplementary (debit) invoice. |
| 15 | Generate e-invoices (IRN) | `invoices` INV-5597, INV-7021, INV-7020 | B2B invoices with no IRN (e-invoice not generated). | Flag; these invoices are not valid tax invoices until IRN is generated. |
| 100 | Generate e-way bills for shipments | `shipment_finance` 26, 45, 63 | Declared goods value > INR 50,000 but no e-way bill. | Flag missing e-way bills (penalty risk). |

### General Ledger Accountant (5)

| # | Task | Where | What's wrong | Expected |
|---|---|---|---|---|
| 116 | Clear suspense and holding accounts | `journal_lines` account 1199 | Suspense holds the Sunday-night INR 5,00,000 'adj' journal and the INR 47,200 unidentified receipt. | Suspense must be zero at close; investigate both items. |
| 117 | Balance sheet account reconciliations | `journal_lines` accounts 1150, 1160 | Gateway clearing (1150) and COD cash in transit (1160) are not zero. | 1150 = missing Sep settlement; 1160 = undeposited + short-deposited COD cash. |
| 118 | Clear suspense and holding accounts | `journal_lines` account 2240 | Unapplied customer receipts include a duplicate payment, an overpayment, typo/no-reference receipts and recent unapplied cash. | Apply, refund or reclass each item. |
| 119 | Reconcile GST, TDS and tax accounts with the books | `journal_lines` accounts 1220-1222 vs gstr2b_entries | ITC booked includes invoices missing from GSTR-2B. | Reverse ineligible ITC before filing. |
| 120 | Flag entries with missing documents | `journal_entries` je_type = manual, has_attachment = false | A manual journal has no supporting document. | Flag journal 'adj' (INR 5,00,000). |

### Tax & Compliance (16)

| # | Task | Where | What's wrong | Expected |
|---|---|---|---|---|
| 1 | Validate customer GSTINs | `customers` 5 | GSTIN has a wrong check digit (last character). | Flag customer 5 GSTIN as invalid. |
| 2 | Validate customer GSTINs | `customers` 9 | GSTIN state code 27 does not match customer state Gujarat. | Flag state-code mismatch for customer 9. |
| 11 | GST calculation per invoice (CGST/SGST/IGST by state) | `invoices` INV-5546 | Wrong GST type: customer is in Telangana but invoice charged CGST+SGST. | Flag; issue credit note and re-invoice with correct tax type. |
| 16 | E-invoicing checks | `invoices` INV-7017 | IRN generated 35 days after invoice date (limit is 30 days). | Flag e-invoice reporting delay. |
| 48 | Check correct TDS section and rate per vendor payment | `vendor_payments` 44 | No TDS deducted on CA professional fees (194J @10% applies). | Flag TDS short-deduction INR 4,000; pay with interest. |
| 76 | Match purchase invoices with GSTR-2B | `vendor_invoices` 36 | Invoice SMT/8989 (Swift Movers Transport) is in books but missing in GSTR-2B; ITC was still claimed. | Reverse/hold ITC; follow up with vendor to file. |
| 77 | Match purchase invoices with GSTR-2B | `vendor_invoices` 60 | Invoice SMT/1431 (Swift Movers Transport) is in books but missing in GSTR-2B; ITC was still claimed. | Reverse/hold ITC; follow up with vendor to file. |
| 78 | Match purchase invoices with GSTR-2B | `gstr2b_entries` SSS/2026/10-14 | Vendor reported taxable value 10% lower than our booked invoice. | Flag value mismatch. |
| 79 | Match purchase invoices with GSTR-2B | `gstr2b_entries` PH/2627/221 | Invoice in GSTR-2B from 'Prime Hydraulics' not found in our books. | Check if bill is missing in books or wrongly reported against our GSTIN. |
| 80 | GSTR-1 preparation | `gst_returns` GSTR-1 2026-10 | October GSTR-1 was due 11-Nov-2026 and is not filed. | Flag overdue return; late fee INR 50/day. |
| 81 | Compliance calendar | `gst_returns` GSTR-1 2026-09 | September GSTR-1 filed 2 days late. | Late fee INR 100 payable. |
| 82 | TDS deduction and filing | `tds_challans` 9 | TDS 194C for Sep paid on 2026-10-12 (due 2026-10-07); interest INR 118.19 paid. | Flag late deposit and interest cost. |
| 85 | Match the books with Form 26AS | `form26as_entries` customer 4 | Customer 4 deducted INR 6,464.00 TDS in Q2 but it is not in Form 26AS. | Follow up with customer to deposit/report TDS. |
| 86 | Match the books with Form 26AS | `form26as_entries` customer 9 | 26AS shows 15% less TDS than deducted per our receipts. | Raise mismatch with customer 9. |
| 88 | Reply to GST and income tax notices | `tax_notices` 1 | GST ASMT-10 reply due 17-Nov-2026, not yet replied. | Prepare reconciliation & reply. |
| 101 | E-way bill checks | `eway_bills` 831432334435 | E-way bill expired a day before delivery. | Flag expired e-way bill in transit. |

### FP&A Analyst (4)

| # | Task | Where | What's wrong | Expected |
|---|---|---|---|---|
| 35 | Cost per delivery / fuel check | `vendor_invoices` 16 | Fuel invoice litres are 8% higher than litres logged by drivers for the same week. | Flag fuel over-billing (compare with driver_daily_logs.fuel_litres). |
| 102 | Profit per order or route | `shipment_finance` long routes (Delhi, Kolkata, Jaipur, Lucknow) | Small individual parcels on 1,900+ km routes cost more than the delivery revenue. | Show loss-making routes; suggest minimum charge. |
| 113 | Budget vs actual | `budgets` 5101 Fuel 2026-10 | October fuel is ~25% over budget. | Highlight variance with explanation (fuel billing issue + volumes). |
| 114 | Budget vs actual | `budgets` 4101 Freight revenue 2026-09 | September freight revenue ~15% below budget. | Highlight shortfall. |

### Internal Auditor / Risk & Controls (23)

| # | Task | Where | What's wrong | Expected |
|---|---|---|---|---|
| 14 | Check for gaps or duplicates in invoice numbers | `invoices` INV-7015 | Invoice number INV-7015 is missing from the sequence. | Flag numbering gap for explanation. |
| 32 | COD money collected vs deposited, per driver | `cod_collections` driver 7 | Undeposited COD cash INR 4,836.82. | Fraud/control exception. |
| 34 | Alert on vendor bank detail changes | `vendor_bank_changes` 1 | Unapproved bank detail change followed by payment. | Raise fraud alert. |
| 36 | Fraud checks | `vendor_invoices` 16 | Fuel billed vs fuel logged mismatch (8%). | Investigate fuel vendor / pilferage. |
| 45 | Duplicate payment and duplicate vendor scan | `vendor_payments` invoices 79, 80 | Same vendor invoice paid twice. | Recover duplicate payment. |
| 47 | Approval-limit checks | `vendor_payments` 1 | Insurance payment INR 849,600 approved by the Financial Controller (limit INR 5,00,000). | Flag approval above authority; CFO approval needed. |
| 50 | Fraud checks (weekend / round-amount / new vendor) | `vendor_payments` 62 | Round-amount INR 3,85,000 paid on Saturday 21:40 to a vendor created 2 days earlier with no KYC. | Raise fraud alert. |
| 51 | Fraud checks (split payments) | `vendor_payments` 63, 64 | One INR 9.7 lakh invoice paid as two payments minutes apart, each just under the Controller's INR 5 lakh limit. | Flag split payment to avoid CFO approval. |
| 53 | Approval-limit checks (who approved what) | `approvals` 43 | Payment approved 4 days AFTER it was paid. | Flag retrospective approval. |
| 55 | Ghost employee check | `payslips` 118 | Aditya Verma (exited 31-Aug-2026) was paid September salary. | Flag ghost payment; recover in full & final settlement. |
| 63 | Segregation-of-duties check | `expense_claims` 93 | Financial Controller approved his own expense claim (and above lodging limit 3,500/night). | Flag self-approval; re-route to CFO. |
| 64 | Fraud checks (unusual refunds) | `refunds` 9 | Refund reason 'damaged' but shipment.damaged = false and no support ticket. | Flag unsupported refund. |
| 65 | Fraud checks (unusual refunds) | `refunds` 10, 11 | Two refunds on the same order, and the order was not delivered late. | Flag duplicate/ineligible refund. |
| 67 | Segregation-of-duties check | `refunds` 13 | Support agent requested and approved the same refund. | Flag self-approval. |
| 69 | Damage and insurance claims tracking | `insurance_claims` 1 | Claim filed 40 days after delivery (policy window 30 days). | Flag late claim; likely to be rejected. |
| 70 | Damage and insurance claims tracking | `shipments` 15 | Damaged shipment with no insurance claim filed. | File claim / record why not. |
| 94 | Segregation-of-duties check | `journal_entries` 608 | GL Accountant created and approved the same manual journal. | Flag self-approval. |
| 97 | Fraud checks (round amounts, weekend, no support) | `journal_entries` 610 | Round INR 5,00,000 moved from HDFC to Suspense on a Sunday at 23:48, narration 'adj', no attachment. | Flag high-risk journal. |
| 106 | Customer and vendor KYC checks | `kyc_records` customer 10 | Customer 10 KYC review was due 1-Jun-2026. | Flag overdue KYC review. |
| 107 | Update the risk register | `risk_register` 8 | Risk not reviewed since Dec 2025 (quarterly reviews expected). | Flag stale risk review. |
| 108 | Test key controls and write findings | `audit_findings` 1, 2, 4 | Open audit findings past their due date (the same control failures recur in this data). | Report overdue findings and repeat failures. |
| 109 | Monitor access logs for strange logins | `erp_access_log` 19-Oct-2026 02:07 | 6 failed logins then success for Rahul Mehta at 2 AM from a TOR IP. | Security alert; reset credentials; review entries posted after. |
| 110 | Monitor access logs for strange logins | `erp_access_log` E014 06-Sep-2026 | Exited employee logged in after exit date. | Disable account; review activity (links to the 6-Sep expense claim). |

### Payroll & Expenses (9)

| # | Task | Where | What's wrong | Expected |
|---|---|---|---|---|
| 54 | Payroll inputs (overtime) | `payslips` 109 | Paid 40 overtime hours but attendance shows 0. | Recover excess overtime. |
| 56 | Full and final settlement | `employee_advances` 1 | Exited employee E014 still has INR 30,000 advance outstanding. | Recover in full & final settlement (net of the ghost September salary). |
| 57 | Check employee expense claims vs policy | `expense_claims` 86 | Meal claim INR 1,850 vs policy limit INR 600/day. | Reject or approve only 600. |
| 58 | Flag duplicate or suspicious receipts | `expense_claims` 87, 88 | Same receipt number claimed twice, 3 weeks apart. | Reject duplicate claim. |
| 59 | Check driver fuel and toll claims against route | `expense_claims` 89 | Toll claimed by driver 4 on 18-Sep, a day with no driver log (driver absent). | Reject claim; investigate. |
| 60 | Check claims against route and distance | `expense_claims` 90 | 420 km claimed in one day for hub visits (Hosur-Tumakuru round trip is ~180 km); no receipt. | Query claim; approve max ~180 km. |
| 61 | Check employee expense claims vs policy | `expense_claims` 91 | Claim submitted 99 days after expense (policy: 60 days). | Reject as time-barred. |
| 62 | Check employee expense claims vs policy | `expense_claims` 92 | Claim by Aditya Verma for 6-Sep, after his exit date (31-Aug). | Reject; investigate who submitted it. |
| 83 | TDS on salary: 24Q return | `tds_returns` 4 | 24Q for Q2 was due 31-Oct-2026 and is not filed. | Flag overdue; late fee INR 200/day u/s 234E. |

### Credit Controller (6)

| # | Task | Where | What's wrong | Expected |
|---|---|---|---|---|
| 5 | Credit-limit review | `customer_credit_profiles` 3 | Credit limit last reviewed June 2024 (policy: every 12 months). | Flag customer 3 for overdue credit review. |
| 6 | Credit check for new business customers | `customer_credit_profiles` 12 | New business customer (onboarded Oct 2026) buying on credit with no credit check, no limit and no KYC. | Block credit or complete credit check before more orders. |
| 7 | Alert when a customer goes over their credit limit | `customer_credit_profiles` 5 | Customer 5 (very slow payer) owes about twice its credit limit and is not on hold. | Alert: exposure > limit; recommend credit hold. |
| 103 | Credit check for new business customers | `credit_applications` 2 | Applicant 2: 1 year old, bureau score 598, 2 defaults, no references, asks INR 25 lakh. | Recommend reject or advance payment only. App 1 approve; app 3 approve lower limit. |
| 104 | Request updated financial statements from large customers | `customer_financials` 2 | Latest financials for customer 2 are FY2023-24. | Request FY2025-26 financials. |
| 105 | Credit-risk score per customer | `customer_financials` 5 | Customer 5 made a loss with current ratio 0.82 and is paying 70+ days late. | Downgrade risk rating to D; reduce limit / hold. |
