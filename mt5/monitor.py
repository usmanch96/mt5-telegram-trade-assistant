import MetaTrader5 as mt5
from typing import Dict, Any, List, Optional
from mt5.connector import check_connection
from services.logger import logger

def get_account_details() -> Optional[Dict[str, Any]]:
    """
    Retrieves current account status.
    Returns a dictionary of account statistics or None if request fails.
    """
    if not check_connection():
        logger.error("Cannot retrieve account details: MT5 not connected.")
        return None
        
    try:
        acc_info = mt5.account_info()
        if acc_info is None:
            logger.error(f"Failed to fetch account info. Error: {mt5.last_error()}")
            return None
            
        return {
            "login": acc_info.login,
            "name": acc_info.name,
            "trade_mode": acc_info.trade_mode,
            "leverage": acc_info.leverage,
            "currency": acc_info.currency,
            "balance": acc_info.balance,
            "equity": acc_info.equity,
            "profit": acc_info.profit,
            "margin": acc_info.margin,
            "margin_free": acc_info.margin_free,
            "margin_level": acc_info.margin_level,
            "company": acc_info.company
        }
    except Exception as e:
        logger.error(f"Exception while getting account details: {e}")
        return None

def get_active_positions(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves all currently active trade positions.
    Optionally filters by a specific symbol.
    """
    if not check_connection():
        logger.error("Cannot retrieve positions: MT5 not connected.")
        return []
        
    try:
        # Retrieve positions
        if symbol:
            raw_positions = mt5.positions_get(symbol=symbol.upper())
        else:
            raw_positions = mt5.positions_get()
            
        if raw_positions is None:
            err = mt5.last_error()
            if err[0] != 1:  # 1 means no positions found (not actually an error)
                logger.error(f"Failed to get active positions. Error: {err}")
            return []
            
        positions = []
        for pos in raw_positions:
            # type: 0 = Buy, 1 = Sell
            pos_type = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
            positions.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": pos_type,
                "price_open": pos.price_open,
                "price_current": pos.price_current,
                "sl": pos.sl,
                "tp": pos.tp,
                "profit": pos.profit,
                "swap": pos.swap,
                "comment": pos.comment,
                "time": pos.time
            })
        return positions
    except Exception as e:
        logger.error(f"Exception while getting active positions: {e}")
        return []

def get_symbol_info(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves information for a specific trading symbol (e.g., pip size, bid/ask, tick size).
    """
    if not check_connection():
        return None
        
    try:
        symbol_upper = symbol.upper()
        # Ensure symbol is visible in Market Watch
        if not mt5.symbol_select(symbol_upper, True):
            logger.error(f"Failed to select symbol {symbol_upper} in Market Watch.")
            return None
            
        info = mt5.symbol_info(symbol_upper)
        if info is None:
            return None
            
        return {
            "name": info.name,
            "digits": info.digits,
            "point": info.point,
            "trade_tick_size": info.trade_tick_size,
            "trade_tick_value": info.trade_tick_value,
            "ask": info.ask,
            "bid": info.bid,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
            "description": info.description
        }
    except Exception as e:
        logger.error(f"Exception getting symbol info for {symbol}: {e}")
        return None

def get_active_orders(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves all currently active pending orders (limit and stop orders).
    Optionally filters by a specific symbol.
    """
    if not check_connection():
        logger.error("Cannot retrieve pending orders: MT5 not connected.")
        return []
        
    try:
        if symbol:
            raw_orders = mt5.orders_get(symbol=symbol.upper())
        else:
            raw_orders = mt5.orders_get()
            
        if raw_orders is None:
            err = mt5.last_error()
            if err[0] != 1:  # 1 means no orders found (not an error)
                logger.error(f"Failed to get active pending orders. Error: {err}")
            return []
            
        orders = []
        for ord in raw_orders:
            ord_type_str = "PENDING"
            if ord.type == mt5.ORDER_TYPE_BUY_LIMIT:
                ord_type_str = "BUY_LIMIT"
            elif ord.type == mt5.ORDER_TYPE_SELL_LIMIT:
                ord_type_str = "SELL_LIMIT"
            elif ord.type == mt5.ORDER_TYPE_BUY_STOP:
                ord_type_str = "BUY_STOP"
            elif ord.type == mt5.ORDER_TYPE_SELL_STOP:
                ord_type_str = "SELL_STOP"
                
            orders.append({
                "ticket": ord.ticket,
                "symbol": ord.symbol,
                "volume": ord.volume_initial,
                "type": ord_type_str,
                "price_open": ord.price_open,
                "price_current": ord.price_current,
                "sl": ord.sl,
                "tp": ord.tp,
                "profit": 0.0,
                "comment": ord.comment,
                "time": ord.time_setup
            })
        return orders
    except Exception as e:
        logger.error(f"Exception while getting active pending orders: {e}")
        return []

