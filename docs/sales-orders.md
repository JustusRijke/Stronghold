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
| Already here and **booked** | Leaves it completely alone |

Booked orders are skipped because booking already moved stock. Rewriting the
lines underneath a completed stock movement would leave the consumption
describing something that no longer exists.

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

What gets copied onto the line depends on which part you map:

| You map | The line gets |
| --- | --- |
| An **assembly** | Its whole bill of materials, one link per component, at the BOM quantities |
| **Any other part** | One link to that part, quantity 1 -- a line selling a single bolt consumes one bolt |

The **Sold SKU** box suggests the SKUs your imported orders actually use, most-
sold first, and drops each one from the list as you map it -- so the key is
picked rather than typed. A SKU that has not been sold yet can still be typed in
by hand. The count in brackets is how many sold line items carry it, which is a
fair guide to what is worth mapping first.

Several SKUs may point at the same part, which is the usual case for variants --
a haybutler with the door on the left (`HBT-H-DL`) and one with it on the right
(`HBT-H-DR`) are the same build, so both rows name the same assembly.

With a SKU mapped, it is applied to matching line items:

- automatically, whenever an order is imported, and
- on demand, with **Prefill from SKUs** on a sales order.

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

**Virtual parts (labour) can be linked too.** They hold no stock, so nothing is
drawn down -- but the sale really did cost that time, so booking records it as a
consumed row at the part's rate and it counts in the cost and margin, exactly
the way a build records labour.

Until an order is booked, its parts count as **demand**: they show up in the
"Needed" column on the part page and feed the suggested order quantity, the same
way a planned build does. Cancelled, refunded and failed orders ask for nothing.

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

Revenue is the line items ex VAT. **Shipping is shown but excluded from margin**:
what the customer paid for postage is a pass-through, not margin on the goods.

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
