"""ReadyCo Market Bot — Main bot logic (Vercel webhook compatible)"""
import json
import logging
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

from config import settings, is_admin, can_perform, get_admin_role
from db.schema import (
    init_db, db_next_ref, db_get_offer, db_get_offers_by_status,
    db_search_offers, db_insert_offer, db_update_channel_msg,
    db_mark_sold, db_delete_offer, db_update_field,
    db_create_lead, db_store_message, db_insert_announcement, db_audit,
    db_get_lead, db_get_lead_by_user, db_list_leads, db_get_messages,
    db_get_lead_history, db_update_lead_status,
)
from bot.formatting import (
    format_offer_card, format_offer_list,
    format_announcement, get_inquiry_keyboard,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def md_escape(text: str | None) -> str:
    """Escape Markdown special characters for Telegram MarkdownV1."""
    if not text:
        return ""
    special = ["_", "*", "[", "]", "`"]
    for ch in special:
        text = text.replace(ch, f"\\{ch}")
    return text

OFFER_FIELDS = [
    ("jurisdiction", "Jurisdiction? (e.g. Poland 🇵🇱)"),
    ("company_type", "Company type? (e.g. Sp. z o.o.)"),
    ("year_established", "Year established? (e.g. 2023)"),
    ("license_type", "License type? (VASP, CASP, EMI, PI, iGaming, or None)"),
    ("license_status", "License status? (Active, or skip)"),
    ("regulator", "Regulator? (e.g. KNF, or skip)"),
    ("bank_emi_account", "Bank / EMI account? (Yes/No)"),
    ("vat_status", "VAT status? (Active/None)"),
    ("turnover_history", "Turnover history? (Yes/No)"),
    ("employees", "Employees? (Yes/No, or number)"),
    ("transfer_time", "Transfer time? (e.g. 5-10 business days)"),
    ("price", "Price? (e.g. €45,000)"),
    ("short_description", "Short description? (or skip)"),
]

HASHTAG_MAP = {
    "VASP": "#VASP #Crypto",
    "CASP": "#CASP #Crypto",
    "EMI": "#EMI #FinTech",
    "PI": "#PI #FinTech",
    "iGaming": "#iGaming #Gaming",
}

# In-memory sessions (works for polling; for webhook use Redis later)
sessions: dict = {}


def get_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Add offer", callback_data="menu_add"),
         InlineKeyboardButton("📋 List offers", callback_data="menu_list")],
        [InlineKeyboardButton("🔍 Search", callback_data="menu_search"),
         InlineKeyboardButton("✏️ Edit", callback_data="menu_edit")],
        [InlineKeyboardButton("✅ Mark sold", callback_data="menu_sold"),
         InlineKeyboardButton("🗑 Delete", callback_data="menu_delete")],
        [InlineKeyboardButton("📢 Announce", callback_data="menu_announce"),
         InlineKeyboardButton("👤 Leads", callback_data="menu_leads")],
        [InlineKeyboardButton("💬 Inbox (Mini App)", url="https://readyco-market.vercel.app"),
         InlineKeyboardButton("⚙️ Manage", callback_data="menu_manage")],
    ])


