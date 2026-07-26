# QS-002 Checkout Integrity Contract

## Scope

QS-002 covers the desktop checkout path and the authenticated `POST /api/sales/`
endpoint. It does not redesign invoices, quotations, subscription payments, or the
separate web `/cash-register/` workflow. QS-001 idempotency remains mandatory.

## Current Architecture Audit

### Desktop sequence before QS-002

```text
Scanner/manual selection
  -> Cash_register_GUI.add_to_cart_logic()
  -> Cash_register_GUI._current_checkout_totals()
  -> Cash_register_GUI.process_sale()
  -> build flat payload with InventoryGUI._build_sale_payload()
  -> POST /api/sales/ or append RECORD_SALE to pending_sync_<user>.json
  -> validate local item existence, quantity, and stock
  -> deduct local inventory
  -> adjust local customer credit
  -> append local receipt
  -> clear cart
```

The transmission/queue step currently occurs before the final local stock and
quantity checks. A checkout rejected by the desktop can therefore already exist on
the server or in the replay queue.

### Backend sequence before QS-002

```text
POST /api/sales/
  -> DRF token/subscription authentication
  -> _record_desktop_sale(request)
  -> ad hoc request.data validation (no serializer)
  -> transaction.atomic()
     -> lock tenant-owned location
     -> return prior sale for (owner, offline_client_ref), if any
     -> load tenant-owned items by SKU
     -> lock location StockRecord rows
     -> validate quantity, stock, price, and discount
     -> finalize_sale()
        -> allocate receipt_no
        -> create Sale
        -> create SaleItem rows
        -> SaleItem.save() deducts stock
        -> recalculate Sale totals
        -> update CashShift total when a shift was supplied
     -> update customer_name
  -> transaction exits and commits
  -> return acknowledgement
```

The direct desktop endpoint does not currently validate operator role/status,
location access, register session, item status/deletion, client totals, client tax,
amount tendered, customer ownership, or checkout payload version. It does not attach
a `CashShift`, set `Sale.amount_paid`/`change_due`, or create a success audit record.

### Persistence model reality

- A POS payment is represented by `Sale.tender`, `Sale.amount_paid`, and
  `Sale.change_due`. The `Payment` model is for subscription-provider payments and
  must not be used for checkout.
- A receipt is represented by the atomically allocated `Sale.receipt_no`; there is no
  separate receipt row for the direct desktop sale.
- Sale lines are `SaleItem` rows.
- Inventory effects are `StockRecord.quantity` updates made by `SaleItem.save()`.
- A successful checkout audit is an `AuditLog` row.

## Existing Partial-Commit and Validation Risks

| Risk | Current location | Required correction |
| --- | --- | --- |
| Remote commit before desktop validation | `Cash_register_GUI.process_sale()` | Validate once before POST or queue creation |
| Negative manual quantity can enter cart | `manual_add()` / `add_to_cart_logic()` | Reject during add and again at checkout |
| Invalid discount text becomes zero | `_current_checkout_totals()` | Return a validation error, never silently coerce |
| Underpayment can complete locally | `process_sale()` | Validate tender plus account credit against total |
| Cash change is also credited to customer | `process_sale()` | Cash overage is change; only non-cash overage is credit |
| Cache drops item ID/status/deletion/tax flags | `_normalize_items()` | Preserve authoritative inventory metadata |
| Client tax math differs from SaleItem math | Desktop totals | Treat shelf price as tax-inclusive, matching the model |
| Backend accepts archived/deleted products | `_record_desktop_sale()` | Require active, non-deleted tenant items |
| Backend ignores submitted totals and tax | `_record_desktop_sale()` | Recompute and compare to cent precision |
| Backend accepts no register | `_record_desktop_sale()` | Require the cashier/location CashShift context |
| Backend stores no payment amounts | `_record_desktop_sale()` | Set payment fields inside the sale transaction |
| Success audit is outside/absent | `_record_desktop_sale()` | Create audit before the outer transaction commits |
| Validation errors are unstructured | API response | Return stable code, field, message, and legacy error text |

All current backend sale, line, stock, and receipt-number writes are inside the outer
`transaction.atomic()` block. The existing commit point is the successful exit from
that block. QS-002 keeps that boundary and moves every validation before the first
write inside it.

## Authoritative Payload Version 1

