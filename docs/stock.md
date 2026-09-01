# Stock

How stock works in Stronghold: where stock items come from, how they move, and
how they connect to purchase orders and build orders.

## The core idea: stock is never destroyed

Every physical thing you own is a **stock item**: a row holding a part, a count,
a status, and a link back to whatever created it.

Stock items are never deleted, and their history is never rewritten. When stock
leaves the shelf -- eaten by a build, or written off in a stocktake -- the
original item's count goes down and a matching **consumed** row is split off
recording what left and what it had cost. Nothing vanishes; every unit that ever
entered the building can still be traced to where it went.

That is why a stock item has one of two statuses:

| Status | Meaning |
| --- | --- |
| **Available** | On the shelf. Counts towards your stock on hand and your stock value. |
| **Consumed** | Gone into an assembly, sold, or written off. Kept for history and costing, never counted as on-hand stock. |

A consequence: stock tables are full of rows sitting at zero -- a receipt whose
units went straight into a build, or a settled shortfall. They are history, not
stock, so the tables hide zero-count rows by default. Don't "correct" them back
to a positive count; that would count the same parts twice.

## How stock items are created

There are four ways stock comes into existence. Whichever way, the item
records the moment it was created, shown as **Created** in every stock table and
on the item's own page. Items that predate this column show the date of the
order they came from; the handful that no order can date show no date at all,
rather than a made-up one.

### 1. Receiving a purchase order

The normal way parts arrive. On a purchase order you receive a line, saying how
many **packs** turned up. Stronghold creates one Available stock item counting
`packs x pack size` (a supplier part records how many items are in a pack), and
stamps it with the purchase order it came from.

That stamp is what gives the stock its price: the item is worth what its own
order actually paid -- **delivery included**, see below.

You may receive more than you ordered -- over-receiving is allowed, suppliers
over-deliver.

#### Delivery cost

An order's delivery cost is not a separate expense to be remembered later: it is
part of what the goods cost you. Stronghold spreads it over the order's lines
**in proportion to their value**, so a EUR 500 line carries more of the freight
than a EUR 5 one.

Order EUR 20 of one part and EUR 80 of another with EUR 10 delivery, and the
freight splits EUR 2 / EUR 8 -- both lines land 10% above their goods price. The
purchase order page shows this per line in the **Landed** column, and breaks the
order into Goods / Delivery / Total underneath. The purchase order list shows
that same total.

That landed price is what the part is then worth and what stock received against
the line is valued at, so delivery flows on into assembly costs and the stock
value report. Change the delivery cost on an order and everything it touches is
repriced immediately; set it to zero and the prices are exactly the goods prices
again.

**The rule: every price you see is landed, except the line prices on the order
itself.** Those stay the bare price you agreed with the supplier, because that
is what you typed and what the delivery cost is then added to. Everywhere else
-- the part's estimated price, the unit price in the purchase order tables on a
part or supplier part page, stock values -- is what the goods really cost you,
delivery included. Ordering again prefills the bare price, not the landed one,
so freight is never charged twice.

### 2. Producing a build order

A build order turns components into an assembly. Each time you produce units,
Stronghold does two things in one step:

- **consumes** the components the build's recipe calls for, oldest stock first
- **produces** one Available stock item of the assembly, stamped with the build

The produced assembly is priced from what the build actually consumed, so its
value is built up from the real purchase prices of its inputs, not a guess.

### 3. A stocktake

Counting the shelf. On the part page you enter the figure you actually counted
and a reason (the reason lists are configurable in Settings).

- **Counted more than expected** -- a new Available stock item is created for
  the surplus.
- **Counted less** -- the difference is drawn off existing stock, oldest first
  (or off one stock item you name), each draw leaving a consumed row carrying
  the price that stock was bought at.

A stocktake corrects the shelf only. It can count the shelf down to empty, but
it can never push a part into debt -- that is a different action, below.

### 4. Recording stock that was used but never booked

