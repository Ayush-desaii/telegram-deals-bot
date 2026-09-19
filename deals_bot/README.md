# Verified Amazon India deals bot

Finds Amazon India products, verifies their current price and availability, and
publishes qualifying offers with Amazon affiliate links to Telegram. GitHub
Actions runs at 09:00, 13:30 and 20:30 IST, with at most two posts per run.
GitHub scheduled runs can be delayed; these are requested times, not guarantees.
The Instagram/Reel application and its Render deployment are separate.

## Deal rules

- Products need a real Amazon India URL/ASIN, positive price, matching product-page
  identity and explicit in-stock text. Missing or blocked data means no post.
- Prices are rechecked after discovery, with stale snapshots rejected after five
  minutes. Verification failure never falls back to discovery prices.
- New products need at least 40% off a valid MRP. Captions say **off MRP**, not
  lowest-ever price or a verified historical discount.
- After seven distinct prior days of verified observations, the comparison is the
  median of daily minimum prices observed in the previous 30 UTC dates. The
  current day is excluded. A deal then needs both a 10% drop and at least ₹50 saved.
- Verified historical drops rank before MRP offers. This is sampled history from
  this bot, not a complete market history. Observations are retained for 90 days.
- Products are suppressed for seven days. A further 10% and ₹50 drop allows an
  early repost after 24 hours. Legacy posts without prices keep seven-day suppression.
- Coupons, delivery fees, seller comparisons and multi-store discovery are not
  covered by this release. Source markup changes can reduce or stop discovery.

## Local setup and preview

Use Python 3.12, install `pip install -r deals_bot/requirements.txt`, and copy
`deals_bot/.env.example` to `deals_bot/.env`. Set `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHANNEL_ID`, and `AMAZON_AFFILIATE_TAG`. Give the bot channel posting rights.

From the repository root:

```sh
python deals_bot/main.py --test
python -m unittest discover -s deals_bot/tests -v
```

Preview uses a temporary copy of the database, allows missing Telegram/affiliate
credentials, and never sends messages or writes production state. To preview the
current production state branch, add `--git-state`. Add `--require-verified` to
fail the preview if no product can be verified, even if discovery returns data.
Release previews with `--require-verified` may check tracked products before their
due time, on the temporary copy only, to validate live access without changing
production backoff or schedules.

`DB_PATH` may set an absolute path; otherwise the file lives beside the bot at
`deals_bot/deals_bot.db`, independent of the working directory. The default CLI
now runs once; GitHub Actions owns scheduling. `--now --git-state` is the explicit
production command. Do not run it locally alongside the scheduled production job.
Plain `--now` uses local SQLite state only and does not coordinate with GitHub.

## Durable state

### Watchlist and source health

Successfully verified products automatically enter a persistent watchlist, even
when their current discount is insufficient. They are checked again when due,
without needing to appear in search results. Add full Amazon India product URLs
to `deals_bot/watchlist.txt`, one per line, to retain up to 20 manual products.
Blank lines and lines beginning with `#` are ignored. URL variants deduplicate
by ASIN. Removing a line removes manual priority; automatic retention can continue.

The watchlist holds at most 100 products. Manual entries are retained. Automatic
entries expire after 30 days without successful verification; at capacity the
oldest successfully checked automatic entry is replaced. Healthy entries are due
after six hours, unavailable items after 24 hours, and source errors back off for
six, twelve, then twenty-four hours. Overdue time sorts first, followed by manual
priority, history days, and ASIN. Errors never erase existing price history.

Each cycle checks up to 30 unique products: 20 watchlist slots and 10 new discovery
slots, with unused capacity shared. Sources are merged by ASIN before verification.
Discovery downloads listings/feed pages only (short links use HEAD resolution);
the central verifier fetches product pages. At most four top eligible products
receive a final refresh before up to two posting attempts.

Collection and verification share a fifteen-minute budget. Discovery is capped at
five minutes to leave time for the watchlist; initial checks reserve the final two
minutes for refreshes. Work that does not fit is `budget_skipped`, not a source
failure. Not-due products stay deferred even when rediscovered. The optional legacy
PA API is marked disabled in the active bounded pipeline; existing Amazon search,
bestseller and Reddit sources remain enabled.

