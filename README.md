# Freshline · Supermarket Automation Software

A working, local coursework demonstration: browser interface, Python API and persistent SQLite database. Currency is Indian rupees (INR); date-filtered accounting reports use UTC. No external runtime dependencies, cloud account or payment service is required.

## Start

Requires Python 3.10 or later and a modern browser. From this folder:

```sh
python app.py --port 8000 --db sas.db
```

On Windows you can alternatively double-click `start.bat` (requires the Python launcher); on macOS/Linux run `./start.sh`. If `python` is not available, try `python3` or Windows `py -3`.

Open http://127.0.0.1:8000 in the browser. Keep the terminal running; Ctrl+C stops the server. If port 8000 is busy, use another port, such as `--port 8001`. The database path can be changed to keep separate demonstrations. Parent folders of a custom path must already exist.

The first launch initializes six products and no sales. Restarting with the same database preserves stock, prices, deliveries, sales and audit records. Tests use temporary databases, not this file. For a fresh demonstration, stop the server and choose a new database filename. Back up the database while the application is stopped.

## Demonstration walkthrough

1. Keep **Sales clerk** selected. Enter barcode `1001` or code `MILK`, quantity `2`, and add to the bill. Products can also be selected with the shortcut buttons. `RICE` / barcode `1003` is sold in kilograms; try `0.375` with **Simulated scale stable** selected.
2. Complete the sale to save a receipt and deduct inventory atomically. This records a demonstration sale; it does not collect money. Open a recent receipt and use **Print receipt** to invoke the browser print dialog. Saving as PDF depends on the browser/operating system.
3. Switch to **Inventory employee**, open **Receive stock**, select a product, and enter a unique delivery reference, quantity and unit cost. Stock and weighted-average cost update together. Reusing exactly the same reference and details does not receive the delivery twice; conflicting details are rejected.
4. Switch to **Manager**. In **Inventory**, edit a selling price. In **Sales reports**, choose inclusive start/end dates and view quantities, revenue and gross profit by product, including products with zero sales. **Print report** uses browser printing.
5. Reload or restart the application using the same database file to check persistence.

The demo roles deliberately separate clerk checkout, employee receiving and manager price/report operations. Inventory is readable without a role. Audit API access is available to employees and managers; only managers receive the price-change log. The audit actor labels are `demo-employee` and `demo-manager`, not verified identities.

## Architecture and requirement mapping

- `static/index.html`, `static/style.css`, `static/app.js`: browser UI for checkout, inventory, goods receiving, reporting and printable receipts. Browser JavaScript submits JSON to the local API.
- `app.py`: standard-library HTTP server, request validation, role gates and domain operations. It binds only to `127.0.0.1`.
- SQLite `items`: product code and barcode lookup, selling price, stock, unit, average cost and price version.
- SQLite `sales` / `sale_lines`: uniquely identified receipts with immutable quantity, price, revenue and cost snapshots.
- SQLite `receipts`: delivery reference, quantity, unit cost, timestamp and demo actor; supports duplicate-receipt prevention.
- SQLite `price_audit`: old/new selling price, timestamp and demo actor.
- `test_app.py`: standard-library tests of arithmetic, transactions, authorization gates, persistence, concurrency and the real HTTP handler.

### API

- `GET /api/items`: catalogue and current stock.
- `POST /api/checkout`: clerk sale; requires request ID and item lines.
- `GET /api/sales`, `GET /api/sales/{serial}`: clerk receipt list/details.
- `POST /api/restock`: employee goods receipt with unique reference.
- `POST /api/price`: manager price change with current version.
- `GET /api/report?start=YYYY-MM-DD&end=YYYY-MM-DD`: manager report.
- `GET /api/audit`: employee/manager delivery audit; manager also receives price audit.

Requests use the `X-Demo-Role` header (`clerk`, `employee`, `manager`); writes require `application/json`. The UI sets that header from the role selector. This is a demonstration control, **not authentication**: a caller can choose any header value. Wrong-role API operations are rejected, but the system is not secure against an intentional local caller.

## Accounting, consistency and design decisions

- `each` quantities must be positive whole numbers. `kg` quantities must be positive and have at most three decimal places. Weighted checkout lines also require a stable simulated reading.
- Money inputs have at most two decimal places. Selling prices are stored as integer minor units (paise, named `price_cents` internally). Arithmetic uses Python `Decimal`. Each sale line's revenue and cost are rounded to minor units using **ROUND_HALF_UP**, and totals sum those rounded lines.
- Stock is stored as decimal text. Receiving computes `(old stock × old average cost + received quantity × new unit cost) / new stock`, rounded half up to six decimal places. The UI may display fewer cost decimals, but accounting uses the stored precision.
- Draft lines capture the selling price when added. **Intentional design restriction:** checkout revalidates that captured price against the current catalogue and rejects a stale price rather than honoring it silently. Remove and re-add the item after a price change. Successful sale lines retain their historical price and cost snapshots permanently; later price/cost changes do not rewrite historical gross profit.
- SQLite `BEGIN IMMEDIATE` serializes stock-changing transactions. All lines are validated and duplicate product quantities aggregated before stock deductions. A failed sale leaves neither partial stock changes nor partial receipt records.
- Checkout request IDs make identical retries idempotent; reusing an ID with a changed payload is rejected. Delivery references provide analogous duplicate protection. Equality is based on the submitted JSON content, not a broader semantic equivalence between differently formatted quantities.
- Manager price changes use optimistic version checking to prevent overwriting another edit. Sales and deliveries do not change that price version.
- Reports use inclusive UTC dates via a start-inclusive, next-day-exclusive interval. Gross profit is recorded revenue minus recorded stock cost, not net profit; taxes, discounts, refunds, overheads and payment reconciliation are not modeled.

## Explicit demonstration boundaries

Barcode entry is a keyboard simulation (a keyboard-wedge scanner might type into the field, but no physical scanner integration has been tested). The scale is simulated by quantity input plus a stable-reading checkbox. Printing is browser printing, not a directly connected till printer. No actual scale, scanner, payment terminal, card processing, payment gateway or cash drawer is integrated.

This is not production POS software. It has no user accounts, verified authentication, hardened authorization, encrypted remote transport, secrets management, production deployment, multi-store support or verified fiscal/tax compliance. Do not expose the server beyond the loopback interface or use real customer/payment data. The catalogue has six seeded items; product creation and supplier/customer administration are outside this implementation. Receipt lists and audit endpoints show the latest 25 records; date reports aggregate all matching saved sales. No external assets or runtime services are needed.

## Run tests

```sh
python -m unittest -v test_app
```

See `TEST_RESULTS.md` for the actual recorded test run and scope. Automated HTTP tests run a real server on an ephemeral loopback port and create their own temporary databases. These tests do not substitute for browser visual/interaction testing, physical hardware certification or a production security review.
