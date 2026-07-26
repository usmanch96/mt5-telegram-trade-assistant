"""
Shared telegram-ext filters for conversation handlers.

Provides NOT_MENU_BUTTON — a combined filter that passes only "real" user
text input, excluding persistent reply-keyboard button labels and commands.
This prevents ConversationHandler states from swallowing menu button presses.
"""
from telegram.ext import filters

# All persistent reply-keyboard button labels (must match keyboards.py exactly)
_MENU_BUTTON_REGEX = (
    r"^("
    r"📊 Dashboard"
    r"|💼 Active Positions"
    r"|🚀 Open Trade"
    r"|🥞 Layering Trade"
    r"|⚡ Batch Controls"
    r"|🔔 Price Alerts"
    r"|🔄 Refresh Status"
    r")$"
)

# Use this instead of `filters.TEXT & ~filters.COMMAND` inside conversation states
NOT_MENU_BUTTON = filters.TEXT & ~filters.COMMAND & ~filters.Regex(_MENU_BUTTON_REGEX)
