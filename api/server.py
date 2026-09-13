"""ReadyCo Market — Vercel Serverless API (single file, includes bot webhook)"""
import json
import os
import asyncio
import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
import aiosqlite

app = FastAPI(title="ReadyCo Market API")

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# === CONFIG ===
BOT_TOKEN = "8817038916:AAH3G9vxsqcptcNkZEBmDIHEIA_JevEXXpk"
CHANNEL_ID = -1004361452090
ADMIN_IDS = [8339164180, 143629845, 8585498778, 6277380476]
DB_PATH = "/tmp/readyco.db"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

ADMIN_NAMES = {8339164180: "Timur", 143629845: "Yaroslav", 8585498778: "CompliChain", 6277380476: "Mikhail"}

# === DB ===
SCHEMA = """
CREATE TABLE IF NOT EXISTS admins (
    telegram_user_id INTEGER PRIMARY KEY, role TEXT DEFAULT 'publisher', name TEXT, is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT UNIQUE NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
    jurisdiction TEXT, company_type TEXT, year_established TEXT, license_type TEXT, license_status TEXT,
    regulator TEXT, bank_emi_account TEXT, vat_status TEXT, turnover_history TEXT, employees TEXT,
    transfer_time TEXT, price TEXT, short_description TEXT, hashtags TEXT,
    channel_message_id INTEGER, views_count INTEGER DEFAULT 0, inquiry_count INTEGER DEFAULT 0,
    created_by INTEGER NOT NULL, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')),
    sold_at TEXT, sold_price TEXT);
CREATE INDEX IF NOT EXISTS idx_offers_status ON offers(status);
CREATE TABLE IF NOT EXISTS offer_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, offer_id INTEGER NOT NULL, payload_json TEXT NOT NULL,
    changed_by INTEGER NOT NULL, changed_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_user_id INTEGER NOT NULL, telegram_username TEXT,
    offer_ref TEXT, offer_id INTEGER, status TEXT DEFAULT 'new', last_contact_at TEXT,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL, direction TEXT NOT NULL,
    text TEXT, sent_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, channel_message_id INTEGER,
    status TEXT DEFAULT 'draft', published_at TEXT, created_by INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, admin_user_id INTEGER NOT NULL, action TEXT NOT NULL,
    entity_type TEXT, entity_id INTEGER, details_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')));
"""

async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    await db.executescript(SCHEMA)
    for uid, name in [(8339164180,'Timur'),(143629845,'Yaroslav'),(8585498778,'CompliChain'),(6277380476,'Mikhail')]:
        await db.execute(f"INSERT OR IGNORE INTO admins (telegram_user_id, role, name) VALUES ({uid}, 'super_admin', '{name}')")
    await db.commit()
    return db

def is_admin(uid): return uid in ADMIN_IDS

def esc(text):
    if not text: return ""
    return text.replace("<","&lt;").replace(">","&gt;").replace("&","&amp;")

# === DB OPS ===
async def db_next_ref():
    db = await get_db()
    try:
        async with db.execute("SELECT COUNT(*) + 1 FROM offers") as c: return f"RC{(await c.fetchone())[0]:03d}"
    finally: await db.close()

async def db_get_offer(ref):
    db = await get_db()
    try:
        async with db.execute("SELECT * FROM offers WHERE ref = ? AND status != 'deleted'", (ref.upper(),)) as c:
            r = await c.fetchone()
            if r: return dict(zip([d[0] for d in c.description], r))
    finally: await db.close()

async def db_get_offers(status):
    db = await get_db()
    try:
        q = "SELECT * FROM offers WHERE status != 'deleted' ORDER BY created_at DESC LIMIT 50" if status=="all" else "SELECT * FROM offers WHERE status = ? ORDER BY created_at DESC LIMIT 50"
        p = () if status=="all" else (status,)
        async with db.execute(q, p) as c:
            rows = await c.fetchall()
            cols = [d[0] for d in c.description]
            return [dict(zip(cols, r)) for r in rows]
    finally: await db.close()

