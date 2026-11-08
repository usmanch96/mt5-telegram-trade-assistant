import MetaTrader5 as mt5
from typing import Dict, Any, Optional
from mt5.connector import check_connection
from services.logger import logger

def get_filling_mode(symbol: str) -> int:
    """
    Checks the broker's supported order execution/filling modes for the symbol.
    Returns the appropriate ORDER_FILLING_* constant.
    """
    sym_info = mt5.symbol_info(symbol)
    if not sym_info:
        return mt5.ORDER_FILLING_FOK
        
    filling = sym_info.filling_mode
    
    # Check bitmask flags (1 = SYMBOL_FILLING_FOK, 2 = SYMBOL_FILLING_IOC)
    if filling & 1:
        return mt5.ORDER_FILLING_FOK
    elif filling & 2:
        return mt5.ORDER_FILLING_IOC
    else:
        return mt5.ORDER_FILLING_RETURN

def translate_error_code(retcode: int) -> str:
    """Translates MT5 retcode errors into human-readable messages."""
    error_map = {
        10004: "Requote. The price changed, try again.",
        10006: "Request rejected by broker.",
        10009: "Trade request executed successfully.",
        10013: "Invalid request parameters (Check lot size or prices).",
        10014: "Invalid volume/lot size.",
        10015: "Invalid price (Price changed or digits mismatch).",
        10016: "Invalid SL or TP (Stops are too close to the current price).",
        10018: "Market is closed for this symbol.",
        10019: "No money (Insufficient margin/equity to open this lot size).",
        10020: "Prices changed. Retry trade.",
        10025: "No changes (Target SL/TP matches current position values).",
        10030: "Unsupported filling mode.",
        10034: "Trade is disabled by broker.",
        10036: "Position has already been closed (stopped out or closed manually).",
    }
    return error_map.get(retcode, f"Unknown error (code: {retcode}).")

def log_trade_request(request: dict):
    """Logs a clean, human-readable summary of an MT5 request instead of a raw dictionary."""
    symbol = request.get("symbol", "")
    vol = request.get("volume", 0.0)
    price = request.get("price", 0.0)
    sl = request.get("sl", 0.0)
    tp = request.get("tp", 0.0)
    
    sl_desc = f"SL: {sl:.5f}" if sl > 0 else "SL: None"
    tp_desc = f"TP: {tp:.5f}" if tp > 0 else "TP: None"
    
    if any(x in symbol for x in ["JPY", "XAU", "BTC", "US30", "NAS100"]):
        sl_desc = f"SL: {sl:.2f}" if sl > 0 else "SL: None"
        tp_desc = f"TP: {tp:.2f}" if tp > 0 else "TP: None"
        price_desc = f"@{price:.2f}" if price > 0 else ""
    else:
        price_desc = f"@{price:.5f}" if price > 0 else ""
        
    action = request.get("action")
    if action == mt5.TRADE_ACTION_DEAL:
        if request.get("position") is not None:
            logger.info(f"MT5 Exec: Close Position #{request.get('position')} {vol:.2f} {symbol} {price_desc}")
    elif action == mt5.TRADE_ACTION_SLTP:
        logger.info(f"MT5 Exec: Modify Stops ticket #{request.get('position')} ({sl_desc}, {tp_desc})")

def close_position(ticket: int, volume_to_close: Optional[float] = None) -> Dict[str, Any]:
    """
    Closes an active position by ticket. Supports partial close if volume_to_close is specified.
    """
    if not check_connection():
        return {"success": False, "message": "MT5 terminal disconnected."}
        
    position = mt5.positions_get(ticket=ticket)
    if not position or len(position) == 0:
        return {"success": False, "message": f"Position ticket #{ticket} not found."}
        
    pos = position[0]
    symbol = pos.symbol
    current_volume = pos.volume
    
    close_vol = float(volume_to_close) if volume_to_close else current_volume
    if close_vol > current_volume:
        return {"success": False, "message": f"Cannot close {close_vol} lots. Position size is only {current_volume} lots."}

    sym_info = mt5.symbol_info(symbol)
    if sym_info and sym_info.volume_step > 0:
        close_vol = min(current_volume, max(sym_info.volume_min, round(round(close_vol / sym_info.volume_step) * sym_info.volume_step, 2)))
        
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return {"success": False, "message": "Failed to get price to close position."}
        
    # Determine closing action (BUY to close SELL, SELL to close BUY)
    if pos.type == mt5.POSITION_TYPE_BUY:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
        
    filling_mode = get_filling_mode(symbol)
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": close_vol,
        "type": order_type,
        "position": ticket,
        "price": float(price),
        "deviation": 50,
        "magic": 991122,
        "comment": "Bot close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }
    
    log_trade_request(request)
    result = mt5.order_send(request)
    
    if result is None:
        return {"success": False, "message": f"Close execution failed. Error: {mt5.last_error()}"}
        
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        err_msg = translate_error_code(result.retcode)
        logger.error(f"Close failed. Retcode: {result.retcode}. Result: {result.comment}")
        return {"success": False, "message": f"{err_msg} ({result.comment})"}
        
    return {"success": True, "message": f"Closed {close_vol} lots of ticket #{ticket}."}

