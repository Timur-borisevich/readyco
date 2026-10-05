"""ReadyCo Market — Vercel Serverless API (Supabase Postgres)"""
import json
import os
import asyncio
import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import asyncpg

app = FastAPI(title="ReadyCo Market API")

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# === CONFIG ===
BOT_TOKEN = "8817038916:AAH3G9vxsqcptcNkZEBmDIHEIA_JevEXXpk"
CHANNEL_ID = -1004361452090
ADMIN_IDS = [8339164180, 143629845, 8585498778, 6277380476]
# Neon Postgres — serverless, free tier
DB_HOST = "ep-hidden-lab-b50u559x.c-7.us-east-2.aws.neon.tech"
DB_PORT = 5432
DB_USER = "neondb_owner"
DB_PASS = "npg_RKEL5XOkS8Zu"
DB_NAME = "neondb"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "public")

ADMIN_NAMES = {8339164180: "Timur", 143629845: "Yaroslav", 8585498778: "CompliChain", 6277380476: "Mikhail"}

# === DB ===
async def get_db():
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS,
        database=DB_NAME, ssl="require"
    )
    return conn

async def release_db(conn):
    await conn.close()

def is_admin(uid): return uid in ADMIN_IDS

def esc(text):
    if not text: return ""
    return text.replace("<","&lt;").replace(">","&gt;").replace("&","&amp;")

# === DB OPS ===
async def db_next_ref():
    conn = await get_db()
    try:
        n = await conn.fetchval("SELECT COUNT(*) + 1 FROM offers")
        return f"RC{n:03d}"
    finally: await release_db(conn)

async def db_get_offer(ref):
    conn = await get_db()
    try:
        row = await conn.fetchrow("SELECT * FROM offers WHERE ref = $1 AND status != 'deleted'", ref.upper())
        return dict(row) if row else None
    finally: await release_db(conn)

async def db_get_offers(status):
    conn = await get_db()
    try:
        if status == "all":
            rows = await conn.fetch("SELECT * FROM offers WHERE status != 'deleted' ORDER BY created_at DESC LIMIT 50")
        else:
            rows = await conn.fetch("SELECT * FROM offers WHERE status = $1 ORDER BY created_at DESC LIMIT 50", status)
        return [dict(r) for r in rows]
    finally: await release_db(conn)

async def db_insert_offer(data, created_by):
    conn = await get_db()
    try:
        oid = await conn.fetchval(
            "INSERT INTO offers (ref, status, jurisdiction, company_type, year_established, license_type, "
            "license_status, regulator, bank_emi_account, vat_status, turnover_history, employees, "
            "transfer_time, price, short_description, hashtags, created_by) "
            "VALUES ($1, 'live', $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16) RETURNING id",
            data.get('ref'), data.get('jurisdiction'), data.get('company_type'), data.get('year_established'),
            data.get('license_type'), data.get('license_status'), data.get('regulator'),
            data.get('bank_emi_account'), data.get('vat_status'), data.get('turnover_history'),
            data.get('employees'), data.get('transfer_time'), data.get('price'),
            data.get('short_description'), data.get('hashtags'), created_by)
        return oid
    finally: await release_db(conn)

async def db_update_channel_msg(oid, mid):
    conn = await get_db()
    try: await conn.execute("UPDATE offers SET channel_message_id = $1 WHERE id = $2", mid, oid)
    finally: await release_db(conn)

async def db_mark_sold(ref):
    conn = await get_db()
    try: await conn.execute("UPDATE offers SET status='sold', sold_at=now() WHERE ref=$1", ref)
    finally: await release_db(conn)

async def db_delete_offer(ref):
    conn = await get_db()
    try: await conn.execute("UPDATE offers SET status='deleted' WHERE ref=$1", ref)
    finally: await release_db(conn)

async def db_update_field(ref, field, value):
    conn = await get_db()
    try: await conn.execute(f"UPDATE offers SET {field}=$1, updated_at=now() WHERE ref=$2", value, ref)
    finally: await release_db(conn)

async def db_create_lead(uid, username, offer_ref=None):
    conn = await get_db()
    try:
        existing = await conn.fetchval("SELECT id FROM leads WHERE telegram_user_id=$1 ORDER BY created_at DESC LIMIT 1", uid)
        if existing:
            if offer_ref:
                row = await conn.fetchrow("SELECT offer_refs FROM leads WHERE id=$1", existing)
                old_refs = (row.get("offer_refs") or "") if row else ""
                all_refs = [r for r in old_refs.split(",") if r]
                if offer_ref not in all_refs:
                    all_refs.append(offer_ref)
                await conn.execute("UPDATE leads SET last_contact_at=now(), telegram_username=$1, offer_ref=$2, offer_refs=$3 WHERE id=$4",
                                 username, offer_ref, ",".join(all_refs), existing)
            else:
                await conn.execute("UPDATE leads SET last_contact_at=now(), telegram_username=$1 WHERE id=$2", username, existing)
            return existing
        lid = await conn.fetchval(
            "INSERT INTO leads (telegram_user_id, telegram_username, offer_ref, offer_refs, status, last_contact_at) "
            "VALUES ($1, $2, $3, $4, 'new', now()) RETURNING id",
            uid, username, offer_ref or "", offer_ref or "")
        return lid
    finally: await release_db(conn)

async def db_get_lead(lid):
    conn = await get_db()
    try:
        row = await conn.fetchrow("SELECT * FROM leads WHERE id=$1", lid)
        return dict(row) if row else None
    finally: await release_db(conn)

async def db_list_leads(limit=50, search=None):
    conn = await get_db()
    try:
        if search:
            rows = await conn.fetch(
                "SELECT l.*, COUNT(m.id) as msg_count, "
                "(SELECT text FROM messages WHERE lead_id=l.id ORDER BY sent_at DESC LIMIT 1) as last_msg, "
                "(SELECT sent_at FROM messages WHERE lead_id=l.id ORDER BY sent_at DESC LIMIT 1) as last_msg_time "
                "FROM leads l LEFT JOIN messages m ON m.lead_id=l.id "
                "WHERE l.is_blocked = 0 AND (l.telegram_username ILIKE $2 OR l.offer_ref ILIKE $2) "
                "GROUP BY l.id, last_msg, last_msg_time ORDER BY "
                "CASE WHEN l.status='new' THEN 0 ELSE 1 END, "
                "COALESCE((SELECT sent_at FROM messages WHERE lead_id=l.id ORDER BY sent_at DESC LIMIT 1), l.last_contact_at) DESC LIMIT $1", limit, f"%{search}%")
        else:
            rows = await conn.fetch(
                "SELECT l.*, COUNT(m.id) as msg_count, "
                "(SELECT text FROM messages WHERE lead_id=l.id ORDER BY sent_at DESC LIMIT 1) as last_msg, "
                "(SELECT sent_at FROM messages WHERE lead_id=l.id ORDER BY sent_at DESC LIMIT 1) as last_msg_time "
                "FROM leads l LEFT JOIN messages m ON m.lead_id=l.id "
                "WHERE l.is_blocked = 0 "
                "GROUP BY l.id, last_msg, last_msg_time ORDER BY "
                "CASE WHEN l.status='new' THEN 0 ELSE 1 END, "
                "COALESCE((SELECT sent_at FROM messages WHERE lead_id=l.id ORDER BY sent_at DESC LIMIT 1), l.last_contact_at) DESC LIMIT $1", limit)
        return [dict(r) for r in rows]
    finally: await release_db(conn)

async def db_mark_read(lid):
    conn = await get_db()
    try: await conn.execute("UPDATE leads SET is_read=1, status='responded' WHERE id=$1 AND status='new'", lid)
    finally: await release_db(conn)

async def db_set_tag(lid, tag):
    conn = await get_db()
    try: await conn.execute("UPDATE leads SET tag=$1 WHERE id=$2", tag, lid)
    finally: await release_db(conn)

async def db_get_messages(lid):
    conn = await get_db()
    try:
        rows = await conn.fetch("SELECT * FROM messages WHERE lead_id=$1 ORDER BY sent_at ASC", lid)
        return [dict(r) for r in rows]
    finally: await release_db(conn)