async def db_insert_offer(data, created_by):
    db = await get_db()
    try:
        async with db.execute(
            "INSERT INTO offers (ref, status, jurisdiction, company_type, year_established, license_type, "
            "license_status, regulator, bank_emi_account, vat_status, turnover_history, employees, "
            "transfer_time, price, short_description, hashtags, created_by) "
            "VALUES (?, 'live', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (data.get('ref'), data.get('jurisdiction'), data.get('company_type'), data.get('year_established'),
             data.get('license_type'), data.get('license_status'), data.get('regulator'),
             data.get('bank_emi_account'), data.get('vat_status'), data.get('turnover_history'),
             data.get('employees'), data.get('transfer_time'), data.get('price'),
             data.get('short_description'), data.get('hashtags'), created_by)) as c:
            oid = (await c.fetchone())[0]
        await db.commit()
        return oid
    finally: await db.close()

async def db_update_channel_msg(oid, mid):
    db = await get_db()
    try: await db.execute("UPDATE offers SET channel_message_id = ? WHERE id = ?", (mid, oid)); await db.commit()
    finally: await db.close()

async def db_mark_sold(ref):
    db = await get_db()
    try: await db.execute("UPDATE offers SET status='sold', sold_at=datetime('now') WHERE ref=?", (ref,)); await db.commit()
    finally: await db.close()

async def db_delete_offer(ref):
    db = await get_db()
    try: await db.execute("UPDATE offers SET status='deleted' WHERE ref=?", (ref,)); await db.commit()
    finally: await db.close()

async def db_update_field(ref, field, value):
    db = await get_db()
    try: await db.execute(f"UPDATE offers SET {field}=?, updated_at=datetime('now') WHERE ref=?", (value, ref)); await db.commit()
    finally: await db.close()

async def db_create_lead(uid, username, offer_ref=None):
    db = await get_db()
    try:
        offer_id = None
        if offer_ref:
            o = await db_get_offer(offer_ref)
            if o: offer_id = o["id"]
        async with db.execute("SELECT id FROM leads WHERE telegram_user_id=? ORDER BY created_at DESC LIMIT 1", (uid,)) as c:
            r = await c.fetchone()
        if r:
            await db.execute("UPDATE leads SET last_contact_at=datetime('now'), telegram_username=?, offer_ref=? WHERE id=?", (username, offer_ref, r[0]))
            await db.commit(); return r[0]
        async with db.execute("INSERT INTO leads (telegram_user_id, telegram_username, offer_ref, offer_id, status, last_contact_at) VALUES (?, ?, ?, ?, 'new', datetime('now')) RETURNING id", (uid, username, offer_ref, offer_id)) as c:
            lid = (await c.fetchone())[0]
        await db.commit(); return lid
    finally: await db.close()

async def db_get_lead(lid):
    db = await get_db()
    try:
        async with db.execute("SELECT * FROM leads WHERE id=?", (lid,)) as c:
            r = await c.fetchone()
            if r: return dict(zip([d[0] for d in c.description], r))
    finally: await db.close()

async def db_list_leads(limit=50):
    db = await get_db()
    try:
        async with db.execute(
            "SELECT l.*, COUNT(m.id) as msg_count, "
            "(SELECT text FROM messages WHERE lead_id=l.id ORDER BY sent_at DESC LIMIT 1) as last_msg "
            "FROM leads l LEFT JOIN messages m ON m.lead_id=l.id GROUP BY l.id ORDER BY l.last_contact_at DESC LIMIT ?", (limit,)) as c:
            rows = await c.fetchall()
            cols = [d[0] for d in c.description]
            return [dict(zip(cols, r)) for r in rows]
    finally: await db.close()

async def db_get_messages(lid):
    db = await get_db()
    try:
        async with db.execute("SELECT * FROM messages WHERE lead_id=? ORDER BY sent_at ASC", (lid,)) as c:
            rows = await c.fetchall()
            cols = [d[0] for d in c.description]
            return [dict(zip(cols, r)) for r in rows]
    finally: await db.close()

