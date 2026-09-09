# Sales orders

What you sold, imported from WooCommerce, and which parts each sale took off
the shelf.

## What Stronghold owns, and what it does not

WooCommerce is the shop. It knows the customer, the prices, the order status and
what products went out of the door, and it stays the authority on all of it --
Stronghold only ever reads those facts, never writes them back.

What WooCommerce cannot know is what a product is *made of*. A sold item is a
product code; the parts behind it live here. So a sales order in Stronghold is
the imported order plus one thing you own: **which parts each sold line
consumes, and how many of each per unit sold.** Map a product SKU to an assembly
once (see below) and that gets filled in for you; anything unmapped you link by
hand.

Once that mapping exists you can *book* the order, and Stronghold takes those
parts out of stock exactly the way a build order consumes its components.

## Importing

The **Import from WooCommerce** button on the sales orders page fetches orders
created in a date range (the last 7 days by default). Importing is safe to
repeat:

| The order is | What the import does |
| --- | --- |
| New | Creates it, with its line items |
| Already here, not booked | Updates the customer, status, prices and lines from WooCommerce |
| Already here and **booked** | Refreshes the order itself (status, customer, shipping, fees) but leaves its line items alone |

A booked order's *lines* are left alone because booking already moved stock:
rewriting them underneath a completed stock movement would leave the
consumption describing something that no longer exists. The order's own
commercial facts are not what was consumed, so a discount or status added in
the shop after picking still arrives.

A variable product's line name arrives from WooCommerce wrapped in the store's
own markup (`Hayfall<span> - </span>Met starterspakket`). The import strips it,
so what you see is plain text. Lines imported before that was fixed keep the
tags until the order's lines are imported again -- and a **booked** order's
never are, by the rule above, so those keep them for good.

Your part mapping survives a re-import: links are matched to line items by their
WooCommerce id, not by position. If a line disappears from the WooCommerce order
entirely, its links go with it and the import reports that it did so.

The connection details (site URL and a read-only API key pair) are entered on
the **Settings** page. The key and secret are stored encrypted, so the data file
you keep in git never holds a readable credential -- see the deployment page for
the key file that decrypts them.

## Product SKUs: linking by hand once, not once per order

The same products sell over and over, so mapping their parts by hand on every
order would be the same work every week. The **Product SKUs** tab, next to
Overview on the sales orders page, is where that mapping lives instead: it points
a sold SKU at the part it is made of.

A mapping is a **list of parts and quantities** -- the same shape as the parts on
a sales order line, and copied onto it verbatim. What the mapping says is exactly
what the line gets, with nothing expanded in between.

That includes an assembly: map a SKU to one and the line consumes *one of that
assembly*, taken off the shelf where a build order put it, which is what selling
a built product actually does. Map loose parts instead and the line consumes
those.

The **Sold SKU** box suggests the SKUs your imported orders actually use, most-
sold first, and drops each one from the list as you map it -- so the key is
picked rather than typed. A SKU that has not been sold yet can still be typed in
by hand. The count in brackets is how many sold line items carry it, which is a
fair guide to what is worth mapping first.

Several SKUs may map to the same parts, which is the usual case for variants --
a haybutler with the door on the left (`HBT-H-DL`) and one with it on the right
(`HBT-H-DR`) are the same build.

With a SKU mapped, it is applied to matching line items:

- automatically, whenever an order is imported, and
- on demand, with **Prefill from SKUs** on a sales order.

## Saving a mapping from an order

The mapping does not have to be built on the Product SKUs tab. Link the parts on
a sales order line until they are right, then press **Save as SKU mapping** on
that line: its SKU is mapped to exactly those parts and quantities, ready for
every future order that sells it.

If the SKU already maps to something, the button reads **Update SKU mapping** and
names what it currently points at before replacing it -- other orders prefill
from that mapping, so it is never overwritten silently. Replacing is a straight
swap, not a merge: a part the line no longer lists leaves the mapping too.

Either way, it is a *starting point* -- ordinary part links are written and you
are then free to edit them. Two rules keep it from ever undoing your work:

- Only a line with **no parts yet** is filled in. Once you have edited a line,
  prefilling again leaves it exactly as you left it.
- Changing a mapping, or the BOM behind it, **never rewrites an order that was
  already filled in.** Orders keep what they were costed against.

A line whose SKU has no mapping is simply left for you to link by hand, as below.
So is one mapped to an assembly whose BOM is still empty: there is nothing to
copy yet, and linking the assembly to itself would not be what you meant.

## Linking parts to a line

On a sales order, each line item lists the parts it consumes -- prefilled from
the SKU mapping above where there is one, and linked by hand where there is not.
**Link a part** adds one; the quantity is *per unit sold*, so a line that sold 3
of a product consuming 2 brackets each needs `2`, and Stronghold works out that the order
needs 6.

One thing is rejected:

- **Changing or removing a link once the order is booked.** Those units are
  already out of stock, so honouring it would mean putting stock back on the
  shelf and unwinding any shortfall it recorded.

**Adding** a part to a booked order is fine, and is the normal way to correct a
mapping you got wrong: link what was missing and book again. Booking only ever
takes what has not been taken yet, so the parts already consumed are left
alone.

Linking a part the line already lists **adds to** it rather than complaining:
link two more of the same nut and the quantity goes from 2 to 4. A line holds
one quantity per part, so there is never a second row for the same one.