async def db_store_message(lid, text, direction, file_id=None, msg_type="text", duration=None):
    conn = await get_db()
    try: await conn.execute("INSERT INTO messages (lead_id, direction, text, file_id, msg_type, duration) VALUES ($1, $2, $3, $4, $5, $6)", lid, direction, text, file_id, msg_type, duration)
    finally: await release_db(conn)

async def db_update_lead_status(lid, status):
    conn = await get_db()
    try: await conn.execute("UPDATE leads SET status=$1 WHERE id=$2", status, lid)
    finally: await release_db(conn)

async def db_block_lead(lid):
    conn = await get_db()
    try: await conn.execute("UPDATE leads SET is_blocked=1 WHERE id=$1", lid)
    finally: await release_db(conn)

async def db_delete_lead(lid):
    conn = await get_db()
    try:
        await conn.execute("DELETE FROM messages WHERE lead_id=$1", lid)
        await conn.execute("DELETE FROM leads WHERE id=$1", lid)
    finally: await release_db(conn)

async def db_is_blocked(uid):
    conn = await get_db()
    try:
        r = await conn.fetchval("SELECT is_blocked FROM leads WHERE telegram_user_id=$1 ORDER BY created_at DESC LIMIT 1", uid)
        return r == 1
    finally: await release_db(conn)

async def db_audit(uid, action, etype=None, eid=None, details=None):
    conn = await get_db()
    try:
        admin_name = ADMIN_NAMES.get(uid, str(uid))
        await conn.execute("INSERT INTO audit_log (admin_user_id, admin_name, action, entity_type, entity_id, details_json) VALUES ($1,$2,$3,$4,$5,$6)",
            uid, admin_name, action, etype or "", str(eid) if eid is not None else "", json.dumps(details or {}))
    finally: await release_db(conn)

async def db_insert_announcement(text, mid, uid):
    conn = await get_db()
    try: await conn.execute("INSERT INTO announcements (text, status, sent_at, created_by) VALUES ($1,'published',now(),$2)", text, uid)
    finally: await release_db(conn)

# === FORMATTING ===
COUNTRY_FLAGS = {
    "poland": "🇵🇱", "lithuania": "🇱🇹", "cyprus": "🇨🇾", "curacao": "🇨🇼",
    "uae": "🇦🇪", "dubai": "🇦🇪", "uk": "🇬🇧", "usa": "🇺🇸", "germany": "🇩🇪",
    "estonia": "🇪🇪", "latvia": "🇱🇻", "malta": "🇲🇹", "ireland": "🇮🇪",
    "netherlands": "🇳🇱", "luxembourg": "🇱🇺", "belgium": "🇧🇪", "france": "🇫🇷",
    "portugal": "🇵🇹", "spain": "🇪🇸", "italy": "🇮🇹", "switzerland": "🇨🇭",
    "singapore": "🇸🇬", "hong kong": "🇭🇰", "seychelles": "🇸🇨", "belize": "🇧🇿",
    "panama": "🇵🇦", "bvi": "🇻🇬", "cayman": "🇰🇾", "gibraltar": "🇬🇮",
    "isle of man": "🇮🇲", "jersey": "🇯🇪", "australia": "🇦🇺", "canada": "🇨🇦",
}

LICENSE_EMOJI = {
    "VASP": "₿", "CASP": "₿", "EMI": "🏦", "PI": "🏦", "PSP": "💳",
    "iGaming": "♠️", "Casino": "🎰", "Betting": "🎲", "Forex": "📈",
}

def get_flag(jurisdiction):
    if not jurisdiction: return "🌍"
    j = jurisdiction.lower().strip()
    for key, flag in COUNTRY_FLAGS.items():
        if key in j: return flag
    return "🌍"

def get_license_emoji(license_type):
    if not license_type: return "📜"
    for key, emoji in LICENSE_EMOJI.items():
        if key.lower() in license_type.lower(): return emoji
    return "📜"

def format_offer_card(offer, sold=False):
    jurisdiction = offer.get('jurisdiction', '')
    flag = get_flag(jurisdiction)
    license_type = offer.get('license_type', '')
    lic_emoji = get_license_emoji(license_type)
    ref = offer.get('ref', '')
    price = offer.get('price', '')
    company_type = offer.get('company_type', '')
    year = offer.get('year_established', '')
    license_status = offer.get('license_status', '')
    regulator = offer.get('regulator', '')
    bank = offer.get('bank_emi_account', '')
    vat = offer.get('vat_status', '')
    turnover = offer.get('turnover_history', '')
    employees = offer.get('employees', '')
    transfer = offer.get('transfer_time', '')
    desc = offer.get('short_description', '')
    hashtags = offer.get('hashtags', '')
    
    if sold:
        # SOLD — everything struck through (HTML <s> tag)
        lines = ["✅ SOLD", ""]
        lines.append(f"<s>{flag} {jurisdiction}</s>")
        if company_type: lines.append(f"<s>{company_type}</s>")
        if license_type: lines.append(f"<s>{lic_emoji} {license_type}</s>")
        if price: lines.append(f"<s>💰 {price}</s>")
        lines.append("")
        lines.append(f"<s>Ref: {ref}</s>")
        lines.append("")
        lines.append("Contact: @ReadyCoAdminBot")
        return ("\n".join(lines), "HTML")
    else:
        # Active offer — structured layout
        lines = []
        lines.append("🔴 FOR SALE")
        lines.append("━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"{flag} {jurisdiction}")
        if company_type: lines.append(f"📦 {company_type}")
        if year: lines.append(f"📅 Established: {year}")
        if license_type: lines.append(f"{lic_emoji} License: {license_type}")
        if license_status: lines.append(f"   Status: {license_status}")
        if regulator: lines.append(f"🏛️ Regulator: {regulator}")
        lines.append("")
        if bank: lines.append(f"🏦 Bank/EMI: {bank}")
        if vat: lines.append(f"📋 VAT: {vat}")
        if turnover: lines.append(f"📊 Turnover: {turnover}")
        if employees: lines.append(f"👤 Employees: {employees}")
        if transfer: lines.append(f"⏱️ Transfer: {transfer}")
        lines.append("")
        lines.append(f"💰 Price: {price}")
        if desc:
            lines.append("━━━━━━━━━━━━━━━━━")
            lines.append(desc)
        if hashtags:
            lines.append("")
            lines.append(hashtags)
        lines.append("━━━━━━━━━━━━━━━━━")
        lines.append(f"Ref: {ref}")
        lines.append("💬 @ReadyCoAdminBot")
        return ("\n".join(lines), None)

def format_announcement(text): return f"📢 {text}"

def inquiry_keyboard(ref):
    return {"inline_keyboard": [[
        {"text": "💬 Ask about this offer", "url": f"https://t.me/ReadyCoAdminBot?start=inquiry_{ref}"},
    ],[
        {"text": "🌐 readyco.market", "url": "https://readyco.vercel.app"},
    ]]}

# === TG API ===
async def tg_send(chat_id, text, reply_markup=None, parse_mode=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode: payload["parse_mode"] = parse_mode
    if reply_markup: payload["reply_markup"] = reply_markup
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload) as r: return await r.json()



async def tg_set_menu_button(chat_id, text, url):
    endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/setChatMenuButton"
    payload = {"chat_id": chat_id, "menu_button": {"type": "web_app", "text": text, "web_app": {"url": url}}}
    async with aiohttp.ClientSession() as s:
        async with s.post(endpoint, json=payload) as r:
            return await r.json()
async def tg_edit(chat_id, msg_id, text, reply_markup=None, parse_mode=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": msg_id, "text": text}
    if reply_markup: payload["reply_markup"] = reply_markup
    if parse_mode: payload["parse_mode"] = parse_mode
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload) as r: return await r.json()

async def tg_delete(chat_id, msg_id):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json={"chat_id": chat_id, "message_id": msg_id}) as r: return await r.json()

# === AUTH ===
async def verify_admin(request):
    auth = request.headers.get("Authorization", "")
    init_data = request.headers.get("X-Telegram-Init-Data", auth.replace("Bearer ", ""))
    if init_data.isdigit():
        uid = int(init_data)
        if is_admin(uid): return uid
        raise HTTPException(403, "Not an admin")
    from urllib.parse import parse_qs, unquote
    if init_data:
        params = parse_qs(init_data)
        uj = params.get("user", [None])[0]
        if uj:
            u = json.loads(unquote(uj))
            uid = u.get("id")
            if uid and is_admin(uid): return uid
            raise HTTPException(403, "Not an admin")
    raise HTTPException(401, "Invalid auth")

