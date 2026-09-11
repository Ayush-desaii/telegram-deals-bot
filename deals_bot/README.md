# 🛍️ Loot Deals Bot — Setup Guide

A fully automated Telegram bot that posts the best Indian loot deals to your channel every hour — and earns you **affiliate commission** on every purchase!

---

## 📁 Project Structure

```
deals_bot/
├── main.py          ← Entry point (run this)
├── config.py        ← Loads settings from .env
├── fetcher.py       ← Fetches deals from DesiDime & Amazon
├── formatter.py     ← Formats deals into beautiful messages
├── poster.py        ← Posts to your Telegram channel
├── database.py      ← Tracks posted deals (no duplicates)
├── .env             ← YOUR SECRET CONFIG (create from .env.example)
├── requirements.txt ← Python dependencies
└── deals_bot.db     ← Auto-created SQLite database
```

---

## ⚙️ Setup (One-Time)

### Step 1 — Create your `.env` file
```bash
copy .env.example .env
```
Then open `.env` and fill in:

| Variable | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Message `@BotFather` → `/newbot` |
| `TELEGRAM_CHANNEL_ID` | Your channel username e.g. `@MyDealsChannel` |
| `AMAZON_AFFILIATE_TAG` | [Amazon Associates India](https://affiliate-program.amazon.in/) → your tag |
| `FETCH_INTERVAL_MINUTES` | How often to post (e.g. `60` = every hour) |
| `MIN_DISCOUNT_PERCENT` | Only post deals with this discount or more (e.g. `40`) |
| `MAX_DEALS_PER_CYCLE` | Max deals per run (e.g. `3`) |

### Step 2 — Add Bot as Admin to Your Channel
1. Open your Telegram channel
2. Go to **Administrators** → **Add Admin**
3. Search your bot username and add it
4. Enable **Post Messages** permission

### Step 3 — Install Dependencies
```bash
pip install -r requirements.txt
```

---

## 🚀 Running the Bot

### Preview deals (no posting)
```bash
python main.py --test
```

### Post once immediately and exit
```bash
python main.py --now
```

### Run forever (auto-posts on schedule) ✅
```bash
python main.py
```

---

## 💰 Earning Money

### Amazon Associates
1. Sign up at [affiliate-program.amazon.in](https://affiliate-program.amazon.in/)
2. Get your affiliate tag (e.g. `yourname-21`)
3. Add it to `.env` as `AMAZON_AFFILIATE_TAG`
4. Bot auto-appends `?tag=yourname-21` to all Amazon links
5. You earn **1–9% commission** on every purchase!

### Typical Earnings
| Subscribers | Monthly Clicks | Avg Sales | Avg Earning |
|---|---|---|---|
| 1,000 | 500 | 25 | ₹1,000–₹5,000 |
| 10,000 | 5,000 | 250 | ₹10,000–₹50,000 |
| 1,00,000 | 50,000 | 2,500 | ₹1,00,000+ |

---

## 🔄 Running 24/7 (Optional)

To keep the bot running even when your PC is off:

### Option A — Windows Task Scheduler
1. Search "Task Scheduler" in Windows
2. Create Basic Task → trigger: At startup
3. Action: Start program → `python` with argument `e:\telegram\deals_bot\main.py`

### Option B — Free Cloud Hosting
- **PythonAnywhere** (free tier) — upload files, run `python main.py`
- **Railway.app** — deploy with one click
- **Google Cloud Run** — scalable, free tier available

---

## 🛠️ Customization

### Change deal sources
Edit `config.py` → `RSS_FEEDS` list to add/remove feed URLs

### Change message format
Edit `formatter.py` → `format_deal_message()` function

### Add more affiliate programs
Edit `fetcher.py` → `make_affiliate_url()` function

---

## ❓ Troubleshooting

| Problem | Fix |
|---|---|
| `Bot token invalid` | Check `.env` — copy token from BotFather exactly |
| `Chat not found` | Make sure bot is admin in channel |
| `No deals found` | Lower `MIN_DISCOUNT_PERCENT` in `.env` |
| `Duplicate deals` | Normal — DB tracks & skips them automatically |