Each GitHub run has a readable job summary and a `deals-preview-report` or
`deals-production-report` artifact retained for fourteen days. Locally:

```sh
python deals_bot/main.py --test --report-dir ./reports
```

`report.json` and `report.md` show unique-product totals, per-source discovery
health, verification results, watchlist counts, and rejection reasons. Source
counts overlap if several sources found the same ASIN. A zero-post cycle can be
healthy (for example, duplicate suppression or insufficient discounts). A cycle
whose attempted verification all failed due to source errors fails after saving
available state. Reports contain no credentials, request headers or product URLs;
no health reports are sent to the channel. Production summaries and source counts
are retained in SQLite for ninety days. Preview reports are not persisted remotely.

### State storage and migration

The `codex/deals-state` branch holds only `deals_bot.db`, separately from application
code. It has the same visibility as the repository: product prices, post titles,
ASINs, timestamps and Telegram message IDs are not private analytics. No tokens,
customer data or credentials are stored there.

The first production run imports the latest restored legacy Actions cache and
any checked-out legacy database, merging posting hashes by latest timestamp.
No historical prices are invented. Later runs restore only the authoritative
state branch. State corruption, inaccessible state or failed pushes stop posting.

Every send has a durable `pending` attempt first. Successful sends save their
Telegram message ID immediately. Network timeouts and unreadable responses remain
pending because Telegram might already have accepted the message. Pending products
are blocked indefinitely, including after restarts. Explicit rejections have a
24-hour cooldown. A photo fallback is allowed only after a definite rejection.

For a pending attempt, inspect the channel and reconcile the message manually
before modifying its status. If it was delivered, record the message ID and mark
it sent; if definitely undelivered, mark it rejected. Make a backup and pause the
workflow before maintenance, then persist the corrected database to the state
branch. Never clear pending records merely because a request timed out.

GitHub's `deals-production` concurrency group serializes manual and scheduled
runs. Non-force state pushes reject a stale competing writer. Repository write
permission is limited to the production job; tests and preview are read-only.

## Release and rollout

1. Run all tests on the implementation branch and review the diff. Never commit
   `.env`, local credentials, virtual environments or working databases to `main`.
2. Fast-forward and push `main`. The **Deals Tests** workflow validates the commit.
3. In **Verified Amazon Deals**, use **Run workflow → main → preview**. Preview
   uses live production sources and restores production history without saving it.
4. Only after tests and the preview succeed, set repository Actions variable
   `DEALS_APPROVED_SHA` to that exact full commit SHA. This enables scheduled posts
   and the explicit manual `post` mode. Any later code commit requires a new preview
   and approval SHA; a failed preview leaves posting gated.
5. Check the next scheduled run's `CYCLE_RESULT` and the state branch. Zero eligible
   offers is valid; zero verified products in the release preview requires investigation.

Required GitHub secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`,
`AMAZON_AFFILIATE_TAG`. Production refuses missing or placeholder affiliate tags.
Tag attachment is tested; commission attribution must still be checked in the
affiliate account and is not guaranteed by the code.

To pause posting, clear `DEALS_APPROVED_SHA`. For rollback, keep posting paused,
revert the application commit, test and preview the resulting commit, then approve
that SHA. Preserve the state branch and pending attempts. Code must understand the
state schema before posting; the old cache-only release must not be restarted
against stale state.

This release adds schema version 2 without deleting existing history or posting
attempts. It bootstraps the watchlist from recent verified ASIN observations, not
irreversible legacy URL hashes. The state branch retains the pre-migration commit
as an ancestor; its SHA is also included in the first migrated cycle's report.
For recovery, preserve that snapshot and use code compatible with schema version
2; the previous version-1 binary deliberately refuses a newer database. A migrated
database must not be replaced with an old snapshot after new sends without first
reconciling those sends, because that could lose duplicate protection.