# === MODELS ===
class ReplyReq(BaseModel):
    lead_id: int
    text: str

class OfferCreate(BaseModel):
    jurisdiction: str
    price: str
    company_type: str = ''
    year_established: str = ''
    license_type: str = ''
    license_status: str = ''
    regulator: str = ''
    bank_emi_account: str = ''
    vat_status: str = ''
    turnover_history: str = ''
    employees: str = ''
    transfer_time: str = ''
    short_description: str = ''

class OfferUpdate(BaseModel):
    jurisdiction: Optional[str] = None
    company_type: Optional[str] = None
    year_established: Optional[str] = None
    license_type: Optional[str] = None
    license_status: Optional[str] = None
    regulator: Optional[str] = None
    bank_emi_account: Optional[str] = None
    vat_status: Optional[str] = None
    turnover_history: Optional[str] = None
    employees: Optional[str] = None
    transfer_time: Optional[str] = None
    price: Optional[str] = None
    short_description: Optional[str] = None

class AnnounceReq(BaseModel):
    text: str

# === BOT WEBHOOK ===
# In-memory sessions (per-instance, works for most cases)
sessions: dict = {}

OFFER_FIELDS = [
    ("jurisdiction", "Jurisdiction? (e.g. Poland)"),
    ("company_type", "Company type? (e.g. Sp. z o.o.)"),
    ("year_established", "Year established? (e.g. 2023)"),
    ("license_type", "License type? (VASP, CASP, EMI, PI, iGaming, or skip)"),
    ("license_status", "License status? (Active, or skip)"),
    ("regulator", "Regulator? (e.g. KNF, or skip)"),
    ("bank_emi_account", "Bank / EMI account? (Yes/No)"),
    ("vat_status", "VAT status? (Active/None)"),
    ("turnover_history", "Turnover history? (Yes/No)"),
    ("employees", "Employees? (Yes/No, or number)"),
    ("transfer_time", "Transfer time? (e.g. 5-10 business days)"),
    ("price", "Price? (e.g. EUR 45,000)"),
    ("short_description", "Short description? (or skip)"),
]

def main_menu_kb():
    return {"inline_keyboard": [
        [{"text":"💬 Open Inbox", "web_app":{"url":"https://readyco.vercel.app/inbox"}}],
        [{"text":"📝 Add offer","callback_data":"menu_add"},{"text":"📋 List offers","callback_data":"menu_list"}],
        [{"text":"🔍 Search","callback_data":"menu_search"},{"text":"✏️ Edit","callback_data":"menu_edit"}],
        [{"text":"✅ Mark sold","callback_data":"menu_sold"},{"text":"🗑 Delete","callback_data":"menu_delete"}],
        [{"text":"📢 Announce","callback_data":"menu_announce"},{"text":"👤 Leads","callback_data":"menu_leads"},{"text":"⚙️ Manage","callback_data":"menu_manage"}],
    ]}

def status_menu_kb():
    return {"inline_keyboard": [
        [{"text":"🟢 Live","callback_data":"list_live"},{"text":"✅ Sold","callback_data":"list_sold"}],
        [{"text":"📋 All","callback_data":"list_all"},{"text":"🔙 Back","callback_data":"menu_main"}],
    ]}

def manage_menu_kb():
    return {"inline_keyboard": [
        [{"text":"🏠 Main menu","callback_data":"menu_main"},{"text":"📊 Stats","callback_data":"manage_stats"}],
        [{"text":"👥 Admins","callback_data":"manage_admins"},{"text":"📜 Audit log","callback_data":"manage_audit"}],
        [{"text":"🔙 Back","callback_data":"menu_main"}],
    ]}

def format_offer_list(offers, status_filter="live"):
    if not offers: return f"No offers with status: {status_filter}"
    lines = [f"📋 Offers ({status_filter}): {len(offers)}\n"]
    for o in offers:
        lines.append(f"{o['ref']} | {o.get('jurisdiction','?')} | {o.get('license_type','No license')} | {o.get('price','?')}")
    return "\n".join(lines)

async def answer_callback(callback_id):
    await aiohttp_request(f"answerCallbackQuery", {"callback_query_id": callback_id})

async def aiohttp_request(method, payload):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload) as r: return await r.json()