async def db_store_message(lid, text, direction):
    db = await get_db()
    try: await db.execute("INSERT INTO messages (lead_id, direction, text) VALUES (?, ?, ?)", (lid, direction, text)); await db.commit()
    finally: await db.close()

async def db_update_lead_status(lid, status):
    db = await get_db()
    try: await db.execute("UPDATE leads SET status=? WHERE id=?", (status, lid)); await db.commit()
    finally: await db.close()

async def db_audit(uid, action, etype=None, eid=None, details=None):
    db = await get_db()
    try: await db.execute("INSERT INTO audit_log (admin_user_id, action, entity_type, entity_id, details_json) VALUES (?, ?, ?, ?, ?)",
        (uid, action, etype, eid, json.dumps(details or {}))); await db.commit()
    finally: await db.close()

async def db_insert_announcement(text, mid, uid):
    db = await get_db()
    try: await db.execute("INSERT INTO announcements (text, channel_message_id, status, published_at, created_by) VALUES (?, ?, 'published', datetime('now'), ?)", (text, mid, uid)); await db.commit()
    finally: await db.close()

# === FORMATTING ===
def format_offer_card(offer, sold=False):
    lines = []
    if sold:
        lines.append("~~FOR SALE~~ ✅ SOLD")
        lines.append(f"~~Ref: {offer.get('ref','')}~~")
        lines.append(f"~~Jurisdiction: {offer.get('jurisdiction','')}~~")
        if offer.get('company_type'): lines.append(f"~~Company type: {offer['company_type']}~~")
        if offer.get('license_type'): lines.append(f"~~License: {offer['license_type']}~~")
        if offer.get('price'): lines.append(f"~~Price: {offer['price']}~~")
        lines.append("")
        lines.append("Contact: @ReadyCoAdminBot")
    else:
        lines.append("FOR SALE")
        lines.append("")
        lines.append(f"Ref: {offer.get('ref','')}")
        lines.append(f"Jurisdiction: {offer.get('jurisdiction','')}")
        if offer.get('company_type'): lines.append(f"Company type: {offer['company_type']}")
        if offer.get('year_established'): lines.append(f"Year: {offer['year_established']}")
        if offer.get('license_type'): lines.append(f"License: {offer['license_type']}")
        if offer.get('license_status'): lines.append(f"License status: {offer['license_status']}")
        if offer.get('regulator'): lines.append(f"Regulator: {offer['regulator']}")
        if offer.get('bank_emi_account'): lines.append(f"Bank / EMI account: {offer['bank_emi_account']}")
        if offer.get('vat_status'): lines.append(f"VAT: {offer['vat_status']}")
        if offer.get('turnover_history'): lines.append(f"Turnover history: {offer['turnover_history']}")
        if offer.get('employees'): lines.append(f"Employees: {offer['employees']}")
        if offer.get('transfer_time'): lines.append(f"Transfer time: {offer['transfer_time']}")
        if offer.get('price'): lines.append(f"Price: {offer['price']}")
        lines.append("")
        if offer.get('short_description'): lines.append(offer['short_description']); lines.append("")
        if offer.get('hashtags'): lines.append(offer['hashtags']); lines.append("")
        lines.append("Contact: @ReadyCoAdminBot")
    return "\n".join(lines)

def format_announcement(text): return f"📢 {text}"

def inquiry_keyboard(ref):
    return {"inline_keyboard": [[
        {"text": "💬 Ask about this offer", "url": f"https://t.me/ReadyCoAdminBot?start=inquiry_{ref}"},
    ],[
        {"text": "🌐 View on website", "url": f"https://readyco.market/offers/{ref.lower()}"},
    ]]}

# === TG API ===
async def tg_send(chat_id, text, reply_markup=None, parse_mode=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode: payload["parse_mode"] = parse_mode
    if reply_markup: payload["reply_markup"] = reply_markup
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload) as r:
            return await r.json()

