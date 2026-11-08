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
    r"📊 Account Status"
    r"|📊 Dashboard"
    r"|💼 Active Positions"
    r"|⚡ Quick SL/TP"
    r"|⚡ Quick Batch SL/TP"
    r"|🔄 Refresh Status"
    r")$"
)

# Use this instead of `filters.TEXT & ~filters.COMMAND` inside conversation states
NOT_MENU_BUTTON = filters.TEXT & ~filters.COMMAND & ~filters.Regex(_MENU_BUTTON_REGEX)