@app.post("/api/webhook")
async def bot_webhook(request: Request):
    data = await request.json()
    
    # === CALLBACK QUERY ===
    cb = data.get("callback_query")
    if cb:
        cb_id = cb.get("id")
        user_id = cb["from"]["id"]
        cb_data = cb.get("data", "")
        msg = cb.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        await answer_callback(cb_id)
        
        if not is_admin(user_id):
            # Client selecting an offer
            if cb_data.startswith("client_offer_"):
                ref = cb_data.replace("client_offer_", "")
                offer = await db_get_offer(ref)
                if not offer:
                    await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": f"Offer {ref} is no longer available."})
                    return {"ok": True}
                lead_id = await db_create_lead(user_id, cb["from"].get("username",""), ref)
                sessions[user_id] = {"action": "client_inquiry", "data": {"offer_ref": ref, "lead_id": lead_id}}
                await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"],
                    "text": f"📋 {ref} — {offer.get('jurisdiction','')} {offer.get('license_type','')}\n📊 Price: {offer.get('price','On request')}\n\nSend your question about this offer here. Our team will respond privately."})
            return {"ok": True}
        
        # Admin callbacks
        if cb_data == "cancel_action":
            if user_id in sessions: del sessions[user_id]
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "✅ Cancelled.", "reply_markup": main_menu_kb()})
            return {"ok": True}
        
        if cb_data == "menu_main":
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "📋 ReadyCo Admin\n\nTap a button.", "reply_markup": main_menu_kb()})
            return {"ok": True}
        
        if cb_data == "menu_add":
            sessions[user_id] = {"action": "add", "step": 0, "data": {}}
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"],
                "text": f"📝 Creating new offer. Step 1/{len(OFFER_FIELDS)}:\n\n{OFFER_FIELDS[0][1]}\n\nSend /cancel to abort."})
            return {"ok": True}
        
        if cb_data == "menu_list":
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "📋 List offers\n\nSelect status:", "reply_markup": status_menu_kb()})
            return {"ok": True}
        
        if cb_data.startswith("list_"):
            status = cb_data.replace("list_", "")
            offers = await db_get_offers(status)
            text = format_offer_list(offers, status)
            kb = {"inline_keyboard": [[{"text":"🔙 Back to menu","callback_data":"menu_main"}]]}
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": text, "reply_markup": kb})
            return {"ok": True}
        
        if cb_data == "menu_search":
            kb = {"inline_keyboard": [[{"text":"🔙 Back","callback_data":"menu_main"}]]}
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"],
                "text": "🔍 Search\n\nUse /search <keyword>\n(e.g. /search VASP Poland)", "reply_markup": kb})
            return {"ok": True}
        
        if cb_data == "menu_edit":
            offers = await db_get_offers("live")
            if not offers:
                await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "No live offers.", "reply_markup": main_menu_kb()})
                return {"ok": True}
            kb = {"inline_keyboard": [[{"text": f"{o['ref']} | {o.get('jurisdiction','?')} | {o.get('price','?')}", "callback_data": f"selectedit_{o['ref']}"}] for o in offers] + [[{"text":"🔙 Back","callback_data":"menu_main"}]]}
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "✏️ Select offer to edit:", "reply_markup": kb})
            return {"ok": True}
        
        if cb_data.startswith("selectedit_"):
            ref = cb_data.replace("selectedit_", "")
            offer = await db_get_offer(ref)
            if not offer: return {"ok": True}
            fields = ["jurisdiction","company_type","year_established","license_type","license_status","regulator","bank_emi_account","vat_status","turnover_history","employees","transfer_time","price","short_description"]
            kb_rows = []
            for i in range(0, len(fields), 2):
                row = [{"text": f, "callback_data": f"edit_{ref}_{f}"} for f in fields[i:i+2]]
                kb_rows.append(row)
            kb_rows.append([{"text":"🔙 Back","callback_data":"menu_edit"}])
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"],
                "text": f"Editing {ref} — {offer.get('jurisdiction','?')} {offer.get('license_type','')}\n\nCurrent price: {offer.get('price','?')}\n\nWhich field to edit?", "reply_markup": {"inline_keyboard": kb_rows}})
            return {"ok": True}
        
        if cb_data == "menu_sold":
            offers = await db_get_offers("live")
            if not offers:
                await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "No live offers.", "reply_markup": main_menu_kb()})
                return {"ok": True}
            kb = {"inline_keyboard": [[{"text": f"{o['ref']} | {o.get('jurisdiction','?')} | {o.get('price','?')}", "callback_data": f"sold_{o['ref']}"}] for o in offers] + [[{"text":"🔙 Back","callback_data":"menu_main"}]]}
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "✅ Which offer is sold?", "reply_markup": kb})
            return {"ok": True}
        
        if cb_data == "menu_delete":
            offers = await db_get_offers("all")
            if not offers:
                await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "No offers.", "reply_markup": main_menu_kb()})
                return {"ok": True}
            kb = {"inline_keyboard": [[{"text": f"{o['ref']} | {o.get('jurisdiction','?')} | {o['status']}", "callback_data": f"delete_{o['ref']}"}] for o in offers[:15]] + [[{"text":"🔙 Back","callback_data":"menu_main"}]]}
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "⚠️ DELETE which offer?", "reply_markup": kb})
            return {"ok": True}
        
        if cb_data == "menu_announce":
            sessions[user_id] = {"action": "announce", "data": {}}
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "📢 Send the announcement text:"})
            return {"ok": True}
        
        if cb_data == "menu_leads":
            leads = await db_list_leads(20)
            if not leads:
                await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "No leads yet.", "reply_markup": main_menu_kb()})
                return {"ok": True}
            kb_rows = []
            for l in leads:
                lname = l.get('telegram_username') or f"ID:{l['telegram_user_id']}"
                kb_rows.append([{"text": f"● {lname} ({l.get('msg_count',0)} msgs)", "callback_data": f"leadview_{l['id']}"}])
            kb_rows.append([{"text":"🔙 Back","callback_data":"menu_main"}])
            kb = {"inline_keyboard": kb_rows}
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "👤 Leads\n\nSelect to view history:", "reply_markup": kb})
            return {"ok": True}
        
        if cb_data.startswith("leadview_"):
            lid = int(cb_data.replace("leadview_", ""))
            lead = await db_get_lead(lid)
            if not lead: return {"ok": True}
            msgs = await db_get_messages(lid)
            name = lead.get("telegram_username") or f"ID:{lead['telegram_user_id']}"
            lines = [f"👤 {name} — {len(msgs)} messages\n"]
            for m in msgs[-10:]:
                d = "👤" if m["direction"] == "client_to_admin" else "💬"
                ts = str(m["sent_at"])[:16] if m.get("sent_at") else ""
                lines.append(f"{d} [{ts}] {esc(m['text'][:100])}")
            kb = {"inline_keyboard": [[{"text":"💬 Reply","callback_data":f"reply_{lid}"},{"text":"🔙 Back to leads","callback_data":"menu_leads"}]]}
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "\n".join(lines), "reply_markup": kb, "parse_mode": "HTML"})
            return {"ok": True}
        
        if cb_data.startswith("reply_"):
            lid = int(cb_data.replace("reply_", ""))
            lead = await db_get_lead(lid)
            if not lead: return {"ok": True}
            sessions[user_id] = {"action": "reply", "data": {"lead_id": lid}}
            name = lead.get("telegram_username") or f"ID:{lead['telegram_user_id']}"
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": f"💬 Replying to {name}\n\nSend your reply text:"})
            return {"ok": True}
        
        if cb_data == "menu_manage":
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"],
                "text": "⚙️ Manage\n\n🏠 Main menu\n📊 Stats\n👥 Admins\n📜 Audit log", "reply_markup": manage_menu_kb()})
            return {"ok": True}
        
        if cb_data == "manage_stats":
            conn = await get_db()
            try:
                live = await conn.fetchval("SELECT COUNT(*) FROM offers WHERE status='live'")
                sold = await conn.fetchval("SELECT COUNT(*) FROM offers WHERE status='sold'")
                leads = await conn.fetchval("SELECT COUNT(*) FROM leads")
                ann = await conn.fetchval("SELECT COUNT(*) FROM announcements WHERE status='published'")
            finally: await release_db(conn)
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"],
                "text": f"📊 Statistics\n\n🟢 Live: {live}\n✅ Sold: {sold}\n👤 Leads: {leads}\n📢 Announcements: {ann}", "reply_markup": manage_menu_kb()})
            return {"ok": True}
        
        if cb_data == "manage_admins":
            conn = await get_db()
            try:
                rows = await conn.fetch("SELECT telegram_user_id, role, name FROM admins WHERE is_active=1 ORDER BY added_at")
            finally: await release_db(conn)
            lines = [f"• {ADMIN_NAMES.get(r['telegram_user_id'], r.get('name','?'))} — {r['role']} (ID: {r['telegram_user_id']})" for r in rows]
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"],
                "text": f"👥 Admins ({len(rows)})\n\n" + "\n".join(lines), "reply_markup": manage_menu_kb()})
            return {"ok": True}
        
        if cb_data == "manage_audit":
            conn = await get_db()
            try:
                rows = await conn.fetch("SELECT admin_user_id, action, entity_type, created_at FROM audit_log ORDER BY created_at DESC LIMIT 10")
            finally: await release_db(conn)
            if not rows:
                await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "📜 Audit log\n\nNo actions yet.", "reply_markup": manage_menu_kb()})
                return {"ok": True}
            lines = [f"• {str(r['created_at'])[:19]} | {ADMIN_NAMES.get(r['admin_user_id'], '?')} → {r['action']} {r.get('entity_type','') or ''}" for r in rows]
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"],
                "text": "📜 Audit log (last 10)\n\n" + "\n".join(lines), "reply_markup": manage_menu_kb()})
            return {"ok": True}
        
        # sold_ callback
        if cb_data.startswith("sold_"):
            ref = cb_data.replace("sold_", "")
            offer = await db_get_offer(ref)
            if not offer: return {"ok": True}
            card = format_offer_card(offer, sold=True)
            kb = {"inline_keyboard": [[{"text":"✅ Confirm SOLD","callback_data":f"confirm_sold_{ref}"},{"text":"❌ Cancel","callback_data":"cancel_action"}]]}
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": f"SOLD PREVIEW\n\n{card}\n\nMark as sold?", "reply_markup": kb})
            return {"ok": True}
        
        if cb_data.startswith("confirm_sold_"):
            ref = cb_data.replace("confirm_sold_", "")
            offer = await db_get_offer(ref)
            if not offer: return {"ok": True}
            card, parse_mode = format_offer_card(offer, sold=True)
            kb = inquiry_keyboard(ref)
            if offer.get("channel_message_id"):
                r = await tg_edit(CHANNEL_ID, offer["channel_message_id"], card, kb, parse_mode=parse_mode)
                if not r.get("ok"):
                    await tg_send(CHANNEL_ID, f"✅ SOLD\n\n{card}", kb, parse_mode=parse_mode)
            await db_mark_sold(ref)
            await db_audit(user_id, "sold", "offer", offer.get("id"), {"ref": ref})
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": f"✅ {ref} marked as SOLD!", "reply_markup": main_menu_kb()})
            return {"ok": True}
        
        # delete_ callback
        if cb_data.startswith("delete_"):
            ref = cb_data.replace("delete_", "")
            offer = await db_get_offer(ref)
            if not offer: return {"ok": True}
            kb = {"inline_keyboard": [[{"text":"⚠️ Delete permanently","callback_data":f"confirm_delete_{ref}"},{"text":"❌ Cancel","callback_data":"cancel_action"}]]}
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"],
                "text": f"⚠️ DELETE {ref}\n\n{offer.get('jurisdiction','?')} | {offer.get('price','?')}\n\nCannot be undone.", "reply_markup": kb})
            return {"ok": True}
        
        if cb_data.startswith("confirm_delete_"):
            ref = cb_data.replace("confirm_delete_", "")
            offer = await db_get_offer(ref)
            if not offer: return {"ok": True}
            if offer.get("channel_message_id"):
                await tg_delete(CHANNEL_ID, offer["channel_message_id"])
            await db_delete_offer(ref)
            await db_audit(user_id, "delete", "offer", offer.get("id"), {"ref": ref})
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": f"✅ {ref} deleted.", "reply_markup": main_menu_kb()})
            return {"ok": True}
        
        # edit_ callback
        if cb_data.startswith("edit_"):
            parts = cb_data.split("_", 2)
            ref, field = parts[1], parts[2]
            offer = await db_get_offer(ref)
            sessions[user_id] = {"action": "edit_field", "data": {"ref": ref, "field": field}}
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"],
                "text": f"Editing {ref} → {field}\nCurrent: {offer.get(field, '')}\n\nSend new value:"})
            return {"ok": True}
        
        # publish_ callback
        if cb_data.startswith("publish_"):
            if cb_data == "publish_announce":
                session = sessions.get(user_id, {})
                text = session.get("data", {}).get("text", "")
                if not text:
                    await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "❌ Session expired."})
                    return {"ok": True}
                formatted = format_announcement(text)
                r = await tg_send(CHANNEL_ID, formatted)
                if r.get("ok"):
                    await db_insert_announcement(text, r["result"]["message_id"], user_id)
                    await db_audit(user_id, "announce", "announcement", None, {"text": text[:100]})
                if user_id in sessions: del sessions[user_id]
                await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "✅ Announcement published!", "reply_markup": main_menu_kb()})
                return {"ok": True}
            else:
                ref = cb_data.replace("publish_", "")
                session = sessions.get(user_id, {})
                d = session.get("data", {})
                if not d:
                    await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "❌ Session expired. Use /add again."})
                    return {"ok": True}
                oid = await db_insert_offer(d, user_id)
                card, _ = format_offer_card(d)
                kb = inquiry_keyboard(ref)
                r = await tg_send(CHANNEL_ID, card, kb)
                if r.get("ok"):
                    await db_update_channel_msg(oid, r["result"]["message_id"])
                await db_audit(user_id, "add", "offer", oid, {"ref": ref})
                if user_id in sessions: del sessions[user_id]
                await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": f"✅ Published {ref} to channel!", "reply_markup": main_menu_kb()})
                return {"ok": True}
        
        if cb_data.startswith("applyedit_"):
            parts = cb_data.split("_", 2)
            ref, field = parts[1], parts[2]
            session = sessions.get(user_id, {})
            new_val = session.get("data", {}).get("new_value", "")
            if not new_val:
                await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "❌ Session expired."})
                return {"ok": True}
            await db_update_field(ref, field, new_val)
            offer = await db_get_offer(ref)
            if offer.get("channel_message_id") and offer["status"] == "live":
                card, _ = format_offer_card(offer)
                await tg_edit(CHANNEL_ID, offer["channel_message_id"], card, inquiry_keyboard(ref))
            await db_audit(user_id, "edit", "offer", offer.get("id"), {"ref": ref, "field": field})
            if user_id in sessions: del sessions[user_id]
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": f"✅ {ref} updated: {field} = {new_val}", "reply_markup": main_menu_kb()})
            return {"ok": True}
        
        if cb_data.startswith("sendreply_"):
            lid = int(cb_data.replace("sendreply_", ""))
            session = sessions.get(user_id, {})
            reply_text = session.get("data", {}).get("reply_text", "")
            if not reply_text:
                await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "❌ Session expired."})
                return {"ok": True}
            lead = await db_get_lead(lid)
            if not lead: return {"ok": True}
            r = await tg_send(lead["telegram_user_id"], f"💬 ReadyCo Market:\n\n{reply_text}")
            if r.get("ok"):
                await db_store_message(lid, reply_text, "admin_to_client")
                await db_update_lead_status(lid, "responded")
                await db_audit(user_id, "reply", "lead", lid, {"text": reply_text[:100]})
            if user_id in sessions: del sessions[user_id]
            await aiohttp_request("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": "✅ Reply sent to client!", "reply_markup": main_menu_kb()})
            return {"ok": True}
        
        return {"ok": True}
    
    # === MESSAGE ===
    msg = data.get("message") or data.get("edited_message")
    if not msg: return {"ok": True}
    user = msg.get("from", {})
    user_id = user.get("id")
    username = user.get("username", "")
    first_name = user.get("first_name", "")
    text = msg.get("text", "")
    chat_id = msg.get("chat", {}).get("id")
    
    # /start
    if text.startswith("/start"):
        args = text.split()[1:] if len(text.split()) > 1 else []
        offer_ref = None
        if args and args[0].startswith("inquiry_"):
            offer_ref = args[0].replace("inquiry_", "")
        if is_admin(user_id):
            await tg_set_menu_button(user_id, "Inbox", "https://readyco.vercel.app/inbox")
            kb = {"inline_keyboard": [[{"text":"💬 Open Inbox", "web_app":{"url":"https://readyco.vercel.app/inbox"}}]]}
            await tg_send(user_id, f"👋 Welcome back, {first_name}!\n\nReadyCo Admin\nChannel: @readyco\n\nTap the button below (or the menu button) to open the Inbox.", reply_markup=kb)
            return {"ok": True}
        lead_id = await db_create_lead(user_id, username, offer_ref)
        if offer_ref:
            offer = await db_get_offer(offer_ref)
            if offer:
                await tg_send(user_id, f"👋 Hi! You asked about {offer_ref} — {offer.get('jurisdiction', '')} {offer.get('license_type', '')}.\n\n📊 Price: {offer.get('price', 'On request')}\n\nSend your question here — our team will respond privately.")
            else:
                await tg_send(user_id, f"👋 Hi! Offer {offer_ref} may no longer be available.\n\nSend your question here — our team will respond privately.")
        else:
            offers = await db_get_offers("live")
            if offers:
                kb = {"inline_keyboard": [[{"text": f"{o['ref']} | {o.get('jurisdiction','?')} | {o.get('license_type','')} | {o.get('price','?')}", "callback_data": f"client_offer_{o['ref']}"}] for o in offers] + [[{"text":"🌐 Visit website","url":"https://readyco.vercel.app"},{"text":"📺 Channel @readyco","url":"https://t.me/readyco"}]]}
                await tg_send(user_id, "👋 Welcome to ReadyCo Market!\n\nWe help you buy and sell licensed companies:\n🏦 FinTech (EMI, PI, PSP)\n₿ Crypto (VASP, CASP, Exchanges)\n♠️ iGaming (Casinos, Betting, Gaming Licenses)\n\nSelect an offer 👇", kb)
            else:
                kb = {"inline_keyboard": [[{"text":"🌐 Visit website","url":"https://readyco.vercel.app"},{"text":"📺 Channel @readyco","url":"https://t.me/readyco"}]]}
                await tg_send(user_id, "👋 Welcome to ReadyCo Market!\n\nWe help you buy and sell licensed companies.\n\nNo offers available yet. Send your question here — our team will respond privately.", kb)
        return {"ok": True}
    
    # /menu
    if text.startswith("/menu") and is_admin(user_id):
        await tg_send(user_id, "📋 ReadyCo Admin\n\nOpen Inbox for chat & offers, or tap a command below.", reply_markup=main_menu_kb())
        return {"ok": True}
    
    # /inbox — open Mini App in Telegram
    if text.startswith("/inbox") and is_admin(user_id):
        await tg_set_menu_button(user_id, "Inbox", "https://readyco.vercel.app/inbox")
        kb = {"inline_keyboard": [[{"text":"💬 Open Inbox","web_app":{"url":"https://readyco.vercel.app/inbox"}}]]}
        await tg_send(user_id, "💬 ReadyCo Inbox\n\nTap the menu button or the button below to open.", reply_markup=kb)
        return {"ok": True}
    
    # /cancel
    if text.startswith("/cancel") and is_admin(user_id):
        if user_id in sessions: del sessions[user_id]
        await tg_send(user_id, "✅ Cancelled.", reply_markup=main_menu_kb())
        return {"ok": True}
    
    # Admin session messages (add/announce/edit_field/reply)
    if is_admin(user_id) and user_id in sessions:
        session = sessions[user_id]
        action = session.get("action")
        
        if action == "add":
            step = session["step"]
            field_name, _ = OFFER_FIELDS[step]
            session["data"][field_name] = text if text.lower() != "skip" else None
            session["step"] += 1
            if session["step"] < len(OFFER_FIELDS):
                next_field, next_prompt = OFFER_FIELDS[session["step"]]
                await tg_send(user_id, f"Step {session['step'] + 1}/{len(OFFER_FIELDS)}:\n\n{next_prompt}\n\nSend /cancel to abort.")
            else:
                d = session["data"]
                ref = await db_next_ref()
                d["ref"] = ref
                HT = {"VASP":"#VASP #Crypto","CASP":"#CASP #Crypto","EMI":"#EMI #FinTech","PI":"#PI #FinTech","iGaming":"#iGaming #Gaming"}
                ht = HT.get(d.get("license_type",""), "")
                if ht:
                    j = (d.get("jurisdiction") or "").split(" ")[0]
                    d["hashtags"] = f"#{j} {ht} #ForSale"
                preview, _ = format_offer_card(d)
                kb = {"inline_keyboard": [[{"text":"✅ Publish","callback_data":f"publish_{ref}"},{"text":"❌ Cancel","callback_data":"cancel_action"}]]}
                await tg_send(user_id, f"📋 PREVIEW\n\n{preview}\n\nPublish to channel?", kb)
            return {"ok": True}
        
        if action == "announce":
            sessions[user_id]["data"]["text"] = text
            formatted = format_announcement(text)
            kb = {"inline_keyboard": [[{"text":"✅ Publish","callback_data":"publish_announce"},{"text":"❌ Cancel","callback_data":"cancel_action"}]]}
            await tg_send(user_id, f"📋 PREVIEW\n\n{formatted}\n\nPublish?", kb)
            return {"ok": True}
        
        if action == "edit_field":
            ref = session["data"]["ref"]
            field = session["data"]["field"]
            session["data"]["new_value"] = text
            offer = await db_get_offer(ref)
            old = offer.get(field, "")
            kb = {"inline_keyboard": [[{"text":"✅ Apply","callback_data":f"applyedit_{ref}_{field}"},{"text":"❌ Cancel","callback_data":"cancel_action"}]]}
            await tg_send(user_id, f"Editing {ref} → {field}\nOld: {old}\nNew: {text}\n\nApply?", kb)
            return {"ok": True}
        
        if action == "reply":
            lid = session["data"]["lead_id"]
            lead = await db_get_lead(lid)
            if not lead:
                await tg_send(user_id, "❌ Lead not found.")
                del sessions[user_id]
                return {"ok": True}
            sessions[user_id]["data"]["reply_text"] = text
            name = lead.get("telegram_username") or f"ID:{lead['telegram_user_id']}"
            kb = {"inline_keyboard": [[{"text":"✅ Send","callback_data":f"sendreply_{lid}"},{"text":"❌ Cancel","callback_data":"cancel_action"}]]}
            await tg_send(user_id, f"📋 Reply preview\n\nTo: {name}\nMessage: {text}\n\nSend?", kb)
            return {"ok": True}
    
    # Client message = forward to admins (unless blocked)
    if not is_admin(user_id):
        if await db_is_blocked(user_id):
            return {"ok": True}
        
        # Determine message type and content
        msg_type = "text"
        msg_content = ""
        photo_file_id = None
        duration = None
        
        if msg.get("text"):
            msg_type = "text"
            msg_content = msg["text"]
        elif msg.get("photo"):
            msg_type = "photo"
            # Get largest photo
            photos = msg["photo"]
            photo_file_id = photos[-1]["file_id"]
            msg_content = msg.get("caption", "[Photo]")
        elif msg.get("sticker"):
            msg_type = "sticker"
            msg_content = f"[Sticker: {msg['sticker'].get('emoji','')}]"
        elif msg.get("document"):
            msg_type = "document"
            doc = msg["document"]
            msg_content = f"[Document: {doc.get('file_name','file')}]"
            photo_file_id = doc.get("file_id")
        elif msg.get("voice"):
            msg_type = "voice"
            msg_content = "[Voice message]"
            photo_file_id = msg["voice"].get("file_id")
            duration = msg["voice"].get("duration")
        elif msg.get("video"):
            msg_type = "video"
            msg_content = f"[Video: {msg.get('caption','')}]"
        elif msg.get("audio"):
            msg_type = "audio"
            msg_content = f"[Audio: {msg.get('caption','')}]"
            photo_file_id = msg["audio"].get("file_id")
            duration = msg["audio"].get("duration")
        elif msg.get("contact"):
            msg_type = "contact"
            c = msg["contact"]
            msg_content = f"[Contact: {c.get('first_name','')} {c.get('phone_number','')}]"
        elif msg.get("location"):
            msg_type = "location"
            msg_content = "[Location]"
        elif msg.get("animation"):
            msg_type = "animation"
            msg_content = f"[GIF: {msg.get('caption','')}]"
        else:
            msg_content = "[Unsupported message type]"
        
        if not msg_content and not photo_file_id:
            return {"ok": True}
        
        lead_id = await db_create_lead(user_id, username)
        await db_store_message(lead_id, msg_content, "client_to_admin", file_id=photo_file_id, msg_type=msg_type, duration=duration)
        lead = await db_get_lead(lead_id)
        offer_info = ""
        if lead and lead.get("offer_ref"):
            offer = await db_get_offer(lead["offer_ref"])
            if offer:
                offer_info = f"\n📊 Offer: {offer['ref']} — {offer.get('jurisdiction','')} {offer.get('license_type','') or ''} | {offer.get('price','?')}"
        history = await db_get_messages(lead_id)
        history_text = ""
        if len(history) > 1:
            lines = []
            for m in history[:-1]:
                d = "Client" if m["direction"] == "client_to_admin" else "Admin"
                ts = str(m["sent_at"])[:16] if m.get("sent_at") else ""
                lines.append(f"[{ts}] {d}: {esc(m['text'][:80])}")
            history_text = "\n\n📜 History:\n" + "\n".join(lines[-5:])
        user_link = f"t.me/{username}" if username else f"tg://user?id={user_id}"
        user_disp = f"@{username}" if username else f"ID: {user_id}"
        
        type_icon = {"photo":"📷","sticker":"🎨","document":"📎","voice":"🎤","video":"🎬","audio":"🎵","contact":"👤","location":"📍","animation":"🎞️","text":"💬"}.get(msg_type, "💬")
        
        admin_text = (
            f"👤 <b>New inquiry</b>\n"
            f"From: {user_disp}\n"
            f"Name: {esc(first_name)}"
            f"{offer_info}\n\n"
            f"{type_icon} {esc(msg_content)}"
            f"{history_text}\n\n"
            f"Reply: {user_link}")
        kb = {"inline_keyboard": [[{"text":"💬 Reply","callback_data":f"reply_{lead_id}"}]]}
        
        for admin_id in ADMIN_IDS:
            try:
                # If photo, send photo to admins
                if photo_file_id and msg_type in ("photo","document"):
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto" if msg_type == "photo" else f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
                    payload = {"chat_id": admin_id, "caption": admin_text, "parse_mode": "HTML", "reply_markup": kb}
                    if msg_type == "photo":
                        payload["photo"] = photo_file_id
                    else:
                        payload["document"] = photo_file_id
                    async with aiohttp.ClientSession() as s:
                        async with s.post(url, json=payload) as r:
                            resp = await r.json()
                            if not resp.get("ok"):
                                await tg_send(admin_id, admin_text, kb, parse_mode="HTML")
                else:
                    await tg_send(admin_id, admin_text, kb, parse_mode="HTML")
            except: pass
        
        await tg_send(user_id, "✅ Thank you! Our team will respond shortly.")
        return {"ok": True}
    
    return {"ok": True}