async def tg_edit(chat_id, msg_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": msg_id, "text": text}
    if reply_markup: payload["reply_markup"] = reply_markup
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload) as r:
            return await r.json()

async def tg_delete(chat_id, msg_id):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json={"chat_id": chat_id, "message_id": msg_id}) as r:
            return await r.json()

async def tg_set_commands(commands, scope=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands"
    payload = {"commands": commands}
    if scope: payload["scope"] = scope
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload) as r:
            return await r.json()

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
    price: str
    short_description: Optional[str] = None

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
@app.post("/api/webhook")
async def bot_webhook(request: Request):
    """Handle Telegram bot updates via webhook."""
    data = await request.json()
    
    # Handle callback queries
    if "callback_query" in data:
        return {"ok": True}
    
    # Handle messages
    msg = data.get("message") or data.get("edited_message")
    if not msg:
        return {"ok": True}
    
    user = msg.get("from", {})
    user_id = user.get("id")
    username = user.get("username", "")
    first_name = user.get("first_name", "")
    text = msg.get("text", "")
    
    # /start command
    if text.startswith("/start"):
        args = text.split()[1:] if len(text.split()) > 1 else []
        offer_ref = None
        if args and args[0].startswith("inquiry_"):
            offer_ref = args[0].replace("inquiry_", "")
        
        if is_admin(user_id):
            await tg_send(user_id, f"👋 Welcome back, {first_name}!\n\nReadyCo Admin\nChannel: @readyco\n\nUse Mini App: https://readyco.vercel.app/inbox")
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
                kb = {"inline_keyboard": [[{"text": f"{o['ref']} | {o.get('jurisdiction','?')} | {o.get('license_type','')} | {o.get('price','?')}", "callback_data": f"client_offer_{o['ref']}"}] for o in offers]}
                await tg_send(user_id, "👋 Welcome to ReadyCo Market!\n\nWe help you buy and sell licensed companies:\n🏦 FinTech (EMI, PI, PSP)\n₿ Crypto (VASP, CASP, Exchanges)\n♠️ iGaming (Casinos, Betting, Gaming Licenses)\n\nSelect an offer to ask about it 👇", kb)
            else:
                await tg_send(user_id, "👋 Welcome to ReadyCo Market!\n\nWe help you buy and sell licensed companies.\n\nNo offers available yet. Send your question here — our team will respond privately.")
        return {"ok": True}
    
    # Regular message from non-admin = client inquiry
    if not is_admin(user_id) and text:
        lead_id = await db_create_lead(user_id, username)
        await db_store_message(lead_id, text, "client_to_admin")
        
        # Get offer info
        lead = await db_get_lead(lead_id)
        offer_info = ""
        if lead and lead.get("offer_ref"):
            offer = await db_get_offer(lead["offer_ref"])
            if offer:
                offer_info = f"\n📊 Offer: {offer['ref']} — {offer.get('jurisdiction','')} {offer.get('license_type','') or ''} | {offer.get('price','?')}"
        
        # Get history
        history = await db_get_messages(lead_id)
        history_text = ""
        if len(history) > 1:
            lines = []
            for m in history[:-1]:
                d = "Client" if m["direction"] == "client_to_admin" else "Admin"
                ts = m["sent_at"][:16] if m["sent_at"] else ""
                lines.append(f"[{ts}] {d}: {esc(m['text'][:80])}")
            history_text = "\n\n📜 History:\n" + "\n".join(lines[-5:])
        
        user_link = f"t.me/{username}" if username else f"tg://user?id={user_id}"
        user_disp = f"@{username}" if username else f"ID: {user_id}"
        
        admin_text = (
            f"👤 <b>New inquiry</b>\n"
            f"From: {user_disp}\n"
            f"Name: {esc(first_name)}"
            f"{offer_info}\n\n"
            f"💬 {esc(text)}"
            f"{history_text}\n\n"
            f"Reply: {user_link}"
        )
        
        kb = {"inline_keyboard": [[{"text": "💬 Reply", "callback_data": f"reply_{lead_id}"}]]}
        
        for admin_id in ADMIN_IDS:
            try:
                await tg_send(admin_id, admin_text, kb, parse_mode="HTML")
            except:
                pass
        
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
async def admin_leads(request: Request):
    await verify_admin(request)
    return {"leads": await db_list_leads(50)}

