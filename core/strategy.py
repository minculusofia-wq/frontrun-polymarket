"""
Frontrun Strategy for Polymarket.
Implements the intelligent frontrunning logic based on behavioral asymmetry.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime

from config.settings import get_settings
from .scanner import MarketScanner, MarketInfo

# Constants for magic numbers
BAIT_SPREAD_OFFSET = 0.02  # Offset from mid for bait orders
FRONTRUN_PRICE_OFFSET = 0.01  # Price improvement for frontrun
COOLDOWN_NO_MARKET = 5  # Seconds to wait when no market found
COOLDOWN_AFTER_TRADE = 2  # Seconds to wait after trade
SORTED_CACHE_TTL = 5.0  # Seconds to cache sorted markets

logger = logging.getLogger(__name__)


class StrategyState(Enum):
    """Current state of the strategy."""
    IDLE = "idle"
    SCANNING = "scanning"
    BAITING = "baiting"
    MONITORING = "monitoring"
    EXECUTING = "executing"
    COOLDOWN = "cooldown"


@dataclass
class TradeOpportunity:
    """Represents a detected frontrun opportunity."""
    token_id: str
    market_name: str
    side: str  # 'BUY' or 'SELL'
    entry_price: float
    target_size: int
    counter_order: Dict
    detected_at: datetime = field(default_factory=datetime.now)
    estimated_profit: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            'token_id': self.token_id,
            'market_name': self.market_name,
            'side': self.side,
            'entry_price': self.entry_price,
            'target_size': self.target_size,
            'counter_order': self.counter_order,
            'detected_at': self.detected_at.isoformat(),
            'estimated_profit': self.estimated_profit
        }


@dataclass
class BaitOrder:
    """Represents our bait (micro) order."""
    order_id: Optional[str] = None
    token_id: str = ""
    side: str = ""
    price: float = 0.0
    size: int = 0
    placed_at: Optional[datetime] = None
    
    @property
    def is_active(self) -> bool:
        return self.order_id is not None


class FrontrunStrategy:
    """
    Intelligent frontrunning strategy.
    
    Flow:
    1. Scan for markets with exploitable spreads
    2. Place micro bait order to tighten spread
    3. Monitor for naive bot reaction (large counter-order)
    4. Frontrun the counter-order if detected
    5. Manage risk and repeat
    """
    
    def __init__(self, scanner: MarketScanner, executor=None):
        self.scanner = scanner
        self.executor = executor
        self.settings = get_settings()
        
        # State
        self.state = StrategyState.IDLE
        self.current_bait: Optional[BaitOrder] = None
        self.current_target: Optional[MarketInfo] = None

        # Cache for sorted markets (avoids O(n log n) sort every scan)
        self._sorted_markets_cache: Optional[List[MarketInfo]] = None
        self._sorted_cache_time: float = 0

        # Statistics
        self.trades_executed = 0
        self.total_pnl = 0.0
        self.last_trade_market = ""
        
        # Callbacks for UI
        self._on_state_change: Optional[Callable] = None
        self._on_opportunity: Optional[Callable] = None
        self._on_trade: Optional[Callable] = None
        
    def set_callbacks(self, 
                      on_state_change: Callable = None,
                      on_opportunity: Callable = None,
                      on_trade: Callable = None):
        """Set UI callback functions."""
        self._on_state_change = on_state_change
        self._on_opportunity = on_opportunity
        self._on_trade = on_trade
    
    def _set_state(self, new_state: StrategyState):
        """Update state and notify UI."""
        self.state = new_state
        logger.info(f"Strategy state: {new_state.value}")
        if self._on_state_change:
            self._on_state_change(new_state.value)
    
    async def find_target(self) -> Optional[MarketInfo]:
        """Find best market to target. Uses cached sorted results."""
        self._set_state(StrategyState.SCANNING)

        markets = await self.scanner.scan_markets()

        if not markets:
            logger.debug("No profitable markets found")
            return None

        # Use cached sorted markets if still valid
        now = time.time()
        if (self._sorted_markets_cache is not None and
            now - self._sorted_cache_time < SORTED_CACHE_TTL and
            len(self._sorted_markets_cache) == len(markets)):
            sorted_markets = self._sorted_markets_cache
        else:
            # Sort by spread (highest first) and liquidity
            sorted_markets = sorted(markets, key=lambda m: (m.spread, m.bid_liquidity + m.ask_liquidity), reverse=True)
            self._sorted_markets_cache = sorted_markets
            self._sorted_cache_time = now

        best = sorted_markets[0]
        logger.info(f"Target selected: {best.market_name} (spread: ${best.spread:.3f})")

        return best
    
    async def place_bait_order(self, market: MarketInfo) -> Optional[BaitOrder]:
        """
        Place micro bait order to tighten spread.
        We place at mid-spread to create artificial signal.
        """
        if not self.executor:
            logger.warning("No executor configured - cannot place orders")
            return None
        
        self._set_state(StrategyState.BAITING)
        
        settings = self.settings
        mid_price = (market.best_bid + market.best_ask) / 2

        # Tighten spread using constant offset
        bait_price = round(mid_price - BAIT_SPREAD_OFFSET, 3)  # Slightly below mid for buy
        
        try:
            order_id = await self.executor.place_limit_order(
                token_id=market.token_id,
                side='BUY',
                price=bait_price,
                size=settings.micro_order_size
            )
            
            bait = BaitOrder(
                order_id=order_id,
                token_id=market.token_id,
                side='BUY',
                price=bait_price,
                size=settings.micro_order_size,
                placed_at=datetime.now()
            )
            
            logger.info(f"Bait order placed: {bait.size} @ ${bait.price} (ID: {order_id})")
            return bait
            
        except Exception as e:
            logger.error(f"Failed to place bait order: {e}")
            return None
    
    async def monitor_for_reaction(self, token_id: str) -> Optional[TradeOpportunity]:
        """
        Monitor order book for naive bot reaction.
        Looking for large orders appearing within reaction threshold.
        """
        self._set_state(StrategyState.MONITORING)
        
        settings = self.settings
        counter = await self.scanner.detect_counter_order(
            token_id=token_id,
            min_size=settings.min_counter_order_size,
            timeout=settings.reaction_time_threshold
        )
        
        if not counter:
            logger.debug("No counter-order detected")
            return None
        
        # Calculate opportunity
        market = self.scanner._market_cache.get(token_id)
        if not market:
            return None
        
        # Determine our frontrun side (opposite of counter)
        if counter['side'] == 'BID':
            # Someone is buying, we should buy first (then sell to them)
            our_side = 'BUY'
            entry_price = counter['price'] - FRONTRUN_PRICE_OFFSET  # Slightly better price
        else:
            # Someone is selling, we should sell first
            our_side = 'SELL'
            entry_price = counter['price'] + FRONTRUN_PRICE_OFFSET
        
        # Estimate profit (simplified)
        spread_capture = abs(counter['price'] - entry_price)
        target_size = min(counter['size'], int(settings.max_trade_amount / entry_price))
        estimated_profit = spread_capture * target_size
        
        opportunity = TradeOpportunity(
            token_id=token_id,
            market_name=market.market_name,
            side=our_side,
            entry_price=entry_price,
            target_size=target_size,
            counter_order=counter,
            estimated_profit=estimated_profit
        )
        
        logger.info(f"Opportunity detected: {our_side} {target_size} @ ${entry_price:.3f} (est. profit: ${estimated_profit:.2f})")
        
        if self._on_opportunity:
            self._on_opportunity(opportunity.to_dict())
        
        return opportunity
    
    async def execute_frontrun(self, opportunity: TradeOpportunity) -> bool:
        """
        Execute frontrun trade.
        Uses market order or limit crossing for immediate execution.
        """
        if not self.executor:
            logger.warning("No executor configured")
            return False
        
        self._set_state(StrategyState.EXECUTING)
        
        try:
            success = await self.executor.execute_market_order(
                token_id=opportunity.token_id,
                side=opportunity.side,
                size=opportunity.target_size
            )
            
            if success:
                self.trades_executed += 1
                self.total_pnl += opportunity.estimated_profit
                self.last_trade_market = opportunity.market_name
                
                logger.info(f"Frontrun executed! PnL: ${opportunity.estimated_profit:.2f}")
                
                if self._on_trade:
                    self._on_trade({
                        'market': opportunity.market_name,
                        'side': opportunity.side,
                        'size': opportunity.target_size,
                        'profit': opportunity.estimated_profit,
                        'total_pnl': self.total_pnl
                    })
                
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Frontrun execution failed: {e}")
            return False
    
    async def cancel_bait(self):
        """Cancel current bait order if active."""
        if self.current_bait and self.current_bait.is_active and self.executor:
            try:
                await self.executor.cancel_order(self.current_bait.order_id)
                logger.info(f"Bait order cancelled: {self.current_bait.order_id}")
            except Exception as e:
                logger.error(f"Failed to cancel bait: {e}")
            finally:
                self.current_bait = None
    
    async def run_cycle(self) -> Optional[Dict[str, Any]]:
        """
        Run one complete strategy cycle.
        Returns trade info if executed, None otherwise.
        """
        try:
            # 1. Find target market
            market = await self.find_target()
            if not market:
                self._set_state(StrategyState.COOLDOWN)
                await asyncio.sleep(COOLDOWN_NO_MARKET)  # Wait before next scan
                return None
            
            self.current_target = market
            
            # 2. Place bait order
            bait = await self.place_bait_order(market)
            if not bait:
                return None
            
            self.current_bait = bait
            
            # 3. Monitor for reaction
            opportunity = await self.monitor_for_reaction(market.token_id)
            
            # 4. Cancel bait regardless of outcome
            await self.cancel_bait()
            
            if not opportunity:
                self._set_state(StrategyState.IDLE)
                return None
            
            # 5. Execute frontrun
            success = await self.execute_frontrun(opportunity)

            self._set_state(StrategyState.COOLDOWN)
            await asyncio.sleep(COOLDOWN_AFTER_TRADE)  # Cooldown after trade
            
            if success:
                return opportunity.to_dict()
            
            return None
            
        except Exception as e:
            logger.error(f"Strategy cycle error: {e}")
            await self.cancel_bait()
            self._set_state(StrategyState.IDLE)
            return None
    
    def get_stats(self) -> Dict:
        """Get current strategy statistics."""
        return {
            'state': self.state.value,
            'trades_executed': self.trades_executed,
            'total_pnl': self.total_pnl,
            'last_trade_market': self.last_trade_market
        }
