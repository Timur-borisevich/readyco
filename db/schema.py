"""ReadyCo Market — SQLite Schema + async helpers (Vercel-compatible)"""

import aiosqlite
import json

DB_PATH = "/tmp/readyco.db"  # Vercel serverless — use /tmp

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS admins (
    telegram_user_id INTEGER PRIMARY KEY,
    role TEXT NOT NULL DEFAULT 'publisher',
    name TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    jurisdiction TEXT,
    company_type TEXT,
    year_established TEXT,
    license_type TEXT,
    license_status TEXT,
    regulator TEXT,
    bank_emi_account TEXT,
    vat_status TEXT,
    turnover_history TEXT,
    employees TEXT,
    transfer_time TEXT,
    price TEXT,
    short_description TEXT,
    hashtags TEXT,
    channel_message_id INTEGER,
    views_count INTEGER DEFAULT 0,
    inquiry_count INTEGER DEFAULT 0,
    created_by INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    sold_at TEXT,
    sold_price TEXT
);

CREATE INDEX IF NOT EXISTS idx_offers_status ON offers(status);

CREATE TABLE IF NOT EXISTS offer_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    changed_by INTEGER NOT NULL,
    changed_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    telegram_username TEXT,
    offer_id INTEGER,
    status TEXT DEFAULT 'new',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    text TEXT,
    sent_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    channel_message_id INTEGER,
    status TEXT DEFAULT 'draft',
    published_at TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id INTEGER,
    details_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
