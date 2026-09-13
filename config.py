"""ReadyCo Market Bot — Configuration"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    bot_token: str = "8817038916:AAH3G9vxsqcptcNkZEBmDIHEIA_JevEXXpk"
    channel_id: int = -1004361452090  # @readyco
    channel_username: str = "readyco"
    
    # Admins (Telegram user IDs) — all super admins for now
    admin_allowlist: list[int] = [8339164180, 143629845, 8585498778, 6277380476] # Timur, Yaroslav, CompliChain, Mikhail
    super_admin_allowlist: list[int] = [8339164180, 143629845, 8585498778, 6277380476]
    
    # Database (SQLite for now, PostgreSQL later)
    database_url: str = "sqlite:///readyco.db"
    
    # Website
    website_base_url: str = "https://readyco.market"
    
    # Brand colors
    color_primary: str = "#FFD400"
    color_navy: str = "#081220"
    color_green: str = "#00CB53"
    color_orange: str = "#FF8A00"
    color_purple: str = "#7A3CFF"
    
    class Config:
        env_prefix = "READYCO_"


settings = Settings()

# Admin roles
ADMIN_ROLES = {
    "super_admin": ["add", "edit", "sold", "delete", "announce", "list", "search", "cancel", "deal", "report"],
    "publisher": ["add", "edit", "sold", "announce", "list", "search", "cancel"],
    "viewer": ["list", "search"],
}

def get_admin_role(user_id: int) -> str | None:
    if user_id in settings.super_admin_allowlist:
        return "super_admin"
    if user_id in settings.admin_allowlist:
        return "publisher"
    return None

def is_admin(user_id: int) -> bool:
    return get_admin_role(user_id) is not None

def can_perform(user_id: int, action: str) -> bool:
    role = get_admin_role(user_id)
    if role is None:
        return False
    return action in ADMIN_ROLES.get(role, [])