For a part a build clearly ate but which was never received into the system --
mostly a repair for imported history. This creates a shortfall, described next.

## Negative stock, and why it exists

Shortages do not block production, and they do not block a sale either. If you
produce a build and a component is short -- or book a sales order for parts you
do not have -- it goes ahead anyway: the part is drained to zero and the missing
quantity goes **on credit**.

That credit is a stock item with a **negative count** -- the one place Available
stock is allowed to go below zero. It means "an order has already eaten parts we
don't have yet."

The rationale: the alternative is worse. Blocking production would mean the
assembly you physically built doesn't exist in the system, and its cost would be
missing entirely. Instead:

- The assembly is costed **in full**, using the part's estimated price for the
  missing quantity, so the build's cost is realistic rather than artificially low.
- The debt nets out of your stock on hand automatically -- a part showing `-3` is
  telling you the truth about your position.
- The debt is deducted from the stock value report, so you are not credited with
  parts you owe.

The produce dialog tells you about the shortfall before you confirm; producing
anyway is your decision, deliberately.

### Settling a shortfall

A debt is settled, never deleted -- it shrinks to zero and stays as a record.
Two ways:

- **Receiving the missing parts on a purchase order.** This happens
  automatically: the debt shrinks and the consumption is repriced from what you
  actually paid (for a build, the assembly's value is recalculated too). The
  settled units go straight to the order that owed them -- they never land on
  the shelf.
- **Settling from stock you already have.** If a part somehow ended up with both
  a debt and stock on the shelf, the stock item page offers **Settle from
  stock**. It draws the stock down oldest-first (or off one item you name) and
  reprices the consumption off what that stock really cost.

Either way the point is the same: replace the estimated cost with the real one.

## How stock links to orders and builds

Each stock item carries up to three links, and they are separate on purpose:

- **Purchase order** -- the order it was received against. Where its price comes
  from.
- **Built by** -- the build order that produced it (assemblies).
- **Consumed by** -- the order that used it up: a build order that ate it, or a
  sales order that shipped it.

An assembly produced by one build and eaten by a later one carries both a
"built by" and a "consumed by" link; collapsing them would lose the trail back
to what it cost to make.

These links are live throughout the app. A stock item page links to its part and
its orders; a purchase order shows what it brought in; a build order shows what
it consumed and what it produced; a part page lists its stock items and where it
is used. Follow the links in either direction.

## Seeing the history: the stock log

Every part page has a **Stock log** section: everything that ever happened to
that part's stock, newest first.

| Column | What it shows |
| --- | --- |
| When | When it happened |
| Event | Received / Produced / Consumed / Stocktake / Owed to build |
| Quantity | How much moved; `(left)` marks a receipt since drawn down |
| Order | The purchase or build order behind it, clickable |
| Reason | The stocktake reason, where there is one |

One caveat about dates: only a stocktake records the exact moment stock moved.
Dates from a purchase or build order are the **order's** date, and are shown with
a `~` prefix to say so.

Alongside the stock log, the **Activity** page is the app-wide audit trail --
every change in the system, newest first, cross-linked to the records involved.

## The stock value report

Reports > Stock value. It answers "what is my stock worth?" and, just as
importantly, "what don't I know the value of?"

Every stock item carries its own unit price and a note of **how that price was
worked out**:

| Priced by | Meaning |
| --- | --- |
| Purchase order | What its own order actually paid, including its share of the delivery cost. The best answer. |
| Build order | Built up from the real cost of what the build consumed. |
| Build order (partly estimated) | Built, but an input was itself estimated or short. Worth **at least** the figure shown, probably more. |
| Part price estimate | Never bought directly; valued at the part's latest known price. |
| Purchase order (no price) | Bought, but the order line has no price filled in. Reported as such -- never quietly valued at zero. |
| Virtual component (labour) | A non-stocked cost such as labour, at the rate its build recorded. |
| No price known | Nothing to go on. |

The report is a plain read of those figures -- there is no "calculate" step and
nothing to refresh. Prices are kept current as you work: receiving stock,
editing an order line, changing a recipe or settling a shortfall all reprice
whatever they affect, immediately.

The four tiles at the top:

- **Value shown** -- the total of the rows currently visible, so filtering the
  table gives you a subtotal.
- **Value of stock on hand** -- the whole picture, Available stock only.
  Consumed rows are excluded because their cost is already counted inside the
  assembly they became; adding them would double-count.
- **Without a price** -- how many items you have no valuation for. Worth
  emptying: usually a purchase order line missing a price.
- **Understated** -- items whose true value is higher than reported, because a
  build was costed partly from estimates. Filter "Priced by" to find them.

Assemblies appear as ordinary stock: they were produced by a build and are worth
what that build consumed.

## Virtual parts

A part can be marked **virtual** -- something with unlimited supply that has a
cost but no physical stock, typically labour. Virtual parts hold no stock on the
shelf and are never drawn down by a build. They exist so an assembly can be
costed properly: their rate shows up in what a build consumed and in the
assembly's unit cost, but they never appear in your stock value.

### The rate is frozen at the moment you build

When a build consumes a virtual component, Stronghold records a consumed stock
row for it, at the rate in force at that moment. **That rate is then frozen.**

This matters when you later change the rate. Put your labour rate up from 10 to
15 and nothing already built moves: past builds stay costed at 10, because
that is what they actually cost. Only builds produced from then on use 15.

Nothing overwrites an already-recorded virtual rate -- not even the
"Recalculate all part prices" maintenance action.

### But changing the rate does move some figures

The freeze covers what a build **actually consumed**. It does not cover
estimates, and the difference is worth understanding, because changing a labour
rate visibly moves the stock value report.

Raising the rate changes the **estimated price** of every assembly that has that
virtual part anywhere in its recipe. Estimates are recalculated from the current
recipe at the current rate -- that is what makes them estimates. Stock priced
that way therefore revalues:

| Stock priced by | Effect of changing the rate |
| --- | --- |
| Build order (`Build order`, `Build order (partly estimated)`) | **Unchanged.** Costed from what the build really consumed, at the frozen rate. |
| Part price estimate | **Revalues.** It was never built, so it is valued at what it *would* cost today. |

So a rate change moves the value of assembly stock you never actually built,
and leaves everything you did build alone. If the report jumps after a rate
change, filter "Priced by" to `Part price estimate` to see exactly which rows
moved.

Two footnotes:

- A virtual part with **no** price when the build was produced had nothing to
  freeze. It shows as "No price known", and giving the part a price fills it in.
- Historical builds from an InvenTree import are baselined at the rate the part
  had on the day of the import, because InvenTree keeps no record of what labour
  cost at the time. That figure is a starting point rather than real history --
  but it is frozen from then on, exactly like a build produced here.

## Expert mode

A switch on the Settings page. It is off by default, and it turns off the rules
that normally keep the books honest:

- **Order status becomes unrestricted.** Normally a Complete or Cancelled
  purchase order is a dead end, and a build order that has produced anything can
  only be Production or Complete. With expert mode on, any status can be set on
  any order. This exists mainly to un-cancel an order cancelled by mistake.
- **Stock counts become directly editable.** The stock item page shows the count
  as an input with an **OK** button beside it. Typing a new figure changes
  nothing on its own: the edit is saved only when you press OK or Enter, and OK
  stays greyed out until the value actually differs from what is stored. Saving
  overwrites the count with no stocktake row, no reason and no consumed row --
  so nothing records where the stock went.
  A row keeps its sign either way: shelf stock cannot be edited negative, and a
  build's shortfall row cannot be edited positive (settle it to 0 instead).

Both bypasses are exactly the safety rails that make the history readable. Turn
it on for the repair, turn it off again afterwards. For an everyday correction
use a stocktake, which keeps the trail.
