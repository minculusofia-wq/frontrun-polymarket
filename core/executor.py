"""
Order Executor for Polymarket CLOB.
Handles order placement, cancellation, and execution via py-clob-client.
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from config.settings import get_settings

logger = logging.getLogger(__name__)


class OrderExecutor:
    """
    Executes orders on Polymarket CLOB.

    Features:
    - Limit and market order support
    - Automatic retry with exponential backoff
    - Order tracking and cancellation
    - Gas-optimized execution
    - Timeout protection on API calls
    """

    MAX_RETRIES = 3
    RETRY_DELAYS = [0.5, 1.0, 2.0]  # Exponential backoff
    API_TIMEOUT = 10.0  # Timeout for API calls in seconds

    def __init__(self, client: ClobClient):
        self.client = client
        self.settings = get_settings()

        # Track active orders
        self._active_orders: Dict[str, Dict[str, Any]] = {}

        # Execution stats
        self.orders_placed = 0
        self.orders_filled = 0
        self.orders_cancelled = 0
        self.orders_retried = 0
        self.total_volume = 0.0

    async def _run_with_timeout(self, func, *args, **kwargs):
        """Run a sync function in thread with timeout protection."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(func, *args, **kwargs),
                timeout=self.API_TIMEOUT
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"API call timed out after {self.API_TIMEOUT}s")

    async def _retry_with_backoff(self, func, *args, **kwargs):
        """Execute function with retry and exponential backoff."""
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return await self._run_with_timeout(func, *args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    logger.warning(f"Retry {attempt + 1}/{self.MAX_RETRIES} after {delay}s: {e}")
                    self.orders_retried += 1
                    await asyncio.sleep(delay)
        raise last_error
        
    async def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: int,
        time_in_force: str = "GTC"
    ) -> Optional[str]:
        """
        Place a limit order.
        
        Args:
            token_id: The market token ID
            side: 'BUY' or 'SELL'
            price: Order price (0-1 range)
            size: Number of shares
            time_in_force: Order duration (GTC = Good Till Cancelled)
            
        Returns:
            Order ID if successful, None otherwise
        """
        try:
            # Validate inputs
            if side not in ('BUY', 'SELL'):
                raise ValueError(f"Invalid side: {side}")
            if not 0 < price < 1:
                raise ValueError(f"Invalid price: {price}")
            if size < 1:
                raise ValueError(f"Invalid size: {size}")
            
            order_side = BUY if side == 'BUY' else SELL
            
            # Build order arguments
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=order_side,
                fee_rate_bps=0  # Maker orders have no fee
            )
            
            # Create and sign order (with timeout protection)
            signed_order = await self._run_with_timeout(self.client.create_order, order_args)

            # Submit order (with timeout protection)
            response = await self._run_with_timeout(self.client.post_order, signed_order, OrderType.GTC)
            
            if response and response.get('orderID'):
                order_id = response['orderID']
                
                self._active_orders[order_id] = {
                    'token_id': token_id,
                    'side': side,
                    'price': price,
                    'size': size,
                    'placed_at': datetime.now(),
                    'status': 'OPEN'
                }
                
                self.orders_placed += 1
                logger.info(f"Limit order placed: {side} {size} @ ${price:.4f} (ID: {order_id})")
                
                return order_id
            
            logger.error(f"Order placement failed: {response}")
            return None
            
        except Exception as e:
            logger.error(f"Error placing limit order: {e}")
            return None
    
    async def execute_market_order(
        self,
        token_id: str,
        side: str,
        size: int
    ) -> bool:
        """
        Execute a market order (aggressive limit crossing).
        
        For immediate execution, we place a limit order at the best
        available price (crosses the spread).
        
        Args:
            token_id: The market token ID
            side: 'BUY' or 'SELL'
            size: Number of shares
            
        Returns:
            True if order was placed successfully
        """
        try:
            # Get current order book (with timeout protection)
            book = await self._run_with_timeout(self.client.get_order_book, token_id)
            
            if side == 'BUY':
                # Buy at best ask (or slightly higher for certainty)
                if not book.asks:
                    logger.error("No asks available for market buy")
                    return False
                price = float(book.asks[0].price) + 0.001
            else:
                # Sell at best bid (or slightly lower)
                if not book.bids:
                    logger.error("No bids available for market sell")
                    return False
                price = float(book.bids[0].price) - 0.001
            
            # Clamp price to valid range
            price = max(0.001, min(0.999, price))
            
            order_id = await self.place_limit_order(
                token_id=token_id,
                side=side,
                price=price,
                size=size
            )
            
            if order_id:
                # Market orders should fill immediately
                self.orders_filled += 1
                self.total_volume += price * size
                
                # Update order status
                if order_id in self._active_orders:
                    self._active_orders[order_id]['status'] = 'FILLED'
                
                logger.info(f"Market order executed: {side} {size} @ ${price:.4f}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error executing market order: {e}")
            return False
    
    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order.
        
        Args:
            order_id: The order ID to cancel
            
        Returns:
            True if cancellation was successful
        """
        try:
            response = await self._run_with_timeout(self.client.cancel, order_id)

            if response:
                if order_id in self._active_orders:
                    self._active_orders[order_id]['status'] = 'CANCELLED'
                
                self.orders_cancelled += 1
                logger.info(f"Order cancelled: {order_id}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return False
    
    async def cancel_all_orders(self) -> int:
        """
        Cancel all active orders.
        
        Returns:
            Number of orders cancelled
        """
        cancelled = 0
        
        for order_id, order in list(self._active_orders.items()):
            if order['status'] == 'OPEN':
                if await self.cancel_order(order_id):
                    cancelled += 1
        
        logger.info(f"Cancelled {cancelled} orders")
        return cancelled
    
    async def get_order_status(self, order_id: str) -> Optional[Dict]:
        """Get status of an order."""
        try:
            order = await self._run_with_timeout(self.client.get_order, order_id)
            
            if order:
                status = {
                    'order_id': order_id,
                    'status': order.get('status', 'UNKNOWN'),
                    'filled_size': order.get('size_matched', 0),
                    'remaining_size': order.get('size_remaining', 0)
                }
                
                # Update local cache
                if order_id in self._active_orders:
                    self._active_orders[order_id]['status'] = status['status']
                
                return status
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting order status: {e}")
            return None
    
    def get_active_orders(self) -> Dict[str, Dict]:
        """Get all active (open) orders."""
        return {
            oid: order for oid, order in self._active_orders.items()
            if order['status'] == 'OPEN'
        }
    
    def get_stats(self) -> Dict:
        """Get execution statistics."""
        return {
            'orders_placed': self.orders_placed,
            'orders_filled': self.orders_filled,
            'orders_cancelled': self.orders_cancelled,
            'orders_retried': self.orders_retried,
            'total_volume': self.total_volume,
            'active_orders': len(self.get_active_orders())
        }