# === PUBLIC ENDPOINTS ===
@app.get("/api/health")
async def health(): return {"ok": True, "service": "readyco-market"}

@app.get("/api/offers")
async def api_offers(status: str = "live"):
    return {"offers": await db_get_offers(status)}

@app.get("/api/offers/{ref}")
async def api_offer(ref: str):
    o = await db_get_offer(ref)
    if not o: raise HTTPException(404, "Not found")
    return {"offer": o}

# === ADMIN: LEADS ===
@app.get("/api/admin/leads")
async def admin_leads(request: Request, search: str = None):
    await verify_admin(request)
    return {"leads": await db_list_leads(50, search)}

@app.get("/api/admin/leads/export")
async def admin_export_leads(request: Request):
    uid = await verify_admin(request)
    leads = await db_list_leads(500)
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID","Username","Telegram ID","Status","Tag","Offer Ref","Messages","Created","Last Contact"])
    for l in leads:
        writer.writerow([l["id"], l.get("telegram_username",""), l["telegram_user_id"], l["status"], l.get("tag",""), l.get("offer_ref",""), l.get("msg_count",0), str(l.get("created_at","")), str(l.get("last_contact_at",""))])
    from fastapi.responses import Response
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=leads.csv"})

@app.get("/api/admin/leads/{lead_id}")
async def admin_lead(lead_id: int, request: Request):
    await verify_admin(request)
    lead = await db_get_lead(lead_id)
    if not lead: raise HTTPException(404, "Lead not found")
    await db_mark_read(lead_id)
    return {"lead": lead, "messages": await db_get_messages(lead_id)}

