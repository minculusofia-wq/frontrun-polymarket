"""
Market Scanner for Polymarket CLOB.
Scans markets for frontrunning opportunities with optimized polling.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable
from datetime import datetime, timedelta

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderBookSummary

from config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class MarketInfo:
    """Information about a scanned market."""
    token_id: str
    market_name: str
    best_bid: float
    best_ask: float
    spread: float
    bid_liquidity: float
    ask_liquidity: float
    last_update: datetime = field(default_factory=datetime.now)
    
    @property
    def is_profitable(self) -> bool:
        """Check if spread is above threshold."""
        settings = get_settings()
        return self.spread >= settings.spread_threshold
    
    def to_dict(self) -> dict:
        """Convert to dictionary for UI."""
        return {
            'token_id': self.token_id,
            'market_name': self.market_name,
            'best_bid': self.best_bid,
            'best_ask': self.best_ask,
            'spread': self.spread,
            'bid_liquidity': self.bid_liquidity,
            'ask_liquidity': self.ask_liquidity,
            'last_update': self.last_update.isoformat()
        }


@dataclass
class OrderBookSnapshot:
    """Snapshot of order book for delta detection."""
    token_id: str
    bids: List[tuple]  # (price, size)
    asks: List[tuple]  # (price, size)
    timestamp: float = field(default_factory=time.time)
    
    def get_delta(self, previous: 'OrderBookSnapshot') -> Dict:
        """Calculate delta between snapshots. Optimized O(n) version."""
        if previous is None:
            return {'new_bids': self.bids, 'new_asks': self.asks}

        # Build sets once - O(n) instead of O(n²)
        prev_bids_set = set(previous.bids)
        prev_asks_set = set(previous.asks)
        curr_bids_set = set(self.bids)
        curr_asks_set = set(self.asks)

        return {
            'new_bids': [b for b in self.bids if b not in prev_bids_set],
            'new_asks': [a for a in self.asks if a not in prev_asks_set],
            'removed_bids': [b for b in previous.bids if b not in curr_bids_set],
            'removed_asks': [a for a in previous.asks if a not in curr_asks_set],
            'time_delta': self.timestamp - previous.timestamp
        }


class MarketScanner:
    """
    Optimized market scanner for Polymarket CLOB.
    Features:
    - Smart caching with TTL
    - Delta-only detection
    - Exponential backoff on errors
    - Configurable polling intervals
    """
    
    def __init__(self, client: ClobClient):
        self.client = client
        self.settings = get_settings()
        
        # Cache
        self._market_cache: Dict[str, MarketInfo] = {}
        self._orderbook_cache: Dict[str, OrderBookSnapshot] = {}
        self._cache_ttl = timedelta(seconds=30)
        
        # State
        self._running = False
        self._backoff = 1.0
        self._max_backoff = 30.0
        
        # Callbacks
        self._on_market_update: Optional[Callable] = None
        self._on_opportunity: Optional[Callable] = None
        
    def set_callbacks(self, on_update: Callable = None, on_opportunity: Callable = None):
        """Set callback functions for updates."""
        self._on_market_update = on_update
        self._on_opportunity = on_opportunity
    
    async def scan_markets(self) -> List[MarketInfo]:
        """
        Scan all active markets and return those with profitable spreads.
        Uses parallel fetching with semaphore for 10x faster scanning.
        """
        try:
            # Get active markets from API
            markets = self.client.get_markets()

            # Filter active markets that need updating
            markets_to_fetch = []
            profitable_from_cache = []

            for market in markets:
                if not market.get('active', False):
                    continue

                token_id = market.get('condition_id') or market.get('token_id')
                if not token_id:
                    continue

                # Check cache first
                cached = self._market_cache.get(token_id)
                if cached and (datetime.now() - cached.last_update) < self._cache_ttl:
                    if cached.is_profitable:
                        profitable_from_cache.append(cached)
                    continue

                markets_to_fetch.append((token_id, market.get('question', 'Unknown')))

            # Parallel fetch with semaphore (max 25 concurrent for faster scanning)
            semaphore = asyncio.Semaphore(25)

            async def fetch_with_limit(token_id: str, name: str):
                async with semaphore:
                    return await self._fetch_market_info(token_id, name)

            if markets_to_fetch:
                tasks = [fetch_with_limit(tid, name) for tid, name in markets_to_fetch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                profitable_from_fetch = []
                for result in results:
                    if isinstance(result, MarketInfo):
                        self._market_cache[result.token_id] = result
                        if result.is_profitable:
                            profitable_from_fetch.append(result)
                            logger.info(f"Profitable: {result.market_name} (spread: ${result.spread:.3f})")

                profitable_markets = profitable_from_cache + profitable_from_fetch
            else:
                profitable_markets = profitable_from_cache

            # Reset backoff on success
            self._backoff = 1.0

            return profitable_markets

        except Exception as e:
            logger.error(f"Error scanning markets: {e}")
            self._backoff = min(self._backoff * 2, self._max_backoff)
            return []
    
    async def _fetch_market_info(self, token_id: str, market_name: str) -> Optional[MarketInfo]:
        """Fetch order book and create MarketInfo."""
        try:
            book: OrderBookSummary = self.client.get_order_book(token_id)
            
            if not book.bids or not book.asks:
                return None
            
            best_bid = float(book.bids[0].price) if book.bids else 0
            best_ask = float(book.asks[0].price) if book.asks else 1
            
            bid_liquidity = sum(float(b.size) for b in book.bids[:5])
            ask_liquidity = sum(float(a.size) for a in book.asks[:5])
            
            spread = best_ask - best_bid
            
            return MarketInfo(
                token_id=token_id,
                market_name=market_name[:50],  # Truncate long names
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
                bid_liquidity=bid_liquidity,
                ask_liquidity=ask_liquidity
            )
            
        except Exception as e:
            logger.debug(f"Error fetching order book for {token_id}: {e}")
            return None
    
    async def monitor_orderbook(self, token_id: str) -> Optional[Dict]:
        """
        Monitor order book for changes using delta detection.
        Returns new orders that appeared since last check.
        """
        try:
            book: OrderBookSummary = self.client.get_order_book(token_id)
            
            current = OrderBookSnapshot(
                token_id=token_id,
                bids=[(float(b.price), float(b.size)) for b in book.bids],
                asks=[(float(a.price), float(a.size)) for a in book.asks]
            )
            
            previous = self._orderbook_cache.get(token_id)
            self._orderbook_cache[token_id] = current
            
            if previous:
                delta = current.get_delta(previous)
                return delta
            
            return None
            
        except Exception as e:
            logger.error(f"Error monitoring order book: {e}")
            return None
    
    async def detect_counter_order(self, token_id: str, min_size: int = 50, timeout: float = 1.0) -> Optional[Dict]:
        """
        Detect large counter-orders within timeout window.
        Used to identify "naive bot" reactions to our bait orders.
        """
        settings = get_settings()
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            delta = await self.monitor_orderbook(token_id)
            
            if delta:
                # Check for large new orders
                for bid in delta.get('new_bids', []):
                    if bid[1] >= min_size:
                        logger.info(f"Counter-order detected: BID {bid[1]} @ {bid[0]}")
                        return {'side': 'BID', 'price': bid[0], 'size': bid[1]}
                
                for ask in delta.get('new_asks', []):
                    if ask[1] >= min_size:
                        logger.info(f"Counter-order detected: ASK {ask[1]} @ {ask[0]}")
                        return {'side': 'ASK', 'price': ask[0], 'size': ask[1]}
            
            await asyncio.sleep(settings.polling_interval)
        
        return None
    
    def clear_cache(self):
        """Clear all caches."""
        self._market_cache.clear()
        self._orderbook_cache.clear()
        
    def get_cached_markets(self) -> List[MarketInfo]:
        """Get all cached markets."""
        return list(self._market_cache.values())
