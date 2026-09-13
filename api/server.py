"""ReadyCo Market — Website API + Mini App backend"""
import json
import asyncio
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
import aiosqlite

from config import settings, is_admin
from db.schema import (
    get_db, db_get_offers_by_status, db_get_offer, db_search_offers,
    db_list_leads, db_get_lead, db_get_messages, db_store_message,
    db_update_lead_status, db_audit,
)

app = FastAPI(title="ReadyCo Market API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BOT_TOKEN = settings.bot_token
DB_PATH = "/tmp/readyco.db"


# === AUTH ===

async def verify_admin(request: Request) -> int:
    """Verify Telegram WebApp initData — returns user_id or raises."""
    auth = request.headers.get("Authorization", "")
    init_data = request.headers.get("X-Telegram-Init-Data", auth.replace("Bearer ", ""))
    
    if not init_data or init_data == "":
        raise HTTPException(401, "No auth data")
    
    # Parse initData — extract user_id
    # For local dev, accept raw user_id
    if init_data.isdigit():
        user_id = int(init_data)
        if is_admin(user_id):
            return user_id
        raise HTTPException(403, "Not an admin")
    
    # Parse Telegram initData
    from urllib.parse import parse_qs, unquote
    params = parse_qs(init_data)
    user_json = params.get("user", [None])[0]
    if user_json:
        user = json.loads(unquote(user_json))
        user_id = user.get("id")
        if user_id and is_admin(user_id):
            return user_id
        raise HTTPException(403, "Not an admin")
    
    raise HTTPException(401, "Invalid auth")


# === MODELS ===

class ReplyRequest(BaseModel):
    lead_id: int
    text: str


# === PUBLIC ENDPOINTS (website) ===

@app.get("/api/offers")
async def api_offers(status: str = "live"):
    """Get all offers — public, for website."""
    offers = await db_get_offers_by_status(status)
    return {"offers": offers, "count": len(offers)}


@app.get("/api/offers/{ref}")
async def api_offer(ref: str):
    """Get single offer by ref — public."""
    offer = await db_get_offer(ref)
    if not offer:
        raise HTTPException(404, "Offer not found")
    return {"offer": offer}


@app.get("/api/search")
async def api_search(q: str):
    """Search offers — public."""
    offers = await db_search_offers(q)
    return {"offers": offers, "count": len(offers)}


# === ADMIN ENDPOINTS (Mini App) ===

@app.get("/api/admin/leads")
async def admin_leads(request: Request):
    """List all leads — admin only."""
    await verify_admin(request)
    leads = await db_list_leads(50)
    return {"leads": leads, "count": len(leads)}


@app.get("/api/admin/leads/{lead_id}")
async def admin_lead_detail(lead_id: int, request: Request):
    """Get lead + full message history — admin only."""
    await verify_admin(request)
    lead = await db_get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    messages = await db_get_messages(lead_id)
    return {"lead": lead, "messages": messages}


@app.post("/api/admin/reply")
async def admin_reply(req: ReplyRequest, request: Request):
    """Send reply to client via bot — admin only."""
    user_id = await verify_admin(request)
    lead = await db_get_lead(req.lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    
    # Send message via Telegram Bot API
    import aiohttp
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": lead["telegram_user_id"],
        "text": f"💬 ReadyCo Market:\n\n{req.text}",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise HTTPException(500, f"Telegram error: {data.get('description')}")
    
    # Store in DB
    await db_store_message(req.lead_id, req.text, "admin_to_client")
    await db_update_lead_status(req.lead_id, "responded")
    await db_audit(user_id, "reply", "lead", req.lead_id, {"text": req.text[:100]})
    
    return {"ok": True, "message": "Reply sent"}


@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    """Dashboard stats — admin only."""
    await verify_admin(request)
    db = await get_db()
    try:
        async with db.execute("SELECT COUNT(*) FROM offers WHERE status='live'") as cur:
            live = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM offers WHERE status='sold'") as cur:
            sold = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM leads") as cur:
            leads = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM leads WHERE status='new'") as cur:
            new_leads = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM leads WHERE status='responded'") as cur:
            responded = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM announcements WHERE status='published'") as cur:
            announcements = (await cur.fetchone())[0]
    finally:
        await db.close()
    
    return {
        "live_offers": live,
        "sold_offers": sold,
        "total_leads": leads,
        "new_leads": new_leads,
        "responded_leads": responded,
        "announcements": announcements,
    }


@app.get("/api/admin/offers")
async def admin_offers(request: Request, status: str = "all"):
    """List offers for admin — admin only."""
    await verify_admin(request)
    offers = await db_get_offers_by_status(status)
    return {"offers": offers, "count": len(offers)}


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


@app.post("/api/admin/offers")
async def admin_create_offer(req: OfferCreate, request: Request):
    """Create offer + post to channel — admin only."""
    user_id = await verify_admin(request)
    from db.schema import db_next_ref, db_insert_offer, db_update_channel_msg
    from bot.formatting import format_offer_card, get_inquiry_keyboard
    
    ref = await db_next_ref()
    data = req.model_dump()
    data["ref"] = ref
    
    # Generate hashtags
    HASHTAG_MAP = {"VASP": "#VASP #Crypto", "CASP": "#CASP #Crypto", "EMI": "#EMI #FinTech", "PI": "#PI #FinTech", "iGaming": "#iGaming #Gaming"}
    hashtags = HASHTAG_MAP.get(data.get("license_type", ""), "")
    if hashtags:
        juris = (data.get("jurisdiction") or "").split(" ")[0]
        data["hashtags"] = f"#{juris} {hashtags} #ForSale"
    
    offer_id = await db_insert_offer(data, user_id)
    card = format_offer_card(data)
    keyboard = get_inquiry_keyboard(ref)
    
    import aiohttp
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": settings.channel_id, "text": card, "reply_markup": {"inline_keyboard": keyboard.to_dict()}}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data_resp = await resp.json()
            if data_resp.get("ok"):
                msg_id = data_resp["result"]["message_id"]
                await db_update_channel_msg(offer_id, msg_id)
    
    await db_audit(user_id, "add", "offer", offer_id, {"ref": ref})
    return {"ok": True, "ref": ref, "id": offer_id}


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


@app.put("/api/admin/offers/{ref}")
async def admin_update_offer(ref: str, req: OfferUpdate, request: Request):
    """Update offer + edit channel post — admin only."""
    user_id = await verify_admin(request)
    from db.schema import db_update_field
    from bot.formatting import format_offer_card, get_inquiry_keyboard
    import aiohttp
    
    offer = await db_get_offer(ref)
    if not offer:
        raise HTTPException(404, "Offer not found")
    
    data = req.model_dump(exclude_none=True)
    for field, value in data.items():
        await db_update_field(ref, field, value)
    
    # Re-fetch and update channel post
    offer = await db_get_offer(ref)
    if offer.get("channel_message_id") and offer["status"] == "live":
        card = format_offer_card(offer)
        keyboard = get_inquiry_keyboard(ref)
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
        payload = {"chat_id": settings.channel_id, "message_id": offer["channel_message_id"], "text": card, "reply_markup": {"inline_keyboard": keyboard.to_dict()}}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                await resp.json()
    
    await db_audit(user_id, "edit", "offer", offer.get("id"), {"ref": ref})
    return {"ok": True}


@app.post("/api/admin/offers/{ref}/sold")
async def admin_mark_sold(ref: str, request: Request):
    """Mark offer as sold + edit channel post with strikethrough — admin only."""
    user_id = await verify_admin(request)
    from db.schema import db_mark_sold
    from bot.formatting import format_offer_card, get_inquiry_keyboard
    import aiohttp
    
    offer = await db_get_offer(ref)
    if not offer:
        raise HTTPException(404, "Offer not found")
    
    card = format_offer_card(offer, sold=True)
    keyboard = get_inquiry_keyboard(ref)
    
    if offer.get("channel_message_id"):
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
        payload = {"chat_id": settings.channel_id, "message_id": offer["channel_message_id"], "text": card, "reply_markup": {"inline_keyboard": keyboard.to_dict()}}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    # Fallback: new post
                    url2 = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    payload2 = {"chat_id": settings.channel_id, "text": f"✅ SOLD\n\n{card}", "reply_markup": {"inline_keyboard": keyboard.to_dict()}}
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url2, json=payload2) as resp2:
                            await resp2.json()
    
    await db_mark_sold(ref)
    await db_audit(user_id, "sold", "offer", offer.get("id"), {"ref": ref})
    return {"ok": True}


@app.delete("/api/admin/offers/{ref}")
async def admin_delete_offer(ref: str, request: Request):
    """Delete offer + remove channel post — admin only."""
    user_id = await verify_admin(request)
    from db.schema import db_delete_offer
    import aiohttp
    
    offer = await db_get_offer(ref)
    if not offer:
        raise HTTPException(404, "Offer not found")
    
    if offer.get("channel_message_id"):
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
        payload = {"chat_id": settings.channel_id, "message_id": offer["channel_message_id"]}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                await resp.json()
    
    await db_delete_offer(ref)
    await db_audit(user_id, "delete", "offer", offer.get("id"), {"ref": ref})
    return {"ok": True}


class AnnounceRequest(BaseModel):
    text: str


@app.post("/api/admin/announce")
async def admin_announce(req: AnnounceRequest, request: Request):
    """Post announcement to channel — admin only."""
    user_id = await verify_admin(request)
    from db.schema import db_insert_announcement
    from bot.formatting import format_announcement
    import aiohttp
    
    formatted = format_announcement(req.text)
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": settings.channel_id, "text": formatted}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            if data.get("ok"):
                msg_id = data["result"]["message_id"]
                await db_insert_announcement(req.text, msg_id, user_id)
                await db_audit(user_id, "announce", "announcement", None, {"text": req.text[:100]})
                return {"ok": True}
            raise HTTPException(500, f"Telegram error: {data.get('description')}")


# === HEALTH ===

@app.get("/api/health")
async def health():
    return {"ok": True, "service": "readyco-market"}


# === MINI APP (admin inbox) ===

@app.get("/inbox")
async def mini_app(request: Request):
    """Serve Mini App HTML — admin inbox."""
    return FileResponse("/Users/timur/readyco/static/index.html")


# === WEBSITE (readyco.market) ===

@app.get("/")
async def website():
    """Serve website — offers page."""
    return FileResponse("/Users/timur/readyco/static/site.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)