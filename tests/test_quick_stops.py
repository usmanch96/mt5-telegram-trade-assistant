import sys
import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from bot.keyboards import (
    get_main_keyboard,
    get_quick_stops_keyboard,
    get_quick_guided_sl_keyboard,
    get_quick_guided_tp_keyboard,
    get_quick_single_sl_keyboard,
    get_quick_single_tp_keyboard
)
from bot.conv_filters import _MENU_BUTTON_REGEX
import re
from bot.handlers.batch import (
    parse_quick_price_input, 
    apply_quick_stops_values,
    compute_portfolio_risk_summary_lines
)

def test_quick_stops_keyboards():
    print("Testing Streamlined Quick Stops Keyboards...")
    # 1. Test Unified Home Page inline menu contains Active Positions, Quick Batch SL/TP, and Refresh Status
    main_kb = get_main_keyboard()
    main_callbacks = [btn.callback_data for row in main_kb.inline_keyboard for btn in row]
    expected_callbacks = ["menu_trades", "baction_quick_stops", "menu_refresh"]
    assert set(main_callbacks) == set(expected_callbacks), f"Main keyboard has unexpected buttons: {main_callbacks}"
    print("  [OK] Main inline keyboard has strictly: Active Positions, Quick Batch SL/TP, Refresh Status.")

    # 2. Test Menu Filter Regex retains safety against former button texts
    assert re.search(_MENU_BUTTON_REGEX, "⚡ Quick SL/TP"), "⚡ Quick SL/TP must be included in _MENU_BUTTON_REGEX!"
    assert re.search(_MENU_BUTTON_REGEX, "💼 Active Positions"), "Active Positions must be included in _MENU_BUTTON_REGEX!"
    assert re.search(_MENU_BUTTON_REGEX, "📊 Account Status"), "Account Status must be included in _MENU_BUTTON_REGEX!"
    assert re.search(_MENU_BUTTON_REGEX, "📊 Dashboard"), "Dashboard must be included in _MENU_BUTTON_REGEX!"
    assert not re.search(_MENU_BUTTON_REGEX, "🚀 Open Trade"), "🚀 Open Trade must NOT match _MENU_BUTTON_REGEX!"
    print("  [OK] Filters strictly restricted to active unified options.")

    # 3. Test single-symbol positions (no redundant pair selector row)
    single_sym_positions = [
        {"ticket": 1001, "symbol": "XAUUSD", "type": "BUY", "price_open": 2740.0, "price_current": 2745.0, "sl": 0.0, "tp": 0.0, "volume": 0.02}
    ]
    quick_kb_single = get_quick_stops_keyboard(single_sym_positions)
    s_callbacks = [btn.callback_data for row in quick_kb_single.inline_keyboard for btn in row]
    assert not any(c.startswith("qpair_") for c in s_callbacks), "Pair selector should not show when only 1 symbol exists!"
    print("  [OK] Single-pair clean layout verified.")

    # 4. Test multi-symbol positions (dynamic 1-tap pair toggle row)
    multi_sym_positions = [
        {"ticket": 1001, "symbol": "XAUUSD", "type": "BUY", "price_open": 2740.0, "price_current": 2745.0, "sl": 0.0, "tp": 0.0, "volume": 0.02},
        {"ticket": 1002, "symbol": "XAUUSD", "type": "BUY", "price_open": 2742.0, "price_current": 2745.0, "sl": 0.0, "tp": 0.0, "volume": 0.02},
        {"ticket": 1003, "symbol": "EURUSD", "type": "BUY", "price_open": 1.0850, "price_current": 1.0860, "sl": 0.0, "tp": 0.0, "volume": 0.10}
    ]
    quick_kb_multi = get_quick_stops_keyboard(multi_sym_positions, selected_symbol="XAUUSD")
    m_callbacks = [btn.callback_data for row in quick_kb_multi.inline_keyboard for btn in row]
    assert "qpair_ALL" in m_callbacks, "qpair_ALL missing!"
    assert "qpair_XAUUSD" in m_callbacks, "qpair_XAUUSD missing!"
    assert "qpair_EURUSD" in m_callbacks, "qpair_EURUSD missing!"
    
    # Check that selected symbol has checkmark
    for row in quick_kb_multi.inline_keyboard:
        for btn in row:
            if btn.callback_data == "qpair_XAUUSD":
                assert "✅" in btn.text, f"Expected checkmark in active pair button, got: {btn.text}"
            elif btn.callback_data == "qpair_EURUSD":
                assert "✅" not in btn.text
    print("  [OK] Multi-pair 1-tap buttons & selection indicator verified.")

    # 5. Check dashboard action buttons
    quick_kb = get_quick_stops_keyboard()
    q_callbacks = [btn.callback_data for row in quick_kb.inline_keyboard for btn in row]
    assert "qstop_mode_both" in q_callbacks, "qstop_mode_both missing!"
    assert "qstop_mode_sl_only" in q_callbacks, "qstop_mode_sl_only missing!"
    assert "qstop_mode_tp_only" in q_callbacks, "qstop_mode_tp_only missing!"
    assert "qstop_mode_be" in q_callbacks, "qstop_mode_be missing!"
    assert "qstop_mode_clear_all" in q_callbacks, "qstop_mode_clear_all missing!"
    assert "batch_back_home" in q_callbacks, "batch_back_home missing!"
    print("  [OK] All Quick Stops dashboard action buttons verified.")

    # 6. Check guided keyboards
    g_sl_cb = [btn.callback_data for row in get_quick_guided_sl_keyboard().inline_keyboard for btn in row]
    assert "qsl_skip" in g_sl_cb and "qsl_clear" in g_sl_cb
    
    g_tp_cb = [btn.callback_data for row in get_quick_guided_tp_keyboard().inline_keyboard for btn in row]
    assert "qtp_skip" in g_tp_cb and "qtp_clear" in g_tp_cb
    print("  [OK] Guided step-by-step keyboards verified.")