```json
{
  "contract_version": 1,
  "offline_client_ref": "DESKTOP-uuid",
  "occurred_at": "2026-07-22T12:00:00+00:00",
  "operator": {"id": 42, "role": "cashier"},
  "tenant": {"id": 7},
  "location": {"id": 3},
  "register": {"id": 19, "location_id": 3, "opened_at": "..."},
  "customer": {"id": null, "name": ""},
  "currency": "JMD",
  "payment": {
    "method": "cash",
    "amount_tendered": "500.00",
    "account_credit": "0.00",
    "change_due": "40.00",
    "credited_overpayment": "0.00"
  },
  "totals": {
    "subtotal": "400.00",
    "discount": "0.00",
    "tax": "60.00",
    "total": "460.00"
  },
  "tax": {"label": "GCT", "rate": "0.1500", "inclusive": true},
  "line_items": [
    {
      "product_id": 11,
      "sku": "ITEM-001",
      "quantity": 2,
      "unit_price": "230.00",
      "unit_cost": "150.00",
      "is_taxable": true,
      "tax_rate": "0.1500",
      "status": "active",
      "is_deleted": false
    }
  ],
  "metadata": {
    "source": "desktop",
    "queue_schema_version": 1
  }
}
```

Money has two decimal places. Tax rate has at most four decimal places and is a
fraction (`0.1500` means 15 percent). Item shelf prices are tax-inclusive. The
identity of operator and tenant is derived from authentication and compared with the
payload; the client cannot select another identity.

## Validation Map

| Contract area | Desktop preflight | Backend authority |
| --- | --- | --- |
| Contract | Version, shape, reference, queue schema | Repeat all shape/version/reference checks |
| Operator | Logged in, active cached status, seller role | Authenticated active user, active profile, seller role, matching ID/tenant |
| Location | Selected cached tenant location | Tenant ownership and `can_be_accessed_by()` |
| Register | Cached open shift assigned to operator/location | Locked `CashShift`, matching operator/location, occurrence within shift interval |
| Cart | Non-empty, unique product/SKU, preserved metadata | Maximum lines and unique product/SKU |
| Item | ID/SKU, active, not deleted, quantity, price/cost/tax | Tenant item lookup by ID and SKU, active/not deleted, canonical price/cost/tax |
| Stock | Cached availability for usability | Locked `StockRecord` quantity |
| Totals | Recompute net subtotal, tax, discount, total | Recompute from canonical server item data and compare exactly |
| Payment | Supported method, funding, change/credit rules | Repeat using canonical total; persist payment fields atomically |
| Customer | Required for account credit/non-cash overage | Locked tenant-owned Customer and sufficient credit |
| Idempotency | Stable `offline_client_ref` | Unique `(owner, sync_token)` and verified acknowledgement |

Validation failures use HTTP 4xx and this backward-compatible response shape:

```json
{
  "ok": false,
  "success": false,
  "code": "INVALID_TOTAL",
  "field": "totals.total",
  "message": "Total does not match the calculated amount.",
  "error": "Total does not match the calculated amount."
}
```

## Target Transaction and Commit Point

```text
parse contract (no writes)
  -> authenticate and authorize operator (no writes)
  -> transaction.atomic()
     -> lock location, register, customer, stock
     -> repeat all business validation (no writes)
     -> check idempotency
     -> create Sale and receipt_no
     -> create SaleItem rows and deduct StockRecord quantities
     -> set embedded payment totals
     -> apply customer credit effects, when present
     -> update CashShift totals
     -> create AuditLog
  -> COMMIT on successful outer transaction exit
  -> acknowledgement
```

Any exception or failed post-write invariant sets rollback or exits by exception. No
Sale, SaleItem, stock deduction, payment fields, receipt number, customer-credit
change, shift total, or AuditLog may survive.

## Implemented Files

- `Desktop_UI/Inventory_gui.py`: preserve item/session metadata, build and validate
  contract v1, and move preflight ahead of transmission and local effects.
- `Desktop_UI/tests/test_checkout_integrity.py`: pure desktop contract and cart
  preservation regression tests.
- `Website/BackEnd/quickstock/api/checkout_contract.py`: payload parsing, decimal
  precision, and structured contract errors.
- `Website/BackEnd/quickstock/api/views.py`: authoritative validation, register and
  tenant checks, canonical totals, atomic payment/credit/audit effects.