def get_admin_keyboard() -> InlineKeyboardMarkup:
    """Persistent keyboard shown alongside every admin message — always shows Menu button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Menu", callback_data="menu_main")],
    ])


def get_manage_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main menu", callback_data="menu_main"),
         InlineKeyboardButton("📊 Stats", callback_data="manage_stats")],
        [InlineKeyboardButton("👥 Admins", callback_data="manage_admins"),
         InlineKeyboardButton("📜 Audit log", callback_data="manage_audit")],
        [InlineKeyboardButton("🔙 Back", callback_data="menu_main")],
    ])


def get_status_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Live", callback_data="list_live"),
         InlineKeyboardButton("✅ Sold", callback_data="list_sold")],
        [InlineKeyboardButton("📋 All", callback_data="list_all"),
         InlineKeyboardButton("🔙 Back", callback_data="menu_main")],
    ])


# === COMMANDS ===

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    if is_admin(user.id):
        await update.message.reply_text(
            f"👋 Welcome back, *{user.first_name}*!\n\n"
            f"ReadyCo Market admin panel\n"
            f"Channel: @readyco\n\n"
            f"Tap a button below 👇",
            parse_mode="Markdown",
            reply_markup=get_main_menu(),
        )
        return

    # Non-admin: welcome + inquiry
    offer_ref = None
    if args and args[0].startswith("inquiry_"):
        offer_ref = args[0].replace("inquiry_", "")

    # Create/update lead with offer_ref
    lead_id = await db_create_lead(user.id, user.username, offer_ref)

    if offer_ref:
        offer = await db_get_offer(offer_ref)
        if offer:
            await update.message.reply_text(
                f"👋 Hi! You asked about *{offer_ref}* — {offer.get('jurisdiction', '')} {offer.get('license_type', '')}.\n\n"
                f"📊 Price: {offer.get('price', 'On request')}\n\n"
                f"Send your question here — our team will respond privately.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"👋 Hi! Offer {offer_ref} may no longer be available.\n\n"
                f"Send your question here — our team will respond privately."
            )
    else:
        await update.message.reply_text(
            "👋 Welcome to ReadyCo Market!\n\n"
            "We help you buy and sell licensed companies:\n"
            "🏦 FinTech (EMI, PI, PSP)\n"
            "₿ Crypto (VASP, CASP, Exchanges)\n"
            "♠️ iGaming (Casinos, Betting, Gaming Licenses)\n\n"
            "What are you interested in?\n"
            "• Buy or sell?\n• What jurisdiction?\n• What license type?\n• Budget range?\n\n"
            "Send your question here — our team will respond privately."
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "📋 *ReadyCo Admin*\n\n"
        "/menu — Show main menu\n"
        "/add — Create new offer\n"
        "/edit — Edit offer\n"
        "/list `[status]` — List offers\n"
        "/search `<keyword>` — Search\n"
        "/sold `<ref>` — Mark as sold\n"
        "/delete `<ref>` — Delete permanently\n"
        "/announce — Post announcement\n"
        "/cancel — Cancel current action\n\n"
        "Or use the menu buttons 👇",
        parse_mode="Markdown",
        reply_markup=get_main_menu(),
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show main menu — /menu command."""
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "📋 *ReadyCo Admin*\n\nTap a button 👇",
        parse_mode="Markdown",
        reply_markup=get_main_menu(),
    )


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Not authorized.")
        return
    user_id = update.effective_user.id
    sessions[user_id] = {"action": "add", "step": 0, "data": {}}
    await update.message.reply_text(
        f"📝 Creating new offer. Step 1/{len(OFFER_FIELDS)}:\n\n{OFFER_FIELDS[0][1]}\n\nSend /cancel to abort."
    )


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    if not args:
        offers = await db_get_offers_by_status("live")
        if not offers:
            await update.message.reply_text("No live offers to edit.")
            return
        keyboard = [[InlineKeyboardButton(
            f"{o['ref']} | {o.get('jurisdiction', '?')} | {o.get('price', '?')}",
            callback_data=f"selectedit_{o['ref']}")] for o in offers]
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
        await update.message.reply_text("✏️ Select offer to edit:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    ref = args[0].upper()
    offer = await db_get_offer(ref)
    if not offer:
        await update.message.reply_text(f"Offer {ref} not found.")
        return
    await _show_edit_fields(update, ref, offer)


async def _show_edit_fields(update_or_query, ref: str, offer: dict):
    fields = ["jurisdiction", "company_type", "year_established", "license_type",
              "license_status", "regulator", "bank_emi_account", "vat_status",
              "turnover_history", "employees", "transfer_time", "price", "short_description"]
    keyboard = []
    for i in range(0, len(fields), 2):
        row = [InlineKeyboardButton(f, callback_data=f"edit_{ref}_{f}") for f in fields[i:i+2]]
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_edit")])
    text = (f"Editing *{ref}* — {offer.get('jurisdiction', '?')} {offer.get('license_type', '')}\n\n"
            f"Current price: {offer.get('price', '?')}\n\nWhich field to edit?")
    if hasattr(update_or_query, 'edit_message_text'):
        await update_or_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update_or_query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    status = context.args[0] if context.args else "live"
    offers = await db_get_offers_by_status(status)
    text = format_offer_list(offers, status)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_menu())


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /search <keyword> (e.g. /search VASP Poland)")
        return
    offers = await db_search_offers(query)
    if not offers:
        await update.message.reply_text(f"No offers found for: {query}")
        return
    text = f"🔍 Search: {query}\n\n" + "\n".join(
        f"`{o['ref']}` | {o.get('jurisdiction', '?')} | {o.get('license_type', 'No license')} | {o.get('price', '?')}"
        for o in offers)
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_sold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    if not args:
        offers = await db_get_offers_by_status("live")
        if not offers:
            await update.message.reply_text("No live offers.")
            return
        keyboard = [[InlineKeyboardButton(
            f"{o['ref']} | {o.get('jurisdiction', '?')} | {o.get('price', '?')}",
            callback_data=f"sold_{o['ref']}")] for o in offers]
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
        await update.message.reply_text("✅ Which offer is sold?", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    await _confirm_sold(update, args[0].upper())


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not can_perform(update.effective_user.id, "delete"):
        await update.message.reply_text("⛔ Only super admins can delete.")
        return
    args = context.args
    if not args:
        offers = await db_get_offers_by_status("all")
        if not offers:
            await update.message.reply_text("No offers.")
            return
        keyboard = [[InlineKeyboardButton(
            f"{o['ref']} | {o.get('jurisdiction', '?')} | {o['status']}",
            callback_data=f"delete_{o['ref']}")] for o in offers[:15]]
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
        await update.message.reply_text("⚠️ Which offer to DELETE?", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    await _confirm_delete(update, args[0].upper())


async def cmd_announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    sessions[update.effective_user.id] = {"action": "announce", "data": {}}
    await update.message.reply_text("📢 Send the announcement text:")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in sessions:
        del sessions[user_id]
    await update.message.reply_text("✅ Cancelled.", reply_markup=get_main_menu())


async def cmd_leads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    leads = await db_list_leads(20)
    if not leads:
        await update.message.reply_text("No leads yet.", reply_markup=get_main_menu())
        return
    keyboard = []
    for l in leads:
        name = l.get("telegram_username") or f"ID:{l['telegram_user_id']}"
        count = l.get("msg_count", 0)
        last = (l.get("last_msg") or "")[:40]
        keyboard.append([InlineKeyboardButton(
            f"● {name} ({count} msgs) {last}",
            callback_data=f"leadview_{l['id']}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data="menu_main")])
    await update.message.reply_text("👤 *Leads*\n\nSelect to view history:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# === MESSAGE HANDLER ===

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    user_id = user.id

    # Only respond to admin sessions (add/announce/edit wizard)
    # Do NOT auto-reply in channel discussion group or to client DMs
    if user_id in sessions:
        session = sessions[user_id]
        if session["action"] == "add":
            await _handle_add_step(update, text)
        elif session["action"] == "announce":
            await _handle_announce_step(update, text)
        elif session["action"] == "edit_field":
            await _handle_edit_field_step(update, text)
        elif session["action"] == "reply":
            await _handle_reply_step(update, text)
        return

    # Non-admin messages — forward to admins with history + [💬 Reply] button
    if not is_admin(user_id):
        lead_id = await db_create_lead(user.id, user.username, None)
        await db_store_message(lead_id, text, "client_to_admin")

        # Get history and lead info
        history = await db_get_messages(lead_id)
        lead = await db_get_lead(lead_id)

        # Get offer info if lead has one
        offer_info = ""
        if lead and lead.get("offer_id"):
            from db.schema import get_db
            db = await get_db()
            try:
                async with db.execute("SELECT ref, jurisdiction, license_type, price FROM offers WHERE id = ?", (lead["offer_id"],)) as cur:
                    row = await cur.fetchone()
                    if row:
                        offer_info = f"\n📊 Offer: {row[0]} — {row[1]} {row[2] or ''} | {row[3] or '?'}"
            finally:
                await db.close()

        # Build admin notification with history
        if user.username:
            user_link = f"t.me/{user.username}"
            user_display = f"@{md_escape(user.username)}"
        else:
            user_link = f"tg://user?id={user.id}"
            user_display = f"ID: {user.id}"

        history_text = ""
        if len(history) > 1:
            history_lines = []
            for msg in history[:-1]:  # exclude current message (already shown)
                direction = "Client" if msg["direction"] == "client_to_admin" else "Admin"
                ts = msg["sent_at"][:16] if msg["sent_at"] else ""
                history_lines.append(f"[{ts}] {direction}: {md_escape(msg['text'][:80])}")
            history_text = "\n\n📜 History:\n" + "\n".join(history_lines[-5:])  # last 5

        admin_text = (
            f"👤 *New inquiry*\n"
            f"From: {user_display}\n"
            f"Name: {md_escape(user.first_name)}"
            f"{offer_info}\n\n"
            f"💬 {md_escape(text)}"
            f"{history_text}\n\n"
            f"Reply: {user_link}"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Reply", callback_data=f"reply_{lead_id}")],
        ])

        for admin_id in settings.admin_allowlist:
            try:
                await context.bot.send_message(chat_id=admin_id, text=admin_text, parse_mode="Markdown", reply_markup=keyboard)
            except Exception as e:
                logger.error(f"Failed to forward to admin {admin_id}: {e}")

        await update.message.reply_text("✅ Thank you! Our team will respond shortly.")
        return


async def _handle_add_step(update: Update, text: str):
    user_id = update.effective_user.id
    session = sessions[user_id]
    step = session["step"]
    field_name, _ = OFFER_FIELDS[step]
    session["data"][field_name] = text if text.lower() != "skip" else None
    session["step"] += 1

    if session["step"] < len(OFFER_FIELDS):
        next_field, next_prompt = OFFER_FIELDS[session["step"]]
        await update.message.reply_text(f"Step {session['step'] + 1}/{len(OFFER_FIELDS)}:\n\n{next_prompt}\n\nSend /cancel to abort.")
    else:
        data = session["data"]
        ref = await db_next_ref()
        data["ref"] = ref
        license_type = data.get("license_type", "")
        hashtags = HASHTAG_MAP.get(license_type, "")
        if hashtags:
            jurisdiction = (data.get("jurisdiction") or "").split(" ")[0]
            data["hashtags"] = f"#{jurisdiction} {hashtags} #ForSale"
        preview = format_offer_card(data)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Publish", callback_data=f"publish_{ref}"),
             InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")],
        ])
        await update.message.reply_text(f"📋 *PREVIEW*\n\n```\n{preview}\n```\n\nPublish to channel?", parse_mode="Markdown", reply_markup=keyboard)


async def _handle_announce_step(update: Update, text: str):
    user_id = update.effective_user.id
    sessions[user_id]["data"]["text"] = text
    formatted = format_announcement(text)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Publish", callback_data="publish_announce"),
         InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")],
    ])
    await update.message.reply_text(f"📋 *PREVIEW*\n\n{formatted}\n\nPublish?", parse_mode="Markdown", reply_markup=keyboard)


async def _handle_edit_field_step(update: Update, text: str):
    user_id = update.effective_user.id
    session = sessions[user_id]
    ref = session["data"]["ref"]
    field = session["data"]["field"]
    session["data"]["new_value"] = text
    offer = await db_get_offer(ref)
    old = offer.get(field, "")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Apply", callback_data=f"applyedit_{ref}_{field}"),
         InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")],
    ])
    await update.message.reply_text(f"Editing {ref} → {field}\nOld: {old}\nNew: {text}\n\nApply?", reply_markup=keyboard)


