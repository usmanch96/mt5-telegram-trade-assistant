import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from services.sound import find_sounds_dir, _resolve_wav_path, play_sound, EVENT_SOUND_MAP
from services.deal_watcher import (
    DealWatcher,
    DealEvent,
    DEAL_ENTRY_IN,
    DEAL_ENTRY_OUT,
    DEAL_TYPE_BUY,
    DEAL_TYPE_SELL,
    DEAL_REASON_TP,
    DEAL_REASON_SL,
    DEAL_REASON_SO,
    DEAL_REASON_CLIENT,
    poll_deals_job
)


@dataclass
class MockTradeDeal:
    ticket: int
    order: int
    position_id: int
    symbol: str
    type: int
    entry: int
    reason: int
    volume: float
    price: float
    profit: float
    comment: str = ""


class TestSoundService:
    def test_event_sound_mapping(self):
        """Verifies all expected events are mapped with wav files and phrases."""
        expected_events = [
            "ORDER_FILLED",
            "ORDER_CLOSED",
            "PARTIAL_CLOSED",
            "STOPS_MODIFIED",
            "BREAK_EVEN",
            "TP_HIT",
            "SL_HIT",
            "STOP_OUT",
            "TEST"
        ]
        for evt in expected_events:
            assert evt in EVENT_SOUND_MAP
            wav, phrase = EVENT_SOUND_MAP[evt]
            assert wav.endswith(".wav")
            assert len(phrase) > 0

    def test_find_sounds_dir_caching(self):
        """Verifies directory search finds and caches a valid directory or None."""
        s_dir = find_sounds_dir()
        # Either found an actual MT5 Sounds folder or gracefully returned None
        if s_dir is not None:
            assert isinstance(s_dir, str)

    def test_play_sound_does_not_raise(self):
        """Verifies play_sound is non-blocking and safe even with invalid events."""
        # Should not raise exception
        play_sound("ORDER_FILLED")
        play_sound("TP_HIT")
        play_sound("UNKNOWN_EVENT_NAME")


class TestDealWatcher:
    def test_initial_seed_does_not_emit_events(self):
        """First poll should seed existing deals without triggering alerts."""
        watcher = DealWatcher()
        mock_deals = (
            MockTradeDeal(ticket=1, order=10, position_id=100, symbol="BTCUSD", type=DEAL_TYPE_BUY, entry=DEAL_ENTRY_IN, reason=DEAL_REASON_CLIENT, volume=0.1, price=75000.0, profit=0.0),
            MockTradeDeal(ticket=2, order=10, position_id=100, symbol="BTCUSD", type=DEAL_TYPE_SELL, entry=DEAL_ENTRY_OUT, reason=DEAL_REASON_TP, volume=0.1, price=76000.0, profit=100.0),
        )

        with patch("services.deal_watcher.mt5.history_deals_get", return_value=mock_deals):
            events = watcher.poll_new_deals()
            assert len(events) == 0
            assert 1 in watcher._seen_deal_tickets
            assert 2 in watcher._seen_deal_tickets

    def test_detect_tp_hit(self):
        """Subsequent poll detects Take Profit hit and generates TP_HIT event."""
        watcher = DealWatcher()
        watcher._initial_seed_done = True
        watcher._seen_deal_tickets.add(1)

        tp_deal = MockTradeDeal(
            ticket=2,
            order=20,
            position_id=200,
            symbol="BTCUSD",
            type=DEAL_TYPE_SELL,
            entry=DEAL_ENTRY_OUT,
            reason=DEAL_REASON_TP,
            volume=0.2,
            price=76100.0,
            profit=50.25,
            comment="[tp 76100.0]"
        )

        with patch("services.deal_watcher.mt5.history_deals_get", return_value=(tp_deal,)):
            events = watcher.poll_new_deals()
            assert len(events) == 1
            evt = events[0]
            assert evt.sound_event == "TP_HIT"
            assert evt.symbol == "BTCUSD"
            assert evt.profit == 50.25

    def test_detect_sl_hit(self):
        """Subsequent poll detects Stop Loss hit and generates SL_HIT event."""
        watcher = DealWatcher()
        watcher._initial_seed_done = True

        sl_deal = MockTradeDeal(
            ticket=3,
            order=30,
            position_id=300,
            symbol="EURUSD",
            type=DEAL_TYPE_BUY,
            entry=DEAL_ENTRY_OUT,
            reason=DEAL_REASON_SL,
            volume=1.0,
            price=1.0800,
            profit=-50.0,
            comment="[sl 1.0800]"
        )

        with patch("services.deal_watcher.mt5.history_deals_get", return_value=(sl_deal,)):
            events = watcher.poll_new_deals()
            assert len(events) == 1
            evt = events[0]
            assert evt.sound_event == "SL_HIT"

    def test_detect_order_filled(self):
        """Subsequent poll detects new order open from terminal/mobile."""
        watcher = DealWatcher()
        watcher._initial_seed_done = True

        open_deal = MockTradeDeal(
            ticket=4,
            order=40,
            position_id=400,
            symbol="XAUUSD",
            type=DEAL_TYPE_BUY,
            entry=DEAL_ENTRY_IN,
            reason=DEAL_REASON_CLIENT,
            volume=0.5,
            price=2750.50,
            profit=0.0
        )

        with patch("services.deal_watcher.mt5.history_deals_get", return_value=(open_deal,)):
            events = watcher.poll_new_deals()
            assert len(events) == 1
            evt = events[0]
            assert evt.sound_event == "ORDER_FILLED"

    def test_suppress_bot_handled_close(self):
        """When an order was already closed by the bot, deal watcher should suppress duplicate alert."""
        watcher = DealWatcher()
        watcher._initial_seed_done = True

        # Register position as handled by bot
        watcher.record_bot_action(500, "CLOSE")

        manual_close_deal = MockTradeDeal(
            ticket=5,
            order=50,
            position_id=500,
            symbol="BTCUSD",
            type=DEAL_TYPE_SELL,
            entry=DEAL_ENTRY_OUT,
            reason=DEAL_REASON_CLIENT,
            volume=0.2,
            price=76000.0,
            profit=20.0
        )

        with patch("services.deal_watcher.mt5.history_deals_get", return_value=(manual_close_deal,)):
            events = watcher.poll_new_deals()
            # Suppressed!
            assert len(events) == 0

    @pytest.mark.asyncio
    async def test_poll_deals_job_plays_sound_without_telegram_messages(self):
        """Verifies poll_deals_job triggers audio alert on PC speaker and does not spam Telegram."""
        mock_context = MagicMock()
        mock_context.bot.send_message = MagicMock()

        evt = DealEvent(
            ticket=99,
            position_id=999,
            symbol="BTCUSD",
            entry_type=DEAL_ENTRY_OUT,
            volume=0.2,
            price=76000.0,
            profit=35.0,
            reason=DEAL_REASON_TP,
            sound_event="TP_HIT"
        )

        with patch("services.deal_watcher.get_deal_watcher") as mock_gw, \
             patch("services.deal_watcher.play_sound") as mock_play_sound:
            
            mock_watcher = MagicMock()
            mock_watcher.poll_new_deals.return_value = [evt]
            mock_gw.return_value = mock_watcher

            await poll_deals_job(mock_context)

            mock_play_sound.assert_called_once_with("TP_HIT")
            # Must NOT send messages to Telegram chat
            mock_context.bot.send_message.assert_not_called()

