from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes
from database.models import is_user_authorized
from services.logger import logger

def restricted(func):
    """
    Decorator that restricts handler access to whitelisted Telegram User IDs.
    Works for both message handlers and callback queries (inline buttons).
    """
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user:
            logger.warning("Received update without effective user.")
            return
            
        user_id = user.id
        
        # Check authorization
        if not is_user_authorized(user_id):
            username = user.username or "Unknown"
            logger.warning(f"Unauthorized access attempt by ID: {user_id} (@{username})")
            
            # Send warning back to user
            text = "⚠️ *Access Denied*\nYour Telegram account is not authorized to interact with this trading bot."
            
            if update.callback_query:
                # If they clicked an inline button, show a popup notification
                await update.callback_query.answer(
                    text="Unauthorized user. Access denied.",
                    show_alert=True
                )
            elif update.message:
                # If they typed a command, reply with a markdown message
                await update.message.reply_text(text, parse_mode="Markdown")
                
            return
            
        return await func(update, context, *args, **kwargs)
        
    return wrapped