"""


async def get_db():
    """Get DB connection — creates schema if needed (Vercel serverless)."""
    db = await aiosqlite.connect(DB_PATH)
    await db.executescript(SCHEMA_SQL)
    await db.execute("INSERT OR IGNORE INTO admins (telegram_user_id, role, name) VALUES (8339164180, 'super_admin', 'Timur')")
    await db.execute("INSERT OR IGNORE INTO admins (telegram_user_id, role, name) VALUES (143629845, 'super_admin', 'Yaroslav')")
    await db.execute("INSERT OR IGNORE INTO admins (telegram_user_id, role, name) VALUES (8585498778, 'super_admin', 'CompliChain')")
    await db.execute("INSERT OR IGNORE INTO admins (telegram_user_id, role, name) VALUES (6277380476, 'super_admin', 'Mikhail')")
    await db.commit()
    return db


async def init_db():
    """Initialize database (call on startup)."""
    db = await get_db()
    await db.close()


# === DB operations ===

async def db_next_ref() -> str:
    db = await get_db()
    try:
        async with db.execute("SELECT COUNT(*) + 1 as next FROM offers") as cur:
            row = await cur.fetchone()
            return f"RC{row[0]:03d}"
    finally:
        await db.close()


async def db_get_offer(ref: str) -> dict | None:
    db = await get_db()
    try:
        async with db.execute("SELECT * FROM offers WHERE ref = ? AND status != 'deleted'", (ref.upper(),)) as cur:
            row = await cur.fetchone()
            if row:
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
            return None
    finally:
        await db.close()


async def db_get_offers_by_status(status: str) -> list[dict]:
    db = await get_db()
    try:
        if status == "all":
            q = "SELECT * FROM offers WHERE status != 'deleted' ORDER BY created_at DESC LIMIT 50"
            params = ()
        else:
            q = "SELECT * FROM offers WHERE status = ? ORDER BY created_at DESC LIMIT 50"
            params = (status,)
        async with db.execute(q, params) as cur:
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
    finally:
        await db.close()


async def db_search_offers(query: str) -> list[dict]:
    db = await get_db()
    try:
        pattern = f"%{query}%"
        q = ("SELECT * FROM offers WHERE status = 'live' AND "
             "(jurisdiction LIKE ? OR license_type LIKE ? OR company_type LIKE ? "
             "OR ref LIKE ? OR short_description LIKE ?) ORDER BY created_at DESC LIMIT 20")
        async with db.execute(q, (pattern, pattern, pattern, pattern, pattern)) as cur:
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
    finally:
        await db.close()


async def db_insert_offer(data: dict, created_by: int) -> int:
    db = await get_db()
    try:
        async with db.execute(
            """INSERT INTO offers (ref, status, jurisdiction, company_type, year_established,
               license_type, license_status, regulator, bank_emi_account,
               vat_status, turnover_history, employees, transfer_time,
               price, short_description, hashtags, created_by)
               VALUES (?, 'live', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               RETURNING id""",
            (data.get("ref"), data.get("jurisdiction"), data.get("company_type"),
             data.get("year_established"), data.get("license_type"), data.get("license_status"),
             data.get("regulator"), data.get("bank_emi_account"), data.get("vat_status"),
             data.get("turnover_history"), data.get("employees"), data.get("transfer_time"),
             data.get("price"), data.get("short_description"), data.get("hashtags"),
             created_by)
        ) as cur:
            row = await cur.fetchone()
            offer_id = row[0]
        await db.execute(
            "INSERT INTO offer_versions (offer_id, payload_json, changed_by) VALUES (?, ?, ?)",
            (offer_id, json.dumps(data), created_by)
        )
        await db.commit()
        return offer_id
    finally:
        await db.close()


async def db_update_channel_msg(offer_id: int, msg_id: int):
    db = await get_db()
    try:
        await db.execute("UPDATE offers SET channel_message_id = ? WHERE id = ?", (msg_id, offer_id))
        await db.commit()
    finally:
        await db.close()


async def db_mark_sold(ref: str):
    db = await get_db()
    try:
        await db.execute("UPDATE offers SET status = 'sold', sold_at = datetime('now') WHERE ref = ?", (ref,))
        await db.commit()
    finally:
        await db.close()


async def db_delete_offer(ref: str):
    db = await get_db()
    try:
        await db.execute("UPDATE offers SET status = 'deleted' WHERE ref = ?", (ref,))
        await db.commit()
    finally:
        await db.close()


async def db_update_field(ref: str, field: str, value: str):
    db = await get_db()
    try:
        # Save old version
        offer = await db_get_offer(ref)
        if offer:
            await db.execute(
                "INSERT INTO offer_versions (offer_id, payload_json, changed_by) VALUES (?, ?, ?)",
                (offer["id"], json.dumps({"field": field, "old": offer.get(field), "new": value}), 0)
            )
        await db.execute(f"UPDATE offers SET {field} = ?, updated_at = datetime('now') WHERE ref = ?", (value, ref))
        await db.commit()
    finally:
        await db.close()


async def db_create_lead(user_id: int, username: str, offer_ref: str = None) -> int:
    """Create or update lead. One lead per user."""
    db = await get_db()
    try:
        offer_id = None
        if offer_ref:
            offer = await db_get_offer(offer_ref)
            if offer:
                offer_id = offer["id"]
        # Check if lead exists for this user
        async with db.execute("SELECT id FROM leads WHERE telegram_user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,)) as cur:
            row = await cur.fetchone()
        if row:
            lead_id = row[0]
            await db.execute("UPDATE leads SET last_contact_at = datetime('now'), telegram_username = ? WHERE id = ?", (username, lead_id))
            await db.commit()
            return lead_id
        async with db.execute(
            "INSERT INTO leads (telegram_user_id, telegram_username, offer_id, status, last_contact_at) VALUES (?, ?, ?, 'new', datetime('now')) RETURNING id",
            (user_id, username, offer_id)
        ) as cur:
            row = await cur.fetchone()
            lead_id = row[0] if row else 0
        await db.commit()
        return lead_id
    finally:
        await db.close()


async def db_get_lead(lead_id: int) -> dict | None:
    db = await get_db()
    try:
        async with db.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)) as cur:
            row = await cur.fetchone()
            if row:
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
            return None
    finally:
        await db.close()


async def db_get_lead_by_user(user_id: int) -> dict | None:
    db = await get_db()
    try:
        async with db.execute("SELECT * FROM leads WHERE telegram_user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,)) as cur:
            row = await cur.fetchone()
            if row:
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
            return None
    finally:
        await db.close()


async def db_list_leads(limit: int = 20) -> list[dict]:
    db = await get_db()
    try:
        async with db.execute(
            "SELECT l.*, COUNT(m.id) as msg_count, "
            "(SELECT text FROM messages WHERE lead_id = l.id ORDER BY sent_at DESC LIMIT 1) as last_msg "
            "FROM leads l LEFT JOIN messages m ON m.lead_id = l.id "
            "GROUP BY l.id ORDER BY l.last_contact_at DESC LIMIT ?",
            (limit,)
        ) as cur:
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
    finally:
        await db.close()


async def db_get_messages(lead_id: int) -> list[dict]:
    db = await get_db()
    try:
        async with db.execute(
            "SELECT * FROM messages WHERE lead_id = ? ORDER BY sent_at ASC", (lead_id,)
        ) as cur:
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
    finally:
        await db.close()


async def db_get_lead_history(user_id: int) -> list[dict]:
    """Get message history for a user via their lead."""
    lead = await db_get_lead_by_user(user_id)
    if not lead:
        return []
    return await db_get_messages(lead["id"])


async def db_update_lead_status(lead_id: int, status: str):
    db = await get_db()
    try:
        await db.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
        await db.commit()
    finally:
        await db.close()


async def db_store_message(lead_id: int, text: str, direction: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO messages (lead_id, direction, text) VALUES (?, ?, ?)",
            (lead_id, direction, text)
        )
        await db.commit()
    finally:
        await db.close()


async def db_insert_announcement(text: str, msg_id: int, created_by: int):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO announcements (text, channel_message_id, status, published_at, created_by) VALUES (?, ?, 'published', datetime('now'), ?)",
            (text, msg_id, created_by)
        )
        await db.commit()
    finally:
        await db.close()


async def db_audit(admin_id: int, action: str, entity_type: str = None, entity_id: int = None, details: dict = None):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO audit_log (admin_user_id, action, entity_type, entity_id, details_json) VALUES (?, ?, ?, ?, ?)",
            (admin_id, action, entity_type, entity_id, json.dumps(details or {}))
        )
        await db.commit()
    finally:
        await db.close()