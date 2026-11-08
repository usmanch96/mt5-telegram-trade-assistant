import time
import datetime
from dataclasses import dataclass
from typing import List, Optional, Set, Dict, Tuple
import MetaTrader5 as mt5

from config import settings
from services.logger import logger
from services.sound import play_sound

# Constants from MT5
DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = 1
DEAL_ENTRY_INOUT = 2
DEAL_ENTRY_OUT_BY = 3

DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1

DEAL_REASON_CLIENT = 0
DEAL_REASON_MOBILE = 1
DEAL_REASON_WEB = 2
DEAL_REASON_EXPERT = 3
DEAL_REASON_SL = 4
DEAL_REASON_TP = 5
DEAL_REASON_SO = 6


@dataclass
class DealEvent:
    ticket: int
    position_id: int
    symbol: str
    entry_type: int
    volume: float
    price: float
    profit: float
    reason: int
    sound_event: Optional[str]
    telegram_message: Optional[str]


class DealWatcher:
    """
    Watches MT5 history deals for fills, TP hits, SL hits, and external closes.
    Differentiates bot-triggered actions from broker or client actions to prevent duplicate alerts.
    """

    def __init__(self):
        self._seen_deal_tickets: Set[int] = set()
        self._initial_seed_done: bool = False
        # Map of position_id -> (action_name, timestamp)
        self._bot_handled_actions: Dict[int, Tuple[str, float]] = {}

    def record_bot_action(self, position_id: int, action: str = "CLOSE") -> None:
        """
        Records that an action on a position was intentionally performed by the bot.
        Suppresses redundant notifications when the deal later appears in deal history.
        """
        now = time.time()
        self._bot_handled_actions[position_id] = (action, now)
        self._cleanup_old_actions()

    def _cleanup_old_actions(self) -> None:
        """Removes tracked bot actions older than 60 seconds."""
        cutoff = time.time() - 60.0
        stale = [pid for pid, (_, t) in self._bot_handled_actions.items() if t < cutoff]
        for pid in stale:
            self._bot_handled_actions.pop(pid, None)

    def was_handled_by_bot(self, position_id: int) -> bool:
        """Checks if the position was closed or modified by the bot within the last 60s."""
        self._cleanup_old_actions()
        return position_id in self._bot_handled_actions

    def _format_price(self, symbol: str, price: float) -> str:
        """Formats price with appropriate decimal precision."""
        if any(x in symbol.upper() for x in ["JPY", "XAU", "BTC", "US30", "NAS100", "ETH", "US500", "DXY"]):
            return f"{price:.2f}"
        return f"{price:.5f}"

    def poll_new_deals(self) -> List[DealEvent]:
        """
        Polls MT5 recent deal history.
        On first invocation, silently seeds existing deals to avoid spamming historical trades.
        On subsequent polls, returns DealEvent objects for newly executed deals.
        """
        try:
            now = datetime.datetime.now()
            # Fetch deals from the last 24 hours
            from_time = now - datetime.timedelta(days=1)
            deals = mt5.history_deals_get(from_time, now)
            if deals is None:
                return []
        except Exception as e:
            logger.debug(f"Deal watcher query error: {e}")
            return []

        # First run: seed all existing deals
        if not self._initial_seed_done:
            for d in deals:
                self._seen_deal_tickets.add(d.ticket)
            self._initial_seed_done = True
            logger.info(f"Deal watcher initialized and seeded with {len(self._seen_deal_tickets)} existing deals.")
            return []

        # Cap seen deals cache to prevent unbounded growth over long runs
        if len(self._seen_deal_tickets) > 3000:
            all_tickets = sorted(list(self._seen_deal_tickets))
            self._seen_deal_tickets = set(all_tickets[-1500:])

        new_events: List[DealEvent] = []

        for d in deals:
            if d.ticket in self._seen_deal_tickets:
                continue

            self._seen_deal_tickets.add(d.ticket)

            # Ignore balance/credit adjustments (only trade orders)
            if d.type not in (DEAL_TYPE_BUY, DEAL_TYPE_SELL):
                continue

            symbol = d.symbol
            vol = float(d.volume)
            price = float(d.price)
            profit = float(d.profit)
            pos_id = int(d.position_id) if d.position_id else int(d.order)
            price_str = self._format_price(symbol, price)

            pnl_emoji = "🟢" if profit >= 0 else "🔴"
            pnl_sign = "+" if profit >= 0 else "-"
            pnl_str = f"{pnl_sign}${abs(profit):.2f}"

            # 1. Position Entry (New trade opened on MT5, mobile, or broker)
            if d.entry == DEAL_ENTRY_IN:
                side_str = "BUY" if d.type == DEAL_TYPE_BUY else "SELL"
                side_emoji = "🟩" if d.type == DEAL_TYPE_BUY else "🟥"
                
                msg = (
                    "📥 *Order Filled*\n"
                    "━━━━━━━━━━\n"
                    f"*{symbol}* · {side_emoji} {side_str} {vol:.2f}L\n"
                    f"🎫 Position `#{pos_id}`\n"
                    f"📍 Entry @ `{price_str}`"
                )
                new_events.append(DealEvent(
                    ticket=d.ticket,
                    position_id=pos_id,
                    symbol=symbol,
                    entry_type=d.entry,
                    volume=vol,
                    price=price,
                    profit=profit,
                    reason=d.reason,
                    sound_event="ORDER_FILLED",
                    telegram_message=msg
                ))

            # 2. Position Exit (Closed trade)
            elif d.entry in (DEAL_ENTRY_OUT, DEAL_ENTRY_INOUT, DEAL_ENTRY_OUT_BY):
                # A. Take Profit Hit on Broker
                if d.reason == DEAL_REASON_TP:
                    msg = (
                        "🎯 *Take Profit Hit!*\n"
                        "━━━━━━━━━━\n"
                        f"*{symbol}* · 🏁 Closed {vol:.2f}L\n"
                        f"🎫 Position `#{pos_id}`\n"
                        f"🏁 Exit @ `{price_str}`\n"
                        f"💰 Realized: {pnl_emoji} {pnl_str}"
                    )
                    new_events.append(DealEvent(
                        ticket=d.ticket,
                        position_id=pos_id,
                        symbol=symbol,
                        entry_type=d.entry,
                        volume=vol,
                        price=price,
                        profit=profit,
                        reason=d.reason,
                        sound_event="TP_HIT",
                        telegram_message=msg
                    ))

                # B. Stop Loss Hit on Broker
                elif d.reason == DEAL_REASON_SL:
                    msg = (
                        "🛑 *Stop Loss Hit!*\n"
                        "━━━━━━━━━━\n"
                        f"*{symbol}* · 🏁 Closed {vol:.2f}L\n"
                        f"🎫 Position `#{pos_id}`\n"
                        f"🏁 Exit @ `{price_str}`\n"
                        f"💰 Realized: {pnl_emoji} {pnl_str}"
                    )
                    new_events.append(DealEvent(
                        ticket=d.ticket,
                        position_id=pos_id,
                        symbol=symbol,
                        entry_type=d.entry,
                        volume=vol,
                        price=price,
                        profit=profit,
                        reason=d.reason,
                        sound_event="SL_HIT",
                        telegram_message=msg
                    ))

                # C. Stop Out / Margin Call Hit
                elif d.reason == DEAL_REASON_SO:
                    msg = (
                        "⚠️ *Stop Out (Margin Call)!*\n"
                        "━━━━━━━━━━\n"
                        f"*{symbol}* · 🏁 Closed {vol:.2f}L\n"
                        f"🎫 Position `#{pos_id}`\n"
                        f"🏁 Exit @ `{price_str}`\n"
                        f"💰 Realized: {pnl_emoji} {pnl_str}"
                    )
                    new_events.append(DealEvent(
                        ticket=d.ticket,
                        position_id=pos_id,
                        symbol=symbol,
                        entry_type=d.entry,
                        volume=vol,
                        price=price,
                        profit=profit,
                        reason=d.reason,
                        sound_event="STOP_OUT",
                        telegram_message=msg
                    ))

                # D. Normal / Manual / Bot Close
                else:
                    if self.was_handled_by_bot(pos_id):
                        # The user already received immediate bot confirmation and audio upon button click
                        logger.debug(f"Deal #{d.ticket} for pos #{pos_id} already handled by bot. Skipping duplicate alert.")
                        continue

                    # External close (e.g. from desktop MT5 or phone MT5 app)
                    msg = (
                        "💼 *Position Closed (External)*\n"
                        "━━━━━━━━━━\n"
                        f"*{symbol}* · 🏁 Closed {vol:.2f}L\n"
                        f"🎫 Position `#{pos_id}`\n"
                        f"🏁 Exit @ `{price_str}`\n"
                        f"💰 Realized: {pnl_emoji} {pnl_str}"
                    )
                    new_events.append(DealEvent(
                        ticket=d.ticket,
                        position_id=pos_id,
                        symbol=symbol,
                        entry_type=d.entry,
                        volume=vol,
                        price=price,
                        profit=profit,
                        reason=d.reason,
                        sound_event="ORDER_CLOSED",
                        telegram_message=msg
                    ))

        return new_events


# Global Singleton instance
_watcher_instance: Optional[DealWatcher] = None

def get_deal_watcher() -> DealWatcher:
    """Returns the singleton DealWatcher instance."""
    global _watcher_instance
    if _watcher_instance is None:
        _watcher_instance = DealWatcher()
    return _watcher_instance

def record_bot_action(position_id: int, action: str = "CLOSE") -> None:
    """Convenience helper to record bot actions."""
    get_deal_watcher().record_bot_action(position_id, action)


async def poll_deals_job(context) -> None:
    """
    Periodic job run by telegram.ext.JobQueue.
    Detects broker TP/SL executions or external fills, plays audio alerts, and alerts users.
    """
    try:
        events = get_deal_watcher().poll_new_deals()
        for event in events:
            # 1. Play local sound immediately on PC speaker
            if event.sound_event:
                play_sound(event.sound_event)

            # 2. Broadcast notification card to authorized Telegram users
            if event.telegram_message:
                for uid in settings.AUTHORIZED_USERS:
                    try:
                        await context.bot.send_message(
                            chat_id=uid,
                            text=event.telegram_message,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Failed to deliver deal alert to user {uid}: {e}")
    except Exception as e:
        logger.error(f"Error in deal watcher job execution: {e}", exc_info=True)