@app.get("/api/admin/leads/{lead_id}")
async def admin_lead(lead_id: int, request: Request):
    await verify_admin(request)
    lead = await db_get_lead(lead_id)
    if not lead: raise HTTPException(404, "Lead not found")
    return {"lead": lead, "messages": await db_get_messages(lead_id)}

@app.post("/api/admin/reply")
async def admin_reply(req: ReplyReq, request: Request):
    uid = await verify_admin(request)
    lead = await db_get_lead(req.lead_id)
    if not lead: raise HTTPException(404, "Lead not found")
    r = await tg_send(lead["telegram_user_id"], f"💬 ReadyCo Market:\n\n{req.text}")
    if not r.get("ok"): raise HTTPException(500, f"TG error: {r.get('description')}")
    await db_store_message(req.lead_id, req.text, "admin_to_client")
    await db_update_lead_status(req.lead_id, "responded")
    await db_audit(uid, "reply", "lead", req.lead_id, {"text": req.text[:100]})
    return {"ok": True}

# === ADMIN: STATS ===
@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    await verify_admin(request)
    db = await get_db()
    try:
        async with db.execute("SELECT COUNT(*) FROM offers WHERE status='live'") as c: live = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM offers WHERE status='sold'") as c: sold = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM leads") as c: leads = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM leads WHERE status='new'") as c: new = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM leads WHERE status='responded'") as c: resp = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM announcements WHERE status='published'") as c: ann = (await c.fetchone())[0]
    finally: await db.close()
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
    HT = {"VASP":"#VASP #Crypto","CASP":"#CASP #Crypto","EMI":"#EMI #FinTech","PI":"#PI #FinTech","iGaming":"#iGaming #Gaming"}
    ht = HT.get(data.get("license_type",""), "")
    if ht:
        j = (data.get("jurisdiction") or "").split(" ")[0]
        data["hashtags"] = f"#{j} {ht} #ForSale"
    oid = await db_insert_offer(data, uid)
    card = format_offer_card(data)
    kb = inquiry_keyboard(ref)
    r = await tg_send(CHANNEL_ID, card, kb)
    if r.get("ok"):
        await db_update_channel_msg(oid, r["result"]["message_id"])
    await db_audit(uid, "add", "offer", oid, {"ref": ref})
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
        await tg_edit(CHANNEL_ID, o["channel_message_id"], format_offer_card(o), inquiry_keyboard(ref))
    await db_audit(uid, "edit", "offer", o.get("id"), {"ref": ref})
    return {"ok": True}

@app.post("/api/admin/offers/{ref}/sold")
async def admin_sold(ref: str, request: Request):
    uid = await verify_admin(request)
    o = await db_get_offer(ref)
    if not o: raise HTTPException(404, "Not found")
    card = format_offer_card(o, sold=True)
    kb = inquiry_keyboard(ref)
    if o.get("channel_message_id"):
        r = await tg_edit(CHANNEL_ID, o["channel_message_id"], card, kb)
        if not r.get("ok"):
            await tg_send(CHANNEL_ID, f"✅ SOLD\n\n{card}", kb)
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

# === STATIC ===
@app.get("/inbox")
async def mini_app():
    p = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(p): return FileResponse(p)
    raise HTTPException(404, "Not found")

@app.get("/")
async def website():
    p = os.path.join(STATIC_DIR, "site.html")
    if os.path.exists(p): return FileResponse(p)
    raise HTTPException(404, "Not found")