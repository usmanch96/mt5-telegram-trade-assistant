import asyncio
import MetaTrader5 as mt5
from telegram.ext import ContextTypes
from database.models import get_active_trailing_stops, remove_trailing_stop
from mt5.monitor import get_active_positions, get_symbol_info
from mt5.executor import modify_position_stops
from config import settings
from services.logger import logger

async def trailing_stop_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Background job that monitors and updates trailing stops for active positions.
    Exposed as a callback for the Telegram JobQueue.
    """
    try:
        # 1. Fetch active trailing stop settings from SQLite
        db_stops = await asyncio.to_thread(get_active_trailing_stops)
        if not db_stops:
            return
            
        # 2. Fetch current active positions from MT5
        active_positions = await asyncio.to_thread(get_active_positions)
        active_tickets = {p["ticket"]: p for p in active_positions}
        
        # 3. Process each configured trailing stop
        for stop in db_stops:
            ticket = stop["ticket"]
            symbol = stop["symbol"]
            trail_distance = stop["trail_distance_pips"]
            min_profit = stop["min_profit_pips"]
            
            # If position is closed, remove from database
            if ticket not in active_tickets:
                logger.info(f"Position ticket #{ticket} no longer active. Removing trailing stop.")
                await asyncio.to_thread(remove_trailing_stop, ticket)
                continue
                
            pos = active_tickets[ticket]
            current_sl = pos["sl"]
            entry_price = pos["price_open"]
            current_price = pos["price_current"]
            direction = pos["type"]
            tp = pos["tp"]
            
            # Fetch symbol tick size details
            sym_info = await asyncio.to_thread(get_symbol_info, symbol)
            if not sym_info:
                continue
                
            digits = sym_info["digits"]
            point = sym_info["point"]
            tick_size = sym_info["trade_tick_size"]
            
            # Determine pip size
            is_forex = (digits == 3 or digits == 5) and ("USD" in symbol or "EUR" in symbol or "GBP" in symbol or "JPY" in symbol or "AUD" in symbol or "CAD" in symbol or "NZD" in symbol or "CHF" in symbol)
            pip_size = 10 * point if is_forex else 10 * tick_size
            
            # Calculate current floating profit in pips
            if direction == "BUY":
                profit_pips = (current_price - entry_price) / pip_size
            else:
                profit_pips = (entry_price - current_price) / pip_size
                
            # If trade has not reached minimum activation profit, skip
            if profit_pips < min_profit:
                continue
                
            # Calculate target SL price based on trailing distance
            if direction == "BUY":
                target_sl = current_price - (trail_distance * pip_size)
                target_sl = round(target_sl, digits)
                # For BUY, we only move stop loss UP
                should_update = (current_sl == 0) or (target_sl > current_sl)
            else:
                target_sl = current_price + (trail_distance * pip_size)
                target_sl = round(target_sl, digits)
                # For SELL, we only move stop loss DOWN
                should_update = (current_sl == 0) or (target_sl < current_sl)
                
            if should_update:
                logger.info(f"Trailing ticket #{ticket} ({symbol}): current SL={current_sl:.{digits}f}, target SL={target_sl:.{digits}f}")

                res = await asyncio.to_thread(modify_position_stops, ticket, target_sl, tp)
                
                if res["success"]:
                    # Build a polished trailing stop notification card
                    profit_bar = "🟩" * min(10, int(profit_pips / 10)) + "⬜" * max(0, 10 - int(profit_pips / 10))
                    move_dir   = "▲" if direction == "BUY" else "▼"

                    notify_msg = (
                        f"📈 *Trailing Stop Moved*\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎫 `#{ticket}` · *{symbol}*\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📥 Entry · `{entry_price:.{digits}f}`\n"
                        f"🔴 New SL · `{target_sl:.{digits}f}` {move_dir}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"💰 Locked Profit · `+{profit_pips:.1f} Pips`\n"
                        f"{profit_bar}"
                    )

                    for admin_id in settings.AUTHORIZED_USERS:
                        try:
                            await context.bot.send_message(
                                chat_id=admin_id,
                                text=notify_msg,
                                parse_mode="Markdown"
                            )
                        except Exception as send_err:
                            logger.error(f"Failed to send trailing notification to {admin_id}: {send_err}")

                else:
                    logger.error(f"Failed to apply trailing SL update for ticket #{ticket}: {res['message']}")
                    
    except Exception as e:
        logger.error(f"Error in trailing stop service job: {e}", exc_info=True)
