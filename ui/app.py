"""
Polymarket Frontrun Bot - Advanced GUI Application.
Modern interface with real-time charts, filters, and hotkeys.
"""

import sys
import asyncio
import threading
import logging
from collections import deque
from pathlib import Path
from datetime import datetime
from typing import List, Dict

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import customtkinter as ctk
from tkinter import messagebox, Canvas

# Configure CustomTkinter
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Colors
COLORS = {
    'bg': '#1a1a2e',
    'surface': '#16213e',
    'primary': '#0f3460',
    'accent': '#e94560',
    'success': '#00d26a',
    'warning': '#ffc107',
    'danger': '#e94560',
    'text': '#eaeaea',
    'text_dim': '#808080',
}


class StatsCard(ctk.CTkFrame):
    """Statistics card widget with animation."""

    def __init__(self, parent, title: str, value: str = "0", **kwargs):
        super().__init__(parent, **kwargs)
        self.configure(fg_color=COLORS['surface'], corner_radius=10)
        self._last_value = value

        self.title_label = ctk.CTkLabel(
            self, text=title, font=("", 11),
            text_color=COLORS['text_dim']
        )
        self.title_label.pack(pady=(10, 2), padx=10)

        self.value_label = ctk.CTkLabel(
            self, text=value, font=("", 22, "bold"),
            text_color=COLORS['accent']
        )
        self.value_label.pack(pady=(0, 10), padx=10)

    def set_value(self, value: str, color: str = None):
        # Flash effect on change
        if value != self._last_value:
            self.configure(fg_color=COLORS['primary'])
            self.after(200, lambda: self.configure(fg_color=COLORS['surface']))
        self._last_value = value
        self.value_label.configure(text=value)
        if color:
            self.value_label.configure(text_color=color)