## Extras thrown in with an order

Sometimes what goes in the box is not what the shop sold: a spare cable, a
handful of bolts, a thank-you part. That belongs to the *order*, not to any line
item, so it gets its own **Extras** block under the line items. **Add an extra
part** links one, with a plain quantity (there is no "per unit sold" here --
the order got what it got).

From there on an extra is an ordinary linked part: it counts as demand while the
order is unbooked, booking consumes it from stock oldest-first, it lands in the
consumed rows at what that stock cost, and it is in both the estimated and the
realised margin. A re-import leaves the extras alone -- WooCommerce does not
know about them and never will.

**Virtual parts (labour) can be linked too.** They hold no stock, so nothing is
drawn down -- but the sale really did cost that time, so booking records it as a
consumed row at the part's rate and it counts in the cost and margin, exactly
the way a build records labour.

Until an order is booked, its parts count as **demand**: they show up in the
"For sales" column on the parts list (and "Needed for sales orders" on the part
page) and feed the suggested order quantity, the same way a planned build does.
Cancelled, refunded and failed orders ask for nothing.

If your store has statuses of its own that should not raise demand -- a quote
from an order-proposal plugin, say -- tick them under
`sales.no_demand_statuses` on the settings page. The list offers whatever
statuses your store actually has.

## Order statuses

A WooCommerce store's statuses are not a fixed set: plugins register their own
(an order-proposal plugin adds a quote status, Blocks checkout adds a draft
one). Stronghold stores whatever slug the order carries, and at every import it
also caches what your store calls each status -- in your store's own language --
from WooCommerce's order totals report. So the status column, its filter and the
order page all show the same wording the shop admin does, and a new plugin needs
no change here.

## Booking

**Book order** consumes the linked parts, oldest stock first, in one step. Each
source stock row is drawn down and a matching consumed row is split off carrying
the price that stock actually cost -- so afterwards you can see not just that the
sale happened, but what the goods that went out were bought for.

Nothing is produced. A build turns components into an assembly; a sale simply
ships stock out.

Booking is also how you mark a sale **handled**. An order with no parts linked
books perfectly well: nothing leaves stock, and the Booked flag records that you
have dealt with it. Plenty of sales work that way -- a service, a digital
product, something shipped from stock you do not track here.

If you link more parts afterwards, the button comes back as **Book added
parts**: it consumes only the new ones.

### Booking when you are short

Booking is never blocked by insufficient stock. The confirm dialog lists what you
are short of, and if you go ahead:

- the available stock is drained to zero,
- the full quantity is still recorded as consumed, priced at the part's estimate,
- and the shortfall is carried as a **negative available row** owed by the sale.

This is the same mechanism a short build uses, and it is deliberate: the goods
physically left the building, so pretending less was consumed would misstate both
your stock and the cost of the sale. The negative row nets out of your on-hand
figure and is deducted from stock value until it is settled.

Receiving those parts on a purchase order settles the debt automatically and
reprices the consumption to what you actually paid, so the sale stops being
costed at a guess.

Stock that arrives any other way -- a build that produced the part, or stock
that was already on the shelf -- does **not** settle it on its own. Use **Settle
from stock** on the negative stock item; see the stock page.

## Margin

The order page shows margin two ways, side by side:

| | Where the cost comes from | When it exists |
| --- | --- | --- |
| **Estimated** | The linked parts' current estimated prices | As soon as parts are linked |
| **Realised** | What the stock this sale actually consumed cost | Once the order is booked |

Each is shown as an amount and as a percentage. The percentage is margin over
revenue -- a sale of 100.00 costing 22.00 reads 78% -- which is the usual retail
sense of "margin", not markup over cost. The sales list shows the percentage,
realised once the order is booked and the estimate before that.

Revenue is the line items ex VAT, plus **fees and discounts**. WooCommerce books
both as fee lines -- a discount is simply a negative one -- and the order page
shows their total just above the shipping charged. That is money that actually
changed hands on this sale, so unlike shipping it always counts: a 386.47 order
with a 299.92 discount reads 86.55 in revenue.

**Coupons are different.** WooCommerce reports each line's price *already net of*
any coupon, so a coupon is in the revenue through the line items themselves.
The order page shows the coupon total for reference -- marked as already off the
line prices -- and does not deduct it a second time.

**Shipping counts only once you have entered
what it actually cost you.** WooCommerce knows what the customer was charged; it
cannot know the carrier bill, so the order page has a "Shipping actually paid"
field of your own. Leave it empty and shipping is left out of both sides --
counting the postage charged alone would book the whole of it as margin. Fill it
in and both sides count: the charged amount joins revenue, the paid amount joins
cost. Empty is not zero: **0.00 means free carriage** and counts like any other
figure.

The two figures differ when the stock you shipped was bought for something other
than the current estimate -- an old lot bought cheaper, or a shortfall that was
costed at an estimate and later settled at the real price.

## What is deliberately not here

- **No SKU matching.** The WooCommerce product code is stored as plain text and
  never matched against part SKUs. Products and parts are different things, and a
  guess that is right most of the time is worse than an explicit mapping.
- **No writing back to WooCommerce.** The connection is read-only.
- **No automatic import.** It runs when you press the button.
- **No partial shipment.** Booking is all-or-nothing per order.
- **VAT is not modelled.** Prices are ex VAT, as imported.