async def _handle_reply_step(update: Update, text: str):
    """Admin sends reply to client via bot."""
    user_id = update.effective_user.id
    session = sessions[user_id]
    lead_id = session["data"]["lead_id"]
    lead = await db_get_lead(lead_id)
    if not lead:
        await update.message.reply_text("❌ Lead not found.")
        del sessions[user_id]
        return

    # Show preview
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Send", callback_data=f"sendreply_{lead_id}"),
         InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")],
    ])
    sessions[user_id]["data"]["reply_text"] = text
    client_name = md_escape(lead.get("telegram_username") or f"ID:{lead['telegram_user_id']}")
    await update.message.reply_text(
        f"📋 *Reply preview*\n\n"
        f"To: {client_name}\n"
        f"Message: {md_escape(text)}\n\n"
        f"Send?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# === CALLBACK HANDLER ===

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if not is_admin(user_id):
        await query.edit_message_text("⛔ Not authorized.")
        return

    if data == "cancel_action":
        if user_id in sessions:
            del sessions[user_id]
        await query.edit_message_text("✅ Cancelled.", reply_markup=get_main_menu())
        return

    if data == "menu_main":
        await query.edit_message_text("📋 *ReadyCo Admin*\n\nTap a button 👇", parse_mode="Markdown", reply_markup=get_main_menu())
        return

    if data == "menu_add":
        if not can_perform(user_id, "add"):
            await query.edit_message_text("⛔ No permission.")
            return
        sessions[user_id] = {"action": "add", "step": 0, "data": {}}
        await query.edit_message_text(f"📝 Creating new offer. Step 1/{len(OFFER_FIELDS)}:\n\n{OFFER_FIELDS[0][1]}\n\nSend /cancel to abort.")
        return

    if data == "menu_list":
        await query.edit_message_text("📋 *List offers*\n\nSelect status:", parse_mode="Markdown", reply_markup=get_status_menu())
        return

    if data.startswith("list_"):
        status = data.replace("list_", "")
        offers = await db_get_offers_by_status(status)
        text = format_offer_list(offers, status)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to menu", callback_data="menu_main")]])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        return

    if data == "menu_search":
        await query.edit_message_text(
            "🔍 *Search*\n\nUse /search <keyword>\n(e.g. /search VASP Poland)",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_main")]]),
        )
        return

    if data == "menu_edit":
        offers = await db_get_offers_by_status("live")
        if not offers:
            await query.edit_message_text("No live offers.", reply_markup=get_main_menu())
            return
        keyboard = [[InlineKeyboardButton(f"{o['ref']} | {o.get('jurisdiction', '?')} | {o.get('price', '?')}",
                     callback_data=f"selectedit_{o['ref']}")] for o in offers]
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
        await query.edit_message_text("✏️ Select offer to edit:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("selectedit_"):
        ref = data.replace("selectedit_", "")
        offer = await db_get_offer(ref)
        if offer:
            await _show_edit_fields(query, ref, offer)
        return

    if data == "menu_sold":
        offers = await db_get_offers_by_status("live")
        if not offers:
            await query.edit_message_text("No live offers.", reply_markup=get_main_menu())
            return
        keyboard = [[InlineKeyboardButton(f"{o['ref']} | {o.get('jurisdiction', '?')} | {o.get('price', '?')}",
                     callback_data=f"sold_{o['ref']}")] for o in offers]
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
        await query.edit_message_text("✅ Which offer is sold?", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "menu_delete":
        if not can_perform(user_id, "delete"):
            await query.edit_message_text("⛔ Only super admins can delete.")
            return
        offers = await db_get_offers_by_status("all")
        if not offers:
            await query.edit_message_text("No offers.", reply_markup=get_main_menu())
            return
        keyboard = [[InlineKeyboardButton(f"{o['ref']} | {o.get('jurisdiction', '?')} | {o['status']}",
                     callback_data=f"delete_{o['ref']}")] for o in offers[:15]]
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
        await query.edit_message_text("⚠️ DELETE which offer?", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "menu_announce":
        sessions[user_id] = {"action": "announce", "data": {}}
        await query.edit_message_text("📢 Send the announcement text:")
        return

    if data.startswith("reply_"):
        lead_id = int(data.replace("reply_", ""))
        lead = await db_get_lead(lead_id)
        if not lead:
            await query.edit_message_text("❌ Lead not found.")
            return
        sessions[user_id] = {"action": "reply", "data": {"lead_id": lead_id}}
        client_name = md_escape(lead.get("telegram_username") or f"ID:{lead['telegram_user_id']}")
        await query.edit_message_text(
            f"💬 Replying to *{client_name}*\n\nSend your reply text:",
            parse_mode="Markdown",
        )
        return

    if data == "menu_leads":
        leads = await db_list_leads(20)
        if not leads:
            await query.edit_message_text("No leads yet.", reply_markup=get_main_menu())
            return
        keyboard = []
        for l in leads:
            name = l.get("telegram_username") or f"ID:{l['telegram_user_id']}"
            count = l.get("msg_count", 0)
            last = (l.get("last_msg") or "")[:40]
            keyboard.append([InlineKeyboardButton(
                f"● {name} ({count} msgs) {last}",
                callback_data=f"leadview_{l['id']}"
            )])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
        await query.edit_message_text("👤 *Leads*\n\nSelect to view history:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("leadview_"):
        lead_id = int(data.replace("leadview_", ""))
        lead = await db_get_lead(lead_id)
        if not lead:
            await query.edit_message_text("❌ Lead not found.")
            return
        messages = await db_get_messages(lead_id)
        name = md_escape(lead.get("telegram_username") or f"ID:{lead['telegram_user_id']}")
        lines = [f"👤 *{name}* — {len(messages)} messages\n"]
        for msg in messages[-10:]:
            direction = "👤" if msg["direction"] == "client_to_admin" else "💬"
            ts = msg["sent_at"][:16] if msg["sent_at"] else ""
            lines.append(f"{direction} [{ts}] {md_escape(msg['text'][:100])}")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Reply", callback_data=f"reply_{lead_id}")],
            [InlineKeyboardButton("🔙 Back to leads", callback_data="menu_leads")],
        ])
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=keyboard)
        return

    if data == "menu_manage":
        await query.edit_message_text(
            "⚙️ *Manage*\n\n"
            "🏠 Main menu — back to offers\n"
            "📊 Stats — offers, leads, sales\n"
            "👥 Admins — list and roles\n"
            "📜 Audit log — recent actions",
            parse_mode="Markdown",
            reply_markup=get_manage_menu(),
        )
        return

    if data == "manage_stats":
        from db.schema import get_db
        db = await get_db()
        try:
            async with db.execute("SELECT COUNT(*) FROM offers WHERE status='live'") as cur:
                live = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM offers WHERE status='sold'") as cur:
                sold = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM leads") as cur:
                leads = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM announcements WHERE status='published'") as cur:
                announcements = (await cur.fetchone())[0]
        finally:
            await db.close()
        await query.edit_message_text(
            f"📊 *Statistics*\n\n"
            f"🟢 Live offers: {live}\n"
            f"✅ Sold: {sold}\n"
            f"👤 Leads: {leads}\n"
            f"📢 Announcements: {announcements}\n",
            parse_mode="Markdown",
            reply_markup=get_manage_menu(),
        )
        return

    if data == "manage_admins":
        from db.schema import get_db
        db = await get_db()
        try:
            async with db.execute("SELECT telegram_user_id, role, name FROM admins WHERE is_active=1 ORDER BY created_at") as cur:
                rows = await cur.fetchall()
        finally:
            await db.close()
        admin_names = {8339164180: "Timur", 143629845: "Yaroslav", 8585498778: "CompliChain", 6277380476: "Mikhail"}
        lines = []
        for uid, role, name in rows:
            display = name or admin_names.get(uid, "Unknown")
            lines.append(f"• {display} — {role} (ID: {uid})")
        await query.edit_message_text(
            f"👥 *Admins* ({len(rows)})\n\n" + "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=get_manage_menu(),
        )
        return

    if data == "manage_audit":
        from db.schema import get_db
        db = await get_db()
        try:
            async with db.execute(
                "SELECT admin_user_id, action, entity_type, details_json, created_at FROM audit_log ORDER BY created_at DESC LIMIT 10"
            ) as cur:
                rows = await cur.fetchall()
        finally:
            await db.close()
        if not rows:
            await query.edit_message_text("📜 *Audit log*\n\nNo actions yet.", parse_mode="Markdown", reply_markup=get_manage_menu())
            return
        admin_names = {8339164180: "Timur", 143629845: "Yaroslav", 8585498778: "CompliChain", 6277380476: "Mikhail"}
        lines = []
        for uid, action, etype, details, ts in rows:
            name = admin_names.get(uid, f"ID:{uid}")
            lines.append(f"• {ts[:19]} | {name} → {action} {etype or ''}")
        await query.edit_message_text(
            "📜 *Audit log* (last 10)\n\n" + "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=get_manage_menu(),
        )
        return

    if data.startswith("publish_"):
        if data == "publish_announce":
            await _do_publish_announce(query, user_id)
        else:
            ref = data.replace("publish_", "")
            await _do_publish_offer(query, user_id, ref)

    elif data.startswith("sold_"):
        await _confirm_sold(query, data.replace("sold_", ""))

    elif data.startswith("delete_"):
        await _confirm_delete(query, data.replace("delete_", ""))

    elif data.startswith("confirm_sold_"):
        await _do_sold(query, user_id, data.replace("confirm_sold_", ""))

    elif data.startswith("confirm_delete_"):
        await _do_delete(query, user_id, data.replace("confirm_delete_", ""))

    elif data.startswith("edit_"):
        parts = data.split("_", 2)
        ref, field = parts[1], parts[2]
        offer = await db_get_offer(ref)
        sessions[user_id] = {"action": "edit_field", "data": {"ref": ref, "field": field}}
        await query.edit_message_text(f"Editing {ref} → {field}\nCurrent: {offer.get(field, '')}\n\nSend new value:")

    elif data.startswith("applyedit_"):
        parts = data.split("_", 2)
        ref, field = parts[1], parts[2]
        await _do_edit(query, user_id, ref, field)

    elif data.startswith("sendreply_"):
        lead_id = int(data.replace("sendreply_", ""))
        await _do_send_reply(query, user_id, lead_id)


async def _confirm_sold(query_or_update, ref: str):
    offer = await db_get_offer(ref)
    if not offer:
        await _reply(query_or_update, f"Offer {ref} not found.")
        return
    card = format_offer_card(offer, sold=True)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm SOLD", callback_data=f"confirm_sold_{ref}"),
         InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")],
    ])
    await _reply(query_or_update, f"📋 *SOLD PREVIEW*\n\n```\n{card}\n```\n\nMark as sold?", parse_mode="Markdown", reply_markup=keyboard)


