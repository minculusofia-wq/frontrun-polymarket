"""
Risk Manager for Polymarket Frontrun Bot.
Implements position sizing, loss limits, and circuit breakers.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional
from enum import Enum

from config.settings import get_settings

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """Current risk assessment level."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class TradeRecord:
    """Record of a completed trade."""
    timestamp: datetime
    market: str
    side: str
    size: int
    entry_price: float
    exit_price: float
    pnl: float
    
    @property
    def is_profitable(self) -> bool:
        return self.pnl > 0


@dataclass
class DailyStats:
    """Daily trading statistics."""
    date: date = field(default_factory=date.today)
    trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    
    @property
    def net_pnl(self) -> float:
        return self.gross_profit - self.gross_loss
    
    @property
    def win_rate(self) -> float:
        if self.trades == 0:
            return 0.0
        return self.wins / self.trades


class RiskManager:
    """
    Manages trading risk and position sizing.
    
    Features:
    - Max 1% bankroll per trade
    - Daily loss circuit breaker
    - Concurrent trade limits
    - PnL tracking
    """
    
    def __init__(self, initial_bankroll: Optional[float] = None):
        self.settings = get_settings()
        
        # Bankroll
        self._initial_bankroll = initial_bankroll or self.settings.bankroll
        self._current_bankroll = self._initial_bankroll
        
        # Trade tracking
        self._trade_history: List[TradeRecord] = []
        self._daily_stats: Dict[date, DailyStats] = {}
        self._active_trades = 0

        # Running counters for O(1) stats access
        self._total_wins = 0
        self._total_losses = 0

        # Circuit breaker state
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = ""
    
    @property
    def current_bankroll(self) -> float:
        """Current available bankroll."""
        return self._current_bankroll
    
    @property
    def total_pnl(self) -> float:
        """Total profit/loss since inception."""
        return self._current_bankroll - self._initial_bankroll
    
    @property
    def pnl_percent(self) -> float:
        """PnL as percentage of initial bankroll."""
        return (self.total_pnl / self._initial_bankroll) * 100
    
    def update_bankroll(self, new_value: float):
        """Update current bankroll."""
        self._current_bankroll = new_value
        logger.info(f"Bankroll updated: ${new_value:.2f}")

    def reset_bankroll(self, new_bankroll: float):
        """Reset bankroll to new value (from settings change)."""
        self._initial_bankroll = new_bankroll
        self._current_bankroll = new_bankroll
        logger.info(f"Bankroll reset to: ${new_bankroll:.2f}")
    
    def get_max_trade_size(self, price: float) -> int:
        """
        Calculate maximum trade size based on risk parameters.
        
        Args:
            price: Current price of the asset
            
        Returns:
            Maximum number of shares to trade
        """
        if self._circuit_breaker_active:
            return 0
        
        settings = self.settings
        max_usd = self._current_bankroll * (settings.max_trade_percent / 100)
        max_shares = int(max_usd / price) if price > 0 else 0
        
        return max(0, max_shares)
    
    def can_trade(self) -> tuple[bool, str]:
        """
        Check if trading is currently allowed.
        
        Returns:
            Tuple of (allowed, reason)
        """
        if self._circuit_breaker_active:
            return False, f"Circuit breaker: {self._circuit_breaker_reason}"
        
        settings = self.settings
        
        # Check concurrent trades
        if self._active_trades >= settings.max_concurrent_trades:
            return False, f"Max concurrent trades ({settings.max_concurrent_trades}) reached"
        
        # Check daily loss limit
        today_stats = self._get_today_stats()
        max_daily_loss = self._initial_bankroll * (settings.max_daily_loss_percent / 100)
        
        if today_stats.net_pnl < -max_daily_loss:
            self._activate_circuit_breaker("Daily loss limit exceeded")
            return False, "Daily loss limit exceeded"
        
        # Check minimum bankroll
        if self._current_bankroll < 1.0:
            self._activate_circuit_breaker("Bankroll depleted")
            return False, "Insufficient bankroll"
        
        return True, "OK"
    
    def _get_today_stats(self) -> DailyStats:
        """Get or create today's stats."""
        today = date.today()
        if today not in self._daily_stats:
            self._daily_stats[today] = DailyStats(date=today)
        return self._daily_stats[today]
    
    def record_trade_open(self):
        """Record that a trade has been opened."""
        self._active_trades += 1
        logger.debug(f"Trade opened. Active trades: {self._active_trades}")
    
    def record_trade_close(self, trade: TradeRecord):
        """
        Record a completed trade.
        
        Args:
            trade: The completed trade record
        """
        self._active_trades = max(0, self._active_trades - 1)
        self._trade_history.append(trade)
        
        # Update bankroll
        self._current_bankroll += trade.pnl
        
        # Update daily stats
        stats = self._get_today_stats()
        stats.trades += 1
        
        if trade.is_profitable:
            stats.wins += 1
            stats.gross_profit += trade.pnl
            self._total_wins += 1  # Running counter
        else:
            stats.losses += 1
            stats.gross_loss += abs(trade.pnl)
            self._total_losses += 1  # Running counter

        logger.info(f"Trade closed: {trade.side} {trade.market} | PnL: ${trade.pnl:.2f}")
    
    def assess_risk_level(self) -> RiskLevel:
        """
        Assess current risk level based on performance.
        
        Returns:
            Current risk level
        """
        settings = self.settings
        today_stats = self._get_today_stats()
        
        # Calculate metrics
        daily_loss_pct = abs(today_stats.net_pnl / self._initial_bankroll * 100) if today_stats.net_pnl < 0 else 0
        bankroll_drawdown = (1 - self._current_bankroll / self._initial_bankroll) * 100
        
        if daily_loss_pct > settings.max_daily_loss_percent * 0.8 or bankroll_drawdown > 20:
            return RiskLevel.CRITICAL
        elif daily_loss_pct > settings.max_daily_loss_percent * 0.5 or bankroll_drawdown > 10:
            return RiskLevel.HIGH
        elif daily_loss_pct > settings.max_daily_loss_percent * 0.3 or bankroll_drawdown > 5:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW
    
    def _activate_circuit_breaker(self, reason: str):
        """Activate the circuit breaker."""
        self._circuit_breaker_active = True
        self._circuit_breaker_reason = reason
        logger.warning(f"🔴 Circuit breaker activated: {reason}")
    
    def reset_circuit_breaker(self):
        """Manually reset the circuit breaker."""
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = ""
        logger.info("Circuit breaker reset")
    
    def get_stats(self) -> Dict:
        """Get comprehensive risk statistics."""
        today_stats = self._get_today_stats()
        
        return {
            'initial_bankroll': self._initial_bankroll,
            'current_bankroll': self._current_bankroll,
            'total_pnl': self.total_pnl,
            'pnl_percent': self.pnl_percent,
            'risk_level': self.assess_risk_level().value,
            'circuit_breaker_active': self._circuit_breaker_active,
            'circuit_breaker_reason': self._circuit_breaker_reason,
            'active_trades': self._active_trades,
            'today': {
                'trades': today_stats.trades,
                'wins': today_stats.wins,
                'losses': today_stats.losses,
                'net_pnl': today_stats.net_pnl,
                'win_rate': today_stats.win_rate
            },
            'all_time': {
                'total_trades': len(self._trade_history),
                'winning_trades': self._total_wins,  # O(1) using running counter
                'losing_trades': self._total_losses  # O(1) using running counter
            }
        }
    
    def should_reduce_size(self) -> bool:
        """Check if position sizes should be reduced due to risk."""
        risk = self.assess_risk_level()
        return risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    
    def get_size_multiplier(self) -> float:
        """
        Get position size multiplier based on risk level.
        Reduces size when risk is elevated.
        """
        risk = self.assess_risk_level()
        
        if risk == RiskLevel.CRITICAL:
            return 0.25
        elif risk == RiskLevel.HIGH:
            return 0.5
        elif risk == RiskLevel.MEDIUM:
            return 0.75
        else:
            return 1.0