class PnLChart(ctk.CTkFrame):
    """Simple P&L chart using canvas."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.configure(fg_color=COLORS['surface'], corner_radius=10)

        # Title
        ctk.CTkLabel(self, text="P&L History", font=("", 12, "bold")).pack(pady=(10, 5))

        # Canvas for chart
        self.canvas = Canvas(
            self, bg=COLORS['bg'], highlightthickness=0,
            width=300, height=120
        )
        self.canvas.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Data points - using deque for O(1) operations
        self.max_points = 50
        self.pnl_history: deque = deque([0.0], maxlen=self.max_points)

    def add_point(self, pnl: float):
        """Add new P&L point. O(1) with deque."""
        self.pnl_history.append(pnl)
        self._draw_chart()

    def _draw_chart(self):
        """Draw the P&L chart."""
        self.canvas.delete("all")

        if len(self.pnl_history) < 2:
            return

        w = self.canvas.winfo_width() or 300
        h = self.canvas.winfo_height() or 120
        padding = 10

        # Calculate scale
        min_val = min(self.pnl_history)
        max_val = max(self.pnl_history)
        range_val = max_val - min_val if max_val != min_val else 1

        # Draw zero line
        zero_y = h - padding - ((0 - min_val) / range_val) * (h - 2 * padding)
        self.canvas.create_line(padding, zero_y, w - padding, zero_y, fill=COLORS['text_dim'], dash=(2, 2))

        # Draw line chart
        points = []
        for i, val in enumerate(self.pnl_history):
            x = padding + (i / (len(self.pnl_history) - 1)) * (w - 2 * padding)
            y = h - padding - ((val - min_val) / range_val) * (h - 2 * padding)
            points.extend([x, y])

        if len(points) >= 4:
            color = COLORS['success'] if self.pnl_history[-1] >= 0 else COLORS['danger']
            self.canvas.create_line(points, fill=color, width=2, smooth=True)

        # Current value label
        current = self.pnl_history[-1]
        color = COLORS['success'] if current >= 0 else COLORS['danger']
        self.canvas.create_text(
            w - padding, padding,
            text=f"${current:+.2f}",
            fill=color, font=("", 10, "bold"),
            anchor="ne"
        )


class LogPanel(ctk.CTkFrame):
    """Log display panel."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.configure(fg_color=COLORS['surface'], corner_radius=10)

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(10, 5))

        ctk.CTkLabel(header, text="Activity Log", font=("", 14, "bold")).pack(side="left")

        ctk.CTkButton(
            header, text="Clear", width=60, height=28,
            command=self.clear
        ).pack(side="right")

        # Log text
        self.log_text = ctk.CTkTextbox(
            self, font=("Consolas", 11), fg_color=COLORS['bg'],
            state="disabled"
        )
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def add_log(self, level: str, message: str):
        """Add log entry."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {level:8} {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")


class MarketsTable(ctk.CTkScrollableFrame):
    """Markets display table with filtering."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.configure(fg_color=COLORS['surface'], corner_radius=10)
        self.rows = []
        self.all_markets: List[Dict] = []
        self.filter_text = ""
        self.only_opportunities = False

        # Header
        header = ctk.CTkFrame(self, fg_color=COLORS['primary'], corner_radius=5)
        header.pack(fill="x", padx=5, pady=5)

        cols = ["Market", "Bid", "Ask", "Spread", "Status"]
        widths = [250, 70, 70, 70, 80]

        for col, width in zip(cols, widths):
            ctk.CTkLabel(
                header, text=col, font=("", 11, "bold"),
                width=width
            ).pack(side="left", padx=5, pady=5)

    def set_filter(self, text: str, only_opps: bool = False):
        """Set filter criteria."""
        self.filter_text = text.lower()
        self.only_opportunities = only_opps
        self._refresh_display()

    def add_market(self, data: dict):
        """Add market to internal list."""
        self.all_markets.append(data)
        if self._matches_filter(data):
            self._add_row(data)

    def update_markets(self, markets: List[Dict]):
        """Replace all markets."""
        self.all_markets = markets
        self._refresh_display()

    def _matches_filter(self, data: dict) -> bool:
        """Check if market matches current filter."""
        name = data.get('market_name', '').lower()
        if self.filter_text and self.filter_text not in name:
            return False
        if self.only_opportunities and data.get('spread', 0) < 0.10:
            return False
        return True

    def _refresh_display(self):
        """Refresh displayed rows based on filter."""
        self.clear()
        # Sort by spread descending
        sorted_markets = sorted(self.all_markets, key=lambda x: x.get('spread', 0), reverse=True)
        for data in sorted_markets:
            if self._matches_filter(data):
                self._add_row(data)

    def _add_row(self, data: dict):
        """Add a visual row."""
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=5, pady=2)

        name = data.get('market_name', 'Unknown')[:35]
        bid = f"${data.get('best_bid', 0):.3f}"
        ask = f"${data.get('best_ask', 0):.3f}"
        spread = data.get('spread', 0)
        spread_text = f"${spread:.3f}"
        status = data.get('status', 'Scanning')

        spread_color = COLORS['success'] if spread >= 0.10 else COLORS['text']
        status_color = COLORS['success'] if status == 'Opportunity' else COLORS['text']

        ctk.CTkLabel(row, text=name, width=250, anchor="w").pack(side="left", padx=5)
        ctk.CTkLabel(row, text=bid, width=70).pack(side="left", padx=5)
        ctk.CTkLabel(row, text=ask, width=70).pack(side="left", padx=5)
        ctk.CTkLabel(row, text=spread_text, width=70, text_color=spread_color).pack(side="left", padx=5)
        ctk.CTkLabel(row, text=status, width=80, text_color=status_color).pack(side="left", padx=5)

        self.rows.append(row)

    def clear(self):
        for row in self.rows:
            row.destroy()
        self.rows.clear()


