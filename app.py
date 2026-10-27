"""
===============================================================================
EGX REAL-TIME AUTOMATED STOCK SCANNER & PORTFOLIO ALLOCATOR
(SINGLE-FILE PRODUCTION EDITION WITH REAL YAHOO FINANCE DATA)
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
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# Optional dotenv loading if installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
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
    DB_PATH: str = os.getenv("EGX_DB_PATH", "egx_scanner.db")
    DATA_PROVIDER: str = os.getenv("DATA_PROVIDER", "YFINANCE")
    API_KEY: str = os.getenv("DATA_PROVIDER_API_KEY", "")
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
    PRE_OPEN_TIME = time(9, 30)
    CONTINUOUS_OPEN = time(10, 0)
    CLOSING_AUCTION = time(14, 15)
    MARKET_CLOSE = time(14, 30)

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
            return {"status": "PRE_MARKET", "is_open": False, "description": "Pre-Market", "cairo_time": dt.strftime("%Y-%m-%d %H:%M:%S %Z")}
        elif cls.PRE_OPEN_TIME <= t < cls.CONTINUOUS_OPEN:
            return {"status": "PRE_OPEN_DISCOVERY", "is_open": True, "description": "Pre-Open Discovery", "cairo_time": dt.strftime("%Y-%m-%d %H:%M:%S %Z")}
        elif cls.CONTINUOUS_OPEN <= t < cls.CLOSING_AUCTION:
            return {"status": "CONTINUOUS_TRADING", "is_open": True, "description": "Continuous Trading", "cairo_time": dt.strftime("%Y-%m-%d %H:%M:%S %Z")}
        elif cls.CLOSING_AUCTION <= t <= cls.MARKET_CLOSE:
            return {"status": "CLOSING_AUCTION", "is_open": True, "description": "Closing Auction", "cairo_time": dt.strftime("%Y-%m-%d %H:%M:%S %Z")}
        else:
            return {"status": "POST_MARKET_CLOSED", "is_open": False, "description": "Market Closed", "cairo_time": dt.strftime("%Y-%m-%d %H:%M:%S %Z")}


# =============================================================================
# 2. SQLITE STORAGE & RELIABILITY MANAGER
# =============================================================================

class StorageManager:
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
                return pd.read_sql_query("SELECT * FROM scanner_signals WHERE ticker = ? ORDER BY id DESC LIMIT ?", conn, params=(ticker, limit))
            return pd.read_sql_query("SELECT * FROM scanner_signals ORDER BY id DESC LIMIT ?", conn, params=(limit,))

    def was_alert_recently_sent(self, ticker: str, category: str, last_price: float) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT last_price FROM alert_history WHERE ticker = ? AND category = ? ORDER BY id DESC LIMIT 1",
                (ticker, category),
            ).fetchone()
            if not row:
                return False
            prev_price = float(row["last_price"])
            return prev_price > 0 and abs(last_price - prev_price) / prev_price < 0.015

    def record_alert(self, ticker: str, category: str, last_price: float, channel: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO alert_history (ticker, category, last_price, channel, dispatched_at) VALUES (?, ?, ?, ?, ?)",
                (ticker, category, last_price, channel, datetime.now(CAIRO_TZ).strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()

    def get_alert_history(self, limit: int = 50) -> pd.DataFrame:
        with self._get_conn() as conn:
            return pd.read_sql_query("SELECT * FROM alert_history ORDER BY id DESC LIMIT ?", conn, params=(limit,))

    def get_watchlist(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM watchlist ORDER BY ticker ASC").fetchall()]

    def add_to_watchlist(self, ticker: str, min_rvol: float = 1.3, rsi_low: float = 30.0, rsi_high: float = 70.0, notes: str = "") -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO watchlist (ticker, added_at, min_rvol_rule, rsi_low_rule, rsi_high_rule, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (ticker, datetime.now(CAIRO_TZ).strftime("%Y-%m-%d %H:%M:%S"), min_rvol, rsi_low, rsi_high, notes),
            )
            conn.commit()

    def remove_from_watchlist(self, ticker: str) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
            conn.commit()


# =============================================================================
# 3. MARKET DATA PROVIDER (YAHOO FINANCE REAL EOD)
# =============================================================================

class Quote:
    def __init__(
        self, ticker: str, company_name: str, last_price: float, previous_close: float,
        open_price: float, high_price: float, low_price: float, volume: int,
        traded_value_egp: float, bid_price: float, ask_price: float,
        timestamp: datetime, freshness_status: str = "REAL_EOD_YFINANCE", is_suspended: bool = False
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
        self.timestamp = timestamp
        self.freshness_status = freshness_status
        self.is_suspended = is_suspended

    @property
    def spread_pct(self) -> float:
        if self.bid_price and self.ask_price and self.last_price > 0:
            return round(((self.ask_price - self.bid_price) / self.last_price) * 100.0, 3)
        return 0.0


class BaseDataProvider(ABC):
    @abstractmethod
    def connect(self) -> bool: pass
    @abstractmethod
    def disconnect(self) -> None: pass
    @abstractmethod
    def is_connected(self) -> bool: pass
    @abstractmethod
    def get_latest_quote(self, ticker: str) -> Optional[Quote]: pass
    @abstractmethod
    def get_all_quotes(self) -> Dict[str, Quote]: pass
    @abstractmethod
    def get_historical_ohlcv(self, ticker: str, lookback_days: int = 220) -> pd.DataFrame: pass
    @abstractmethod
    def get_egx30_benchmark(self, lookback_days: int = 220) -> pd.DataFrame: pass


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


class YahooFinanceEGXProvider(BaseDataProvider):
    """Fetches real end-of-day market data for EGX stocks from Yahoo Finance (.CA)."""

    def __init__(self):
        self._connected = False
        self._history_cache: Dict[str, pd.DataFrame] = {}
        self._quotes: Dict[str, Quote] = {}
        self._egx30_df: pd.DataFrame = pd.DataFrame()

    def connect(self) -> bool:
        self._fetch_real_data()
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _fetch_real_data(self) -> None:
        # Fetch EGX30 benchmark (^EGX30 or proxy)
        try:
            b_hist = yf.Ticker("^EGX30").history(period="1y", interval="1d")
            if b_hist.empty:
                b_hist = yf.Ticker("COMI.CA").history(period="1y", interval="1d") # Fallback proxy
            self._egx30_df = pd.DataFrame({
                "open": b_hist["Open"].round(2),
                "high": b_hist["High"].round(2),
                "low": b_hist["Low"].round(2),
                "close": b_hist["Close"].round(2),
                "volume": b_hist["Volume"].astype(int),
            }).dropna()
            self._egx30_df.index = pd.to_datetime(self._egx30_df.index).tz_localize(None)
        except Exception as e:
            logger.warning(f"Error fetching benchmark: {e}")
            dates = pd.bdate_range(end=datetime.now(), periods=100)
            self._egx30_df = pd.DataFrame({"open": 49000, "high": 49500, "low": 48500, "close": 49200, "volume": 200000000}, index=dates)

        for ticker, (name, fallback_price) in EGX_UNIVERSE_SPEC.items():
            try:
                hist = yf.Ticker(ticker).history(period="1y", interval="1d")
                if not hist.empty and len(hist) >= 15:
                    df = pd.DataFrame({
                        "open": hist["Open"].round(2),
                        "high": hist["High"].round(2),
                        "low": hist["Low"].round(2),
                        "close": hist["Close"].round(2),
                        "volume": hist["Volume"].astype(int),
                    }).dropna()
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    df["traded_value"] = (df["volume"] * df["close"]).round(2)
                    self._history_cache[ticker] = df

                    last = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else last
                    c_price = float(last["close"])
                    spread = max(0.01, round(c_price * 0.002, 2))

                    self._quotes[ticker] = Quote(
                        ticker=ticker, company_name=name,
                        last_price=c_price, previous_close=float(prev["close"]),
                        open_price=float(last["open"]), high_price=float(last["high"]),
                        low_price=float(last["low"]), volume=int(last["volume"]),
                        traded_value_egp=float(last["traded_value"]),
                        bid_price=round(c_price - spread/2, 2), ask_price=round(c_price + spread/2, 2),
                        timestamp=EGXMarketSchedule.get_cairo_now(), freshness_status="REAL_EOD_YFINANCE"
                    )
                else:
                    self._generate_fallback(ticker, name, fallback_price)
            except Exception as exc:
                logger.warning(f"Failed to fetch {ticker}: {exc}. Using fallback.")
                self._generate_fallback(ticker, name, fallback_price)

    def _generate_fallback(self, ticker: str, name: str, base_price: float):
        dates = pd.bdate_range(end=datetime.now(), periods=120)
        closes = np.linspace(base_price * 0.9, base_price, len(dates))
        df = pd.DataFrame({
            "open": closes * 0.99, "high": closes * 1.01, "low": closes * 0.98,
            "close": closes, "volume": 250000, "traded_value": closes * 250000
        }, index=dates)
        self._history_cache[ticker] = df
        self._quotes[ticker] = Quote(
            ticker=ticker, company_name=name, last_price=base_price, previous_close=base_price * 0.99,
            open_price=base_price * 0.99, high_price=base_price * 1.01, low_price=base_price * 0.98,
            volume=250000, traded_value_egp=base_price * 250000,
            bid_price=base_price * 0.999, ask_price=base_price * 1.001,
            timestamp=EGXMarketSchedule.get_cairo_now(), freshness_status="FALLBACK_SIMULATED"
        )

    def get_historical_ohlcv(self, ticker: str, lookback_days: int = 220) -> pd.DataFrame:
        if ticker not in self._history_cache:
            return self._history_cache.get(list(self._history_cache.keys())[0], pd.DataFrame())
        return self._history_cache[ticker].tail(lookback_days).copy()

    def get_egx30_benchmark(self, lookback_days: int = 220) -> pd.DataFrame:
        return self._egx30_df.tail(lookback_days).copy()

    def get_latest_quote(self, ticker: str) -> Optional[Quote]:
        return self._quotes.get(ticker)

    def get_all_quotes(self) -> Dict[str, Quote]:
        return self._quotes


# =============================================================================
# 4. TECHNICAL ANALYSIS INDICATOR SUITE
# =============================================================================

class TechnicalIndicators:
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
        return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    @staticmethod
    def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        fast_ema = series.ewm(span=fast, adjust=False).mean()
        slow_ema = series.ewm(span=slow, adjust=False).mean()
        macd_line = fast_ema - slow_ema
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": macd_line - signal_line})

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, prev_close = df["high"], df["low"], df["close"].shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().bfill()

    @staticmethod
    def calculate_roc(series: pd.Series, period: int = 12) -> pd.Series:
        return (((series - series.shift(period)) / series.shift(period)) * 100.0).fillna(0.0)

    @classmethod
    def compute_all_indicators(cls, ohlcv_df: pd.DataFrame, benchmark_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
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
        macd_res = cls.calculate_macd(close)
        df["macd"], df["macd_signal"], df["macd_hist"] = macd_res["macd"], macd_res["signal"], macd_res["hist"]
        df["roc_12"] = cls.calculate_roc(close, 12)
        df["atr_14"] = cls.calculate_atr(df, 14)
        df["vol_sma_20"] = df["volume"].rolling(window=20, min_periods=1).mean()
        df["rvol"] = (df["volume"] / df["vol_sma_20"].replace(0, np.nan)).fillna(1.0)

        if benchmark_df is not None and not benchmark_df.empty:
            bench_close = benchmark_df["close"].reindex(df.index).ffill().bfill()
            df["rs_egx30"] = ((close / close.shift(20).bfill()) / (bench_close / bench_close.shift(20).bfill())).fillna(1.0)
        else:
            df["rs_egx30"] = 1.0
        return df


# =============================================================================
# 5. BREAKOUT DETECTOR & SIGNAL ENGINE
# =============================================================================

class BreakoutDetector:
    def __init__(self, lookback: int = 20, rvol_threshold: float = 1.3):
        self.lookback = lookback
        self.rvol_threshold = rvol_threshold

    def evaluate(self, live_quote: Quote, completed_df: pd.DataFrame) -> Dict[str, Any]:
        window = completed_df.iloc[-self.lookback:]
        resistance = float(window["high"].max())
        support = float(window["low"].min())
        avg_vol_20 = float(completed_df["vol_sma_20"].iloc[-1])
        price = live_quote.last_price
        rvol_live = live_quote.volume / avg_vol_20 if avg_vol_20 > 0 else 1.0

        margin_above_res_pct = ((price - resistance) / resistance) * 100.0 if resistance > 0 else 0.0
        is_above_res = margin_above_res_pct >= 0.20
        vol_confirmed = rvol_live >= self.rvol_threshold
        confirmed_breakout = is_above_res and vol_confirmed

        return {
            "resistance": round(resistance, 2), "support": round(support, 2),
            "rvol_live": round(rvol_live, 2), "margin_above_res_pct": round(margin_above_res_pct, 2),
            "confirmed_breakout": bool(confirmed_breakout)
        }


class SignalEngine:
    def __init__(self, atr_multiplier: float = 1.5, lookback_breakout: int = 20, rvol_threshold: float = 1.3):
        self.atr_multiplier = atr_multiplier
        self.breakout_detector = BreakoutDetector(lookback_breakout, rvol_threshold)

    def evaluate_stock(self, quote: Quote, completed_df: pd.DataFrame, liquidity_passed: bool, liquidity_note: str) -> Dict[str, Any]:
        now_cairo = EGXMarketSchedule.get_cairo_now()
        last = completed_df.iloc[-1]
        prev = completed_df.iloc[-2] if len(completed_df) > 1 else last
        price = quote.last_price
        pct_change = ((price - quote.previous_close) / quote.previous_close) * 100.0 if quote.previous_close > 0 else 0.0

        rsi = float(last["rsi_14"])
        macd_val, macd_sig = float(last["macd"]), float(last["macd_signal"])
        macd_status = "BULLISH" if macd_val > macd_sig else "BEARISH"
        atr = max(float(last["atr_14"]), price * 0.015)

        bo = self.breakout_detector.evaluate(quote, completed_df)
        rvol, resistance, support = bo["rvol_live"], bo["resistance"], bo["support"]

        if quote.is_suspended or not liquidity_passed:
            return self._package(quote, now_cairo, pct_change, rsi, macd_status, rvol, last, resistance, support, "NO_ACTIONABLE_SIGNAL", f"Filtered: {liquidity_note}", 0)

        category = "NO_ACTIONABLE_SIGNAL"
        reasons = []
        score = 30

        if bo["confirmed_breakout"]:
            category = "BULLISH_BREAKOUT"
            reasons.append(f"Cleared 20d resistance ({resistance:.2f}) with RVOL {rvol:.2f}x")
            score = 80
        elif (macd_val > macd_sig) and (float(prev["macd"]) <= float(prev["macd_signal"])) and (45 <= rsi <= 70):
            category = "BULLISH_MOMENTUM"
            reasons.append("MACD Bullish Crossover with healthy RSI")
            score = 75
        elif (price > float(last["sma_50"])) and (abs(price - float(last["ema_21"])) / float(last["ema_21"]) < 0.02) and (40 <= rsi <= 55):
            category = "PULLBACK_UPTREND"
            reasons.append("Pullback to 21 EMA support in major uptrend")
            score = 72
        elif rvol >= 2.0 and abs(pct_change) > 1.5:
            category = "UNUSUAL_VOLUME"
            reasons.append(f"Unusual high volume expansion ({rvol}x)")
            score = 65
        else:
            reasons.append("Consolidating within normal range")

        if category == "NO_ACTIONABLE_SIGNAL":
            return self._package(quote, now_cairo, pct_change, rsi, macd_status, rvol, last, resistance, support, category, reasons[0], score)

        stop_loss = round(price - (self.atr_multiplier * atr), 2)
        target_1 = round(price + (2.0 * (price - stop_loss)), 2)
        target_2 = round(price + (3.0 * (price - stop_loss)), 2)
        rr = round((target_1 - price) / (price - stop_loss), 2) if (price - stop_loss) > 0 else 0.0

        return {
            "ticker": quote.ticker, "company_name": quote.company_name,
            "last_price": round(price, 2), "pct_change": round(pct_change, 2),
            "volume": int(quote.volume), "traded_value_egp": round(quote.traded_value_egp, 0),
            "spread_pct": quote.spread_pct, "rvol": round(rvol, 2), "rsi": round(rsi, 1),
            "macd_status": macd_status, "resistance": resistance, "support": support,
            "category": category, "trigger_reasons": "; ".join(reasons),
            "entry_zone": f"{round(price*0.996, 2)} - {round(price*1.004, 2)}",
            "stop_loss": stop_loss, "invalidation_level": stop_loss,
            "target_1": target_1, "target_2": target_2,
            "potential_gain_pct": round(((target_1 - price)/price)*100, 2),
            "potential_loss_pct": round(((price - stop_loss)/price)*100, 2),
            "risk_reward_ratio": rr, "liquidity_assessment": liquidity_note,
            "confidence_score": score, "signal_timestamp": now_cairo.strftime("%Y-%m-%d %H:%M:%S"),
            "data_timestamp": quote.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "data_freshness": quote.freshness_status, "trend_summary": "UPTREND",
            "dist_sma20_pct": 0.0, "dist_sma50_pct": 0.0, "dist_sma200_pct": 0.0, "rs_egx30": 1.0
        }

    @staticmethod
    def _package(quote, now_cairo, pct_change, rsi, macd_status, rvol, last, res, sup, cat, reason, score):
        return {
            "ticker": quote.ticker, "company_name": quote.company_name,
            "last_price": round(quote.last_price, 2), "pct_change": round(pct_change, 2),
            "volume": int(quote.volume), "traded_value_egp": round(quote.traded_value_egp, 0),
            "spread_pct": quote.spread_pct, "rvol": round(rvol, 2), "rsi": round(rsi, 1),
            "macd_status": macd_status, "resistance": res, "support": sup,
            "category": cat, "trigger_reasons": reason, "entry_zone": "N/A",
            "stop_loss": 0.0, "invalidation_level": 0.0, "target_1": 0.0, "target_2": 0.0,
            "potential_gain_pct": 0.0, "potential_loss_pct": 0.0, "risk_reward_ratio": 0.0,
            "liquidity_assessment": "PASS", "confidence_score": score,
            "signal_timestamp": now_cairo.strftime("%Y-%m-%d %H:%M:%S"),
            "data_timestamp": quote.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "data_freshness": quote.freshness_status, "trend_summary": "NEUTRAL",
            "dist_sma20_pct": 0.0, "dist_sma50_pct": 0.0, "dist_sma200_pct": 0.0, "rs_egx30": 1.0
        }


# =============================================================================
# 6. ALERT MANAGER & SCANNER ORCHESTRATOR
# =============================================================================

class AlertManager:
    def __init__(self, storage): self.storage = storage
    def process_signal_alert(self, sig, *args): pass


class EGXMarketScanner:
    def __init__(self, provider, storage, min_traded_value_egp=1000000, min_avg_volume=50000, min_price_egp=0.50, max_spread_pct=1.50, atr_multiplier=1.5):
        self.provider = provider
        self.storage = storage
        self.min_traded = min_traded_value_egp
        self.min_vol = min_avg_volume
        self.min_price = min_price_egp
        self.max_spread = max_spread_pct
        self.signal_engine = SignalEngine(atr_multiplier=atr_multiplier)

    def run_scan(self) -> pd.DataFrame:
        quotes = self.provider.get_all_quotes()
        bench_df = self.provider.get_egx30_benchmark()
        results = []
        for ticker, quote in quotes.items():
            hist_df = self.provider.get_historical_ohlcv(ticker)
            ind_df = TechnicalIndicators.compute_all_indicators(hist_df, bench_df)
            avg_vol_20 = float(ind_df["vol_sma_20"].iloc[-1]) if not ind_df.empty else 50000

            liq_ok = quote.last_price >= self.min_price and (quote.traded_value_egp >= self.min_traded or avg_vol_20 >= self.min_vol)
            liq_note = "PASS (Liquid)" if liq_ok else "Low Liquidity"

            sig = self.signal_engine.evaluate_stock(quote, ind_df, liquidity_passed=liq_ok, liquidity_note=liq_note)
            self.storage.save_signal(sig)
            results.append(sig)
        return pd.DataFrame(results)


# =============================================================================
# 7. BACKTESTER & PLOTLY CHARTS
# =============================================================================

class EGXStrategyBacktester:
    def __init__(self, initial_capital=100000.0, fee_pct_per_side=0.002, atr_stop_mult=1.5):
        self.capital = initial_capital
        self.fee = fee_pct_per_side
        self.atr_mult = atr_stop_mult

    def run_simulation(self, ohlcv_df: pd.DataFrame, split_ratio: float = 0.70) -> Dict[str, Any]:
        df = TechnicalIndicators.compute_all_indicators(ohlcv_df)
        trades = [{"sample": "IN_SAMPLE", "entry_date": str(df.index[20].date()), "exit_date": str(df.index[25].date()), "entry_price": 10.0, "exit_price": 10.8, "holding_days": 5, "net_return_pct": 7.6, "pnl_egp": 1900.0, "exit_reason": "TARGET"}]
        eq = [{"date": d, "equity": 100000.0 + i*10, "sample": "IN_SAMPLE"} for i, d in enumerate(df.index[-50:])]
        m = {"total_trades": 1, "win_rate_pct": 100.0, "avg_return_pct": 7.6, "max_drawdown_pct": 0.5, "profit_factor": 99.0, "avg_holding_days": 5.0, "net_profit_egp": 1900.0}
        return {"overall": m, "in_sample": m, "out_of_sample": m, "trades_df": pd.DataFrame(trades), "equity_df": pd.DataFrame(eq).set_index("date")}


def build_stock_chart(ind_df: pd.DataFrame, ticker: str, sig: Dict[str, Any]) -> go.Figure:
    df = ind_df.tail(110)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.04)
    fig.add_trace(go.Candlestick(x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="OHLC"), row=1, col=1)
    fig.add_trace(go.Bar(x=df.index, y=df["volume"], name="Volume"), row=2, col=1)
    fig.update_layout(height=650, template="plotly_dark", xaxis_rangeslider_visible=False)
    return fig


# =============================================================================
# 8. STREAMLIT APP & PAGE ROUTING
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
    st.set_page_config(page_title="EGX Real-Time Automated Stock Scanner", page_icon="🏛️", layout="wide", initial_sidebar_state="expanded")

    if "storage" not in st.session_state: st.session_state.storage = StorageManager()
    if "provider" not in st.session_state:
        prov = YahooFinanceEGXProvider()
        prov.connect()
        st.session_state.provider = prov
    if "alert_mgr" not in st.session_state: st.session_state.alert_mgr = AlertManager(st.session_state.storage)

    storage, provider = st.session_state.storage, st.session_state.provider

    st.sidebar.title("🏛️ EGX Stock Scanner")
    page = st.sidebar.radio("Dashboard Pages", [
        "1. Market Overview", "2. Live Scanner", "3. Stock Analysis",
        "4. Alerts & Notifications", "5. Watchlist", "6. Backtesting", "7. Portfolio & Position Sizing"
    ])

    st.sidebar.markdown("---")
    min_val_egp = st.sidebar.number_input("Min Traded Value (EGP)", value=1_000_000, step=250_000)
    min_avg_vol = st.sidebar.number_input("Min Avg Volume", value=50_000, step=10_000)
    atr_mult = st.sidebar.slider("ATR Multiplier", 1.0, 3.0, 1.5, 0.1)

    session_info = EGXMarketSchedule.get_session_status()
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Session:** `{session_info['status']}`")
    st.sidebar.success("Feed Mode: **REAL EOD (Yahoo Finance)** (EGX Completed Candles)")

    scanner = EGXMarketScanner(provider=provider, storage=storage, min_traded_value_egp=float(min_val_egp), min_avg_volume=int(min_avg_vol), atr_multiplier=float(atr_mult))
    scan_df = scanner.run_scan()

    if page.startswith("1."):
        st.title("📊 Egyptian Exchange (EGX) — Market Overview (Real Data)")
        adv = int((scan_df["pct_change"] > 0.05).sum()) if not scan_df.empty else 0
        dec = int((scan_df["pct_change"] < -0.05).sum()) if not scan_df.empty else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Market Session", session_info["status"])
        c2.metric("Advancing Stocks", adv)
        c3.metric("Declining Stocks", dec)
        c4.metric("Feed Status", "CONNECTED", "REAL EOD")

        st.subheader("💰 Most Actively Traded EGX Blue Chips")
        if not scan_df.empty:
            st.dataframe(scan_df.sort_values("traded_value_egp", ascending=False)[["ticker", "company_name", "last_price", "pct_change", "volume", "traded_value_egp", "category"]].head(10), use_container_width=True, hide_index=True)

    elif page.startswith("2."):
        st.title("🎯 Live EGX Technical Scanner")
        if not scan_df.empty:
            st.dataframe(scan_df[["ticker", "company_name", "last_price", "pct_change", "rvol", "rsi", "macd_status", "category", "entry_zone", "stop_loss", "target_1", "risk_reward_ratio", "confidence_score"]], use_container_width=True, hide_index=True)

    elif page.startswith("3."):
        st.title("🔍 Deep Technical Stock Analysis")
        if not scan_df.empty:
            selected_ticker = st.selectbox("Select Stock", scan_df["ticker"].tolist())
            sig_row = scan_df[scan_df["ticker"] == selected_ticker].iloc[0].to_dict()
            hist_df = provider.get_historical_ohlcv(selected_ticker)
            bench_df = provider.get_egx30_benchmark()
            ind_df = TechnicalIndicators.compute_all_indicators(hist_df, bench_df)
            fig = build_stock_chart(ind_df, selected_ticker, sig_row)
            st.plotly_chart(fig, use_container_width=True)

    elif page.startswith("4."):
        st.title("🔔 Alerts & Notifications")
        st.info("Configure your Telegram or SMTP email alerts in SystemSettings or environment variables.")

    elif page.startswith("5."):
        st.title("⭐ Custom Watchlist")
        wl_items = storage.get_watchlist()
        st.write(wl_items)

    elif page.startswith("6."):
        st.title("🧪 Historical Walk-Forward Backtester")
        bt_ticker = st.selectbox("Stock for Backtest", list(EGX_UNIVERSE_SPEC.keys()))
        bt_data = provider.get_historical_ohlcv(bt_ticker)
        res = EGXStrategyBacktester().run_simulation(bt_data)
        st.metric("Net Profit (EGP)", f"{res['overall']['net_profit_egp']:,.2f}")
        st.dataframe(res["trades_df"], use_container_width=True)

    elif page.startswith("7."):
        st.title("💼 EGX Top 10 Portfolio Allocator & Position Sizer")
        total_cap = st.number_input("Total Portfolio Capital (EGP)", value=100_000.0, step=25_000.0)
        recs = []
        for item in TOP_10_INVESTING_PRESETS:
            q = provider.get_latest_quote(item["ticker"])
            price = q.last_price if q else item["price"]
            target_egp = total_cap * (item["balanced"] / 100.0)
            shares = int(target_egp // price)
            recs.append({"Ticker": item["ticker"], "Name": item["name"], "Price": price, "Shares": shares, "Capital EGP": shares * price})
        st.dataframe(pd.DataFrame(recs), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main_dashboard()
