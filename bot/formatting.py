"""ReadyCo Market Bot — Offer formatting and channel posting"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import settings


def generate_ref(pool, conn=None) -> str:
    """Generate next RC reference number."""
    # This will be called with a connection
    pass


def format_offer_card(offer: dict, sold: bool = False) -> str:
    """Format an offer as a Telegram channel card."""
    lines = []
    
    if sold:
        lines.append("~~FOR SALE~~ ✅ SOLD")
        lines.append(f"~~Ref: {offer['ref']}~~")
        lines.append(f"~~Jurisdiction: {offer['jurisdiction']}~~")
        if offer.get('company_type'):
            lines.append(f"~~Company type: {offer['company_type']}~~")
        if offer.get('year_established'):
            lines.append(f"~~Year: {offer['year_established']}~~")
        if offer.get('license_type'):
            lines.append(f"~~License: {offer['license_type']}~~")
        if offer.get('bank_emi_account'):
            lines.append(f"~~Bank / EMI account: {offer['bank_emi_account']}~~")
        if offer.get('vat_status'):
            lines.append(f"~~VAT: {offer['vat_status']}~~")
        lines.append(f"~~Price: {offer['price']}~~")
        lines.append("")
        lines.append("Contact: @ReadyCoAdminBot")
    else:
        lines.append("FOR SALE")
        lines.append("")
        lines.append(f"Ref: {offer['ref']}")
        lines.append(f"Jurisdiction: {offer['jurisdiction']}")
        if offer.get('company_type'):
            lines.append(f"Company type: {offer['company_type']}")
        if offer.get('year_established'):
            lines.append(f"Year: {offer['year_established']}")
        if offer.get('license_type'):
            lines.append(f"License: {offer['license_type']}")
        if offer.get('license_status'):
            lines.append(f"License status: {offer['license_status']}")
        if offer.get('regulator'):
            lines.append(f"Regulator: {offer['regulator']}")
        if offer.get('bank_emi_account'):
            lines.append(f"Bank / EMI account: {offer['bank_emi_account']}")
        if offer.get('vat_status'):
            lines.append(f"VAT: {offer['vat_status']}")
        if offer.get('turnover_history'):
            lines.append(f"Turnover history: {offer['turnover_history']}")
        if offer.get('employees'):
            lines.append(f"Employees: {offer['employees']}")
        if offer.get('transfer_time'):
            lines.append(f"Transfer time: {offer['transfer_time']}")
        lines.append(f"Price: {offer['price']}")
        lines.append("")
        if offer.get('short_description'):
            lines.append(offer['short_description'])
            lines.append("")
        if offer.get('hashtags'):
            lines.append(offer['hashtags'])
            lines.append("")
        lines.append("Contact: @ReadyCoAdminBot")
    
    return "\n".join(lines)


def get_inquiry_keyboard(offer_ref: str) -> InlineKeyboardMarkup:
    """Inline keyboard for channel posts — 'Ask about this offer' button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "💬 Ask about this offer",
            url=f"https://t.me/ReadyCoAdminBot?start=inquiry_{offer_ref}"
        )],
        [InlineKeyboardButton(
            "🌐 View on website",
            url=f"{settings.website_base_url}/offers/{offer_ref.lower()}"
        )],
    ])


def format_announcement(text: str) -> str:
    """Format an announcement post."""
    return f"📢 {text}"


def format_preview(offer: dict) -> str:
    """Format preview for admin confirmation."""
    return f"📋 *PREVIEW*\n\n{format_offer_card(offer)}\n\n_Publish to channel?_"


def format_offer_list(offers: list[dict], status_filter: str = "live") -> str:
    """Format a list of offers for /list command."""
    if not offers:
        return f"No offers with status: {status_filter}"
    
    lines = [f"📋 Offers ({status_filter}): {len(offers)}\n"]
    for o in offers:
        price = o.get('price', '?')
        lines.append(f"`{o['ref']}` | {o.get('jurisdiction', '?')} | {o.get('license_type', 'No license')} | {price}")
    
    return "\n".join(lines)