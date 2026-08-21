# Sales orders

What you sold, imported from WooCommerce, and which parts each sale took off
the shelf.

## What Stronghold owns, and what it does not

WooCommerce is the shop. It knows the customer, the prices, the order status and
what products went out of the door, and it stays the authority on all of it --
Stronghold only ever reads those facts, never writes them back.

What WooCommerce cannot know is what a product is *made of*. A sold item is a
product code; the parts behind it live here. So a sales order in Stronghold is
the imported order plus one thing you add by hand: **which parts each sold line
consumes, and how many of each per unit sold.**

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

## Linking parts to a line

On a sales order, each line item lists the parts it consumes. **Link a part**
adds one; the quantity is *per unit sold*, so a line that sold 3 of a product
consuming 2 brackets each needs `2`, and Stronghold works out that the order
needs 6.

Two things are rejected:

- **Virtual parts.** They hold no stock (they are labour rates for build
  costing), so there is nothing for a sale to draw down.
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
costed at a guess. You can also settle it from stock already on the shelf -- see
the stock page.

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