async def _confirm_delete(query_or_update, ref: str):
    offer = await db_get_offer(ref)
    if not offer:
        await _reply(query_or_update, f"Offer {ref} not found.")
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ Delete permanently", callback_data=f"confirm_delete_{ref}"),
         InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")],
    ])
    await _reply(query_or_update, f"⚠️ *DELETE {ref}*\n\n{offer.get('jurisdiction', '?')} | {offer.get('price', '?')}\n\nCannot be undone.", parse_mode="Markdown", reply_markup=keyboard)


async def _do_publish_offer(query, user_id: int, ref: str):
    session = sessions.get(user_id, {})
    data = session.get("data", {})
    if not data:
        await query.edit_message_text("❌ Session expired. Use /add again.")
        return
    offer_id = await db_insert_offer(data, user_id)
    card = format_offer_card(data)
    keyboard = get_inquiry_keyboard(ref)
    try:
        msg = await query.bot.send_message(chat_id=settings.channel_id, text=card, reply_markup=keyboard)
        await db_update_channel_msg(offer_id, msg.message_id)
        await db_audit(user_id, "add", "offer", offer_id, {"ref": ref})
        if user_id in sessions:
            del sessions[user_id]
        await query.edit_message_text(f"✅ Published {ref} to channel!", reply_markup=get_main_menu())
    except Exception as e:
        logger.error(f"Failed to post: {e}")
        await query.edit_message_text(f"❌ Failed: {e}")


