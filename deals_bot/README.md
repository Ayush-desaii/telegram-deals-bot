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

`DB_PATH` may set an absolute path; otherwise the file lives beside the bot at
`deals_bot/deals_bot.db`, independent of the working directory. The default CLI
now runs once; GitHub Actions owns scheduling. `--now --git-state` is the explicit
production command. Do not run it locally alongside the scheduled production job.
Plain `--now` uses local SQLite state only and does not coordinate with GitHub.

## Durable state

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