def test_quick_price_parser():
    print("\nTesting Smart Quick Price Parser...")
    mock_positions_buy = [
        {"ticket": 1001, "symbol": "XAUUSD", "type": "BUY", "price_open": 2740.0, "price_current": 2745.0, "sl": 0.0, "tp": 0.0, "volume": 0.02},
        {"ticket": 1002, "symbol": "XAUUSD", "type": "BUY", "price_open": 2742.0, "price_current": 2745.0, "sl": 0.0, "tp": 0.0, "volume": 0.02},
    ]

    # Test 1: Standard two numbers "2735.50 2790.00"
    sl, tp, target_pos, err = parse_quick_price_input("2735.50 2790.00", mock_positions_buy)
    assert err is None
    assert sl == 2735.50 and tp == 2790.00
    assert len(target_pos) == 2
    print("  [OK] '2735.50 2790.00' parsed -> SL: 2735.5, TP: 2790.0")

    # Test 2: Inverted order "2790.00 2735.50" (BUY auto-detection)
    sl, tp, target_pos, err = parse_quick_price_input("2790.00 2735.50", mock_positions_buy)
    assert err is None
    assert sl == 2735.50 and tp == 2790.00
    print("  [OK] Inverted order '2790.00 2735.50' auto-aligned -> SL: 2735.5, TP: 2790.0")

    # Test 3: Explicit 'sl' prefix
    sl, tp, target_pos, err = parse_quick_price_input("sl 2730.00", mock_positions_buy)
    assert err is None
    assert sl == 2730.00 and tp is None
    print("  [OK] 'sl 2730.00' parsed -> SL: 2730.0, TP: unchanged")

    # Test 4: Explicit 'tp' prefix
    sl, tp, target_pos, err = parse_quick_price_input("tp 2785.00", mock_positions_buy)
    assert err is None
    assert sl is None and tp == 2785.00
    print("  [OK] 'tp 2785.00' parsed -> SL: unchanged, TP: 2785.0")

    # Test 5: Single number below market (BUY auto-detection as SL)
    sl, tp, target_pos, err = parse_quick_price_input("2732.00", mock_positions_buy)
    assert err is None
    assert sl == 2732.00 and tp is None
    print("  [OK] Single value '2732.00' (< market 2745) auto-detected as SL.")

    # Test 6: Single number above market (BUY auto-detection as TP)
    sl, tp, target_pos, err = parse_quick_price_input("2788.00", mock_positions_buy)
    assert err is None
    assert sl is None and tp == 2788.00
    print("  [OK] Single value '2788.00' (> market 2745) auto-detected as TP.")

    # Test 7: Clear SL ("0 2790")
    sl, tp, target_pos, err = parse_quick_price_input("0 2790.00", mock_positions_buy)
    assert err is None
    assert sl == 0.0 and tp == 2790.00
    print("  [OK] '0 2790.00' parsed -> Clear SL, Set TP.")

    # Test 8: Pair selection via text alias "gold 2740 2790" in multi-pair environment
    multi_positions = [
        {"ticket": 1001, "symbol": "XAUUSD", "type": "BUY", "price_open": 2740.0, "price_current": 2745.0, "sl": 0.0, "tp": 0.0, "volume": 0.02},
        {"ticket": 1002, "symbol": "EURUSD", "type": "BUY", "price_open": 1.0850, "price_current": 1.0860, "sl": 0.0, "tp": 0.0, "volume": 0.10}
    ]
    sl, tp, target_pos, err = parse_quick_price_input("gold 2740 2790", multi_positions)
    assert err is None
    assert sl == 2740.0 and tp == 2790.0
    assert len(target_pos) == 1 and target_pos[0]["symbol"] == "XAUUSD"
    print("  [OK] 'gold 2740 2790' parsed -> Targeted XAUUSD only.")

    # Test 9: Pair selection via default_symbol="EURUSD"
    sl, tp, target_pos, err = parse_quick_price_input("1.0820 1.0900", multi_positions, default_symbol="EURUSD")
    assert err is None
    assert sl == 1.0820 and tp == 1.0900
    assert len(target_pos) == 1 and target_pos[0]["symbol"] == "EURUSD"
    print("  [OK] default_symbol='EURUSD' -> Targeted EURUSD only.")

    # Test 10: Non-existent pair error
    sl, tp, target_pos, err = parse_quick_price_input("btc 90000 95000", multi_positions)
    assert err is not None
    assert "No open positions found" in err
    print("  [OK] Non-existent pair correctly returned user-friendly error.")