class SettingsPanel(ctk.CTkScrollableFrame):
    """Settings configuration panel."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, **kwargs)
        self.app = app
        self.configure(fg_color=COLORS['surface'], corner_radius=10)

        self.entries = {}
        self._create_settings()

    def _create_settings(self):
        from config.settings import get_settings
        settings = get_settings()

        # Authentication
        self._add_section("Authentication")
        self.entries['private_key'] = self._add_entry(
            "Private Key:", settings.private_key or "", show="*"
        )

        # Trading
        self._add_section("Trading Parameters")
        self.entries['bankroll'] = self._add_entry("Bankroll ($):", str(settings.bankroll))
        self.entries['max_trade_percent'] = self._add_entry("Max Trade (%):", str(settings.max_trade_percent))
        self.entries['micro_order_size'] = self._add_entry("Bait Order Size:", str(settings.micro_order_size))
        self.entries['spread_threshold'] = self._add_entry("Min Spread ($):", str(settings.spread_threshold))
        self.entries['polling_interval'] = self._add_entry("Polling (sec):", str(settings.polling_interval))

        # Risk
        self._add_section("Risk Management")
        self.entries['max_daily_loss_percent'] = self._add_entry("Max Daily Loss (%):", str(settings.max_daily_loss_percent))
        self.entries['min_counter_order_size'] = self._add_entry("Min Counter-Order:", str(settings.min_counter_order_size))

        # Save button
        ctk.CTkButton(
            self, text="Save Settings", fg_color=COLORS['success'],
            command=self.save_settings
        ).pack(pady=20)

    def _add_section(self, title: str):
        ctk.CTkLabel(
            self, text=title, font=("", 14, "bold"),
            text_color=COLORS['accent']
        ).pack(pady=(20, 10), anchor="w", padx=10)

    def _add_entry(self, label: str, value: str, show: str = None) -> ctk.CTkEntry:
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(frame, text=label, width=150, anchor="w").pack(side="left")

        entry = ctk.CTkEntry(frame, width=200, show=show)
        entry.insert(0, value)
        entry.pack(side="left", padx=10)

        return entry

    def save_settings(self):
        from config.settings import update_settings

        try:
            pk = self.entries['private_key'].get().strip()
            if pk.startswith('0x'):
                pk = pk[2:]

            new_settings = {
                'private_key': pk,
                'bankroll': float(self.entries['bankroll'].get()),
                'max_trade_percent': float(self.entries['max_trade_percent'].get()),
                'micro_order_size': int(self.entries['micro_order_size'].get()),
                'spread_threshold': float(self.entries['spread_threshold'].get()),
                'polling_interval': float(self.entries['polling_interval'].get()),
                'max_daily_loss_percent': float(self.entries['max_daily_loss_percent'].get()),
                'min_counter_order_size': int(self.entries['min_counter_order_size'].get()),
            }

            update_settings(**new_settings)

            # FIX: Update RiskManager if bot is active
            if self.app.bot and self.app.bot.risk_manager:
                self.app.bot.risk_manager.reset_bankroll(new_settings['bankroll'])

            # FIX: Update stat cards immediately
            self.app.stat_cards["BANKROLL"].set_value(f"${new_settings['bankroll']:.2f}")

            self.app.log("SUCCESS", "Settings saved successfully")
            messagebox.showinfo("Success", "Settings saved!")

        except Exception as e:
            self.app.log("ERROR", f"Failed to save settings: {e}")
            messagebox.showerror("Error", str(e))


class FrontrunBotApp(ctk.CTk):
    """Main application window with advanced features."""

    def __init__(self):
        super().__init__()

        self.title("Polymarket Frontrun Bot")
        self.geometry("1400x900")
        self.configure(fg_color=COLORS['bg'])

        self.bot = None
        self.is_running = False
        self.async_loop = None

        self._init_bot()
        self._create_ui()
        self._setup_hotkeys()
        self._start_async_loop()
        self._start_stats_timer()

    def _init_bot(self):
        """Initialize bot instance."""
        try:
            from bot import get_bot
            self.bot = get_bot()
            self.bot.set_callbacks(
                on_state_change=self._on_state_change,
                on_log=self.log,
                on_market_update=self._on_market_update,
                on_trade=self._on_trade,
                on_stats_update=self._on_stats_update
            )
        except Exception as e:
            logging.error(f"Failed to initialize bot: {e}")

    def _setup_hotkeys(self):
        """Setup keyboard shortcuts."""
        self.bind("<Control-s>", lambda e: self.toggle_bot())
        self.bind("<Control-r>", lambda e: self.refresh_markets())
        self.bind("<Escape>", lambda e: self._emergency_stop())
        self.bind("<Control-q>", lambda e: self.destroy())

    def _emergency_stop(self):
        """Emergency stop with confirmation bypass."""
        if self.is_running:
            self.log("WARNING", "EMERGENCY STOP triggered!")
            self.stop_bot()

    def _create_ui(self):
        """Create the user interface."""
        # Header
        header = ctk.CTkFrame(self, fg_color=COLORS['primary'], height=70, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(side="left", padx=20, pady=10)

        ctk.CTkLabel(
            title_frame, text="POLYMARKET FRONTRUN BOT",
            font=("", 18, "bold")
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_frame, text="Ctrl+S: Start/Stop | Ctrl+R: Refresh | Esc: Emergency Stop",
            font=("", 10), text_color=COLORS['text_dim']
        ).pack(anchor="w")

        # Status indicator
        status_frame = ctk.CTkFrame(header, fg_color="transparent")
        status_frame.pack(side="right", padx=20)

        self.status_dot = ctk.CTkLabel(
            status_frame, text="●", font=("", 16),
            text_color=COLORS['danger']
        )
        self.status_dot.pack(side="left", padx=5)

        self.status_label = ctk.CTkLabel(
            status_frame, text="STOPPED", font=("", 14, "bold")
        )
        self.status_label.pack(side="left", padx=(0, 20))

        self.start_btn = ctk.CTkButton(
            status_frame, text="Start Bot", width=120, height=40,
            fg_color=COLORS['success'], hover_color="#00ff7f",
            command=self.toggle_bot
        )
        self.start_btn.pack(side="left")

        # Main content
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=20, pady=20)

        # Stats cards row
        stats_frame = ctk.CTkFrame(content, fg_color="transparent")
        stats_frame.pack(fill="x", pady=(0, 15))

        self.stat_cards = {}
        cards_data = [
            ("STATUS", "STOPPED"),
            ("BANKROLL", "$100.00"),
            ("TOTAL P&L", "$0.00"),
            ("TRADES", "0"),
            ("WIN RATE", "0%"),
            ("CYCLES", "0"),
        ]

        for title, value in cards_data:
            card = StatsCard(stats_frame, title, value)
            card.pack(side="left", padx=5, expand=True, fill="x")
            self.stat_cards[title] = card

        # Tabs
        self.tabview = ctk.CTkTabview(content, fg_color=COLORS['surface'])
        self.tabview.pack(fill="both", expand=True)

        # Dashboard tab
        dashboard = self.tabview.add("Dashboard")

        dash_content = ctk.CTkFrame(dashboard, fg_color="transparent")
        dash_content.pack(fill="both", expand=True, padx=10, pady=10)

        # Left column: Markets + Filters
        left_column = ctk.CTkFrame(dash_content, fg_color="transparent")
        left_column.pack(side="left", fill="both", expand=True, padx=(0, 10))

        # Filter bar
        filter_bar = ctk.CTkFrame(left_column, fg_color="transparent")
        filter_bar.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(filter_bar, text="Markets", font=("", 14, "bold")).pack(side="left")

        self.filter_entry = ctk.CTkEntry(filter_bar, placeholder_text="Search...", width=150)
        self.filter_entry.pack(side="left", padx=10)
        self.filter_entry.bind("<KeyRelease>", lambda e: self._apply_filter())

        self.opps_only_var = ctk.BooleanVar(value=False)
        self.opps_checkbox = ctk.CTkCheckBox(
            filter_bar, text="Opportunities only",
            variable=self.opps_only_var, command=self._apply_filter
        )
        self.opps_checkbox.pack(side="left", padx=10)

        ctk.CTkButton(
            filter_bar, text="Refresh", width=80,
            command=self.refresh_markets
        ).pack(side="right")

        self.markets_table = MarketsTable(left_column, height=350)
        self.markets_table.pack(fill="both", expand=True)

        # Right column: Chart + Logs
        right_column = ctk.CTkFrame(dash_content, fg_color="transparent", width=450)
        right_column.pack(side="right", fill="both")
        right_column.pack_propagate(False)

        # P&L Chart
        self.pnl_chart = PnLChart(right_column, height=150)
        self.pnl_chart.pack(fill="x", pady=(0, 10))

        # Logs
        self.log_panel = LogPanel(right_column)
        self.log_panel.pack(fill="both", expand=True)

        # Settings tab
        settings = self.tabview.add("Settings")
        self.settings_panel = SettingsPanel(settings, self)
        self.settings_panel.pack(fill="both", expand=True, padx=10, pady=10)

        # Initial log
        self.log("INFO", "Application started")
        self.log("INFO", "Hotkeys: Ctrl+S=Start/Stop, Ctrl+R=Refresh, Esc=Emergency Stop")

    def _apply_filter(self):
        """Apply search filter to markets."""
        text = self.filter_entry.get()
        opps_only = self.opps_only_var.get()
        self.markets_table.set_filter(text, opps_only)

    def _start_async_loop(self):
        """Start async event loop in separate thread."""
        def run_loop():
            self.async_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.async_loop)
            self.async_loop.run_forever()

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()

    def _start_stats_timer(self):
        """Start timer for periodic UI updates."""
        def update():
            if self.is_running and self.bot:
                try:
                    stats = self.bot.get_stats()
                    self._on_stats_update(stats)
                except:
                    pass
            self.after(1000, update)

        self.after(1000, update)

    def log(self, level: str, message: str):
        """Add log entry (thread-safe)."""
        self.after(0, lambda: self.log_panel.add_log(level, message))

    def toggle_bot(self):
        """Start or stop the bot."""
        if self.is_running:
            self.stop_bot()
        else:
            self.start_bot()

    def start_bot(self):
        """Start the trading bot."""
        from config.settings import get_settings
        settings = get_settings()

        if not settings.is_configured:
            messagebox.showwarning(
                "Configuration Required",
                "Please configure your private key in the Settings tab."
            )
            self.tabview.set("Settings")
            return

        self.log("INFO", "Starting bot...")

        if self.bot and self.async_loop:
            asyncio.run_coroutine_threadsafe(
                self._async_start_bot(),
                self.async_loop
            )

    async def _async_start_bot(self):
        """Async bot start."""
        try:
            success = await self.bot.start()
            if success:
                self.after(0, lambda: self._set_running(True))
                self.log("SUCCESS", "Bot started successfully!")
            else:
                self.log("ERROR", "Failed to start bot")
        except Exception as e:
            self.log("ERROR", f"Error starting bot: {e}")

    def stop_bot(self):
        """Stop the trading bot."""
        self.log("INFO", "Stopping bot...")

        if self.bot and self.async_loop:
            asyncio.run_coroutine_threadsafe(
                self._async_stop_bot(),
                self.async_loop
            )

    async def _async_stop_bot(self):
        """Async bot stop."""
        try:
            await self.bot.stop()
            self.after(0, lambda: self._set_running(False))
            self.log("WARNING", "Bot stopped")
        except Exception as e:
            self.log("ERROR", f"Error stopping bot: {e}")

    def _set_running(self, is_running: bool):
        """Update running state."""
        self.is_running = is_running

        if is_running:
            self.start_btn.configure(text="Stop Bot", fg_color=COLORS['danger'])
            self.status_label.configure(text="RUNNING")
            self.status_dot.configure(text_color=COLORS['success'])
            self.stat_cards["STATUS"].set_value("RUNNING", COLORS['success'])
        else:
            self.start_btn.configure(text="Start Bot", fg_color=COLORS['success'])
            self.status_label.configure(text="STOPPED")
            self.status_dot.configure(text_color=COLORS['danger'])
            self.stat_cards["STATUS"].set_value("STOPPED", COLORS['danger'])

    def _on_state_change(self, state: str):
        """Handle bot state change."""
        self.after(0, lambda: self._set_running(state == "RUNNING"))

    def _on_market_update(self, market_data: dict):
        """Handle market update."""
        self.after(0, lambda: self.markets_table.add_market(market_data))

    def _on_trade(self, trade_info: dict):
        """Handle trade execution."""
        market = trade_info.get('market', 'Unknown')
        side = trade_info.get('side', '')
        profit = trade_info.get('profit', 0)
        self.log("TRADE", f"{side} on {market} | Profit: ${profit:.2f}")

    def _on_stats_update(self, stats: dict):
        """Handle stats update."""
        def update():
            risk = stats.get('risk', {})
            today = risk.get('today', {})

            # Update bankroll
            bankroll = risk.get('current_bankroll', 100)
            self.stat_cards["BANKROLL"].set_value(f"${bankroll:.2f}")

            # Update P&L with chart
            pnl = risk.get('total_pnl', 0)
            pnl_color = COLORS['success'] if pnl >= 0 else COLORS['danger']
            self.stat_cards["TOTAL P&L"].set_value(f"${pnl:+.2f}", pnl_color)
            self.pnl_chart.add_point(pnl)

            # Trades and win rate
            trades = today.get('trades', 0)
            wins = today.get('wins', 0)
            self.stat_cards["TRADES"].set_value(str(trades))
            winrate = (wins / trades * 100) if trades > 0 else 0
            self.stat_cards["WIN RATE"].set_value(f"{winrate:.0f}%")

            # Cycles
            cycles = stats.get('cycles_run', 0)
            self.stat_cards["CYCLES"].set_value(str(cycles))

        self.after(0, update)

    def refresh_markets(self):
        """Refresh markets list."""
        self.log("INFO", "Refreshing markets...")
        self.markets_table.all_markets.clear()
        self.markets_table.clear()

        if self.bot:
            markets = self.bot.get_cached_markets()
            # Add status field
            for m in markets:
                m['status'] = 'Opportunity' if m.get('spread', 0) >= 0.10 else 'Scanning'
            self.markets_table.update_markets(markets)
            self.log("INFO", f"Found {len(markets)} markets")


def setup_logging():
    """Configure logging."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('bot.log', encoding='utf-8')
        ]
    )
    logging.getLogger('urllib3').setLevel(logging.WARNING)


def main():
    """Main entry point."""
    setup_logging()
    logging.info("Starting Polymarket Frontrun Bot")

    app = FrontrunBotApp()
    app.mainloop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