async def _do_publish_announce(query, user_id: int):
    session = sessions.get(user_id, {})
    text = session.get("data", {}).get("text", "")
    if not text:
        await query.edit_message_text("❌ Session expired.")
        return
    formatted = format_announcement(text)
    try:
        msg = await query.bot.send_message(chat_id=settings.channel_id, text=formatted)
        await db_insert_announcement(text, msg.message_id, user_id)
        await db_audit(user_id, "announce", "announcement", None, {"text": text[:100]})
        if user_id in sessions:
            del sessions[user_id]
        await query.edit_message_text("✅ Announcement published!", reply_markup=get_main_menu())
    except Exception as e:
        await query.edit_message_text(f"❌ Failed: {e}")


async def _do_sold(query, user_id: int, ref: str):
    offer = await db_get_offer(ref)
    if not offer:
        await query.edit_message_text(f"❌ {ref} not found.")
        return
    card = format_offer_card(offer, sold=True)
    keyboard = get_inquiry_keyboard(ref)
    msg_id = offer.get("channel_message_id")
    try:
        if msg_id:
            await query.bot.edit_message_text(chat_id=settings.channel_id, message_id=msg_id, text=card, reply_markup=keyboard)
        await db_mark_sold(ref)
        await db_audit(user_id, "sold", "offer", offer.get("id"), {"ref": ref})
        await query.edit_message_text(f"✅ {ref} marked as SOLD!", reply_markup=get_main_menu())
    except Exception as e:
        logger.error(f"Edit failed: {e}")
        try:
            await query.bot.send_message(chat_id=settings.channel_id, text=f"✅ SOLD\n\n{card}", reply_markup=keyboard)
            await db_mark_sold(ref)
            await query.edit_message_text(f"✅ {ref} marked as SOLD (new post).", reply_markup=get_main_menu())
        except:
            await query.edit_message_text(f"❌ Failed: {e}")