@pytest.mark.asyncio
async def test_compute_portfolio_risk_summary():
    print("\nTesting compute_portfolio_risk_summary_lines...")
    mock_positions = [
        {"ticket": 1001, "symbol": "XAUUSD", "type": "BUY", "price_open": 2740.0, "price_current": 2745.0, "sl": 2735.0, "tp": 2790.0, "profit": 10.0, "volume": 0.02},
        {"ticket": 1002, "symbol": "XAUUSD", "type": "BUY", "price_open": 2742.0, "price_current": 2745.0, "sl": 2735.0, "tp": 2790.0, "profit": 6.0, "volume": 0.02},
    ]
    mock_sym_info = {
        "digits": 2,
        "point": 0.01,
        "trade_tick_size": 0.01,
        "trade_tick_value": 1.0,
    }
    mock_acc = {
        "currency": "USD"
    }

    with patch("bot.handlers.batch.get_symbol_info", return_value=mock_sym_info), \
         patch("bot.handlers.batch.get_account_details", return_value=mock_acc):
        lines = await compute_portfolio_risk_summary_lines(mock_positions)
        assert len(lines) == 3
        assert "📊 *Float P&L* ·" in lines[0]
        assert "🔴 *Total SL Risk* ·" in lines[1]
        assert "🟢 *Total TP Target* ·" in lines[2]
        print(f"  [OK] Risk lines computed:\n    {lines[0]}\n    {lines[1]}\n    {lines[2]}")

    # Empty positions test
    empty_lines = await compute_portfolio_risk_summary_lines([])
    assert "📊 *Float P&L* · `$0.00`" in empty_lines[0]
    assert "🔴 *Total SL Risk* · _No open positions_" in empty_lines[1]
    assert "🟢 *Total TP Target* · _No open positions_" in empty_lines[2]
    print("  [OK] Empty positions handled gracefully.")

@pytest.mark.asyncio
async def test_apply_quick_stops():
    print("\nTesting apply_quick_stops_values execution...")
    mock_positions = [
        {"ticket": 1001, "symbol": "XAUUSD", "type": "BUY", "price_open": 2740.0, "price_current": 2745.0, "sl": 0.0, "tp": 0.0, "profit": 10.0, "volume": 0.02},
        {"ticket": 1002, "symbol": "XAUUSD", "type": "BUY", "price_open": 2742.0, "price_current": 2745.0, "sl": 0.0, "tp": 0.0, "profit": 6.0, "volume": 0.02},
    ]
    mock_sym_info = {
        "name": "XAUUSD",
        "digits": 2,
        "bid": 2745.0,
        "ask": 2745.3,
        "point": 0.01,
        "trade_tick_size": 0.01,
        "trade_tick_value": 1.0,
    }
    mock_acc = {
        "currency": "USD"
    }

    with patch("bot.handlers.batch.get_symbol_info", return_value=mock_sym_info), \
         patch("bot.handlers.batch.get_account_details", return_value=mock_acc), \
         patch("bot.handlers.batch.get_active_positions", return_value=mock_positions), \
         patch("bot.handlers.batch.modify_position_stops", return_value={"success": True, "message": "OK"}) as mock_modify:
        
        res = await apply_quick_stops_values(mock_positions, sl_val=2735.0, tp_val=2790.0)
        assert "Quick Stops Applied!" in res
        assert "Successful:* `2/2`" in res
        assert "📊 *Float P&L* · " in res
        assert "🔴 *Total SL Risk* · " in res
        assert "🟢 *Total TP Target* · " in res
        assert mock_modify.call_count == 2
        print("  [OK] Both SL & TP applied across 2 positions successfully with Float P&L, SL Risk, and TP Target.")

    print("\nAll Quick Stops tests passed with 100% success!")

def main():
    test_quick_stops_keyboards()
    test_quick_price_parser()
    asyncio.run(test_compute_portfolio_risk_summary())
    asyncio.run(test_apply_quick_stops())

if __name__ == "__main__":
    main()
