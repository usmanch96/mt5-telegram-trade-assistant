from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from config import settings
from bot.handlers.account import cmd_start, cb_account_status, cb_main_menu, cmd_authorize_user, cmd_revoke_user
from bot.handlers.positions import (
    cb_active_positions,
    cb_view_position,
    cb_close_position_action,
    cb_be_position_action,
    cb_partial_close_options,
    cb_partial_close_execute,
    cb_edit_stops_menu,
    cb_adjust_stops_execute,
    cb_trailing_menu,
    cb_trailing_preset_execute,
    get_position_modification_handler
)
from bot.handlers.trade import get_trade_conv_handler, start_trade_flow
from bot.handlers.batch import cb_batch_menu, get_batch_conv_handler
from bot.handlers.layer import get_layer_conv_handler, cmd_start_layer_wizard
from bot.handlers.alerts import (
    cb_alerts_menu,
    cb_view_active_alerts,
    cb_delete_alert_action,
    get_alert_conv_handler
)
from bot.handlers.calculator import get_risk_calculator_handler, cmd_start_calculator, cb_cancel_calc

from services.trail_service import trailing_stop_job
from services.alert_service import price_alert_job
from services.logger import logger

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs unhandled errors and prevents bot loop crashes from network timeouts."""
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
    
    # Schedule repeating background jobs
    if app.job_queue:
        app.job_queue.run_repeating(trailing_stop_job, interval=10, first=5, name="trailing_stop")
        app.job_queue.run_repeating(price_alert_job, interval=5, first=5, name="price_alert")
        logger.info("Background jobs (trailing stop, price alert) scheduled.")
        
    # State-based Conversation Handlers (MUST be registered first)
    app.add_handler(get_trade_conv_handler())
    app.add_handler(get_position_modification_handler())
    app.add_handler(get_alert_conv_handler())
    app.add_handler(get_batch_conv_handler())
    app.add_handler(get_layer_conv_handler())
    app.add_handler(get_risk_calculator_handler())

    
    # Command Handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CommandHandler("account", cb_account_status))
    app.add_handler(CommandHandler("authorize", cmd_authorize_user))
    app.add_handler(CommandHandler("revoke", cmd_revoke_user))
    
    # Persistent Reply Keyboard Text Message Handlers (App-like persistent buttons)
    app.add_handler(MessageHandler(filters.Regex("^📊 Dashboard$|^🔄 Refresh Status$"), cmd_start))
    app.add_handler(MessageHandler(filters.Regex("^💼 Active Positions$"), cb_active_positions))
    app.add_handler(MessageHandler(filters.Regex("^🚀 Open Trade$"), start_trade_flow))
    app.add_handler(MessageHandler(filters.Regex("^🥞 Layering Trade$"), cmd_start_layer_wizard))
    app.add_handler(MessageHandler(filters.Regex("^⚡ Batch Controls$"), cb_batch_menu))
    app.add_handler(MessageHandler(filters.Regex("^🔔 Price Alerts$"), cb_alerts_menu))
    
    # Callback Handlers (Main Navigations)
    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern="^menu_main$"))
    app.add_handler(CallbackQueryHandler(cb_account_status, pattern="^menu_account$"))
    app.add_handler(CallbackQueryHandler(cb_active_positions, pattern="^menu_trades$"))
    app.add_handler(CallbackQueryHandler(cb_account_status, pattern="^menu_refresh$"))
    
    # Callback Handlers (Individual Position Actions)
    app.add_handler(CallbackQueryHandler(cb_view_position, pattern=r"^pos_view_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_close_position_action, pattern=r"^pos_close_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_be_position_action, pattern=r"^pos_be_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_partial_close_options, pattern=r"^pos_partial_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_partial_close_execute, pattern=r"^pos_partexec_\d+_\d+\.\d+$"))
    
    # Callback Handlers (Incremental Stops Adjustments & Trailing Stops)
    app.add_handler(CallbackQueryHandler(cb_edit_stops_menu, pattern=r"^pos_edit_menu_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_adjust_stops_execute, pattern=r"^pos_adj_\d+_(sl|tp)_(?!custom).+$"))

    app.add_handler(CallbackQueryHandler(cb_trailing_menu, pattern=r"^pos_trail_menu_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_trailing_preset_execute, pattern=r"^trail_set_\d+_-?\d+$"))
    
    # Callback Handlers (Batch Actions)
    app.add_handler(CallbackQueryHandler(cb_batch_menu, pattern="^menu_batch$"))
    
    # Callback Handlers (Price Alerts)
    app.add_handler(CallbackQueryHandler(cb_alerts_menu, pattern="^menu_alerts$"))
    app.add_handler(CallbackQueryHandler(cb_view_active_alerts, pattern="^alert_list$"))

    # Callback Handlers (Calculator)
    app.add_handler(CallbackQueryHandler(cb_cancel_calc, pattern="^calc_cancel$"))

    app.add_handler(CallbackQueryHandler(cb_delete_alert_action, pattern=r"^alert_del_\d+$"))

    # Noop handler for decorative section-header buttons
    app.add_handler(CallbackQueryHandler(cb_noop, pattern="^noop$"))
    
    logger.info("Telegram Bot application successfully configured.")
    return app

def run_bot_polling():
    """Runs the bot in blocking polling mode (standard for local runners)."""
    app = build_bot_app()
    logger.info("Starting Telegram Bot polling...")
    app.run_polling()