- `Website/BackEnd/quickstock/inventory/sales.py`: pass the validated location tax
  rate into each SaleItem while preserving existing callers.
- `Website/BackEnd/quickstock/api/tests.py`: API rejection, rollback, tenant,
  authorization, payment, tax, and duplicate tests.
- `SYNC_IMPLEMENTATION_GUIDE.md`: link QS-001 replay to the validated QS-002 payload.

## Regression Risks

- Legacy queued sales do not contain enough payment, register, product-ID, or tax
  evidence to prove validity. They must be enriched from current trusted cache/session
  data or retained as dead letters for manual review; silently guessing is prohibited.
- Desktop sales now require an open server-issued register snapshot. Operators without
  one are blocked rather than creating unassigned financial records.
- Tax-inclusive shelf-price calculation changes desktop previews that previously added
  tax on top of shelf price when `tax_inclusive` was false.
- Stale desktop item price, cost, status, customer, location, or register data causes a
  safe rejection and requires synchronization.
- The web `/cash-register/` endpoint remains a separate flow and is not migrated in
  this ticket.

## Acceptance Verification

- [x] Validation runs before desktop transmission, queue creation, local deduction,
  customer-credit adjustment, or receipt append.
- [x] Backend recomputes every financial value from tenant-owned canonical records.
- [x] Invalid checkout has zero persistent effects across all checkout tables and balances.
- [x] Valid checkout commits sale, lines, stock, payment fields, receipt number, shift
  total, customer-credit effects, and audit together.
- [x] Duplicate replay retains QS-001 exactly-once behavior.
- [x] Desktop, focused API, and prior QS-001 regression tests pass.
- [x] The known WiPay date-boundary test is the only unrelated full-suite failure.
- [x] QS-003 was not started.

The post-write rollback test forces `AuditLog.objects.create()` to fail after the
sale, sale lines, stock deduction, shift update, payment fields, receipt allocation,
and customer-credit update have executed. The response is a structured validation
failure and every database effect is absent after the outer transaction exits.

## Verification Evidence

Verification date: 2026-07-22

| Scope | Command | Result |
| --- | --- | --- |
| Desktop QS-001/QS-002 focused | `venv/bin/python -m pytest -q Desktop_UI/tests/test_checkout_integrity.py Desktop_UI/tests/test_offline_sale_queue.py` | PASS: 35 |
| API checkout/sync focused | `python3 manage.py test api.tests.SyncPullInventoryTests --verbosity 2` | PASS: 23 |
| Full desktop regression | `venv/bin/python -m pytest -q Desktop_UI/tests` | PASS: 52 |
| Full backend regression | `python3 manage.py test --verbosity 1` | 200 PASS, 1 unrelated FAIL |

The sole backend failure is
`WeekOneSecurityTests.test_wipay_success_preserves_checkout_billing_cycle_and_reference_redirect`.
At the current date boundary, the implementation produced `2026-08-22` while the
test expected `2026-08-21`. No QS-002 test failed.

## Backward Compatibility

- Contract-v1 queue entries retain the QS-001 queue schema, stable
  `offline_client_ref`, retry metadata, and acknowledgement rules.
- Existing flat queue files are still read, backed up as `*.v0.bak`, upgraded with
  durable queue metadata, and never discarded.
- A legacy sale without enough register, operator, payment, product, tax, or total
  evidence is retained as a `dead_letter`. It is not silently guessed or transmitted.
- Existing non-sale queue actions keep their current payload and replay behavior.
- The web `/cash-register/` workflow remains outside QS-002 and is unchanged.

## Rollback Considerations

QS-002 adds no database migration. A code rollback must deploy the desktop and API
together. Contract-v1 sale entries should remain queued while an older API is active,
because the older endpoint cannot verify the v1 acknowledgement and integrity
contract. Preserve all pending queue files and their backups during rollback.

## Remaining Risks

- Production database concurrency still requires release-gate verification on the
  configured production database engine; the automated suite currently runs on the
  Django test database.
- Legacy flat sale dead letters require manual review or a separately validated
  migration procedure.
- Stale desktop item, customer, register, location, or tax data intentionally blocks
  checkout until synchronization succeeds.
- The separate web cash-register workflow has its own validation path and was not
  unified under this ticket.

## Completion Decision

**QS-002: PASS.** The checkout-integrity contract is implemented, regression tested,
and acceptance verified. QS-003 remains unstarted.
