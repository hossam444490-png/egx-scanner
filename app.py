"""
===============================================================================
EGX REAL-TIME AUTOMATED STOCK SCANNER & PORTFOLIO ALLOCATOR
(SINGLE-FILE PRODUCTION EDITION WITH PORTFOLIO POSITION SIZING)
===============================================================================
Run Dashboard : streamlit run app.py
Run Unit Tests: pytest app.py
===============================================================================
"""

import os
import sqlite3
import smtplib
import logging
from abc import ABC, abstractmethod
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, time, date, timedelta
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd
import pytz
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# Optional dotenv loading if installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not installed — continue without loading .env
    pass
# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("EGXScanner")


# =============================================================================
# 1. CONFIGURATION & EGX TRADING SESSION / CALENDAR ENGINE
# =============================================================================

CAIRO_TZ = pytz.timezone("Africa/Cairo")

STATIC_EGX_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # Bank Holiday
    date(2026, 1, 7),   # Coptic Christmas
    date(2026, 1, 25),  # Revolution & Police Day
    date(2026, 3, 20),  # Eid al-Fitr
    date(2026, 3, 21),  # Eid al-Fitr
    date(2026, 3, 22),  # Eid al-Fitr Holiday
    date(2026, 4, 13),  # Sham El-Nessim
    date(2026, 4, 25),  # Sinai Liberation Day
    date(2026, 5, 1),   # Labour Day
    date(2026, 5, 26),  # Arafat Day
    date(2026, 5, 27),  # Eid al-Adha
    date(2026, 5, 28),  # Eid al-Adha
    date(2026, 6, 17),  # Islamic New Year
    date(2026, 6, 30),  # June 30 Revolution
    date(2026, 7, 23),  # July 23 Revolution Day
    date(2026, 8, 26),  # Prophet's Birthday
    date(2026, 10, 6),  # Armed Forces Day
}


class SystemSettings:
    """Centralized environment-backed settings."""
    DB_PATH: str = os.getenv("EGX_DB_PATH", "egx_scanner.db")
    DATA_PROVIDER: str = os.getenv("DATA_PROVIDER", "SIMULATED")
    API_KEY: str = os.getenv("DATA_PROVIDER_API_KEY", "")
    WS_URL: str = os.getenv("DATA_PROVIDER_WS_URL", "")
    REST_URL: str = os.getenv("DATA_PROVIDER_REST_URL", "")
    MIN_TRADED_VALUE_EGP: float = float(os.getenv("MIN_TRADED_VALUE_EGP", "1000000.0"))
    MIN_AVG_VOLUME: int = int(os.getenv("MIN_AVG_VOLUME", "50000"))
    MIN_PRICE_EGP: float = float(os.getenv("MIN_PRICE_EGP", "0.50"))
    MAX_SPREAD_PCT: float = float(os.getenv("MAX_SPREAD_PCT", "1.50"))
    ATR_MULTIPLIER_SL: float = float(os.getenv("ATR_MULTIPLIER_SL", "1.5"))
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    SMTP_SERVER: str = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    ALERT_RECIPIENT_EMAIL: str = os.getenv("ALERT_RECIPIENT_EMAIL", "")


class EGXMarketSchedule:
    """Official Egyptian Exchange trading hours & holiday verification."""

    PRE_OPEN_TIME = time(9, 30)      # Exploratory / Discovery Session
    CONTINUOUS_OPEN = time(10, 0)     # Continuous Trading Start
    CLOSING_AUCTION = time(14, 15)    # Closing Value Auction
    MARKET_CLOSE = time(14, 30)       # Official Session Close

    @staticmethod
    def get_cairo_now() -> datetime:
        return datetime.now(CAIRO_TZ)

    @classmethod
    def is_trading_day(cls, dt: Optional[datetime] = None) -> bool:
        if dt is None:
            dt = cls.get_cairo_now()
        elif dt.tzinfo is None:
            dt = CAIRO_TZ.localize(dt)
        else:
            dt = dt.astimezone(CAIRO_TZ)

        # EGX trading week: Sunday (6) through Thursday (3). Friday (4) & Saturday (5) are closed.
        if dt.weekday() in (4, 5):
            return False
        if dt.date() in STATIC_EGX_HOLIDAYS_2026:
            return False
        return True

    @classmethod
    def get_session_status(cls, dt: Optional[datetime] = None) -> Dict[str, Any]:
        if dt is None:
            dt = cls.get_cairo_now()
        elif dt.tzinfo is None:
            dt = CAIRO_TZ.localize(dt)
        else:
            dt = dt.astimezone(CAIRO_TZ)

        t = dt.time()

        if not cls.is_trading_day(dt):
            reason = "Holiday" if dt.date() in STATIC_EGX_HOLIDAYS_2026 else "Weekend (Fri/Sat)"
            return {
                "status": "CLOSED_HOLIDAY_WEEKEND",
                "is_open": False,
                "description": f"EGX Closed ({reason})",
                "cairo_time": dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
            }

        if t < cls.PRE_OPEN_TIME:
            return {
                "status": "PRE_MARKET",
                "is_open": False,
                "description": "Pre-Market (Discovery auction starts at 09:30 Cairo)",
                "cairo_time": dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
            }
        elif cls.PRE_OPEN_TIME <= t < cls.CONTINUOUS_OPEN:
            return {
                "status": "PRE_OPEN_DISCOVERY",
                "is_open": True,
                "description": "Pre-Open Discovery Auction (09:30 - 10:00 Cairo)",
                "cairo_time": dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
            }
        elif cls.CONTINUOUS_OPEN <= t < cls.CLOSING_AUCTION:
            return {
                "status": "CONTINUOUS_TRADING",
                "is_open": True,
                "description": "Continuous Trading Session (10:00 - 14:15 Cairo)",
                "cairo_time": dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
            }
        elif cls.CLOSING_AUCTION <= t <= cls.MARKET_CLOSE:
            return {
                "status": "CLOSING_AUCTION",
                "is_open": True,
                "description": "Closing Price Auction & Trade-at-Close (14:15 - 14:30 Cairo)",
                "cairo_time": dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
            }
        else:
            return {
                "status": "POST_MARKET_CLOSED",
                "is_open": False,
                "description": "Trading Session Closed for the Day (>14:30 Cairo)",
                "cairo_time": dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
            }


# =============================================================================
# 2. SQLITE STORAGE & RELIABILITY MANAGER
# =============================================================================

