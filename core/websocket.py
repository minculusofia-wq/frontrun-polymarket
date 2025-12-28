"""
WebSocket Manager for Polymarket CLOB.
Real-time order book streaming with <50ms latency.
"""

import asyncio
import json
import logging
import time
from typing import Dict, Optional, Callable, Set, List
from dataclasses import dataclass, field
from enum import Enum

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """WebSocket connection state."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


@dataclass
class OrderBookUpdate:
    """Real-time order book update from WebSocket."""
    token_id: str
    bids: List[tuple]  # (price, size)
    asks: List[tuple]  # (price, size)
    timestamp: float = field(default_factory=time.time)
    is_snapshot: bool = False


class WebSocketManager:
    """
    WebSocket connection manager for Polymarket CLOB.

    Features:
    - Auto-reconnect with exponential backoff
    - Multiple market subscriptions
    - Real-time order book cache
    - Callback system for updates
    """

    # Polymarket WebSocket endpoints
    WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    WS_USER_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"

    def __init__(self):
        self._connection: Optional[websockets.WebSocketClientProtocol] = None
        self._state = ConnectionState.DISCONNECTED
        self._subscribed_markets: Set[str] = set()

        # Order book cache (real-time from WebSocket)
        self._orderbook_cache: Dict[str, OrderBookUpdate] = {}

        # Callbacks
        self._on_orderbook_update: Optional[Callable[[OrderBookUpdate], None]] = None
        self._on_connection_change: Optional[Callable[[ConnectionState], None]] = None

        # Reconnection settings
        self._backoff = 1.0
        self._max_backoff = 30.0
        self._reconnect_task: Optional[asyncio.Task] = None

        # Running state
        self._running = False
        self._receive_task: Optional[asyncio.Task] = None

        # Stats
        self._messages_received = 0
        self._last_message_time: Optional[float] = None

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == ConnectionState.CONNECTED

    def set_callbacks(
        self,
        on_orderbook_update: Callable[[OrderBookUpdate], None] = None,
        on_connection_change: Callable[[ConnectionState], None] = None
    ):
        """Set callback functions."""
        self._on_orderbook_update = on_orderbook_update
        self._on_connection_change = on_connection_change

    def _set_state(self, new_state: ConnectionState):
        """Update connection state and notify."""
        self._state = new_state
        logger.info(f"WebSocket state: {new_state.value}")
        if self._on_connection_change:
            try:
                self._on_connection_change(new_state)
            except Exception as e:
                logger.error(f"Connection change callback error: {e}")

    async def connect(self) -> bool:
        """
        Connect to WebSocket server.

        Returns:
            True if connection successful
        """
        if self._state in (ConnectionState.CONNECTED, ConnectionState.CONNECTING):
            return self._state == ConnectionState.CONNECTED

        self._set_state(ConnectionState.CONNECTING)
        self._running = True

        try:
            self._connection = await asyncio.wait_for(
                websockets.connect(
                    self.WS_MARKET_URL,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5
                ),
                timeout=10
            )

            self._set_state(ConnectionState.CONNECTED)
            self._backoff = 1.0  # Reset backoff on success

            # Start receive loop
            self._receive_task = asyncio.create_task(self._receive_loop())

            # Resubscribe to markets
            await self._resubscribe()

            logger.info("WebSocket connected successfully")
            return True

        except asyncio.TimeoutError:
            logger.error("WebSocket connection timeout")
            self._set_state(ConnectionState.DISCONNECTED)
            return False
        except Exception as e:
            logger.error(f"WebSocket connection error: {e}")
            self._set_state(ConnectionState.DISCONNECTED)
            return False

    async def disconnect(self):
        """Disconnect from WebSocket server."""
        self._running = False

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        if self._connection:
            try:
                await self._connection.close()
            except Exception:
                pass

        self._connection = None
        self._set_state(ConnectionState.DISCONNECTED)
        logger.info("WebSocket disconnected")

    async def subscribe_market(self, token_id: str) -> bool:
        """
        Subscribe to order book updates for a market.

        Args:
            token_id: The token ID to subscribe to

        Returns:
            True if subscription successful
        """
        if token_id in self._subscribed_markets:
            return True

        if not self.is_connected:
            # Queue for subscription when connected
            self._subscribed_markets.add(token_id)
            return False

        try:
            subscribe_msg = {
                "type": "subscribe",
                "channel": "book",
                "market": token_id
            }
            await self._connection.send(json.dumps(subscribe_msg))
            self._subscribed_markets.add(token_id)
            logger.debug(f"Subscribed to market: {token_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to subscribe to {token_id}: {e}")
            return False

    async def unsubscribe_market(self, token_id: str):
        """Unsubscribe from a market."""
        if token_id not in self._subscribed_markets:
            return

        if self.is_connected:
            try:
                unsubscribe_msg = {
                    "type": "unsubscribe",
                    "channel": "book",
                    "market": token_id
                }
                await self._connection.send(json.dumps(unsubscribe_msg))
            except Exception as e:
                logger.error(f"Failed to unsubscribe from {token_id}: {e}")

        self._subscribed_markets.discard(token_id)
        self._orderbook_cache.pop(token_id, None)

    async def _resubscribe(self):
        """Resubscribe to all markets after reconnection."""
        for token_id in list(self._subscribed_markets):
            try:
                subscribe_msg = {
                    "type": "subscribe",
                    "channel": "book",
                    "market": token_id
                }
                await self._connection.send(json.dumps(subscribe_msg))
                await asyncio.sleep(0.05)  # Rate limit
            except Exception as e:
                logger.error(f"Failed to resubscribe to {token_id}: {e}")

    async def _receive_loop(self):
        """Main receive loop for WebSocket messages."""
        while self._running and self._connection:
            try:
                message = await self._connection.recv()
                self._messages_received += 1
                self._last_message_time = time.time()

                await self._handle_message(message)

            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error receiving message: {e}")
                await asyncio.sleep(0.1)

        # Connection lost, attempt reconnect
        if self._running:
            self._set_state(ConnectionState.RECONNECTING)
            self._reconnect_task = asyncio.create_task(self._reconnect_with_backoff())

    async def _handle_message(self, raw_message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(raw_message)
            msg_type = data.get("type", "")

            if msg_type == "book":
                await self._handle_orderbook_update(data)
            elif msg_type == "subscribed":
                logger.debug(f"Subscription confirmed: {data.get('market')}")
            elif msg_type == "error":
                logger.error(f"WebSocket error: {data.get('message')}")
            elif msg_type == "ping":
                # Respond to ping
                await self._connection.send(json.dumps({"type": "pong"}))

        except json.JSONDecodeError:
            logger.error(f"Invalid JSON message: {raw_message[:100]}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def _handle_orderbook_update(self, data: dict):
        """Handle order book update message."""
        token_id = data.get("market")
        if not token_id:
            return

        # Parse bids and asks
        bids = [(float(b["price"]), float(b["size"])) for b in data.get("bids", [])]
        asks = [(float(a["price"]), float(a["size"])) for a in data.get("asks", [])]

        update = OrderBookUpdate(
            token_id=token_id,
            bids=bids,
            asks=asks,
            is_snapshot=data.get("snapshot", False)
        )

        # Update cache
        self._orderbook_cache[token_id] = update

        # Notify callback
        if self._on_orderbook_update:
            try:
                self._on_orderbook_update(update)
            except Exception as e:
                logger.error(f"Order book callback error: {e}")

    async def _reconnect_with_backoff(self):
        """Reconnect with exponential backoff."""
        while self._running:
            logger.info(f"Reconnecting in {self._backoff:.1f}s...")
            await asyncio.sleep(self._backoff)

            if await self.connect():
                return

            # Increase backoff
            self._backoff = min(self._backoff * 2, self._max_backoff)

    def get_orderbook(self, token_id: str) -> Optional[OrderBookUpdate]:
        """
        Get cached order book for a market.

        Args:
            token_id: The token ID

        Returns:
            Cached order book or None
        """
        return self._orderbook_cache.get(token_id)

    def get_best_prices(self, token_id: str) -> Optional[tuple]:
        """
        Get best bid/ask for a market.

        Returns:
            (best_bid, best_ask) or None
        """
        book = self._orderbook_cache.get(token_id)
        if not book or not book.bids or not book.asks:
            return None

        best_bid = book.bids[0][0] if book.bids else 0
        best_ask = book.asks[0][0] if book.asks else 1
        return (best_bid, best_ask)

    def get_stats(self) -> Dict:
        """Get WebSocket statistics."""
        return {
            "state": self._state.value,
            "subscribed_markets": len(self._subscribed_markets),
            "cached_orderbooks": len(self._orderbook_cache),
            "messages_received": self._messages_received,
            "last_message_age": time.time() - self._last_message_time if self._last_message_time else None
        }


# Singleton instance
_ws_manager: Optional[WebSocketManager] = None


def get_websocket_manager() -> WebSocketManager:
    """Get or create WebSocket manager instance."""
    global _ws_manager
    if _ws_manager is None:
        _ws_manager = WebSocketManager()
    return _ws_manager
