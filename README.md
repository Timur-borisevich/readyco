# ReadyCo Market

B2B marketplace for licensed companies — Telegram bot + website.

## Structure

```
readyco/
├── config.py           — Settings (bot token, channel, admins, DB)
├── requirements.txt    — Python dependencies
├── bot/
│   ├── main.py         — Telegram bot (commands, wizard, client intake)
│   └── formatting.py   — Offer card formatting, channel posts
├── db/
│   └── schema.py       — PostgreSQL schema (offers, leads, deals, audit)
└── api/
    └── server.py       — FastAPI read-only API for website
```

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start PostgreSQL
brew install postgresql
brew services start postgresql
createdb readyco

# 3. Run bot
python -m bot.main

# 4. Run API (separate terminal)
python -m api.server
```

## Bot Commands

| Command | Who | Description |
|---|---|---|
| /add | Admin | Create new offer (step-by-step wizard) |
| /edit | Admin | Edit offer fields |
| /list [status] | Admin | List offers (live/sold/draft/all) |
| /search <keyword> | Admin | Search offers |
| /sold <ref> | Admin | Mark as sold (strikethrough in channel) |
| /delete <ref> | Super Admin | Delete permanently |
| /announce | Admin | Post announcement |
| /cancel | Admin | Cancel current wizard |
| /help | Admin | Show all commands |
| /start | Client | Welcome + intake |

## Features

- ✅ Admin allowlist + role-based permissions
- ✅ Preview → confirm → publish for all actions
- ✅ Offer lifecycle: draft → live → sold/deleted
- ✅ Sold offers: strikethrough in channel, kept in DB
- ✅ Client intake with auto-reply and admin forwarding
- ✅ Audit log for all admin actions
- ✅ Offer versions (undo/history)
- ✅ Inline buttons on channel posts ("Ask about this offer")
- ✅ 48h edit fallback (post new SOLD message if edit fails)
- ✅ Website API reads from same PostgreSQL DB
- ✅ PostgreSQL (not SQLite) for concurrent admins

## Brand

- Yellow: #FFD400
- Navy: #081220
- Green (FinTech): #00CB53
- Orange (Crypto): #FF8A00
- Purple (iGaming): #7A3CFF
- Font: Poppins