@app.post("/api/admin/leads/{lead_id}/tag")
async def admin_set_tag(lead_id: int, request: Request):
    uid = await verify_admin(request)
    body = await request.json()
    tag = body.get("tag")
    await db_set_tag(lead_id, tag)
    await db_audit(uid, "tag", "lead", lead_id, {"tag": tag})
    return {"ok": True}

@app.post("/api/admin/leads/{lead_id}/block")
async def admin_block_lead(lead_id: int, request: Request):
    uid = await verify_admin(request)
    lead = await db_get_lead(lead_id)
    if not lead: raise HTTPException(404, "Lead not found")
    await db_block_lead(lead_id)
    await db_audit(uid, "block", "lead", lead_id, {})
    return {"ok": True}

@app.delete("/api/admin/leads/{lead_id}")
async def admin_delete_lead(lead_id: int, request: Request):
    uid = await verify_admin(request)
    lead = await db_get_lead(lead_id)
    if not lead: raise HTTPException(404, "Lead not found")
    await db_delete_lead(lead_id)
    await db_audit(uid, "delete_lead", "lead", lead_id, {})
    return {"ok": True}

@app.post("/api/admin/reply")
async def admin_reply(req: ReplyReq, request: Request):
    uid = await verify_admin(request)
    lead = await db_get_lead(req.lead_id)
    if not lead: raise HTTPException(404, "Lead not found")
    admin_name = ADMIN_NAMES.get(uid, f"Admin {uid}")
    r = await tg_send(lead["telegram_user_id"], f"💬 ReadyCo Market:\n\n{req.text}")
    if not r.get("ok"): raise HTTPException(500, f"TG error: {r.get('description')}")
    await db_store_message(req.lead_id, req.text, "admin_to_client")
    await db_update_lead_status(req.lead_id, "responded")
    await db_audit(uid, "reply", "lead", req.lead_id, {"text": req.text[:100], "admin": admin_name})
    return {"ok": True, "admin_name": admin_name}

