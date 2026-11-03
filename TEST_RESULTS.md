# Backend and HTTP verification

Executed using Python 3.12.14. Test suite: `python -m unittest -v test_app`.

Result: **41 tests passed**, with the exact runner output below. No cases were skipped. Temporary databases and an ephemeral loopback HTTP server were used; the demonstration database was not modified.

## Coverage

- Decimal validation, finite/range restrictions, money precision, whole-item and kilogram quantities, half-up rounding.
- Empty/invalid carts, captured-price validation, stable-scale and unit checks, duplicate-product quantity aggregation.
- Atomic insufficient-stock rejection; rollback after an injected SQLite trigger failure.
- Identical checkout retry, request-ID collision, concurrent stock contention and concurrent identical checkout.
- Weighted-average receiving, zero-stock receiving, reference idempotency/collision and invalid receiving data.
- Demo-role denials, price optimistic versioning, recorded demo audit actors and audit visibility.
- Immutable historical price/cost/profit, inclusive UTC report boundaries, zero-sale products and invalid date ranges.
- Persistence after constructing a new Store over the same file, without reseeding depleted stock.
- Real HTTP endpoints: checkout, sales/receipt, inventory, receiving, price, report, audit; forbidden roles, malformed JSON, unsupported content type, cross-origin write rejection, unknown routes and static resources.

## Scope and limitations

These are backend/HTTP tests, not browser interaction or visual tests. They do not verify real peripherals, payment processing, production authentication, arbitrary hostile inputs or deployment security. Missing receipt IDs currently return HTTP 400 with a validation error; unknown routes return HTTP 404. That distinction is asserted, not disguised as REST-style resource 404 behavior.

The initial 37-test run passed. Three additional audit/static-serving tests were then added, and the 40-test run passed. An additional extreme-exponent regression test was added after a manual probe found an uncaught Decimal overflow; the implementation was corrected to use context-free absolute comparison, and the final 41-test run passed. The number-validation test also now covers that input.

## Browser verification status

JavaScript syntax verification (`node --check static/app.js`) passed. Live-browser visual/interaction verification and print-dialog verification were not executed: browser startup was blocked by an environment socket restriction, and the attempted environment setup also failed. These are unverified, not passed. Backend tests verify serving the HTML, CSS and JavaScript resources, but do not execute them.

## Exact final test output

```text
test_audit_endpoint_roles (test_app.HTTPTests.test_audit_endpoint_roles) ... ok
test_complete_http_workflow (test_app.HTTPTests.test_complete_http_workflow) ... ok
test_cross_origin_rejected (test_app.HTTPTests.test_cross_origin_rejected) ... ok
test_extreme_decimal_returns_validation_error (test_app.HTTPTests.test_extreme_decimal_returns_validation_error) ... ok
test_forbidden_roles (test_app.HTTPTests.test_forbidden_roles) ... ok
test_items_and_unknown_endpoint (test_app.HTTPTests.test_items_and_unknown_endpoint) ... ok
test_malformed_payload_and_content_type (test_app.HTTPTests.test_malformed_payload_and_content_type) ... ok
test_static_assets (test_app.HTTPTests.test_static_assets) ... ok
test_decimal_invalid (test_app.Numbers.test_decimal_invalid) ... ok
test_decimal_valid (test_app.Numbers.test_decimal_valid) ... ok
test_money_exact (test_app.Numbers.test_money_exact) ... ok
test_money_rejects_negative_and_extra_places (test_app.Numbers.test_money_rejects_negative_and_extra_places) ... ok
test_quantity_invalid (test_app.Numbers.test_quantity_invalid) ... ok
test_quantity_precision (test_app.Numbers.test_quantity_precision) ... ok
test_round_half_up (test_app.Numbers.test_round_half_up) ... ok
test_aggregate_duplicate_lines_stock (test_app.StoreTests.test_aggregate_duplicate_lines_stock) ... ok
test_audit_role_filtering (test_app.StoreTests.test_audit_role_filtering) ... ok
test_changed_price_requires_refresh (test_app.StoreTests.test_changed_price_requires_refresh) ... ok
test_checkout_receipt_and_stock (test_app.StoreTests.test_checkout_receipt_and_stock) ... ok
test_concurrent_checkout_no_oversell (test_app.StoreTests.test_concurrent_checkout_no_oversell) ... ok
test_concurrent_same_id_exactly_once (test_app.StoreTests.test_concurrent_same_id_exactly_once) ... ok
test_database_failure_rolls_back_inserted_sale (test_app.StoreTests.test_database_failure_rolls_back_inserted_sale) ... ok
test_duplicate_lines_success (test_app.StoreTests.test_duplicate_lines_success) ... ok
test_empty_checkout (test_app.StoreTests.test_empty_checkout) ... ok
test_historical_profit_immutable_after_cost_and_price (test_app.StoreTests.test_historical_profit_immutable_after_cost_and_price) ... ok
test_idempotent_sale_exact_payload (test_app.StoreTests.test_idempotent_sale_exact_payload) ... ok
test_persistence_recreate_store_no_reseed (test_app.StoreTests.test_persistence_recreate_store_no_reseed) ... ok
test_price_optimistic_version_audit (test_app.StoreTests.test_price_optimistic_version_audit) ... ok
test_report_inclusive_utc_dates_and_zero_items (test_app.StoreTests.test_report_inclusive_utc_dates_and_zero_items) ... ok
test_report_invalid_ranges (test_app.StoreTests.test_report_invalid_ranges) ... ok
test_request_collision_rejected (test_app.StoreTests.test_request_collision_rejected) ... ok
test_request_id_required (test_app.StoreTests.test_request_id_required) ... ok
test_restock_idempotency_and_collision (test_app.StoreTests.test_restock_idempotency_and_collision) ... ok
test_restock_invalid (test_app.StoreTests.test_restock_invalid) ... ok
test_restock_weighted_cost_and_audit (test_app.StoreTests.test_restock_weighted_cost_and_audit) ... ok
test_restock_zero_stock_uses_new_cost (test_app.StoreTests.test_restock_zero_stock_uses_new_cost) ... ok
test_roles_denied (test_app.StoreTests.test_roles_denied) ... ok
test_seed_six_items_no_sales (test_app.StoreTests.test_seed_six_items_no_sales) ... ok
test_stock_failure_atomic_multiple_items (test_app.StoreTests.test_stock_failure_atomic_multiple_items) ... ok
test_unit_and_quantity_invalid (test_app.StoreTests.test_unit_and_quantity_invalid) ... ok
test_weight_rounding_and_stable_required (test_app.StoreTests.test_weight_rounding_and_stable_required) ... ok

----------------------------------------------------------------------
Ran 41 tests in 4.155s

OK
```

## Static frontend integrity check

All 50 literal JavaScript element-ID references matched the HTML; all 53 HTML IDs were unique. This is a source-level check, not browser interaction verification.