def modify_position_stops(ticket: int, sl: float, tp: float) -> Dict[str, Any]:
    """
    Modifies SL and TP parameters of an active position.
    """
    if not check_connection():
        return {"success": False, "message": "MT5 terminal disconnected."}
        
    position = mt5.positions_get(ticket=ticket)
    if not position or len(position) == 0:
        return {"success": False, "message": f"Position ticket #{ticket} not found."}
        
    pos = position[0]
    
    tick = mt5.symbol_info_tick(pos.symbol)
    sym_info = mt5.symbol_info(pos.symbol)
    
    digits = 5
    if sym_info:
        digits = sym_info.digits
        point = sym_info.point
        stops_level = sym_info.trade_stops_level
        min_distance = stops_level * point
        
        sl = round(float(sl), digits) if sl > 0 else 0.0
        tp = round(float(tp), digits) if tp > 0 else 0.0
        
        if tick:
            if pos.type == mt5.POSITION_TYPE_BUY:
                current_price = tick.bid
                if sl > 0:
                    max_allowed_sl = current_price - min_distance
                    if sl > max_allowed_sl:
                        logger.warning(
                            f"SL adjustment for Buy ticket #{ticket} skipped: "
                            f"SL ({sl:.{digits}f}) is too close to Bid ({current_price:.{digits}f}). "
                            f"Stops level distance is {stops_level} points ({min_distance:.{digits}f})."
                        )
                        return {"success": False, "message": "Stops Level violation: SL is too close to Bid."}
                if tp > 0:
                    min_allowed_tp = current_price + min_distance
                    if tp < min_allowed_tp:
                        logger.warning(
                            f"TP adjustment for Buy ticket #{ticket} skipped: "
                            f"TP ({tp:.{digits}f}) is too close to Bid ({current_price:.{digits}f})."
                        )
                        return {"success": False, "message": "Stops Level violation: TP is too close to Bid."}
            else:
                current_price = tick.ask
                if sl > 0:
                    min_allowed_sl = current_price + min_distance
                    if sl < min_allowed_sl:
                        logger.warning(
                            f"SL adjustment for Sell ticket #{ticket} skipped: "
                            f"SL ({sl:.{digits}f}) is too close to Ask ({current_price:.{digits}f}). "
                            f"Stops level distance is {stops_level} points ({min_distance:.{digits}f})."
                        )
                        return {"success": False, "message": "Stops Level violation: SL is too close to Ask."}
                if tp > 0:
                    max_allowed_tp = current_price - min_distance
                    if tp > max_allowed_tp:
                        logger.warning(
                            f"TP adjustment for Sell ticket #{ticket} skipped: "
                            f"TP ({tp:.{digits}f}) is too close to Ask ({current_price:.{digits}f})."
                        )
                        return {"success": False, "message": "Stops Level violation: TP is too close to Ask."}

    # Check if target SL & TP already match current position parameters
    curr_sl = round(float(pos.sl), digits) if (sym_info and pos.sl > 0) else float(pos.sl)
    curr_tp = round(float(pos.tp), digits) if (sym_info and pos.tp > 0) else float(pos.tp)
    if sl == curr_sl and tp == curr_tp:
        logger.info(f"Modify stops skipped for ticket #{ticket}: Target SL ({sl}) & TP ({tp}) match current values.")
        return {"success": True, "message": f"Target SL/TP levels already set for ticket #{ticket}."}

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol": pos.symbol,
        "sl": sl,
        "tp": tp,
    }

    log_trade_request(request)
    result = mt5.order_send(request)

    if result is None:
        return {"success": False, "message": f"Modification failed. Error: {mt5.last_error()}"}

    # Retcode 10025 is "TRADE_RETCODE_NO_CHANGES" — treat as success
    if result.retcode == getattr(mt5, "TRADE_RETCODE_NO_CHANGES", 10025):
        logger.info(f"Modify stops for ticket #{ticket} completed: Retcode 10025 (No changes).")
        return {"success": True, "message": f"Target SL/TP already set for ticket #{ticket}."}

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        err_msg = translate_error_code(result.retcode)
        logger.error(f"Modify stops failed. Retcode: {result.retcode}. Result: {result.comment}")
        return {"success": False, "message": f"{err_msg} ({result.comment})"}
        
    return {"success": True, "message": f"Modified SL/TP parameters for ticket #{ticket}."}