@app.get("/api/admin/file/{file_id}")
async def admin_get_file(file_id: str, request: Request):
    """Get Telegram file URL for display in Mini App."""
    await verify_admin(request)
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile"
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json={"file_id": file_id}) as r:
            data = await r.json()
            if data.get("ok"):
                file_path = data["result"]["file_path"]
                file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                return {"url": file_url}
            raise HTTPException(404, "File not found")

@app.post("/api/admin/sendfile")
async def admin_send_file(request: Request):
    """Admin sends file to client via bot."""
    uid = await verify_admin(request)
    form = await request.form()
    file = form.get("file")
    lead_id = int(form.get("lead_id"))
    lead = await db_get_lead(lead_id)
    if not lead: raise HTTPException(404, "Lead not found")
    admin_name = ADMIN_NAMES.get(uid, f"Admin {uid}")
    
    # Read file
    contents = await file.read()
    filename = file.filename or "file"
    is_image = file.content_type and file.content_type.startswith("image/")
    
    # Send to client via Telegram
    if is_image:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        data = aiohttp.FormData()
        data.add_field("chat_id", str(lead["telegram_user_id"]))
        data.add_field("caption", f"💬 ReadyCo Market")
        data.add_field("photo", contents, filename=filename, content_type=file.content_type)
    else:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
        data = aiohttp.FormData()
        data.add_field("chat_id", str(lead["telegram_user_id"]))
        data.add_field("caption", f"💬 ReadyCo Market")
        data.add_field("document", contents, filename=filename, content_type=file.content_type)
    
    async with aiohttp.ClientSession() as s:
        async with s.post(url, data=data) as r:
            resp = await r.json()
            if not resp.get("ok"):
                raise HTTPException(500, f"TG error: {resp.get('description')}")
            # Get file_id from response
            result = resp["result"]
            file_id = None
            if "photo" in result:
                file_id = result["photo"][-1]["file_id"]
            elif "document" in result:
                file_id = result["document"]["file_id"]
    
    msg_type = "photo" if is_image else "document"
    await db_store_message(lead_id, f"[{msg_type}: {filename}]", "admin_to_client", file_id=file_id, msg_type=msg_type)
    await db_update_lead_status(lead_id, "responded")
    await db_audit(uid, "sendfile", "lead", lead_id, {"file": filename, "admin": admin_name})
    return {"ok": True}

# === ADMIN: STATS ===
@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    await verify_admin(request)
    conn = await get_db()
    try:
        live = await conn.fetchval("SELECT COUNT(*) FROM offers WHERE status='live'")
        sold = await conn.fetchval("SELECT COUNT(*) FROM offers WHERE status='sold'")
        leads = await conn.fetchval("SELECT COUNT(*) FROM leads")
        new = await conn.fetchval("SELECT COUNT(*) FROM leads WHERE status='new'")
        resp = await conn.fetchval("SELECT COUNT(*) FROM leads WHERE status='responded'")
        ann = await conn.fetchval("SELECT COUNT(*) FROM announcements WHERE status='published'")
    finally: await release_db(conn)
    return {"live_offers": live, "sold_offers": sold, "total_leads": leads, "new_leads": new, "responded_leads": resp, "announcements": ann}

# === ADMIN: OFFERS ===
@app.get("/api/admin/offers")
async def admin_offers(request: Request, status: str = "all"):
    await verify_admin(request)
    return {"offers": await db_get_offers(status)}

@app.post("/api/admin/offers")
async def admin_create(req: OfferCreate, request: Request):
    uid = await verify_admin(request)
    ref = await db_next_ref()
    data = req.model_dump()
    data["ref"] = ref
    # Ensure no None values — Neon schema has NOT NULL on all text columns
    for k, v in data.items():
        if v is None:
            data[k] = ''
    HT = {"VASP":"#VASP #Crypto","CASP":"#CASP #Crypto","EMI":"#EMI #FinTech","PI":"#PI #FinTech","iGaming":"#iGaming #Gaming"}
    ht = HT.get(data.get("license_type",""), "")
    if ht:
        j = (data.get("jurisdiction") or "").split(" ")[0]
        data["hashtags"] = f"#{j} {ht} #ForSale"
    else:
        data.setdefault("hashtags", "")
    oid = await db_insert_offer(data, uid)
    card, _ = format_offer_card(data)
    kb = inquiry_keyboard(ref)
    r = await tg_send(CHANNEL_ID, card, kb)
    if r.get("ok"):
        await db_update_channel_msg(oid, r["result"]["message_id"])
    await db_audit(uid, "add", "offer", str(oid), {"ref": ref})
    return {"ok": True, "ref": ref}