class StorageManager:
    """Manages SQLite persistence for signals, alerts, watchlists, and status."""

    def __init__(self, db_path: str = SystemSettings.DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS scanner_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    company_name TEXT,
                    category TEXT NOT NULL,
                    last_price REAL,
                    entry_zone TEXT,
                    stop_loss REAL,
                    invalidation_level REAL,
                    target_1 REAL,
                    target_2 REAL,
                    risk_reward_ratio REAL,
                    confidence_score INTEGER,
                    trigger_reasons TEXT,
                    data_freshness TEXT,
                    signal_timestamp TEXT,
                    UNIQUE(ticker, category, signal_timestamp)
                );

                CREATE TABLE IF NOT EXISTS alert_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    category TEXT NOT NULL,
                    last_price REAL,
                    channel TEXT NOT NULL,
                    dispatched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS watchlist (
                    ticker TEXT PRIMARY KEY,
                    added_at TEXT NOT NULL,
                    min_rvol_rule REAL DEFAULT 1.3,
                    rsi_low_rule REAL DEFAULT 30.0,
                    rsi_high_rule REAL DEFAULT 70.0,
                    notes TEXT DEFAULT ''
                );
                """
            )
            cur = conn.execute("SELECT COUNT(*) as cnt FROM watchlist")
            if cur.fetchone()["cnt"] == 0:
                defaults = [
                    ("COMI.CA", "Core Banking Leader"),
                    ("TMGH.CA", "Real Estate Bellwether"),
                    ("FWRY.CA", "Fintech High-Beta"),
                    ("SWDY.CA", "Industrials & Cables"),
                ]
                now_str = datetime.now(CAIRO_TZ).strftime("%Y-%m-%d %H:%M:%S")
                for t, note in defaults:
                    conn.execute(
                        "INSERT OR IGNORE INTO watchlist (ticker, added_at, notes) VALUES (?, ?, ?)",
                        (t, now_str, note),
                    )
            conn.commit()

    def save_signal(self, sig: Dict[str, Any]) -> None:
        if sig.get("category") in ("NO_ACTIONABLE_SIGNAL", "FILTERED_ILLIQUID", "DATA_INSUFFICIENT"):
            return
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO scanner_signals (
                    ticker, company_name, category, last_price, entry_zone,
                    stop_loss, invalidation_level, target_1, target_2,
                    risk_reward_ratio, confidence_score, trigger_reasons,
                    data_freshness, signal_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sig["ticker"], sig["company_name"], sig["category"],
                    sig["last_price"], sig["entry_zone"], sig["stop_loss"],
                    sig["invalidation_level"], sig["target_1"], sig["target_2"],
                    sig["risk_reward_ratio"], sig["confidence_score"],
                    sig["trigger_reasons"], sig["data_freshness"],
                    sig["signal_timestamp"][:16],
                ),
            )
            conn.commit()

    def get_recent_signals(self, ticker: Optional[str] = None, limit: int = 50) -> pd.DataFrame:
        with self._get_conn() as conn:
            if ticker:
                query = "SELECT * FROM scanner_signals WHERE ticker = ? ORDER BY id DESC LIMIT ?"
                df = pd.read_sql_query(query, conn, params=(ticker, limit))
            else:
                query = "SELECT * FROM scanner_signals ORDER BY id DESC LIMIT ?"
                df = pd.read_sql_query(query, conn, params=(limit,))
        return df

    def was_alert_recently_sent(self, ticker: str, category: str, last_price: float) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT last_price, dispatched_at FROM alert_history
                WHERE ticker = ? AND category = ?
                ORDER BY id DESC LIMIT 1
                """,
                (ticker, category),
            ).fetchone()
            if not row:
                return False
            prev_price = float(row["last_price"])
            if prev_price > 0 and abs(last_price - prev_price) / prev_price < 0.015:
                return True
            return False

    def record_alert(self, ticker: str, category: str, last_price: float, channel: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO alert_history (ticker, category, last_price, channel, dispatched_at) VALUES (?, ?, ?, ?, ?)",
                (ticker, category, last_price, channel, datetime.now(CAIRO_TZ).strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()

    def get_alert_history(self, limit: int = 50) -> pd.DataFrame:
        with self._get_conn() as conn:
            return pd.read_sql_query(
                "SELECT * FROM alert_history ORDER BY id DESC LIMIT ?", conn, params=(limit,)
            )

    def get_watchlist(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM watchlist ORDER BY ticker ASC").fetchall()
            return [dict(r) for r in rows]

    def add_to_watchlist(
        self, ticker: str, min_rvol: float = 1.3, rsi_low: float = 30.0, rsi_high: float = 70.0, notes: str = ""
    ) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO watchlist (ticker, added_at, min_rvol_rule, rsi_low_rule, rsi_high_rule, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ticker, datetime.now(CAIRO_TZ).strftime("%Y-%m-%d %H:%M:%S"), min_rvol, rsi_low, rsi_high, notes),
            )
            conn.commit()

    def remove_from_watchlist(self, ticker: str) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
            conn.commit()


# =============================================================================
# 3. MARKET DATA PROVIDER ABSTRACTION & SIMULATED EGX FEED
# =============================================================================

class Quote:
    """Standardized Level 1 Quote Representation for an EGX Security."""

    def __init__(
        self,
        ticker: str,
        company_name: str,
        last_price: float,
        previous_close: float,
        open_price: float,
        high_price: float,
        low_price: float,
        volume: int,
        traded_value_egp: float,
        bid_price: float,
        ask_price: float,
        bid_size: int,
        ask_size: int,
        timestamp: datetime,
        is_realtime: bool = False,
        freshness_status: str = "SIMULATED",
        is_suspended: bool = False,
    ):
        self.ticker = ticker
        self.company_name = company_name
        self.last_price = last_price
        self.previous_close = previous_close
        self.open_price = open_price
        self.high_price = high_price
        self.low_price = low_price
        self.volume = volume
        self.traded_value_egp = traded_value_egp
        self.bid_price = bid_price
        self.ask_price = ask_price
        self.bid_size = bid_size
        self.ask_size = ask_size
        self.timestamp = timestamp
        self.is_realtime = is_realtime
        self.freshness_status = freshness_status
        self.is_suspended = is_suspended

    @property
    def spread_pct(self) -> float:
        if self.bid_price and self.ask_price and self.last_price > 0:
            return round(((self.ask_price - self.bid_price) / self.last_price) * 100.0, 3)
        return 0.0


class BaseDataProvider(ABC):
    """Abstract interface for EGX Market Data Providers."""

    @abstractmethod
    def connect(self) -> bool:
        pass

    @abstractmethod
    def disconnect(self) -> None:
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        pass

    @abstractmethod
    def get_latest_quote(self, ticker: str) -> Optional[Quote]:
        pass

    @abstractmethod
    def get_all_quotes(self) -> Dict[str, Quote]:
        pass

    @abstractmethod
    def get_historical_ohlcv(self, ticker: str, lookback_days: int = 220) -> pd.DataFrame:
        pass

    @abstractmethod
    def get_egx30_benchmark(self, lookback_days: int = 220) -> pd.DataFrame:
        pass


EGX_UNIVERSE_SPEC = {
    "COMI.CA": ("Commercial International Bank (CIB)", 120.10),
    "TMGH.CA": ("Talaat Moustafa Group Holding", 81.60),
    "ETEL.CA": ("Telecom Egypt (WE)", 117.00),
    "SWDY.CA": ("Elsewedy Electric", 102.02),
    "FWRY.CA": ("Fawry for Banking Technology", 16.82),
    "EAST.CA": ("Eastern Company", 30.03),
    "EFIH.CA": ("e-finance Investment Group", 20.75),
    "ABUK.CA": ("Abu Qir Fertilizers", 88.63),
    "HRHO.CA": ("EFG Holding", 23.91),
    "ORAS.CA": ("Orascom Construction PLC", 773.00),
    "MFPC.CA": ("Misr Fertilizers Production (MOPCO)", 52.00),
    "ESRS.CA": ("Ezz Steel", 95.00),
    "AMOC.CA": ("Alexandria Mineral Oils (AMOC)", 9.80),
    "SKPC.CA": ("Sidi Kerir Petrochemicals (SIDPEC)", 28.50),
    "CIEB.CA": ("Credit Agricole Egypt", 21.00),
    "ISPH.CA": ("Ibnsina Pharma", 3.45),
    "JUFO.CA": ("Juhayna Food Industries", 18.90),
    "HELI.CA": ("Heliopolis Housing & Development", 11.20),
    "MNHD.CA": ("Madinet Masr for Housing (MMHD)", 4.15),
    "EKHO.CA": ("Egypt Kuwait Holding", 1.18),
}


class SimulatedEGXDataProvider(BaseDataProvider):
    """
    Calibrated EGX Market Simulator.
    Generates completed historical daily OHLCV candles plus live intraday quotes.
    """

    def __init__(self, seed: int = 42):
        self.seed = seed
        self._connected = False
        self._history_cache: Dict[str, pd.DataFrame] = {}
        self._quotes: Dict[str, Quote] = {}
        self._egx30_df: pd.DataFrame = pd.DataFrame()
        self._init_histories()

    def _init_histories(self) -> None:
        rng = np.random.default_rng(self.seed)
        end_date = EGXMarketSchedule.get_cairo_now().date()

        trading_dates = []
        cur = end_date - timedelta(days=380)
        while cur < end_date:
            dt = datetime(cur.year, cur.month, cur.day, 12, 0, tzinfo=CAIRO_TZ)
            if EGXMarketSchedule.is_trading_day(dt):
                trading_dates.append(pd.Timestamp(cur))
            cur += timedelta(days=1)

        trading_dates = trading_dates[-225:]
        n_days = len(trading_dates)

        # Generate EGX30 Index Series
        idx_rets = rng.normal(0.0005, 0.015, n_days)
        idx_closes = 49500.0 * np.exp(np.cumsum(idx_rets))
        self._egx30_df = pd.DataFrame(
            {
                "open": np.round(idx_closes * 0.996, 2),
                "high": np.round(idx_closes * 1.008, 2),
                "low": np.round(idx_closes * 0.991, 2),
                "close": np.round(idx_closes, 2),
                "volume": rng.integers(150_000_000, 450_000_000, n_days),
            },
            index=trading_dates,
        )

        # Generate Stock Histories
        for idx, (ticker, (name, base_price)) in enumerate(EGX_UNIVERSE_SPEC.items()):
            drift = 0.0014 if idx % 3 == 0 else (0.0007 if idx % 3 == 1 else -0.0002)
            rets = rng.normal(drift, 0.022, n_days)
            closes = base_price * np.exp(np.cumsum(rets))

            records = []
            for i, d in enumerate(trading_dates):
                c = float(closes[i])
                span = c * float(rng.uniform(0.012, 0.036))
                h = max(c + abs(float(rng.normal(0, span * 0.5))), c * 1.002)
                l = min(c - abs(float(rng.normal(0, span * 0.5))), c * 0.998)
                o = l + (h - l) * float(rng.uniform(0.2, 0.8))
                h = max(h, o, c)
                l = min(l, o, c)
                vol = int(rng.lognormal(mean=13.1, sigma=0.65))
                if i == n_days - 1 and idx % 4 == 0:
                    c = float(max(r["high"] for r in records[-20:]) * 1.015)
                    h = max(h, c * 1.005)
                    vol = int(vol * 2.4)
                val = vol * ((o + h + l + c) / 4.0)
                records.append(
                    {
                        "date": d,
                        "open": round(o, 2),
                        "high": round(h, 2),
                        "low": round(l, 2),
                        "close": round(c, 2),
                        "volume": vol,
                        "traded_value": round(val, 2),
                    }
                )

            df = pd.DataFrame(records).set_index("date").sort_index()
            df = self.validate_ohlcv(df)
            self._history_cache[ticker] = df

            last_row = df.iloc[-1]
            prev_row = df.iloc[-2]
            spread = max(0.01, round(float(last_row["close"]) * 0.002, 2))
            self._quotes[ticker] = Quote(
                ticker=ticker,
                company_name=name,
                last_price=float(last_row["close"]),
                previous_close=float(prev_row["close"]),
                open_price=float(last_row["open"]),
                high_price=float(last_row["high"]),
                low_price=float(last_row["low"]),
                volume=int(last_row["volume"]),
                traded_value_egp=float(last_row["traded_value"]),
                bid_price=round(float(last_row["close"]) - spread / 2, 2),
                ask_price=round(float(last_row["close"]) + spread / 2, 2),
                bid_size=int(rng.integers(2000, 35000)),
                ask_size=int(rng.integers(2000, 35000)),
                timestamp=EGXMarketSchedule.get_cairo_now(),
                is_realtime=False,
                freshness_status="SIMULATED",
                is_suspended=False,
            )

    @staticmethod
    def validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
        clean = df[~df.index.duplicated(keep="last")].copy()
        clean = clean.dropna(subset=["open", "high", "low", "close", "volume"])
        clean["high"] = clean[["open", "high", "low", "close"]].max(axis=1)
        clean["low"] = clean[["open", "high", "low", "close"]].min(axis=1)
        return clean

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_historical_ohlcv(self, ticker: str, lookback_days: int = 220) -> pd.DataFrame:
        if ticker not in self._history_cache:
            raise KeyError(f"Unknown EGX ticker: {ticker}")
        return self._history_cache[ticker].tail(lookback_days).copy()

    def get_egx30_benchmark(self, lookback_days: int = 220) -> pd.DataFrame:
        return self._egx30_df.tail(lookback_days).copy()

    def get_latest_quote(self, ticker: str) -> Optional[Quote]:
        if not self._connected or ticker not in self._quotes:
            return None
        self._step_intraday_quote(ticker)
        return self._quotes[ticker]

    def get_all_quotes(self) -> Dict[str, Quote]:
        if not self._connected:
            return {}
        for ticker in self._quotes:
            self._step_intraday_quote(ticker)
        return self._quotes

    def _step_intraday_quote(self, ticker: str) -> None:
        q = self._quotes[ticker]
        shock = float(np.random.normal(0.0001, 0.0025))
        raw_p = round(q.last_price * (1.0 + shock), 2)
        upper_limit = round(q.previous_close * 1.20, 2)
        lower_limit = max(0.05, round(q.previous_close * 0.80, 2))
        new_p = min(upper_limit, max(lower_limit, raw_p))

        inc_vol = int(np.random.exponential(2500))
        q.last_price = new_p
        q.high_price = max(q.high_price, new_p)
        q.low_price = min(q.low_price, new_p)
        q.volume += inc_vol
        q.traded_value_egp = round(q.traded_value_egp + inc_vol * new_p, 2)
        spread = max(0.01, round(new_p * 0.0018, 2))
        q.bid_price = round(new_p - spread / 2, 2)
        q.ask_price = round(new_p + spread / 2, 2)
        q.timestamp = EGXMarketSchedule.get_cairo_now()


# =============================================================================
# 4. TECHNICAL ANALYSIS INDICATOR SUITE (COMPLETED CANDLES ONLY)
# =============================================================================

class TechnicalIndicators:
    """Calculates Trend, Momentum, Volume, and Volatility indicators."""

    @staticmethod
    def calculate_ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_sma(series: pd.Series, period: int) -> pd.Series:
        return series.rolling(window=period, min_periods=1).mean()

    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi.fillna(50.0)

    @staticmethod
    def calculate_macd(
        series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> pd.DataFrame:
        fast_ema = series.ewm(span=fast, adjust=False).mean()
        slow_ema = series.ewm(span=slow, adjust=False).mean()
        macd_line = fast_ema - slow_ema
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        hist = macd_line - signal_line
        return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
        ).max(axis=1)
        return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().bfill()

    @staticmethod
    def calculate_roc(series: pd.Series, period: int = 12) -> pd.Series:
        return (((series - series.shift(period)) / series.shift(period)) * 100.0).fillna(0.0)

    @classmethod
    def compute_all_indicators(
        cls, ohlcv_df: pd.DataFrame, benchmark_df: Optional[pd.DataFrame] = None
    ) -> pd.DataFrame:
        df = ohlcv_df.copy()
        close = df["close"]

        df["ema_9"] = cls.calculate_ema(close, 9)
        df["ema_21"] = cls.calculate_ema(close, 21)
        df["sma_20"] = cls.calculate_sma(close, 20)
        df["sma_50"] = cls.calculate_sma(close, 50)
        df["sma_200"] = cls.calculate_sma(close, 200)

        df["dist_sma20_pct"] = ((close - df["sma_20"]) / df["sma_20"]) * 100.0
        df["dist_sma50_pct"] = ((close - df["sma_50"]) / df["sma_50"]) * 100.0
        df["dist_sma200_pct"] = ((close - df["sma_200"]) / df["sma_200"]) * 100.0

        df["rsi_14"] = cls.calculate_rsi(close, 14)
        macd_res = cls.calculate_macd(close, 12, 26, 9)
        df["macd"] = macd_res["macd"]
        df["macd_signal"] = macd_res["signal"]
        df["macd_hist"] = macd_res["hist"]
        df["roc_12"] = cls.calculate_roc(close, 12)

        df["atr_14"] = cls.calculate_atr(df, 14)
        df["vol_sma_20"] = df["volume"].rolling(window=20, min_periods=1).mean()
        df["rvol"] = (df["volume"] / df["vol_sma_20"].replace(0, np.nan)).fillna(1.0)

        if benchmark_df is not None and not benchmark_df.empty:
            bench_close = benchmark_df["close"].reindex(df.index).ffill().bfill()
            stock_20d_ret = close / close.shift(20).bfill()
            bench_20d_ret = bench_close / bench_close.shift(20).bfill()
            df["rs_egx30"] = (stock_20d_ret / bench_20d_ret.replace(0, np.nan)).fillna(1.0)
        else:
            df["rs_egx30"] = 1.0

        return df


# =============================================================================
# 5. BREAKOUT DETECTOR & FALSE-BREAKOUT RISK ENGINE
# =============================================================================

class BreakoutDetector:
    """Detects resistance breakouts and support breakdowns."""

    def __init__(self, lookback: int = 20, rvol_threshold: float = 1.35):
        self.lookback = lookback
        self.rvol_threshold = rvol_threshold

    def evaluate(
        self, live_quote: Quote, completed_df: pd.DataFrame
    ) -> Dict[str, Any]:
        window = completed_df.iloc[-self.lookback :]
        resistance = float(window["high"].max())
        support = float(window["low"].min())
        avg_vol_20 = float(completed_df["vol_sma_20"].iloc[-1])

        price = live_quote.last_price
        rvol_live = live_quote.volume / avg_vol_20 if avg_vol_20 > 0 else 1.0

        margin_above_res_pct = ((price - resistance) / resistance) * 100.0 if resistance > 0 else 0.0
        is_above_res = margin_above_res_pct >= 0.25
        vol_confirmed = rvol_live >= self.rvol_threshold

        upper_wick_trap = (live_quote.high_price > resistance * 1.003) and (price <= resistance)
        rsi_val = float(completed_df["rsi_14"].iloc[-1])
        false_breakout_risk = upper_wick_trap or (is_above_res and not vol_confirmed) or (rsi_val > 79.0)

        confirmed_breakout = is_above_res and vol_confirmed and not false_breakout_risk

        return {
            "resistance": round(resistance, 2),
            "support": round(support, 2),
            "rvol_live": round(rvol_live, 2),
            "margin_above_res_pct": round(margin_above_res_pct, 2),
            "confirmed_breakout": bool(confirmed_breakout),
            "false_breakout_risk": bool(false_breakout_risk),
            "upper_wick_trap": bool(upper_wick_trap),
        }


# =============================================================================
# 6. CONFIGURABLE 7-CATEGORY SIGNAL ENGINE & RISK CALCULATOR
# =============================================================================

class SignalEngine:
    """Evaluates all 7 required signal categories and risk levels."""

    def __init__(
        self,
        atr_multiplier: float = SystemSettings.ATR_MULTIPLIER_SL,
        lookback_breakout: int = 20,
        rvol_threshold: float = 1.35,
    ):
        self.atr_multiplier = atr_multiplier
        self.breakout_detector = BreakoutDetector(lookback_breakout, rvol_threshold)

    @staticmethod
    def classify_trends(price: float, last_ind: pd.Series) -> Tuple[str, str]:
        ema9 = float(last_ind["ema_9"])
        ema21 = float(last_ind["ema_21"])
        sma50 = float(last_ind["sma_50"])
        sma200 = float(last_ind["sma_200"])

        if price > ema9 > ema21:
            short_trend = "BULLISH"
        elif price < ema9 < ema21:
            short_trend = "BEARISH"
        else:
            short_trend = "NEUTRAL"

        if price > sma50 > sma200:
            med_trend = "UPTREND"
        elif price < sma50 < sma200:
            med_trend = "DOWNTREND"
        else:
            med_trend = "RANGE"

        return short_trend, med_trend

    def evaluate_stock(
        self,
        quote: Quote,
        completed_df: pd.DataFrame,
        liquidity_passed: bool,
        liquidity_note: str,
    ) -> Dict[str, Any]:
        now_cairo = EGXMarketSchedule.get_cairo_now()
        last = completed_df.iloc[-1]
        prev = completed_df.iloc[-2] if len(completed_df) > 1 else last

        price = quote.last_price
        pct_change = ((price - quote.previous_close) / quote.previous_close) * 100.0 if quote.previous_close > 0 else 0.0
        short_trend, med_trend = self.classify_trends(price, last)

        rsi = float(last["rsi_14"])
        macd_val = float(last["macd"])
        macd_sig = float(last["macd_signal"])
        macd_cross_up = (macd_val > macd_sig) and (float(prev["macd"]) <= float(prev["macd_signal"]))
        macd_status = "BULL_CROSS" if macd_cross_up else ("BULLISH" if macd_val > macd_sig else "BEARISH")
        roc = float(last["roc_12"])
        rs_egx30 = float(last["rs_egx30"])
        atr = max(float(last["atr_14"]), price * 0.015)

        bo = self.breakout_detector.evaluate(quote, completed_df)
        rvol = bo["rvol_live"]
        resistance = bo["resistance"]
        support = bo["support"]

        if quote.is_suspended or not liquidity_passed:
            return self._package_result(
                quote, now_cairo, pct_change, short_trend, med_trend, rsi, macd_status,
                roc, rs_egx30, rvol, last, resistance, support,
                category="NO_ACTIONABLE_SIGNAL",
                reasons=f"Filtered out: {liquidity_note}",
                entry_zone="N/A", stop_loss=0.0, invalidation=0.0,
                t1=0.0, t2=0.0, gain_pct=0.0, loss_pct=0.0, rr=0.0,
                liquidity_status=liquidity_note, score=0,
            )

        category = "NO_ACTIONABLE_SIGNAL"
        reasons: List[str] = []
        score = 25

        if bo["confirmed_breakout"]:
            category = "BULLISH_BREAKOUT"
            reasons.append(
                f"Cleared {self.breakout_detector.lookback}d resistance ({resistance:.2f} EGP) by +{bo['margin_above_res_pct']:.2f}% with RVOL {rvol:.2f}x"
            )
            score = 75
            if med_trend == "UPTREND":
                score += 10
                reasons.append("Aligned with medium-term SMA50/SMA200 uptrend")
            if rs_egx30 > 1.02:
                score += 7
                reasons.append(f"Outperforming EGX30 (RS={rs_egx30:.2f})")

        elif (macd_cross_up or (short_trend == "BULLISH" and roc > 3.0)) and (50.0 <= rsi <= 70.0) and rvol >= 1.2:
            category = "BULLISH_MOMENTUM"
            reasons.append(f"Constructive momentum (RSI={rsi:.1f}, ROC={roc:+.1f}%, MACD={macd_status}, RVOL={rvol:.2f}x)")
            score = 70 + (8 if rs_egx30 > 1.0 else 0)

        elif med_trend == "UPTREND" and (abs(price - float(last["ema_21"])) / float(last["ema_21"]) <= 0.018) and (38.0 <= rsi <= 54.0):
            category = "PULLBACK_UPTREND"
            reasons.append(
                f"Controlled pullback to 21-EMA ({float(last['ema_21']):.2f} EGP) inside primary uptrend (RSI={rsi:.1f})"
            )
            score = 72 + (6 if rvol < 1.0 else 0)

        elif rvol >= 2.0 and abs(pct_change) >= 1.5:
            category = "UNUSUAL_VOLUME"
            flow_dir = "Accumulation" if pct_change > 0 else "Distribution"
            reasons.append(f"Unusual volume expansion ({rvol:.2f}x 20d avg) with {pct_change:+.2f}% price move ({flow_dir})")
            score = 66

        elif price < support * 0.997 and rvol >= 1.25:
            category = "BEARISH_BREAKDOWN"
            reasons.append(f"Broke below 20d swing support ({support:.2f} EGP) on elevated RVOL ({rvol:.2f}x)")
            score = 70

        elif rsi <= 29.0 or rsi >= 78.0:
            category = "OVERBOUGHT_OVERSOLD"
            state_lbl = "Oversold (<29)" if rsi <= 29.0 else "Overbought (>78)"
            reasons.append(f"RSI(14) reached extreme {state_lbl} reading ({rsi:.1f})")
            score = 58

        else:
            if bo["upper_wick_trap"]:
                reasons.append(f"False-breakout warning: Intraday high pierced {resistance:.2f} EGP but rejected back below.")
            else:
                reasons.append("Trading within normal range; no high-conviction trigger active.")

        if category == "NO_ACTIONABLE_SIGNAL":
            return self._package_result(
                quote, now_cairo, pct_change, short_trend, med_trend, rsi, macd_status,
                roc, rs_egx30, rvol, last, resistance, support,
                category=category, reasons="; ".join(reasons),
                entry_zone="N/A", stop_loss=0.0, invalidation=0.0,
                t1=0.0, t2=0.0, gain_pct=0.0, loss_pct=0.0, rr=0.0,
                liquidity_status=liquidity_note, score=score,
            )

        is_bearish = (category == "BEARISH_BREAKDOWN") or (category == "OVERBOUGHT_OVERSOLD" and rsi >= 78.0)

        if not is_bearish:
            entry_low = round(price * 0.996, 2)
            entry_high = round(price * 1.004, 2)
            atr_stop = round(price - (self.atr_multiplier * atr), 2)
            struct_stop = round(support * 0.99, 2) if support < price else atr_stop
            stop_loss = max(atr_stop, struct_stop)
            if stop_loss >= price * 0.99:
                stop_loss = round(price - (self.atr_multiplier * atr), 2)
            invalidation = round(min(support, stop_loss), 2)
            risk_per_share = max(price - stop_loss, price * 0.015)
            t1 = round(max(price + 1.8 * risk_per_share, resistance if resistance > price * 1.02 else price + 1.8 * risk_per_share), 2)
            t2 = round(price + 2.8 * risk_per_share, 2)
            gain_pct = round(((t1 - price) / price) * 100.0, 2)
            loss_pct = round(((price - stop_loss) / price) * 100.0, 2)
            rr = round((t1 - price) / (price - stop_loss), 2) if (price - stop_loss) > 0 else 0.0
        else:
            entry_low = round(price * 0.996, 2)
            entry_high = round(price * 1.004, 2)
            stop_loss = round(price + (self.atr_multiplier * atr), 2)
            invalidation = round(max(resistance, stop_loss), 2)
            risk_per_share = max(stop_loss - price, price * 0.015)
            t1 = round(max(0.10, price - 1.8 * risk_per_share), 2)
            t2 = round(max(0.10, price - 2.8 * risk_per_share), 2)
            gain_pct = round(((price - t1) / price) * 100.0, 2)
            loss_pct = round(((stop_loss - price) / price) * 100.0, 2)
            rr = round((price - t1) / (stop_loss - price), 2) if (stop_loss - price) > 0 else 0.0

        return self._package_result(
            quote, now_cairo, pct_change, short_trend, med_trend, rsi, macd_status,
            roc, rs_egx30, rvol, last, resistance, support,
            category=category, reasons="; ".join(reasons),
            entry_zone=f"{entry_low:.2f} - {entry_high:.2f}",
            stop_loss=stop_loss, invalidation=invalidation,
            t1=t1, t2=t2, gain_pct=gain_pct, loss_pct=loss_pct, rr=rr,
            liquidity_status=liquidity_note, score=min(95, score),
        )

    @staticmethod
    def _package_result(
        quote: Quote, now_cairo: datetime, pct_change: float,
        short_trend: str, med_trend: str, rsi: float, macd_status: str,
        roc: float, rs_egx30: float, rvol: float, last_ind: pd.Series,
        resistance: float, support: float, category: str, reasons: str,
        entry_zone: str, stop_loss: float, invalidation: float,
        t1: float, t2: float, gain_pct: float, loss_pct: float,
        rr: float, liquidity_status: str, score: int,
    ) -> Dict[str, Any]:
        return {
            "ticker": quote.ticker,
            "company_name": quote.company_name,
            "last_price": round(quote.last_price, 2),
            "pct_change": round(pct_change, 2),
            "intraday_high": round(quote.high_price, 2),
            "intraday_low": round(quote.low_price, 2),
            "volume": int(quote.volume),
            "traded_value_egp": round(quote.traded_value_egp, 0),
            "spread_pct": quote.spread_pct,
            "rvol": round(rvol, 2),
            "rsi": round(rsi, 1),
            "macd_status": macd_status,
            "roc_12": round(roc, 2),
            "rs_egx30": round(rs_egx30, 2),
            "dist_sma20_pct": round(float(last_ind["dist_sma20_pct"]), 2),
            "dist_sma50_pct": round(float(last_ind["dist_sma50_pct"]), 2),
            "dist_sma200_pct": round(float(last_ind["dist_sma200_pct"]), 2),
            "short_trend": short_trend,
            "med_trend": med_trend,
            "trend_summary": f"{short_trend} / {med_trend}",
            "resistance": resistance,
            "support": support,
            "category": category,
            "trigger_reasons": reasons,
            "entry_zone": entry_zone,
            "stop_loss": stop_loss,
            "invalidation_level": invalidation,
            "target_1": t1,
            "target_2": t2,
            "potential_gain_pct": gain_pct,
            "potential_loss_pct": loss_pct,
            "risk_reward_ratio": rr,
            "liquidity_assessment": liquidity_status,
            "confidence_score": score,
            "signal_timestamp": now_cairo.strftime("%Y-%m-%d %H:%M:%S"),
            "data_timestamp": quote.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "data_freshness": quote.freshness_status,
        }


# =============================================================================
# 7. ALERT MANAGER (TELEGRAM, EMAIL SMTP & DASHBOARD DEDUPLICATION)
# =============================================================================

class AlertManager:
    """Dispatches deduplicated alerts to Telegram and Email."""

    def __init__(self, storage: StorageManager):
        self.storage = storage

    def send_telegram(self, bot_token: str, chat_id: str, message: str) -> Tuple[bool, str]:
        if not bot_token or not chat_id:
            return False, "Missing Telegram Bot Token or Chat ID."
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            resp = requests.post(
                url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=8
            )
            if resp.status_code == 200:
                return True, "Telegram alert sent."
            return False, f"Telegram API HTTP {resp.status_code}: {resp.text}"
        except Exception as exc:
            return False, f"Telegram error: {exc}"

    def send_email(
        self, smtp_server: str, smtp_port: int, username: str, password: str, recipient: str, subject: str, body: str
    ) -> Tuple[bool, str]:
        if not (username and password and recipient):
            return False, "Missing SMTP credentials or recipient email."
        try:
            msg = MIMEMultipart()
            msg["From"] = username
            msg["To"] = recipient
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                server.starttls()
                server.login(username, password)
                server.send_message(msg)
            return True, "Email alert sent."
        except Exception as exc:
            return False, f"SMTP error: {exc}"

    def process_signal_alert(
        self, sig: Dict[str, Any], tg_token: str = "", tg_chat: str = "",
        smtp_user: str = "", smtp_pass: str = "", smtp_to: str = ""
    ) -> Optional[str]:
        cat = sig["category"]
        if cat == "NO_ACTIONABLE_SIGNAL":
            return None
        ticker = sig["ticker"]
        price = sig["last_price"]

        if self.storage.was_alert_recently_sent(ticker, cat, price):
            return None

        text_msg = (
            f"🏛️ *EGX Scanner Signal*\n"
            f"*Ticker:* `{ticker}` ({sig['company_name']})\n"
            f"*Category:* `{cat}` (Rule Score: {sig['confidence_score']}/100)\n"
            f"*Price:* {price:.2f} EGP ({sig['pct_change']:+.2f}%)\n"
            f"*Entry Zone:* {sig['entry_zone']} EGP\n"
            f"*Stop-Loss:* {sig['stop_loss']:.2f} EGP | *Invalidation:* {sig['invalidation_level']:.2f}\n"
            f"*Targets:* T1={sig['target_1']:.2f} / T2={sig['target_2']:.2f} (R:R {sig['risk_reward_ratio']})\n"
            f"*Trigger:* {sig['trigger_reasons']}\n"
            f"*Feed:* {sig['data_freshness']} ({sig['data_timestamp']})"
        )

        self.storage.record_alert(ticker, cat, price, "DASHBOARD")
        if tg_token and tg_chat:
            ok, _ = self.send_telegram(tg_token, tg_chat, text_msg)
            if ok:
                self.storage.record_alert(ticker, cat, price, "TELEGRAM")
        if smtp_user and smtp_pass and smtp_to:
            ok, _ = self.send_email(
                SystemSettings.SMTP_SERVER, SystemSettings.SMTP_PORT,
                smtp_user, smtp_pass, smtp_to,
                f"EGX Alert: {ticker} - {cat}", text_msg.replace("*", "")
            )
            if ok:
                self.storage.record_alert(ticker, cat, price, "EMAIL")
        return text_msg


# =============================================================================
# 8. REAL-TIME MULTI-STOCK SCANNER ORCHESTRATOR
# =============================================================================

class EGXMarketScanner:
    """Orchestrates liquidity filtering, indicator computation, and signal ranking."""

    def __init__(
        self,
        provider: BaseDataProvider,
        storage: StorageManager,
        min_traded_value_egp: float = SystemSettings.MIN_TRADED_VALUE_EGP,
        min_avg_volume: int = SystemSettings.MIN_AVG_VOLUME,
        min_price_egp: float = SystemSettings.MIN_PRICE_EGP,
        max_spread_pct: float = SystemSettings.MAX_SPREAD_PCT,
        atr_multiplier: float = SystemSettings.ATR_MULTIPLIER_SL,
        lookback_breakout: int = 20,
    ):
        self.provider = provider
        self.storage = storage
        self.min_traded_value_egp = min_traded_value_egp
        self.min_avg_volume = min_avg_volume
        self.min_price_egp = min_price_egp
        self.max_spread_pct = max_spread_pct
        self.signal_engine = SignalEngine(
            atr_multiplier=atr_multiplier, lookback_breakout=lookback_breakout
        )

    def run_scan(self) -> pd.DataFrame:
        quotes = self.provider.get_all_quotes()
        bench_df = self.provider.get_egx30_benchmark(lookback_days=220)
        results: List[Dict[str, Any]] = []

        for ticker, quote in quotes.items():
            hist_df = self.provider.get_historical_ohlcv(ticker, lookback_days=220)
            ind_df = TechnicalIndicators.compute_all_indicators(hist_df, bench_df)
            avg_vol_20 = float(ind_df["vol_sma_20"].iloc[-1])

            liq_reasons = []
            if quote.is_suspended:
                liq_reasons.append("Suspended")
            if quote.last_price < self.min_price_egp:
                liq_reasons.append(f"Price < {self.min_price_egp} EGP")
            if quote.traded_value_egp < self.min_traded_value_egp:
                liq_reasons.append(f"Turnover < {self.min_traded_value_egp:,.0f} EGP")
            if avg_vol_20 < self.min_avg_volume:
                liq_reasons.append(f"20d Avg Vol < {self.min_avg_volume:,}")
            if quote.spread_pct > self.max_spread_pct:
                liq_reasons.append(f"Spread {quote.spread_pct:.2f}% > {self.max_spread_pct:.2f}%")

            liquidity_passed = len(liq_reasons) == 0
            liquidity_note = "PASS (Liquid)" if liquidity_passed else ", ".join(liq_reasons)

            sig = self.signal_engine.evaluate_stock(
                quote=quote,
                completed_df=ind_df,
                liquidity_passed=liquidity_passed,
                liquidity_note=liquidity_note,
            )
            self.storage.save_signal(sig)
            results.append(sig)

        return pd.DataFrame(results)


# =============================================================================
# 9. WALK-FORWARD HISTORICAL BACKTESTER (ZERO LOOK-AHEAD BIAS)
# =============================================================================

class EGXStrategyBacktester:
    """Historical trade simulator with walk-forward validation and fee accounting."""

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        position_size_pct: float = 0.25,
        fee_pct_per_side: float = 0.002,
        slippage_pct: float = 0.001,
        atr_stop_mult: float = 1.5,
        rr_target_mult: float = 2.0,
        max_holding_days: int = 12,
        rvol_trigger: float = 1.25,
    ):
        self.initial_capital = initial_capital
        self.position_size_pct = position_size_pct
        self.fee_pct = fee_pct_per_side
        self.slippage = slippage_pct
        self.atr_stop_mult = atr_stop_mult
        self.rr_target_mult = rr_target_mult
        self.max_holding_days = max_holding_days
        self.rvol_trigger = rvol_trigger

    def run_simulation(
        self, ohlcv_df: pd.DataFrame, split_ratio: float = 0.70
    ) -> Dict[str, Any]:
        df = TechnicalIndicators.compute_all_indicators(ohlcv_df)
        split_idx = max(30, int(len(df) * split_ratio))

        trades: List[Dict[str, Any]] = []
        capital = self.initial_capital
        equity_curve = [{"date": df.index[24], "equity": capital, "sample": "IN_SAMPLE"}]

        in_position = False
        entry_price = 0.0
        stop_price = 0.0
        target_price = 0.0
        entry_idx = 0
        entry_sample = "IN_SAMPLE"

        for i in range(25, len(df)):
            cur = df.iloc[i]
            prev = df.iloc[i - 1]
            sample_tag = "IN_SAMPLE" if i < split_idx else "OUT_OF_SAMPLE"

            if not in_position:
                swing_high_20 = float(df["high"].iloc[i - 21 : i - 1].max())
                breakout_cond = (
                    float(prev["close"]) > swing_high_20
                    and float(prev["rvol"]) >= self.rvol_trigger
                    and 48.0 <= float(prev["rsi_14"]) <= 75.0
                )
                pullback_cond = (
                    float(prev["close"]) > float(prev["sma_50"])
                    and float(prev["ema_9"]) > float(prev["ema_21"])
                    and abs(float(prev["close"]) - float(prev["ema_21"])) / float(prev["ema_21"]) <= 0.018
                    and 40.0 <= float(prev["rsi_14"]) <= 55.0
                )

                if breakout_cond or pullback_cond:
                    entry_price = float(cur["open"]) * (1.0 + self.slippage)
                    atr_val = max(float(prev["atr_14"]), entry_price * 0.018)
                    risk = self.atr_stop_mult * atr_val
                    stop_price = entry_price - risk
                    target_price = entry_price + (self.rr_target_mult * risk)
                    in_position = True
                    entry_idx = i
                    entry_sample = sample_tag
            else:
                held = i - entry_idx
                hit_stop = float(cur["low"]) <= stop_price
                hit_target = float(cur["high"]) >= target_price
                time_exit = held >= self.max_holding_days

                if hit_stop or hit_target or time_exit:
                    raw_exit = stop_price if hit_stop else (target_price if hit_target else float(cur["close"]))
                    exit_price = raw_exit * (1.0 - self.slippage)

                    gross_ret = (exit_price - entry_price) / entry_price
                    net_ret = gross_ret - (2.0 * self.fee_pct)
                    allocated = capital * self.position_size_pct
                    pnl_egp = allocated * net_ret
                    capital += pnl_egp

                    trades.append(
                        {
                            "sample": entry_sample,
                            "entry_date": str(df.index[entry_idx].date()),
                            "exit_date": str(df.index[i].date()),
                            "entry_price": round(entry_price, 2),
                            "exit_price": round(exit_price, 2),
                            "holding_days": held,
                            "net_return_pct": round(net_ret * 100.0, 2),
                            "pnl_egp": round(pnl_egp, 2),
                            "exit_reason": "STOP_LOSS" if hit_stop else ("TARGET" if hit_target else "TIME_LIMIT"),
                        }
                    )
                    in_position = False

            equity_curve.append({"date": df.index[i], "equity": round(capital, 2), "sample": sample_tag})

        trades_df = pd.DataFrame(trades)
        eq_df = pd.DataFrame(equity_curve).set_index("date")

        return {
            "overall": self._compute_metrics(trades_df, eq_df, self.initial_capital),
            "in_sample": self._compute_metrics(
                trades_df[trades_df["sample"] == "IN_SAMPLE"] if not trades_df.empty else trades_df,
                eq_df[eq_df["sample"] == "IN_SAMPLE"],
                self.initial_capital,
            ),
            "out_of_sample": self._compute_metrics(
                trades_df[trades_df["sample"] == "OUT_OF_SAMPLE"] if not trades_df.empty else trades_df,
                eq_df[eq_df["sample"] == "OUT_OF_SAMPLE"],
                self.initial_capital,
            ),
            "trades_df": trades_df,
            "equity_df": eq_df,
        }

    @staticmethod
    def _compute_metrics(trades_df: pd.DataFrame, eq_df: pd.DataFrame, start_cap: float) -> Dict[str, Any]:
        if trades_df is None or trades_df.empty:
            return {
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "avg_return_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "profit_factor": 0.0,
                "avg_holding_days": 0.0,
                "net_profit_egp": 0.0,
            }

        wins = trades_df[trades_df["net_return_pct"] > 0]
        losses = trades_df[trades_df["net_return_pct"] <= 0]
        win_rate = (len(wins) / len(trades_df)) * 100.0
        gross_win = float(wins["pnl_egp"].sum()) if not wins.empty else 0.0
        gross_loss = abs(float(losses["pnl_egp"].sum())) if not losses.empty else 0.0
        pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else (99.9 if gross_win > 0 else 0.0)

        if not eq_df.empty:
            eq_vals = eq_df["equity"].values
            peaks = np.maximum.accumulate(eq_vals)
            dd = ((peaks - eq_vals) / peaks) * 100.0
            max_dd = float(np.max(dd))
        else:
            max_dd = 0.0

        return {
            "total_trades": int(len(trades_df)),
            "win_rate_pct": round(win_rate, 1),
            "avg_return_pct": round(float(trades_df["net_return_pct"].mean()), 2),
            "max_drawdown_pct": round(max_dd, 2),
            "profit_factor": pf,
            "avg_holding_days": round(float(trades_df["holding_days"].mean()), 1),
            "net_profit_egp": round(float(trades_df["pnl_egp"].sum()), 2),
        }


# =============================================================================
# 10. PLOTLY CHARTING ENGINE
# =============================================================================

def build_stock_chart(ind_df: pd.DataFrame, ticker: str, sig: Dict[str, Any]) -> go.Figure:
    df = ind_df.tail(110)
    fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.50, 0.16, 0.17, 0.17],
        subplot_titles=(
            f"{ticker} ({sig['company_name']}) — Daily Completed Candles & Levels",
            "Volume & 20-Day SMA",
            "MACD (12, 26, 9)",
            "RSI (14)",
        ),
    )

    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
            name="OHLC", increasing_line_color="#10b981", decreasing_line_color="#ef4444"
        ),
        row=1, col=1,
    )
    fig.add_trace(go.Scatter(x=df.index, y=df["ema_9"], name="EMA 9", line=dict(color="#38bdf8", width=1.1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["ema_21"], name="EMA 21", line=dict(color="#c084fc", width=1.1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["sma_50"], name="SMA 50", line=dict(color="#f59e0b", width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["sma_200"], name="SMA 200", line=dict(color="#ec4899", width=1.5, dash="dot")), row=1, col=1)

    fig.add_hline(y=sig["resistance"], line_dash="dot", line_color="#60a5fa", annotation_text=f"20d Res: {sig['resistance']}", row=1, col=1)
    fig.add_hline(y=sig["support"], line_dash="dot", line_color="#9ca3af", annotation_text=f"20d Sup: {sig['support']}", row=1, col=1)

    if sig["stop_loss"] > 0:
        fig.add_hline(y=sig["stop_loss"], line_dash="dash", line_color="#ef4444", annotation_text=f"Stop: {sig['stop_loss']}", row=1, col=1)
        fig.add_hline(y=sig["target_1"], line_dash="dash", line_color="#10b981", annotation_text=f"T1: {sig['target_1']}", row=1, col=1)

    vol_colors = ["#10b981" if c >= o else "#ef4444" for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(x=df.index, y=df["volume"], name="Volume", marker_color=vol_colors), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["vol_sma_20"], name="Vol SMA 20", line=dict(color="#fbbf24", width=1.3)), row=2, col=1)

    hist_colors = ["#10b981" if h >= 0 else "#ef4444" for h in df["macd_hist"]]
    fig.add_trace(go.Bar(x=df.index, y=df["macd_hist"], name="MACD Hist", marker_color=hist_colors), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["macd"], name="MACD", line=dict(color="#38bdf8", width=1.2)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["macd_signal"], name="Signal", line=dict(color="#f97316", width=1.2)), row=3, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=df["rsi_14"], name="RSI (14)", line=dict(color="#a855f7", width=1.5)), row=4, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color="#ef4444", row=4, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color="#10b981", row=4, col=1)

    fig.update_layout(
        height=850,
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        margin=dict(l=30, r=30, t=40, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


# =============================================================================
# 11. STREAMLIT INTERACTIVE MULTI-PAGE DASHBOARD (7 PAGES)
# =============================================================================

TOP_10_INVESTING_PRESETS = [
    {"ticker": "COMI.CA", "name": "Commercial International Bank", "sector": "Banking", "price": 120.10, "balanced": 22, "export": 15, "growth": 15, "stop_dist": 4.5},
    {"ticker": "TMGH.CA", "name": "Talaat Moustafa Group", "sector": "Real Estate", "price": 81.60, "balanced": 14, "export": 10, "growth": 15, "stop_dist": 5.5},
    {"ticker": "ETEL.CA", "name": "Telecom Egypt (WE)", "sector": "Telecom & Infrastructure", "price": 117.00, "balanced": 12, "export": 14, "growth": 8, "stop_dist": 4.8},
    {"ticker": "SWDY.CA", "name": "Elsewedy Electric", "sector": "Industrials & Cables", "price": 102.02, "balanced": 12, "export": 18, "growth": 10, "stop_dist": 5.0},
    {"ticker": "FWRY.CA", "name": "Fawry for Banking Tech", "sector": "Fintech & Payments", "price": 16.82, "balanced": 8, "export": 4, "growth": 16, "stop_dist": 6.5},
    {"ticker": "EAST.CA", "name": "Eastern Company", "sector": "Consumer Staples", "price": 30.03, "balanced": 8, "export": 6, "growth": 4, "stop_dist": 4.0},
    {"ticker": "EFIH.CA", "name": "e-finance Investment Group", "sector": "Fintech & GovTech", "price": 20.75, "balanced": 7, "export": 4, "growth": 12, "stop_dist": 5.8},
    {"ticker": "ABUK.CA", "name": "Abu Qir Fertilizers", "sector": "Fertilizers / USD Exporter", "price": 88.63, "balanced": 7, "export": 15, "growth": 7, "stop_dist": 5.2},
    {"ticker": "HRHO.CA", "name": "EFG Holding", "sector": "Financial Services", "price": 23.91, "balanced": 5, "export": 4, "growth": 8, "stop_dist": 6.0},
    {"ticker": "ORAS.CA", "name": "Orascom Construction", "sector": "Construction / USD Earner", "price": 773.00, "balanced": 5, "export": 10, "growth": 5, "stop_dist": 5.0},
]


def main_dashboard() -> None:
    st.set_page_config(
        page_title="EGX Real-Time Automated Stock Scanner",
        page_icon="🏛️",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    if "storage" not in st.session_state:
        st.session_state.storage = StorageManager()
    if "provider" not in st.session_state:
        prov = YahooFinanceEGXProvider()
        prov.connect()
        st.session_state.provider = prov
    if "alert_mgr" not in st.session_state:
        st.session_state.alert_mgr = AlertManager(st.session_state.storage)
    if "tg_token" not in st.session_state:
        st.session_state.tg_token = SystemSettings.TELEGRAM_BOT_TOKEN
        st.session_state.tg_chat = SystemSettings.TELEGRAM_CHAT_ID
        st.session_state.smtp_user = SystemSettings.SMTP_USERNAME
        st.session_state.smtp_pass = SystemSettings.SMTP_PASSWORD
        st.session_state.smtp_to = SystemSettings.ALERT_RECIPIENT_EMAIL

    storage: StorageManager = st.session_state.storage
    provider: SimulatedEGXDataProvider = st.session_state.provider
    alert_mgr: AlertManager = st.session_state.alert_mgr

    st.sidebar.title("🏛️ EGX Stock Scanner")
    page = st.sidebar.radio(
        "Dashboard Pages",
        [
            "1. Market Overview",
            "2. Live Scanner",
            "3. Stock Analysis",
            "4. Alerts & Notifications",
            "5. Watchlist",
            "6. Backtesting",
            "7. Portfolio & Position Sizing",
        ],
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("⚙️ Global Scanner Rules")
    min_val_egp = st.sidebar.number_input("Min Daily Traded Value (EGP)", value=1_000_000, step=250_000)
    min_avg_vol = st.sidebar.number_input("Min 20d Avg Volume (Shares)", value=50_000, step=10_000)
    min_price = st.sidebar.number_input("Min Stock Price (EGP)", value=0.50, step=0.25)
    max_spread = st.sidebar.number_input("Max Bid-Ask Spread (%)", value=1.50, step=0.25)
    atr_mult = st.sidebar.slider("ATR Stop-Loss Multiplier", 1.0, 3.0, 1.5, 0.1)

    if st.sidebar.button("🔄 Trigger New Scan Sweep", use_container_width=True):
        pass

    session_info = EGXMarketSchedule.get_session_status()
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Session:** `{session_info['status']}`")
    st.sidebar.caption(session_info["description"])
    st.sidebar.caption(f"🕒 {session_info['cairo_time']}")
    st.sidebar.warning(
st.sidebar.success(
    "Feed Mode: **REAL EOD (Yahoo Finance)** (EGX Completed Candles)"
)

st.sidebar.warning(
    "Connect a licensed DirectFN/LSEG key in `.env` for live exchange streaming."
)        "Connect a licensed DirectFN/LSEG key in `.env` for live exchange streaming."
    )

    scanner = EGXMarketScanner(
        provider=provider,
        storage=storage,
        min_traded_value_egp=float(min_val_egp),
        min_avg_volume=int(min_avg_vol),
        min_price_egp=float(min_price),
        max_spread_pct=float(max_spread),
        atr_multiplier=float(atr_mult),
    )
    scan_df = scanner.run_scan()

    for _, row in scan_df.iterrows():
        alert_mgr.process_signal_alert(
            row.to_dict(),
            st.session_state.tg_token,
            st.session_state.tg_chat,
            st.session_state.smtp_user,
            st.session_state.smtp_pass,
            st.session_state.smtp_to,
        )

    # =========================================================================
    # PAGE 1: MARKET OVERVIEW
    # =========================================================================
    if page.startswith("1."):
        st.title("📊 Egyptian Exchange (EGX) — Market Overview")

        egx30_df = provider.get_egx30_benchmark(lookback_days=30)
        egx30_last = float(egx30_df["close"].iloc[-1])
        egx30_prev = float(egx30_df["close"].iloc[-2])
        egx30_chg = ((egx30_last - egx30_prev) / egx30_prev) * 100.0

        adv = int((scan_df["pct_change"] > 0.05).sum())
        dec = int((scan_df["pct_change"] < -0.05).sum())
        actionable_cnt = int((scan_df["category"] != "NO_ACTIONABLE_SIGNAL").sum())

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("EGX Session", session_info["status"], "Open" if session_info["is_open"] else "Closed")
        c2.metric("EGX30 Index (Sim)", f"{egx30_last:,.0f} pts", f"{egx30_chg:+.2f}%")
        c3.metric("Advancing / Declining", f"{adv} / {dec}", f"Breadth: {adv - dec:+d}")
        c4.metric("Actionable Signals", actionable_cnt, f"Out of {len(scan_df)} stocks")
        c5.metric("Feed Status", "CONNECTED" if provider.is_connected() else "DISCONNECTED", "SIMULATED")

        col_l, col_r = st.columns(2)
        with col_l:
            st.subheader("🚀 Largest Percentage Gainers")
            st.dataframe(
                scan_df.sort_values("pct_change", ascending=False)[
                    ["ticker", "company_name", "last_price", "pct_change", "rvol", "category"]
                ].head(6),
                use_container_width=True,
                hide_index=True,
            )
        with col_r:
            st.subheader("🔻 Largest Percentage Losers")
            st.dataframe(
                scan_df.sort_values("pct_change", ascending=True)[
                    ["ticker", "company_name", "last_price", "pct_change", "rvol", "category"]
                ].head(6),
                use_container_width=True,
                hide_index=True,
            )

        st.subheader("💰 Most Actively Traded EGX Stocks (By Traded Value in EGP)")
        st.dataframe(
            scan_df.sort_values("traded_value_egp", ascending=False)[
                ["ticker", "company_name", "last_price", "pct_change", "volume", "traded_value_egp", "spread_pct", "category"]
            ].head(10),
            use_container_width=True,
            hide_index=True,
        )

    # =========================================================================
    # PAGE 2: LIVE SCANNER
    # =========================================================================
    elif page.startswith("2."):
        st.title("🎯 Live EGX Technical & Liquidity Scanner")

        f1, f2, f3 = st.columns(3)
        sig_filter = f1.selectbox(
            "Filter by Signal Category",
            [
                "ALL_STOCKS",
                "ACTIONABLE_ONLY",
                "BULLISH_BREAKOUT",
                "BULLISH_MOMENTUM",
                "PULLBACK_UPTREND",
                "UNUSUAL_VOLUME",
                "BEARISH_BREAKDOWN",
                "OVERBOUGHT_OVERSOLD",
            ],
        )
        sort_col = f2.selectbox(
            "Sort Results By",
            ["confidence_score", "traded_value_egp", "rvol", "pct_change", "risk_reward_ratio"],
        )
        only_liquid = f3.checkbox("Show Only Liquidity-Qualified Securities", value=True)

        filtered = scan_df.copy()
        if only_liquid:
            filtered = filtered[filtered["liquidity_assessment"] == "PASS (Liquid)"]
        if sig_filter == "ACTIONABLE_ONLY":
            filtered = filtered[filtered["category"] != "NO_ACTIONABLE_SIGNAL"]
        elif sig_filter != "ALL_STOCKS":
            filtered = filtered[filtered["category"] == sig_filter]

        filtered = filtered.sort_values(by=sort_col, ascending=False)

        display_cols = [
            "ticker", "company_name", "last_price", "pct_change", "rvol",
            "rsi", "macd_status", "trend_summary", "category", "entry_zone",
            "stop_loss", "target_1", "risk_reward_ratio", "confidence_score",
            "traded_value_egp", "data_freshness"
        ]
        st.dataframe(filtered[display_cols], use_container_width=True, hide_index=True)
        st.caption(
            "Note: Confidence score is a transparent rule-alignment score (0–95), not a calibrated statistical probability of profit. "
            "Entry, Stop-Loss, and Target levels are technical estimates, not guaranteed execution prices."
        )

    # =========================================================================
    # PAGE 3: STOCK ANALYSIS
    # =========================================================================
    elif page.startswith("3."):
        st.title("🔍 Deep Technical Stock Analysis")

        selected_ticker = st.selectbox("Select EGX Security", scan_df["ticker"].tolist())
        sig_row = scan_df[scan_df["ticker"] == selected_ticker].iloc[0].to_dict()
        hist_df = provider.get_historical_ohlcv(selected_ticker, lookback_days=220)
        bench_df = provider.get_egx30_benchmark(lookback_days=220)
        ind_df = TechnicalIndicators.compute_all_indicators(hist_df, bench_df)

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Last Price", f"{sig_row['last_price']:.2f} EGP", f"{sig_row['pct_change']:+.2f}%")
        m2.metric("Signal Category", sig_row["category"])
        m3.metric("Entry Zone", sig_row["entry_zone"])
        m4.metric("Stop-Loss / Inval.", f"{sig_row['stop_loss']:.2f} EGP", f"Loss: -{sig_row['potential_loss_pct']:.2f}%", delta_color="inverse")
        m5.metric("Target 1 / Target 2", f"{sig_row['target_1']:.2f} / {sig_row['target_2']:.2f}", f"Gain: +{sig_row['potential_gain_pct']:.2f}%")
        m6.metric("Risk-to-Reward", f"1 : {sig_row['risk_reward_ratio']:.2f}", f"Score: {sig_row['confidence_score']}/100")

        st.info(f"**Trigger Conditions:** {sig_row['trigger_reasons']} | **Liquidity:** {sig_row['liquidity_assessment']}")

        fig = build_stock_chart(ind_df, selected_ticker, sig_row)
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("📐 Distance from Key Moving Averages & Relative Strength")
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Distance from SMA 20", f"{sig_row['dist_sma20_pct']:+.2f}%")
        d2.metric("Distance from SMA 50", f"{sig_row['dist_sma50_pct']:+.2f}%")
        d3.metric("Distance from SMA 200", f"{sig_row['dist_sma200_pct']:+.2f}%")
        d4.metric("RS vs. EGX30 (20d)", f"{sig_row['rs_egx30']:.2f}x")

        st.subheader("📜 Stored Signal History for This Ticker (SQLite)")
        st.dataframe(storage.get_recent_signals(selected_ticker, limit=15), use_container_width=True, hide_index=True)

    # =========================================================================
    # PAGE 4: ALERTS & NOTIFICATIONS
    # =========================================================================
    elif page.startswith("4."):
        st.title("🔔 Multi-Channel Alerts & Notification History")

        with st.form("alert_config_form"):
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Telegram Bot Configuration")
                tg_tok = st.text_input("Telegram Bot Token", value=st.session_state.tg_token, type="password")
                tg_cid = st.text_input("Telegram Chat ID", value=st.session_state.tg_chat)
            with c2:
                st.subheader("Email (SMTP) Configuration")
                em_usr = st.text_input("SMTP Username", value=st.session_state.smtp_user)
                em_pwd = st.text_input("SMTP App Password", value=st.session_state.smtp_pass, type="password")
                em_to = st.text_input("Recipient Email", value=st.session_state.smtp_to)

            if st.form_submit_button("💾 Save Alert Settings"):
                st.session_state.tg_token = tg_tok
                st.session_state.tg_chat = tg_cid
                st.session_state.smtp_user = em_usr
                st.session_state.smtp_pass = em_pwd
                st.session_state.smtp_to = em_to
                st.success("Alert credentials updated for current session.")

        if st.button("📨 Send Test Telegram Alert"):
            ok, msg = alert_mgr.send_telegram(
                st.session_state.tg_token,
                st.session_state.tg_chat,
                "✅ *EGX Automated Stock Scanner*: Test notification successful.",
            )
            if ok:
                st.success(msg)
            else:
                st.error(msg)

        st.subheader("📋 Deduplicated Alert Dispatch Log (SQLite)")
        st.dataframe(storage.get_alert_history(limit=40), use_container_width=True, hide_index=True)

    # =========================================================================
    # PAGE 5: WATCHLIST
    # =========================================================================
    elif page.startswith("5."):
        st.title("⭐ Custom EGX Watchlist & Custom Technical Rules")

        with st.form("add_watchlist_form"):
            w1, w2, w3, w4, w5 = st.columns(5)
            wl_ticker = w1.selectbox("EGX Ticker", list(EGX_UNIVERSE_SPEC.keys()))
            wl_rvol = w2.number_input("Custom Min RVOL Alert", value=1.4, step=0.1)
            wl_rsi_l = w3.number_input("Oversold RSI Alert (<)", value=30.0, step=1.0)
            wl_rsi_h = w4.number_input("Overbought RSI Alert (>)", value=70.0, step=1.0)
            wl_note = w5.text_input("Strategy Note", value="Swing setup")
            if st.form_submit_button("➕ Add / Update Watchlist Stock"):
                storage.add_to_watchlist(wl_ticker, wl_rvol, wl_rsi_l, wl_rsi_h, wl_note)
                st.success(f"{wl_ticker} saved to SQLite watchlist.")
                st.rerun()

        wl_items = storage.get_watchlist()
        if wl_items:
            wl_rows = []
            for item in wl_items:
                t = item["ticker"]
                match = scan_df[scan_df["ticker"] == t]
                if not match.empty:
                    r = match.iloc[0]
                    custom_hit = []
                    if r["rvol"] >= item["min_rvol_rule"]:
                        custom_hit.append(f"RVOL >= {item['min_rvol_rule']}")
                    if r["rsi"] <= item["rsi_low_rule"]:
                        custom_hit.append(f"RSI <= {item['rsi_low_rule']}")
                    if r["rsi"] >= item["rsi_high_rule"]:
                        custom_hit.append(f"RSI >= {item['rsi_high_rule']}")
                    wl_rows.append(
                        {
                            "Ticker": t,
                            "Last (EGP)": r["last_price"],
                            "Chg %": r["pct_change"],
                            "RVOL": r["rvol"],
                            "RSI": r["rsi"],
                            "Scanner Signal": r["category"],
                            "Custom Rule Status": ", ".join(custom_hit) if custom_hit else "Watching",
                            "Notes": item["notes"],
                        }
                    )
            st.dataframe(pd.DataFrame(wl_rows), use_container_width=True, hide_index=True)

            rem_col1, rem_col2 = st.columns([3, 1])
            rem_t = rem_col1.selectbox("Select Ticker to Remove", [x["ticker"] for x in wl_items])
            if rem_col2.button("🗑️ Remove from Watchlist"):
                storage.remove_from_watchlist(rem_t)
                st.rerun()

    # =========================================================================
    # PAGE 6: WALK-FORWARD BACKTESTING
    # =========================================================================
    elif page.startswith("6."):
        st.title("🧪 Walk-Forward Historical Backtesting Engine")
        st.markdown(
            "Evaluates historical breakout and uptrend-pullback setups without look-ahead bias "
            "(entries execute on the **next day's open** + slippage, deducting EGX brokerage & regulatory fees)."
        )

        b1, b2, b3, b4 = st.columns(4)
        bt_ticker = b1.selectbox("Select EGX Stock", list(EGX_UNIVERSE_SPEC.keys()))
        bt_cap = b2.number_input("Initial Capital (EGP)", value=100_000.0, step=25_000.0)
        bt_fee = b3.number_input("One-Way Fee % (Broker+EGX)", value=0.20, step=0.05) / 100.0
        bt_split = b4.slider("In-Sample / Out-of-Sample Split", 0.50, 0.85, 0.70, 0.05)

        hist_df = provider.get_historical_ohlcv(bt_ticker, lookback_days=220)
        backtester = EGXStrategyBacktester(
            initial_capital=bt_cap, fee_pct_per_side=bt_fee, atr_stop_mult=atr_mult
        )
        res = backtester.run_simulation(hist_df, split_ratio=bt_split)
        ov = res["overall"]
        ins = res["in_sample"]
        oos = res["out_of_sample"]

        k1, k2, k3, k4, k5, k6 = st.columns(6)
        k1.metric("Total Trades", ov["total_trades"])
        k2.metric("Win Rate", f"{ov['win_rate_pct']:.1f}%")
        k3.metric("Avg Trade Net Return", f"{ov['avg_return_pct']:+.2f}%")
        k4.metric("Profit Factor", f"{ov['profit_factor']:.2f}")
        k5.metric("Max Drawdown", f"-{ov['max_drawdown_pct']:.2f}%")
        k6.metric("Net Profit (After Fees)", f"{ov['net_profit_egp']:+,.2f} EGP")

        st.subheader("📊 Walk-Forward Validation Comparison (In-Sample vs. Out-of-Sample)")
        comp_df = pd.DataFrame([ins, oos], index=["In-Sample (Training Window)", "Out-of-Sample (Validation Window)"])
        st.dataframe(comp_df, use_container_width=True)

        if not res["equity_df"].empty:
            eq_fig = go.Figure()
            eq_fig.add_trace(
                go.Scatter(
                    x=res["equity_df"].index,
                    y=res["equity_df"]["equity"],
                    mode="lines",
                    name="Portfolio Equity (EGP)",
                    line=dict(color="#10b981", width=2),
                )
            )
            eq_fig.update_layout(
                title=f"{bt_ticker} Walk-Forward Equity Curve (Net of EGX Fees & Slippage)",
                height=360,
                template="plotly_dark",
            )
            st.plotly_chart(eq_fig, use_container_width=True)

        st.subheader("📑 Historical Trade Execution Log")
        st.dataframe(res["trades_df"], use_container_width=True, hide_index=True)

    # =========================================================================
    # PAGE 7: PORTFOLIO ALLOCATION & POSITION SIZING CALCULATOR
    # =========================================================================
    elif page.startswith("7."):
        st.title("💼 EGX Top 10 Portfolio Allocator & Position Sizer")
        st.markdown(
            "Institutional capital sizing framework tailored for Egyptian Exchange blue chips. "
            "Accounts for integer share execution, cash buffers, EGX round-trip brokerage & regulatory fees (~0.40%), "
            "and structural ATR risk budgeting."
        )

        p1, p2, p3 = st.columns(3)
        total_portfolio_cap = p1.number_input(
            "Total Portfolio Capital (EGP)", value=100_000.0, step=25_000.0, min_value=10_000.0
        )
        strategy_preset = p2.selectbox(
            "Allocation Strategy Profile",
            [
                "Balanced Blue-Chip Core (Standard)",
                "USD & Inflation Exporters Hedge",
                "High-Beta Swing & Growth",
            ],
        )
        atr_risk_mult = p3.slider("ATR Stop-Loss Scale Multiplier", 1.0, 2.5, 1.5, 0.1)

        weight_key = "balanced"
        if "USD" in strategy_preset:
            weight_key = "export"
        elif "High-Beta" in strategy_preset:
            weight_key = "growth"

        alloc_records = []
        total_invested_egp = 0.0
        total_risk_at_stop = 0.0

        for item in TOP_10_INVESTING_PRESETS:
            ticker = item["ticker"]
            name = item["name"]
            sector = item["sector"]

            q = provider.get_latest_quote(ticker)
            price = round(q.last_price if q else item["price"], 2)

            weight_pct = item[weight_key]
            target_egp = total_portfolio_cap * (weight_pct / 100.0)

            shares = int(target_egp // price)
            actual_egp = round(shares * price, 2)
            actual_weight = round((actual_egp / total_portfolio_cap) * 100.0, 2) if total_portfolio_cap > 0 else 0.0

            stop_dist_pct = item["stop_dist"] * (atr_risk_mult / 1.5)
            stop_price = round(price * (1.0 - (stop_dist_pct / 100.0)), 2)
            risk_per_share = price - stop_price
            target_price = round(price + (2.0 * risk_per_share), 2)
            target_gain_pct = round(((target_price - price) / price) * 100.0, 2)

            total_stock_risk_egp = round(shares * risk_per_share, 2)
            total_invested_egp += actual_egp
            total_risk_at_stop += total_stock_risk_egp

            alloc_records.append({
                "Ticker": ticker,
                "Company Name": name,
                "Sector": sector,
                "Price (EGP)": price,
                "Target %": f"{weight_pct}%",
                "Shares to Buy": shares,
                "Capital (EGP)": actual_egp,
                "Actual Weight": f"{actual_weight}%",
                "Stop-Loss (EGP)": stop_price,
                "Target (2.0R)": target_price,
                "Max Risk (EGP)": total_stock_risk_egp,
                "sector_raw": sector,
                "cap_raw": actual_egp,
            })

        df_alloc = pd.DataFrame(alloc_records)
        cash_reserve_egp = round(total_portfolio_cap - total_invested_egp, 2)
        portfolio_risk_pct = round((total_risk_at_stop / total_portfolio_cap) * 100.0, 2) if total_portfolio_cap > 0 else 0.0
        est_egx_fees = round(total_invested_egp * 0.004, 2)

        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("Active Capital Invested", f"{total_invested_egp:,.2f} EGP", f"{(total_invested_egp/total_portfolio_cap)*100:.1f}% Allocation")
        s2.metric("Unallocated Cash Reserve", f"{cash_reserve_egp:,.2f} EGP", "Remnants Buffer")
        s3.metric("Max Portfolio Risk", f"-{total_risk_at_stop:,.2f} EGP", f"-{portfolio_risk_pct:.2f}% of Total Cap", delta_color="inverse")
        s4.metric("Est. EGX Round-Trip Fees", f"{est_egx_fees:,.2f} EGP", "0.40% Brokerage & MCDR")
        s5.metric("Target Risk-to-Reward", "1 : 2.00", "ATR Asymmetric Targets")

        st.subheader("📋 Institutional Capital Allocation & Share Order Manifest")
        st.dataframe(
            df_alloc[[
                "Ticker", "Company Name", "Sector", "Price (EGP)", "Target %",
                "Shares to Buy", "Capital (EGP)", "Actual Weight", "Stop-Loss (EGP)",
                "Target (2.0R)", "Max Risk (EGP)"
            ]],
            use_container_width=True,
            hide_index=True,
        )

        c_left, c_right = st.columns([1, 1])

        with c_left:
            st.subheader("📊 Capital Deployment by Stock")
            bar_fig = go.Figure(
                go.Bar(
                    x=df_alloc["Capital (EGP)"],
                    y=df_alloc["Ticker"],
                    orientation="h",
                    text=[f"{v:,.0f} EGP ({w})" for v, w in zip(df_alloc["Capital (EGP)"], df_alloc["Actual Weight"])],
                    textposition="auto",
                    marker=dict(color="#38bdf8"),
                )
            )
            bar_fig.update_layout(
                height=380,
                template="plotly_dark",
                yaxis=dict(autorange="reversed"),
                margin=dict(l=30, r=30, t=20, b=20),
            )
            st.plotly_chart(bar_fig, use_container_width=True)

        with c_right:
            st.subheader("🥧 Sector Diversification")
            sector_grp = df_alloc.groupby("sector_raw")["cap_raw"].sum().reset_index()
            pie_fig = go.Figure(
                go.Pie(
                    labels=sector_grp["sector_raw"],
                    values=sector_grp["cap_raw"],
                    hole=0.45,
                    textinfo="label+percent",
                    marker=dict(colors=["#38bdf8", "#34d399", "#f59e0b", "#c084fc", "#f43f5e", "#fb923c", "#a78bfa"]),
                )
            )
            pie_fig.update_layout(
                height=380,
                template="plotly_dark",
                margin=dict(l=20, r=20, t=20, b=20),
            )
            st.plotly_chart(pie_fig, use_container_width=True)

        st.markdown("---")
        st.subheader("🎯 Standalone Single-Trade Sizer (Fixed Fractional Risk Model)")
        st.caption("Calculate exactly how many shares to buy for an individual stock without risking more than your predefined risk per trade.")

        t1, t2, t3, t4 = st.columns(4)
        single_cap = t1.number_input("Account Balance (EGP)", value=100_000.0, step=10_000.0, key="single_cap")
        single_risk_pct = t2.slider("Risk Per Trade (% of Account)", 0.5, 3.0, 1.5, 0.25, key="single_risk_pct")
        single_entry = t3.number_input("Entry Price (EGP)", value=120.10, step=1.0, min_value=0.1, key="single_entry")
        single_stop = t4.number_input("Stop-Loss Price (EGP)", value=114.70, step=1.0, min_value=0.05, key="single_stop")

        if single_stop >= single_entry:
            st.error("Stop-loss price must be below the entry price for a long position.")
        else:
            dollar_risk = single_cap * (single_risk_pct / 100.0)
            risk_per_sh = single_entry - single_stop
            rec_shares = int(dollar_risk // risk_per_sh)
            trade_cost = round(rec_shares * single_entry, 2)
            actual_loss_at_stop = round(rec_shares * risk_per_sh, 2)
            actual_risk_pct = round((actual_loss_at_stop / single_cap) * 100.0, 2) if single_cap > 0 else 0.0

            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Maximum EGP Risk Budget", f"{dollar_risk:,.2f} EGP", f"{single_risk_pct}% of balance")
            r2.metric("Shares to Buy", f"{rec_shares:,} Shares", f"Risk/Sh: {risk_per_sh:.2f} EGP")
            r3.metric("Total Order Value", f"{trade_cost:,.2f} EGP", f"{(trade_cost/single_cap)*100:.1f}% of Account")
            r4.metric("Loss if Stop Hit", f"-{actual_loss_at_stop:,.2f} EGP", f"-{actual_risk_pct:.2f}% of Account", delta_color="inverse")


# =============================================================================
# 12. EMBEDDED UNIT TEST SUITE (Run via: pytest app.py)
# =============================================================================

def test_egx_market_schedule_sunday_to_thursday():
    friday = CAIRO_TZ.localize(datetime(2026, 10, 23, 11, 0))
    saturday = CAIRO_TZ.localize(datetime(2026, 10, 24, 11, 0))
    sunday_open = CAIRO_TZ.localize(datetime(2026, 10, 25, 11, 30))
    holiday = CAIRO_TZ.localize(datetime(2026, 10, 6, 11, 30))

    assert EGXMarketSchedule.is_trading_day(friday) is False
    assert EGXMarketSchedule.is_trading_day(saturday) is False
    assert EGXMarketSchedule.is_trading_day(sunday_open) is True
    assert EGXMarketSchedule.is_trading_day(holiday) is False
    assert EGXMarketSchedule.get_session_status(sunday_open)["status"] == "CONTINUOUS_TRADING"


def test_technical_indicators_accuracy():
    prov = SimulatedEGXDataProvider(seed=99)
    prov.connect()
    df = prov.get_historical_ohlcv("COMI.CA", lookback_days=210)
    bench = prov.get_egx30_benchmark(lookback_days=210)
    ind = TechnicalIndicators.compute_all_indicators(df, bench)

    assert "sma_200" in ind.columns
    assert "rsi_14" in ind.columns
    assert "rs_egx30" in ind.columns
    assert (ind["rsi_14"] >= 0.0).all() and (ind["rsi_14"] <= 100.0).all()
    assert (ind["atr_14"] > 0.0).all()


def test_signal_engine_and_backtester():
    prov = provider = YahooFinanceEGXProvider()
    prov.connect()
    quote = prov.get_latest_quote("COMI.CA")
    df = prov.get_historical_ohlcv("COMI.CA", lookback_days=220)
    ind = TechnicalIndicators.compute_all_indicators(df)

    engine = SignalEngine()
    sig = engine.evaluate_stock(quote, ind, liquidity_passed=True, liquidity_note="PASS (Liquid)")
    assert "category" in sig
    assert "risk_reward_ratio" in sig
    assert 0 <= sig["confidence_score"] <= 95

    bt = EGXStrategyBacktester(initial_capital=100_000.0)
    res = bt.run_simulation(df)
    assert "overall" in res
    assert "in_sample" in res
    assert "out_of_sample" in res


def test_portfolio_allocation_math():
    cap = 100_000.0
    weight = 22.0
    price = 120.10
    target_egp = cap * (weight / 100.0)
    shares = int(target_egp // price)
    actual_egp = shares * price

    assert shares == 183
    assert actual_egp <= target_egp
    assert (cap - actual_egp) >= 0.0
import yfinance as yf

class YahooFinanceEGXProvider(SimulatedEGXDataProvider):
    """
    يجلب البيانات الحقيقية للأسهم المصرية (.CA) من Yahoo Finance
    مع الحفاظ على نفس تصميم المنصة وحاسبة المحفظة.
    """
    def _init_histories(self) -> None:
        super()._init_histories()  # كاحتياطي لو تعذر الاتصال
        for ticker, (name, _) in EGX_UNIVERSE_SPEC.items():
            try:
                hist = yf.Ticker(ticker).history(period="1y", interval="1d")
                if not hist.empty and len(hist) >= 30:
                    df = pd.DataFrame({
                        "open": hist["Open"].round(2),
                        "high": hist["High"].round(2),
                        "low": hist["Low"].round(2),
                        "close": hist["Close"].round(2),
                        "volume": hist["Volume"].astype(int),
                    })
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    df["traded_value"] = (df["volume"] * df["close"]).round(2)
                    df = self.validate_ohlcv(df)
                    self._history_cache[ticker] = df

                    last = df.iloc[-1]
                    prev = df.iloc[-2]
                    self._quotes[ticker] = Quote(
                        ticker=ticker,
                        company_name=name,
                        last_price=float(last["close"]),
                        previous_close=float(prev["close"]),
                        open_price=float(last["open"]),
                        high_price=float(last["high"]),
                        low_price=float(last["low"]),
                        volume=int(last["volume"]),
                        traded_value_egp=float(last["traded_value"]),
                        bid_price=round(float(last["close"]) * 0.999, 2),
                        ask_price=round(float(last["close"]) * 1.001, 2),
                        bid_size=0,
                        ask_size=0,
                        timestamp=EGXMarketSchedule.get_cairo_now(),
                        is_realtime=False,
                        freshness_status="REAL_EOD_YFINANCE",
                        is_suspended=False,
                    )
            except Exception as exc:
                logger.warning(f"تعذر جلب الداتا الحقيقية لـ {ticker} وتم الاستبدال بالافتراضي: {exc}")

    def _step_intraday_quote(self, ticker: str) -> None:
        pass


if __name__ == "__main__":
    main_dashboard()