async def _do_delete(query, user_id: int, ref: str):
    offer = await db_get_offer(ref)
    if not offer:
        await query.edit_message_text(f"❌ {ref} not found.")
        return
    msg_id = offer.get("channel_message_id")
    try:
        if msg_id:
            await query.bot.delete_message(chat_id=settings.channel_id, message_id=msg_id)
    except:
        pass
    await db_delete_offer(ref)
    await db_audit(user_id, "delete", "offer", offer.get("id"), {"ref": ref})
    await query.edit_message_text(f"✅ {ref} deleted.", reply_markup=get_main_menu())


async def _do_edit(query, user_id: int, ref: str, field: str):
    session = sessions.get(user_id, {})
    new_value = session.get("data", {}).get("new_value", "")
    if not new_value:
        await query.edit_message_text("❌ Session expired.")
        return
    await db_update_field(ref, field, new_value)
    offer = await db_get_offer(ref)
    msg_id = offer.get("channel_message_id")
    if msg_id and offer["status"] == "live":
        card = format_offer_card(offer)
        keyboard = get_inquiry_keyboard(ref)
        try:
            await query.bot.edit_message_text(chat_id=settings.channel_id, message_id=msg_id, text=card, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Channel edit failed: {e}")
    await db_audit(user_id, "edit", "offer", offer.get("id"), {"ref": ref, "field": field})
    if user_id in sessions:
        del sessions[user_id]
    await query.edit_message_text(f"✅ {ref} updated: {field} = {new_value}", reply_markup=get_main_menu())


async def _reply(query_or_update, text: str, **kwargs):
    if hasattr(query_or_update, 'edit_message_text'):
        await query_or_update.edit_message_text(text, **kwargs)
    else:
        await query_or_update.message.reply_text(text, **kwargs)


async def _do_send_reply(query, user_id: int, lead_id: int):
    """Send admin reply to client via bot."""
    session = sessions.get(user_id, {})
    reply_text = session.get("data", {}).get("reply_text", "")
    if not reply_text:
        await query.edit_message_text("❌ Session expired.")
        return

    lead = await db_get_lead(lead_id)
    if not lead:
        await query.edit_message_text("❌ Lead not found.")
        return

    client_chat_id = lead["telegram_user_id"]
    try:
        await query.bot.send_message(
            chat_id=client_chat_id,
            text=f"💬 ReadyCo Market:\n\n{reply_text}",
        )
        await db_store_message(lead_id, reply_text, "admin_to_client")
        await db_update_lead_status(lead_id, "responded")
        await db_audit(user_id, "reply", "lead", lead_id, {"text": reply_text[:100]})
        if user_id in sessions:
            del sessions[user_id]
        await query.edit_message_text("✅ Reply sent to client!", reply_markup=get_main_menu())
    except Exception as e:
        logger.error(f"Failed to send reply: {e}")
        await query.edit_message_text(f"❌ Failed to send: {e}")


# === APP SETUP ===

async def post_init(app: Application):
    """Set bot commands — different for admins vs clients."""
    from config import is_admin
    
    # Admin commands (visible in bot menu for admins)
    admin_commands = [
        ("menu", "🏠 Главное меню"),
        ("add", "📝 Добавить оффер"),
        ("edit", "✏️ Редактировать"),
        ("list", "📋 Список офферов"),
        ("search", "🔍 Поиск"),
        ("sold", "✅ Пометить продан"),
        ("delete", "🗑 Удалить"),
        ("announce", "📢 Анонс"),
        ("leads", "👤 Лиды"),
        ("cancel", "❌ Отмена"),
        ("help", "❓ Помощь"),
    ]
    
    # Client commands (minimal — just ask a question)
    client_commands = [
        ("start", "👋 Начать"),
        ("help", "❓ Помощь"),
    ]
    
    # Set admin commands for each admin user
    for admin_id in settings.admin_allowlist:
        try:
            await app.bot.set_my_commands(admin_commands, scope={"type": "chat", "chat_id": admin_id})
        except Exception as e:
            logger.error(f"Failed to set admin commands for {admin_id}: {e}")
    
    # Set default (client) commands for everyone else
    await app.bot.set_my_commands(client_commands)


def create_app() -> Application:
    app = Application.builder().token(settings.bot_token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("edit", cmd_edit))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("sold", cmd_sold))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("announce", cmd_announce))
    app.add_handler(CommandHandler("leads", cmd_leads))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


async def run_polling():
    """Run bot in polling mode (local dev)."""
    await init_db()
    app = create_app()
    logger.info("Starting ReadyCo bot (polling)...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    # Keep running
    import signal
    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    await stop_event.wait()
    await app.updater.stop()
    await app.stop()
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(run_polling())