@app.put("/api/admin/offers/{ref}")
async def admin_update(ref: str, req: OfferUpdate, request: Request):
    uid = await verify_admin(request)
    o = await db_get_offer(ref)
    if not o: raise HTTPException(404, "Not found")
    for f, v in req.model_dump(exclude_none=True).items():
        await db_update_field(ref, f, v)
    o = await db_get_offer(ref)
    if o.get("channel_message_id") and o["status"] == "live":
        card, _ = format_offer_card(o)
        await tg_edit(CHANNEL_ID, o["channel_message_id"], card, inquiry_keyboard(ref))
    await db_audit(uid, "edit", "offer", o.get("id"), {"ref": ref})
    return {"ok": True}

@app.post("/api/admin/offers/{ref}/sold")
async def admin_sold(ref: str, request: Request):
    uid = await verify_admin(request)
    o = await db_get_offer(ref)
    if not o: raise HTTPException(404, "Not found")
    card, parse_mode = format_offer_card(o, sold=True)
    kb = inquiry_keyboard(ref)
    if o.get("channel_message_id"):
        r = await tg_edit(CHANNEL_ID, o["channel_message_id"], card, kb)
        if not r.get("ok"):
            await tg_send(CHANNEL_ID, f"✅ SOLD\n\n{card}", kb, parse_mode=parse_mode)
    await db_mark_sold(ref)
    await db_audit(uid, "sold", "offer", o.get("id"), {"ref": ref})
    return {"ok": True}

@app.delete("/api/admin/offers/{ref}")
async def admin_delete(ref: str, request: Request):
    uid = await verify_admin(request)
    o = await db_get_offer(ref)
    if not o: raise HTTPException(404, "Not found")
    if o.get("channel_message_id"):
        await tg_delete(CHANNEL_ID, o["channel_message_id"])
    await db_delete_offer(ref)
    await db_audit(uid, "delete", "offer", o.get("id"), {"ref": ref})
    return {"ok": True}

@app.post("/api/admin/announce")
async def admin_announce(req: AnnounceReq, request: Request):
    uid = await verify_admin(request)
    r = await tg_send(CHANNEL_ID, format_announcement(req.text))
    if r.get("ok"):
        await db_insert_announcement(req.text, r["result"]["message_id"], uid)
        await db_audit(uid, "announce", "announcement", None, {"text": req.text[:100]})
        return {"ok": True}
    raise HTTPException(500, f"TG error: {r.get('description')}")

@app.get("/api/admin/backup")
async def admin_backup(request: Request):
    """Export full DB as JSON, optionally send to Telegram."""
    uid = await verify_admin(request)
    conn = await get_db()
    try:
        offers = await conn.fetch("SELECT * FROM offers WHERE status != 'deleted'")
        leads = await conn.fetch("SELECT * FROM leads")
        messages = await conn.fetch("SELECT * FROM messages")
        announcements = await conn.fetch("SELECT * FROM announcements")
        audit = await conn.fetch("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 100")
    finally:
        await release_db(conn)
    
    backup = {
        "version": 1,
        "created_at": __import__("datetime").datetime.now().isoformat(),
        "offers": [dict(r) for r in offers],
        "leads": [dict(r) for r in leads],
        "messages": [dict(r) for r in messages],
        "announcements": [dict(r) for r in announcements],
        "audit_log": [dict(r) for r in audit],
    }
    
    # Convert non-serializable fields
    def clean(obj):
        if isinstance(obj, dict):
            return {k: str(v) if hasattr(v, 'isoformat') else v for k, v in obj.items()}
        return obj
    
    backup = {k: [clean(item) for item in v] if isinstance(v, list) else v for k, v in backup.items()}
    
    json_str = json.dumps(backup, indent=2, default=str)
    
    # Send to all admins as a document
    import aiohttp
    from datetime import datetime
    filename = f"readyco_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    
    for admin_id in ADMIN_IDS:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
            data = aiohttp.FormData()
            data.add_field("chat_id", str(admin_id))
            data.add_field("caption", f"💾 ReadyCo Backup\n{len(offers)} offers, {len(leads)} leads, {len(messages)} messages")
            data.add_field("document", json_str.encode(), filename=filename, content_type="application/json")
            async with aiohttp.ClientSession() as s:
                async with s.post(url, data=data) as r:
                    await r.json()
        except:
            pass
    
    await db_audit(uid, "backup", "database", None, {"offers": len(offers), "leads": len(leads)})
    
    from fastapi.responses import Response
    return Response(content=json_str, media_type="application/json", 
                    headers={"Content-Disposition": f"attachment; filename={filename}"})

@app.post("/api/admin/restore")
async def admin_restore(request: Request):
    """Restore DB from uploaded JSON backup."""
    uid = await verify_admin(request)
    form = await request.form()
    file = form.get("file")
    if not file: raise HTTPException(400, "No file provided")
    contents = await file.read()
    data = json.loads(contents)
    
    conn = await get_db()
    try:
        # Clear existing data
        await conn.execute("DELETE FROM messages")
        await conn.execute("DELETE FROM leads")
        await conn.execute("DELETE FROM offer_versions")
        await conn.execute("DELETE FROM offers")
        await conn.execute("DELETE FROM announcements")
        await conn.execute("DELETE FROM audit_log")
        
        # Restore offers
        for o in data.get("offers", []):
            await conn.execute(
                "INSERT INTO offers (ref, status, jurisdiction, company_type, year_established, license_type, "
                "license_status, regulator, bank_emi_account, vat_status, turnover_history, employees, "
                "transfer_time, price, short_description, hashtags, channel_message_id, created_by, created_at, sold_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)",
                o.get("ref"), o.get("status"), o.get("jurisdiction"), o.get("company_type"),
                o.get("year_established"), o.get("license_type"), o.get("license_status"),
                o.get("regulator"), o.get("bank_emi_account"), o.get("vat_status"),
                o.get("turnover_history"), o.get("employees"), o.get("transfer_time"),
                o.get("price"), o.get("short_description"), o.get("hashtags"),
                o.get("channel_message_id"), o.get("created_by"), o.get("created_at"), o.get("sold_at")
            )
        
        # Restore leads
        for l in data.get("leads", []):
            await conn.execute(
                "INSERT INTO leads (telegram_user_id, telegram_username, offer_ref, status, last_contact_at, created_at) "
                "VALUES ($1,$2,$3,$4,$5,$6)",
                l.get("telegram_user_id"), l.get("telegram_username"), l.get("offer_ref"),
                l.get("status"), l.get("last_contact_at"), l.get("created_at")
            )
        
        count = len(data.get("offers", [])) + len(data.get("leads", []))
    finally:
        await release_db(conn)
    
    await db_audit(uid, "restore", "database", None, {"items": count})
    return {"ok": True, "restored": {"offers": len(data.get("offers",[])), "leads": len(data.get("leads",[]))}}

# === STATIC ===
@app.get("/logo.png")
async def logo():
    p = os.path.join(STATIC_DIR, "logo.png")
    if os.path.exists(p): return FileResponse(p, media_type="image/png")
    raise HTTPException(404, "Not found")

@app.get("/images/{path:path}")
async def images(path: str):
    p = os.path.join(STATIC_DIR, "images", path)
    if os.path.exists(p) and os.path.isfile(p):
        mt = "image/jpeg" if p.endswith(".jpg") or p.endswith(".jpeg") else "image/png"
        return FileResponse(p, media_type=mt)
    raise HTTPException(404, "Not found")

@app.get("/inbox")
async def mini_app():
    p = os.path.join(STATIC_DIR, "admin.html")
    if os.path.exists(p): return FileResponse(p)
    raise HTTPException(404, "Not found")

@app.get("/admin")
async def admin_site():
    p = os.path.join(STATIC_DIR, "admin.html")
    if os.path.exists(p): return FileResponse(p)
    raise HTTPException(404, "Not found")

@app.get("/")
async def website():
    p = os.path.join(STATIC_DIR, "site.html")
    if os.path.exists(p): return FileResponse(p)
    raise HTTPException(404, "Not found")

@app.get("/offer.html")
async def offer_page():
    p = os.path.join(STATIC_DIR, "offer.html")
    if os.path.exists(p): return FileResponse(p)
    raise HTTPException(404, "Not found")

@app.get("/listings.html")
async def listings_page():
    p = os.path.join(STATIC_DIR, "listings.html")
    if os.path.exists(p): return FileResponse(p)
    raise HTTPException(404, "Not found")