from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from config import settings
from bot.handlers.account import cmd_start, cb_main_menu
from bot.handlers.positions import (
    cb_active_positions,
    cb_view_position,
    cb_close_position_action,
    cb_be_position_action,
    cb_partial_close_options,
    cb_partial_close_execute,
    cb_adjust_stops_execute
)
from bot.handlers.batch import get_batch_conv_handler, cmd_quick_stops
from services.logger import logger

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs unhandled errors and prevents bot loop crashes from network timeouts."""
    err = context.error
    if err:
        err_str = str(err).lower()
        # Silently ignore harmless Telegram UI updates where state didn't change or old button was tapped
        if "message is not modified" in err_str or "query is too old" in err_str:
            return
    logger.error("Exception while handling an update:", exc_info=context.error)

async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silently acknowledges section header buttons that are display-only."""
    if update.callback_query:
        await update.callback_query.answer()

def build_bot_app():
    """Builds and configures the Telegram Bot application."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in environment variables.")
        
    # Configure custom HTTP request timeouts (20s) to absorb network latency
    t_request = HTTPXRequest(connect_timeout=20.0, read_timeout=20.0)
    
    app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).request(t_request).build()
    
    # Register global error handler
    app.add_error_handler(global_error_handler)
    
    # State-based Conversation Handlers (Unified Quick SL/TP for batch & single trades)
    app.add_handler(get_batch_conv_handler())
    
    # Unified Account Status / Dashboard Commands
    app.add_handler(CommandHandler(["start", "menu", "account", "status", "dashboard"], cmd_start))
    app.add_handler(CommandHandler(["quickstops", "faststops"], cmd_quick_stops))
    app.add_handler(CommandHandler(["positions", "trades"], cb_active_positions))
    
    # Callback Handlers (Unified Navigation & Refresh)
    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern=r"^(menu_main|menu_account|menu_refresh)$"))
    app.add_handler(CallbackQueryHandler(cb_active_positions, pattern="^menu_trades$"))
    
    # Callback Handlers (Individual Position Actions)
    app.add_handler(CallbackQueryHandler(cb_view_position, pattern=r"^pos_view_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_close_position_action, pattern=r"^pos_close_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_be_position_action, pattern=r"^pos_be_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_partial_close_options, pattern=r"^pos_partial_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_partial_close_execute, pattern=r"^pos_partexec_\d+_\d+\.\d+$"))
    
    # Callback Handlers (Incremental Stops Adjustments)
    app.add_handler(CallbackQueryHandler(cb_adjust_stops_execute, pattern=r"^pos_adj_\d+_(sl|tp)_(?!custom).+$"))

    # Noop handler for decorative buttons
    app.add_handler(CallbackQueryHandler(cb_noop, pattern="^noop$"))
    
    logger.info("Telegram Bot application successfully configured.")
    return app

def run_bot_polling():
    """Runs the bot in blocking polling mode (standard for local runners)."""
    app = build_bot_app()
    logger.info("Starting Telegram Bot polling...")
    app.run_polling()
