# Stronghold

Open-source inventory/stock tracking for small and medium businesses.

## Features

- Track stock items and their counts through a web interface
- Maintain a part catalog (SKU + description); every stock item is linked to a
  part
- Every list (parts, stock, suppliers, supplier parts, purchase orders, build
  orders) is a
  data grid with a search box under each column header; each row is a link to its
  detail page (left-click opens it, right-click opens it in a new tab), where all
  editing happens. Below each table a count reads "X of N rows": how many the
  filters let through, out of how many there are. Add records with the "Add"
  button; remove asks to confirm. The
  parts list also shows each part's total stock on hand, how many units the
  planned build orders still have to consume ("Needed"), how
  many are still to be received on open purchase orders ("On order"), and the
  resulting shortfall to buy ("To order" = needed - in stock - on order)
- Detail pages let you edit every user-facing field (a purchase order's status,
  dates, and costs, a supplier part's pack size, and so on); related records
  (a part's stock, supplier parts, and orders) appear as the same filterable,
  sortable grids used for the top-level lists
- Every field that picks another record (a part, a supplier, a supplier part) is
  a type-to-search box rather than a long dropdown; where a missing record would
  block you, a "+" beside it creates one inline without leaving the page. Buying
  a brand-new part is therefore one page: create the part and the supplier from
  the new supplier part form, then "Purchase this part" raises the order and its
  first line in one go, asking for the quantity and price per pack (the price
  prefilled from that supplier part's own last purchase price, else the part's
  estimate; the quantity prefilled with enough packs to cover the shortfall,
  rounded up per supplier part because pack sizes differ). The part page shows
  the same in stock / needed / on order / suggested figures
- A part's supplier parts table shows what each supplier last charged per item
  (the line price divided by the pack size, to 4 decimals) and the date of that
  order, so the cheapest source is visible at a glance. The 🛒 button on a row
  purchases from that supplier specifically, using the same dialog
- A purchase order's lines are a full table (quantity, pack size, unit price,
  line total, received, and an order total): edit a line's quantity or price in
  place and the part's estimated price -- plus any stock already booked against
  that line -- is revalued to match. Lines lock once the order is completed or
  cancelled
- Every order carries a reference and an order date. Both are required; leave
  the reference blank and it is filled in as "PO-0042" from the order's own
  number, and the date defaults to today. The order date decides which order
  counts as a part's latest price, so it can never be empty
- Manage suppliers and per-supplier product catalogs, raise purchase orders with
  lines, and receive (book) incoming goods straight into stock -- each booked
  item stays linked to its order line for later price/provenance lookups.
  Receive a whole order in one click; an order flips to "Complete" automatically
  once every line is fully received
- Mark parts as assemblies and give them a bill of materials (BOM): open a part
  to edit the components and quantities it is built from; filter the parts list
  by the Assembly column to see only assemblies
- Mark a part virtual (e.g. labour) to give it unlimited stock: it never holds a
  stock item, is only used for BOM pricing, and builds never consume it. A part
  cannot be both an assembly and virtual
- Untick "Purchasable" on a part that is never bought (e.g. made in house): its
  supplier parts and purchase orders disappear from the part page, and no new
  supplier part can be created for it. Untick it only once the part has no
  supplier parts left
- Raise build orders against an assembly and produce them in batches. A new
  build starts as a "Draft" (a scratchpad: it asks for no stock); move it to
  "Pending" once it is planned and its components show up as demand on the
  parts overview. To produce (once the build is set to "Production"): enter how many to make and the components are
  consumed from stock per the BOM (oldest stock first); the finished units are
  produced into stock, stamped with the build. The produce dialog shows any
  component shortfall so you can decide -- you may produce anyway (short
  components are consumed down to zero, the full quantity is still produced).
  When the ordered quantity is fully produced the build completes automatically
- Every part page lists the build orders it is involved in. For an assembly
  these are the builds that make it (with how many each has produced, and a
  button to raise a new one); for any other part they are the builds that
  consume it, showing how much each requires of it ("Required") and how much
  stock it has actually taken so far ("Consumed"). A sub-assembly is both built
  and consumed, so an assembly page also has a "Used in build orders" section
  with the consumed-by table. Like the build orders overview, the lists show
  open builds and hide finished ones until you unhide them with the status
  filter
- A build order keeps its own copy of the assembly's BOM, taken when the order
  was created, so editing the BOM later does not change what an existing build
  is set to consume. If the two drift apart the build page says so and offers
  to update the order to the current BOM -- only while the order is still open;
  a Complete or Cancelled build keeps the components it was built with. The
  build page also lists the stock the build consumed, at the price it was
  bought for, which is what the produced units are worth. The order also keeps
  the labour rate it was costed at, so changing your hourly rate later does not
  re-price builds you already finished. Builds imported from InvenTree are
  baselined at the rate current when you import (InvenTree does not record what
  a past build was costed at), so re-importing re-baselines them.
- Virtual components (labour) are listed as consumed by the build that
  consumed them, at the rate that build recorded, so the work shows up alongside the
  parts and counts toward what the assembly cost. They hold no stock, so
  nothing is drawn down and they are never counted as stock you own
- Consumed stock is never destroyed -- it is recorded as a "Consumed by build
  order" item linked to the build and its source purchase, so you can trace
  exactly which stock went into each build (and what it cost)
- The part page carries a **Stock log**: what happened to that part's stock,
  newest first -- received, produced, consumed by a build, owed to a build, or
  corrected by a stocktake, each linking to the stock item and the order behind
  it. It is derived from the stock rows themselves rather than a separate event
  table, which is why dates are marked `~`: a receipt or consumption is dated by
  its purchase or build order, not the moment the stock actually moved. Only a
  stocktake records the exact time. An incoming row later eaten by a build shows
  what is left of it, marked "(left)"
- Correct a counted quantity with **Stocktake** on the part page. Every
  stocktake needs a reason: click one of the suggestions or type your own. The
  suggestions differ by direction -- finding stock (Found, Refurbished/repaired,
  Returned by customer) and losing it (Damaged, Warranty claim by customer,
  Lost) have little vocabulary in common, with Unknown offered for both. Both
  lists are settings, editable on the settings page. Counting more adds an item
  for the surplus; counting less comes off a stock item you pick (the oldest by
  default), and what left is kept as a consumed item carrying the reason and
  date, shown on the stock page -- so nothing is destroyed and the price it was
  bought at stays traceable. Stock counts are corrected here and nowhere else --
  the stock page shows a count but does not edit it
- **Add negative stock item**, also on the part page, records stock a build
  consumed that was never booked in -- mostly a repair for imported build orders
  completed without their stock fully allocated. It offers only the build orders
  whose components actually include the part, and the shortfall settles itself
  when the parts are received on a purchase order
- Each stock item carries its own value, shown on the stock page: what the
  purchase order it came from actually paid. This is deliberately not the part's
  current price -- a batch bought last year at 0.07 stays worth 0.07 even if the
  part now costs 0.08. Only stock with no priced order of its own falls back to
  the part's estimated price
- Assembly stock produced by a build order is worth what that build actually
  consumed, not the BOM estimate -- so a batch built when components were cheap
  keeps its real cost. It reads "partly estimated" when an input was itself a
  guess, which includes parts the build still owes
- You can finish a build you do not have all the parts for. The assembly is
  produced in full and the missing parts are recorded as a **negative stock
  item** linked to that build, valued at the part's estimated price and deducted
  from your stock value -- so an almost-complete assembly becomes available
  without the shortfall going untracked. Receiving those parts on a purchase
  order clears the negative item automatically, reprices the build at what you
  actually paid, and says so in the activity feed. If the stock is already on
  the shelf instead, **Settle from stock** on the negative item's page pays the
  shortfall off by hand: it asks how many, and which stock to take them from
  (oldest first by default, like a stocktake). A settled shortfall and a
  receipt that went straight into a build both end at zero count, so stock
  tables hide zero-count rows by default -- flip the "In stock" filter to see
  them
- Stock tables show the order each item came from -- the purchase order it was
  received on, or the build order that produced it (or owes it) -- as a link
  straight to that order
- A "Reports" tab with a stock value report, listing every stock item at its
  value. Totals cover stock on hand; consumed rows stay listed for provenance
  but are not counted again (they are already inside the assembly they built). The table shows how each row was priced (and links to the purchase
  order), filters and sorts like the other overviews, and totals both the
  filtered rows and all stock. An "Understated" tile counts the rows whose value
  rests on an estimate (a build whose inputs were themselves estimated, or which
  still owes parts) so an underestimate cannot hide inside the total
- Every part carries an estimated unit price, shown on the part page and in the
  parts overview. A purchased part is worth its most recent purchase price; an
  assembly is worth the sum of its BOM components (recursively, so assemblies
  inside assemblies roll up). A price marked "partial" (`*` in the overview) is
  a floor: some component has never been purchased, so the real cost is higher.
  Prices are recalculated automatically by the changes that affect them --
  adding or removing a purchase order line, editing a BOM, cancelling an order
  -- so they are never stale. "Reports" has a button to recalculate everything
  at once, for after an import
- Assemblies are built, not bought: a part sold by a supplier cannot be marked
  as an assembly, and an assembly cannot be given a supplier part
- Virtual parts (labour, overhead) are never purchased or stocked, so their page
  hides supplier parts, stock and purchase orders and offers a price field
  instead. Set an hourly rate there and every assembly listing it is priced
  accordingly; leave it blank and those assemblies stay "partial"
- A search box (top right) live-searches parts, suppliers, supplier parts,
  purchase orders, and build orders by text (SKU, description, name,
  reference -- never by ID), grouped by type; a checkbox includes inactive
  records, otherwise only active ones show. Click a result to jump straight
  to its detail page
- An activity log (clock icon, top right) records what you did -- parts and
  orders created, stock received, builds produced -- as a filterable table of
  timestamped entries, each linking to the items it touched. It is a history to
  glance over, not an undo
- Import your part catalog, BOMs, suppliers, supplier catalogs, purchase orders,
  build orders, and current stock from an InvenTree server (read-only, one-shot
  migration; nothing on the InvenTree side is changed). Builds have no "On
  Hold" status here, so a held InvenTree build is imported as "Pending"
- Nothing is ever deleted: parts are deactivated and stock is marked consumed
  rather than removed, so your history and data stay intact
- Your data as a readable SQL file: `inventory.sql` is kept up to date next
  to the database and *is* the source of truth -- the `.db` is rebuilt from it
  every time the app starts. Keep it in version control (git) and rolling back
  to any earlier state is `git checkout` plus a restart.
  This repo gitignores it while pre-1.0, since it only holds throwaway
  development data -- un-ignore it once you run Stronghold in production
- Configurable via an optional `settings.toml` (database location, web port,
  logging) -- see `settings.toml.example`. It is read from the
  **current working directory**, so start the app from the directory holding
  it. Startup prints which settings file and database are in use (and says so
  when no file was found); both are also shown on the Settings page

## Getting started

Stronghold is a Python backend (FastAPI + SQLite) plus a SvelteKit web app.

```bash
# 1. build the web app (needs Node 20+)
cd frontend && npm install && npm run build && cd ..

# 2. run the server -- it serves both the API and the built web app
uv sync
cd backend && uv run main.py   # http://localhost:8080
```

For development, run the two halves separately with live reload:

```bash
cd backend && uv run uvicorn main:app --reload --port 8080   # API
cd frontend && npm run dev                                   # web app on :5173
```

## For developers

See [ARCHITECTURE.md](ARCHITECTURE.md) for the technical design.

### Contributing

Work happens on feature branches; `main` is never committed to directly.

```bash
git checkout -b some-feature   # branch off main
uv run pytest                  # backend tests must pass
cd frontend && npm run check   # frontend type/template check
gh pr create                   # open a PR against main
```

- One branch per change, named for what it does (`add-stock-filter`, not `fixes`).
- Keep commits isolated -- one logical change each, short subject lines.
- After changing a DTO in `backend/api.py`, run `npm run gen:types` in
  `frontend/` and commit the regenerated files. A pre-commit hook catches this,
  but it is opt-in per clone: `git config core.hooksPath .githooks`.
- Update the README in the same PR as any feature change.

Stronghold is pre-1.0, so breaking changes to the API, CLI flags and output
formats are allowed -- call them out in the PR description.
