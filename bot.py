"""
Polymarket Frontrun Bot - Main Orchestrator.
Coordinates scanner, strategy, executor, and risk management.
"""

import asyncio
import logging
import sys
from datetime import datetime
from typing import Optional, Callable, Dict, Any
from pathlib import Path

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

from config.settings import get_settings, Settings
from core.scanner import MarketScanner
from core.strategy import FrontrunStrategy
from core.executor import OrderExecutor
from core.risk import RiskManager, TradeRecord

logger = logging.getLogger(__name__)


class BotState:
    """Bot state enumeration."""
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class FrontrunBot:
    """
    Main bot orchestrator.
    
    Coordinates all components:
    - MarketScanner: Finds opportunities
    - FrontrunStrategy: Executes strategy logic
    - OrderExecutor: Places/cancels orders
    - RiskManager: Controls risk exposure
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.state = BotState.STOPPED
        
        # Components (initialized on start)
        self.client: Optional[ClobClient] = None
        self.scanner: Optional[MarketScanner] = None
        self.strategy: Optional[FrontrunStrategy] = None
        self.executor: Optional[OrderExecutor] = None
        self.risk_manager: Optional[RiskManager] = None
        
        # Runtime
        self._running = False
        self._main_task: Optional[asyncio.Task] = None
        
        # Statistics
        self.start_time: Optional[datetime] = None
        self.cycles_run = 0
        
        # UI Callbacks
        self._on_state_change: Optional[Callable] = None
        self._on_log: Optional[Callable] = None
        self._on_market_update: Optional[Callable] = None
        self._on_trade: Optional[Callable] = None
        self._on_stats_update: Optional[Callable] = None
        
    def set_callbacks(
        self,
        on_state_change: Callable = None,
        on_log: Callable = None,
        on_market_update: Callable = None,
        on_trade: Callable = None,
        on_stats_update: Callable = None
    ):
        """Set UI callback functions."""
        self._on_state_change = on_state_change
        self._on_log = on_log
        self._on_market_update = on_market_update
        self._on_trade = on_trade
        self._on_stats_update = on_stats_update
    
    def _set_state(self, new_state: str):
        """Update bot state and notify UI."""
        self.state = new_state
        logger.info(f"Bot state: {new_state}")
        if self._on_state_change:
            self._on_state_change(new_state)
    
    def _log(self, level: str, message: str):
        """Log message and send to UI."""
        log_func = getattr(logger, level.lower(), logger.info)
        log_func(message)
        if self._on_log:
            self._on_log(level, message)
    
    def _initialize_client(self) -> bool:
        """Initialize Polymarket CLOB client."""
        settings = self.settings
        
        if not settings.is_configured:
            self._log("ERROR", "Private key not configured. Please set PRIVATE_KEY in .env")
            return False
        
        try:
            # Build API credentials if available
            creds = None
            if settings.polymarket_api_key:
                creds = ApiCreds(
                    api_key=settings.polymarket_api_key,
                    api_secret=settings.polymarket_api_secret or "",
                    api_passphrase=settings.polymarket_api_passphrase or ""
                )
            
            # Create client
            self.client = ClobClient(
                host=settings.clob_url,
                chain_id=settings.chain_id,
                key=settings.private_key,
                creds=creds
            )
            
            # Derive L2 credentials if not provided
            if not creds:
                try:
                    self._log("INFO", "Deriving L2 API credentials...")
                    self.client.set_api_creds(self.client.derive_api_key())
                except Exception as e:
                    self._log("WARNING", f"Could not derive L2 creds (read-only mode): {e}")
            
            self._log("INFO", "CLOB client initialized successfully")
            return True
            
        except Exception as e:
            self._log("ERROR", f"Failed to initialize CLOB client: {e}")
            return False
    
    def _initialize_components(self):
        """Initialize all bot components."""
        self.scanner = MarketScanner(self.client)
        self.executor = OrderExecutor(self.client)
        self.risk_manager = RiskManager(self.settings.bankroll)
        self.strategy = FrontrunStrategy(self.scanner, self.executor)
        
        # Wire up callbacks
        self.strategy.set_callbacks(
            on_state_change=lambda s: self._log("INFO", f"Strategy: {s}"),
            on_opportunity=lambda o: self._log("INFO", f"Opportunity: {o.get('market_name')}"),
            on_trade=self._handle_trade
        )
        
        self._log("INFO", "All components initialized")
    
    def _handle_trade(self, trade_info: Dict):
        """Handle completed trade."""
        if self._on_trade:
            self._on_trade(trade_info)
        
        # Update stats
        if self._on_stats_update:
            self._on_stats_update(self.get_stats())
    
    async def _main_loop(self):
        """Main bot execution loop."""
        self._log("INFO", "Starting main loop...")
        
        while self._running:
            try:
                # Check if trading allowed
                can_trade, reason = self.risk_manager.can_trade()
                
                if not can_trade:
                    self._log("WARNING", f"Trading paused: {reason}")
                    await asyncio.sleep(30)
                    continue
                
                # Run strategy cycle
                self.cycles_run += 1
                self._log("DEBUG", f"Running cycle #{self.cycles_run}")
                
                trade = await self.strategy.run_cycle()
                
                if trade:
                    # Record trade
                    record = TradeRecord(
                        timestamp=datetime.now(),
                        market=trade.get('market_name', ''),
                        side=trade.get('side', ''),
                        size=trade.get('target_size', 0),
                        entry_price=trade.get('entry_price', 0),
                        exit_price=trade.get('entry_price', 0),  # Simplified
                        pnl=trade.get('estimated_profit', 0)
                    )
                    self.risk_manager.record_trade_close(record)
                    
                    # Update markets table
                    if self._on_market_update:
                        self._on_market_update({
                            'market_name': trade.get('market_name'),
                            'token_id': trade.get('token_id'),
                            'spread': 0,
                            'volume': trade.get('target_size'),
                            'action': f"{trade.get('side')} (frontrun)",
                            'profit': trade.get('estimated_profit')
                        })
                
                # Update stats every cycle (real-time)
                if self._on_stats_update:
                    self._on_stats_update(self.get_stats())
                
                # Small delay between cycles
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log("ERROR", f"Loop error: {e}")
                await asyncio.sleep(5)
        
        self._log("INFO", "Main loop stopped")
    
    async def start(self) -> bool:
        """Start the bot."""
        if self.state == BotState.RUNNING:
            self._log("WARNING", "Bot is already running")
            return False
        
        self._set_state(BotState.STARTING)
        
        # Initialize client
        if not self._initialize_client():
            self._set_state(BotState.ERROR)
            return False
        
        # Initialize components
        self._initialize_components()
        
        # Start main loop
        self._running = True
        self.start_time = datetime.now()
        self._main_task = asyncio.create_task(self._main_loop())
        
        self._set_state(BotState.RUNNING)
        return True
    
    async def stop(self):
        """Stop the bot gracefully."""
        if self.state != BotState.RUNNING:
            return
        
        self._set_state(BotState.STOPPING)
        self._running = False
        
        # Cancel pending orders
        if self.executor:
            await self.executor.cancel_all_orders()
        
        # Cancel strategy bait
        if self.strategy:
            await self.strategy.cancel_bait()
        
        # Wait for main task
        if self._main_task:
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass
        
        self._set_state(BotState.STOPPED)
        self._log("INFO", "Bot stopped")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive bot statistics."""
        stats = {
            'state': self.state,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'cycles_run': self.cycles_run,
            'uptime_seconds': (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        }
        
        if self.risk_manager:
            stats['risk'] = self.risk_manager.get_stats()
        
        if self.strategy:
            stats['strategy'] = self.strategy.get_stats()
        
        if self.executor:
            stats['executor'] = self.executor.get_stats()
        
        return stats
    
    def get_cached_markets(self):
        """Get cached market data for UI."""
        if self.scanner:
            return [m.to_dict() for m in self.scanner.get_cached_markets()]
        return []


def setup_logging(log_file: str = "bot.log", log_level: str = "INFO"):
    """Configure logging for the bot."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)
    
    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    
    # Configure root logger
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)
    
    # Reduce noise from libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)


# Singleton instance
_bot_instance: Optional[FrontrunBot] = None


def get_bot() -> FrontrunBot:
    """Get or create bot instance."""
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = FrontrunBot()
    return _bot_instance
