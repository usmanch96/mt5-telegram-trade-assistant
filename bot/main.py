from telegram import Update
from telegram.error import NetworkError, TimedOut, RetryAfter
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from config import settings
from bot.handlers.account import cmd_start, cb_main_menu, cmd_sound_test
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
    """Logs unhandled errors gracefully and prevents bot loop crashes from network timeouts."""
    err = context.error
    if not err:
        return

    err_str = str(err).lower()

    # 1. Silently ignore harmless Telegram UI callbacks (e.g. repeated tap, message unchanged)
    if any(m in err_str for m in ["message is not modified", "query is too old", "message to edit not found"]):
        return

    # 2. Transient Telegram API / Cloudflare network hiccups (Bad Gateway 502, Gateway Timeout 504, TimedOut)
    if isinstance(err, (NetworkError, TimedOut)) or any(k in err_str for k in ["bad gateway", "gateway timeout", "timed out", "connection reset", "remote disconnected"]):
        logger.warning(f"Telegram network blip: {err}. Polling loop will auto-reconnect automatically.")
        return

    # 3. Flood control / Rate limit
    if isinstance(err, RetryAfter):
        logger.warning(f"Telegram rate limit: retrying in {err.retry_after}s.")
        return

    # 4. Actual unexpected code exceptions
    logger.error("Exception while handling an update:", exc_info=context.error)

async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silently acknowledges section header buttons that are display-only."""
    if update.callback_query:
        await update.callback_query.answer()

def build_bot_app():
    """Builds and configures the Telegram Bot application."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in environment variables.")
        
    # Configure resilient HTTP request timeouts (30s read, HTTP/1.1 to prevent 1-hour H2 proxy drops)
    t_request = HTTPXRequest(
        connect_timeout=15.0,
        read_timeout=30.0,
        write_timeout=15.0,
        pool_timeout=15.0,
        http_version="1.1"
    )
    
    app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).request(t_request).build()
    
    # Register global error handler
    app.add_error_handler(global_error_handler)
    
    # State-based Conversation Handlers (Unified Quick SL/TP for batch & single trades)
    app.add_handler(get_batch_conv_handler())
    
    # Unified Account Status / Dashboard Commands
    app.add_handler(CommandHandler(["start", "menu", "account", "status", "dashboard"], cmd_start))
    app.add_handler(CommandHandler(["quickstops", "faststops"], cmd_quick_stops))
    app.add_handler(CommandHandler(["positions", "trades"], cb_active_positions))
    app.add_handler(CommandHandler(["sound", "testsound", "soundtest", "audio"], cmd_sound_test))
    
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

    # Register Background MT5 Deal Watcher (Monitors broker TP/SL executions & fills)
    if settings.DEAL_WATCHER_ENABLED:
        from services.deal_watcher import poll_deals_job
        app.job_queue.run_repeating(
            poll_deals_job,
            interval=settings.DEAL_WATCHER_INTERVAL,
            first=2.0,
            name="mt5_deal_watcher"
        )
        logger.info(f"Background MT5 Deal Watcher enabled (polling every {settings.DEAL_WATCHER_INTERVAL}s).")
    
    logger.info("Telegram Bot application successfully configured.")
    return app

def run_bot_polling():
    """Runs the bot in blocking polling mode (standard for local runners)."""
    app = build_bot_app()
    logger.info("Starting Telegram Bot polling...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
