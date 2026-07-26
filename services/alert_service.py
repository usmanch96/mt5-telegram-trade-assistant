import asyncio
import MetaTrader5 as mt5
from telegram.ext import ContextTypes
from database.models import get_active_price_alerts, deactivate_price_alert
from mt5.connector import check_connection
from mt5.monitor import get_symbol_info
from config import settings
from services.logger import logger

async def price_alert_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Background job that monitors active price alerts.
    Exposed as a callback for the Telegram JobQueue.
    """
    try:
        # Check connection before processing
        connected = await asyncio.to_thread(check_connection)
        if not connected:
            return
            
        # 1. Fetch active alerts from database
        alerts = await asyncio.to_thread(get_active_price_alerts)
        if not alerts:
            return
            
        # Group alerts by symbol to minimize MT5 tick queries
        symbol_groups = {}
        for alert in alerts:
            symbol = alert["symbol"]
            symbol_groups.setdefault(symbol, []).append(alert)
            
        # 2. Check price for each symbol
        for symbol, symbol_alerts in symbol_groups.items():
            # Get symbol info and current tick
            sym_info = await asyncio.to_thread(get_symbol_info, symbol)
            if not sym_info:
                logger.warning(f"Could not get symbol info for alert check: {symbol}")
                continue
                
            tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
            if not tick:
                logger.warning(f"Could not get current tick price for alert check: {symbol}")
                continue
                
            # Use bid price as standard close/current price reference
            current_price = tick.bid
            digits = sym_info["digits"]
            
            for alert in symbol_alerts:
                alert_id = alert["id"]
                target = alert["target_price"]
                condition = alert["condition_type"] # 'ABOVE' or 'BELOW'
                
                trigger = False
                if condition == "ABOVE" and current_price >= target:
                    trigger = True
                elif condition == "BELOW" and current_price <= target:
                    trigger = True
                    
                if trigger:
                    logger.info(f"Price alert triggered: {symbol} hit {current_price} (Target: {condition} {target})")
                    
                    # Deactivate in database first to prevent double-firing
                    deactivated = await asyncio.to_thread(deactivate_price_alert, alert_id)
                    if not deactivated:
                        logger.error(f"Failed to deactivate price alert #{alert_id} in DB.")
                        continue
                        
                    # Format alert text
                    alert_direction_emoji = "📈" if condition == "ABOVE" else "📉"
                    msg_text = (
                        f"🔔 *Price Alert Triggered!*\n\n"
                        f"🔣 *Symbol:* `{symbol}`\n"
                        f"{alert_direction_emoji} *Target:* `{condition} {target:.{digits}f}`\n"
                        f"💲 *Current Price:* `{current_price:.{digits}f}`\n"
                    )
                    
                    # Notify whitelisted users
                    for user_id in settings.AUTHORIZED_USERS:
                        try:
                            await context.bot.send_message(
                                chat_id=user_id,
                                text=msg_text,
                                parse_mode="Markdown"
                            )
                        except Exception as send_err:
                            logger.error(f"Failed to send alert to user {user_id}: {send_err}")
                            
    except Exception as e:
        logger.error(f"Error in price alert background service: {e}", exc_info=True)
