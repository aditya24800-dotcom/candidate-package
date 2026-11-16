# Decisions

## Something in the data that surprised me

`E020` is an order with no order data in it: one sentence, "Go ahead with items 1, 2 and 4". The items exist only in Ridgeline's quote, quoted underneath. I had assumed an order restates its items, as `E015` does. `E012` has the same shape and must return nothing, so "read the history" could not be the rule.

## Something in the brief or the README I disagree with, and what I did about it

The README says line items never come from "quoted earlier messages". Its own class table and key break that: a `purchase_order` is "go ahead with items 1–3", and a revision carries the complete order. In `E020` all three key lines are quoted text, in `E004` six of eight. So the model returns quoted items only when the current message points into them or changes an earlier list.

## My predicted held-out numbers, and the two kinds of email I expect to get wrong

| Measure | Dev (24 emails) | Predicted (~40 emails) |
|---|---|---|
| Silent losses | 0 of 19 | 1 |
| Hallucinated lines | 1 of 210 | 5%, about 18 of 360 |
| Lost / phantom requests | 0 / 0 | 1 / 0 |
| Classification accuracy | 24/24 | 37/40 |
| Line recall / precision | 99.5% / 99.5% | 94% / 95% |
| qty / uom / source on paired lines | 209/209 each | 97% / 97% / 98% |
| Context carried | 209/209 | 92% |
| `revises` / `duplicate_of` links | 2 of 2 | 3 of 4 |
| `needs_review` precision / recall | 2/2, 2/2 | 75% / 85% |
| Matcher: wrong confident / accuracy | 0 / 208 of 209 | 2% of lines / 90% |

Dev is a live run from an empty cache (`gpt-6.1-terra`, 15 Nov), scored by my own scorer; predictions are lower because prompt and matcher were built against dev. I expect to get wrong:

1. **Phone photos and scans:** the model reads the same photo differently each run (`E023`), and the key pairs lines on `raw`.
2. **A resend not caught as a duplicate:** of 9 resend cases I tested, 5 were missed (below), and each returns a second set of lines.

## How this runs for real

| Situation | What happens | Built? |
|---|---|---|
| 3,000 emails a day by webhook | The webhook queues each email; workers run the `parse` steps per email. Dev rates: $12 a day, 9 seconds of model time each. | Per-email steps, cache, cost ledger: yes. Webhook, queue, workers: no. |
| Provider down for two hours | Each call is retried 3 times, then the email is flagged `needs_review` and the run carries on. The next run reads it. | Yes, tested. A queue that holds and retries: no. |
| A 300-page PDF | A scan is transcribed one page per call. Text is read in one-page parts, one call each, with the document's opening lines. No page limit. | Yes. 300 text pages: 6,300 of 6,300 lines, 150 calls, $2.21, 77 minutes. Scans tested at 6 pages. |
| The same email delivered twice | Found before the model, at no cost: same `Message-ID`, also across runs, or the same request resent by the same sender. | Yes. A resend with a new `Message-ID` in a later run: no. |

## Where the model decides and where code decides

Code does everything with a fixed answer: unpacking, reading attachments, labelling quoted text, duplicates, revision links, the catalog match. The model transcribes photos and scans, then classifies each email and lists its lines. It never picks a part number: a wrong part number is a wrong quote.

## How I measured it

Every run is scored against the dev key: class, flags, links, each line's fields, then the matcher. Dev was run live from an empty cache, then with no API key, and the files compared. The 300-page PDF test is `diagnostics/long_pdf.py`. Tested locally, not committed: 12 emails I wrote myself and a 40-page scan.

## What I rejected

- The model picking catalog parts (too risky) or finding duplicates (code does it free).
- A second model read as a check: double the cost, and it caught one email on one run.
- OCR, tried twice locally: about 12 of 32 quantities right on a long scan.

## What I know is broken

- **Photos are read differently each run** (`E023`), which loses points on `raw`.
- **The matcher can miss a stocked part.** It finds 933 of the catalog's own 937 descriptions, but customer wording is a fixed list: "non-return valve" comes out `not_in_catalog`, though check valves are stocked.
- **A resend with a new `Message-ID` is missed** when it has a different sender, an added "Hi again" line, the words "another copy", a re-saved PDF, or no `Date` header. On purpose: a double quote costs a minute, a dropped order costs a customer.

## What's next

- A queue whose worker retries with a growing wait when the provider is down, and reading the parts of a long document in parallel.
- Across batches: resends and revisions of earlier emails (today only an exact `Message-ID` repeat is found).

## The catalog matcher

Each description is normalised into product, size, spec, material, ends and extras, as the catalog is. Every stated attribute must agree. No part left: `not_in_catalog`; more than one: `ambiguous`; a part one attribute off is never offered. Dev: 208 of 209, no wrong part named.

**Time spent:** about 9 hours over 13–15 Nov, by the agent session logs.
