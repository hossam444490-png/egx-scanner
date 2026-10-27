<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EGX Real-Time Automated Stock Scanner</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
  <style>
    body { background-color: #0b0f19; color: #e2e8f0; font-family: system-ui, -apple-system, sans-serif; }
    .card { background-color: #111827; border: 1px solid #1f2937; border-radius: 0.75rem; }
    .badge-bull { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }
    .badge-bear { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }
    .badge-vol { background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); }
    .badge-neutral { background: rgba(156, 163, 175, 0.12); color: #9ca3af; }
  </style>
</head>
<body class="min-h-screen flex flex-col">

  <!-- Top Header -->
  <header class="border-b border-gray-800 bg-gray-900/80 backdrop-blur sticky top-0 z-50 px-6 py-3 flex flex-wrap justify-between items-center gap-4">
    <div class="flex items-center space-x-3">
      <span class="text-2xl">🏛️</span>
      <div>
        <h1 class="text-lg font-bold tracking-tight text-white">EGX Quantitative Stock Scanner</h1>
        <p class="text-xs text-gray-400">Egyptian Exchange • Technical & Liquidity Analytics Engine</p>
      </div>
    </div>

    <div class="flex items-center space-x-6 text-xs">
      <div class="card px-3 py-1.5 flex items-center space-x-2">
        <span id="session-dot" class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></span>
        <div>
          <div class="text-gray-400">EGX Session (Africa/Cairo)</div>
          <div id="cairo-clock" class="font-mono font-semibold text-white">Loading...</div>
        </div>
      </div>

      <div class="card px-3 py-1.5">
        <div class="text-gray-400">Feed Mode</div>
        <div class="font-mono font-semibold text-amber-400">SIMULATED (GBM Micro-Tick)</div>
      </div>
    </div>
  </header>

  <!-- Main Content Container -->
  <main class="flex-1 max-w-7xl w-full mx-auto px-4 py-6 space-y-6">

    <!-- Market Breadth Summary -->
    <section class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="card p-4">
        <div class="text-xs text-gray-400 uppercase">Advancing Stocks</div>
        <div id="metric-adv" class="text-2xl font-bold text-emerald-400 mt-1">0</div>
        <div class="text-xs text-gray-500 mt-1">EGX30 Active Universe</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-gray-400 uppercase">Declining Stocks</div>
        <div id="metric-dec" class="text-2xl font-bold text-rose-400 mt-1">0</div>
        <div class="text-xs text-gray-500 mt-1">EGX30 Active Universe</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-gray-400 uppercase">Actionable Signals</div>
        <div id="metric-sig" class="text-2xl font-bold text-sky-400 mt-1">0</div>
        <div class="text-xs text-gray-500 mt-1">High-Conviction Setups</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-gray-400 uppercase">Total Traded Value</div>
        <div id="metric-val" class="text-2xl font-bold text-white mt-1">0M EGP</div>
        <div class="text-xs text-gray-500 mt-1">Simulated Intraday Turnover</div>
      </div>
    </section>

    <!-- Controls & Filters -->
    <section class="card p-4 flex flex-wrap items-center justify-between gap-4">
      <div class="flex flex-wrap items-center gap-4">
        <div>
          <label class="block text-xs text-gray-400 mb-1">Signal Category Filter</label>
          <select id="filter-category" class="bg-gray-800 border border-gray-700 text-sm rounded px-3 py-1.5 text-white">
            <option value="ALL">All Scanned Stocks (20)</option>
            <option value="ACTIONABLE">All Actionable Signals Only</option>
            <option value="BULLISH_BREAKOUT">Bullish Breakout</option>
            <option value="BULLISH_MOMENTUM">Bullish Momentum</option>
            <option value="PULLBACK_UPTREND">Pullback in Uptrend</option>
            <option value="UNUSUAL_VOLUME">Unusual Volume</option>
            <option value="BEARISH_BREAKDOWN">Bearish Breakdown</option>
          </select>
        </div>

        <div>
          <label class="block text-xs text-gray-400 mb-1">Min Traded Value (EGP)</label>
          <input id="filter-val" type="number" value="1000000" step="500000" class="bg-gray-800 border border-gray-700 text-sm rounded px-3 py-1.5 text-white w-36" />
        </div>

        <div>
          <label class="block text-xs text-gray-400 mb-1">Scan Interval</label>
          <select id="scan-speed" class="bg-gray-800 border border-gray-700 text-sm rounded px-3 py-1.5 text-white">
            <option value="3000">Every 3 Seconds</option>
            <option value="5000">Every 5 Seconds</option>
            <option value="10000">Every 10 Seconds</option>
          </select>
        </div>
      </div>

      <div class="flex items-center gap-2">
        <button onclick="runScannerSweep()" class="bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium px-4 py-2 rounded transition">
          ⚡ Force Scan Sweep Now
        </button>
      </div>
    </section>

    <!-- Live Scanner Table -->
    <section class="card overflow-hidden">
      <div class="px-4 py-3 border-b border-gray-800 flex justify-between items-center">
        <h2 class="font-semibold text-sm text-white">🎯 Live EGX Technical Scanner Table (Click any row to inspect chart & backtest)</h2>
        <span id="last-scan-time" class="text-xs text-gray-400 font-mono"></span>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-left border-collapse text-xs">
          <thead>
            <tr class="bg-gray-800/60 text-gray-300 uppercase">
              <th class="py-3 px-3">Ticker</th>
              <th class="py-3 px-3">Last (EGP)</th>
              <th class="py-3 px-3">Chg %</th>
              <th class="py-3 px-3">RVOL</th>
              <th class="py-3 px-3">RSI (14)</th>
              <th class="py-3 px-3">MACD</th>
              <th class="py-3 px-3">Trend</th>
              <th class="py-3 px-3">Signal Type</th>
              <th class="py-3 px-3">Entry Zone</th>
              <th class="py-3 px-3">Stop Loss</th>
              <th class="py-3 px-3">Target 1</th>
              <th class="py-3 px-3">R:R</th>
              <th class="py-3 px-3">Score</th>
            </tr>
          </thead>
          <tbody id="scanner-tbody" class="divide-y divide-gray-800 font-mono"></tbody>
        </table>
      </div>
    </section>

    <!-- Deep Stock Analysis & Backtester Panel -->
    <section class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <!-- Interactive Chart -->
      <div class="card p-4 lg:col-span-2">
        <div class="flex justify-between items-center mb-3">
          <div>
            <h3 id="chart-title" class="text-base font-bold text-white">COMI.CA - Commercial International Bank</h3>
            <p id="chart-reason" class="text-xs text-emerald-400 mt-0.5"></p>
          </div>
          <span class="text-xs text-gray-400">Candlesticks + EMA 9/21 + SMA 50 + SL/Target Overlays</span>
        </div>
        <div id="plotly-chart" class="w-full h-[460px]"></div>
      </div>

      <!-- Setup Card & Walk-Forward Backtest -->
      <div class="space-y-6">
        <div class="card p-4">
          <h3 class="text-sm font-bold text-white border-b border-gray-800 pb-2 mb-3">📐 Active Trade Setup</h3>
          <div id="setup-details" class="space-y-2 text-xs"></div>
          <p class="text-[11px] text-gray-500 mt-3 italic">
            Disclaimer: Calculated technical levels only. Not guaranteed execution prices. Accounts for EGX +/-20% circuit limits.
          </p>
        </div>

        <div class="card p-4">
          <h3 class="text-sm font-bold text-white border-b border-gray-800 pb-2 mb-3">🧪 120-Day Walk-Forward Backtest</h3>
          <p class="text-xs text-gray-400 mb-3">Includes 0.20% EGX brokerage/MCDR fees + 0.10% slippage per leg.</p>
          <div id="backtest-metrics" class="grid grid-cols-2 gap-3 text-xs"></div>
        </div>
      </div>
    </section>

  </main>

  <script>
    // =========================================================================
    // 1. EGX30 UNIVERSE & HISTORICAL SEED GENERATOR
    // =========================================================================
    const EGX_STOCKS = [
      { ticker: "COMI.CA", name: "Commercial International Bank", base: 88.50 },
      { ticker: "TMGH.CA", name: "Talaat Moustafa Group", base: 62.00 },
      { ticker: "FWRY.CA", name: "Fawry Banking Tech", base: 6.85 },
      { ticker: "ETEL.CA", name: "Telecom Egypt", base: 42.10 },
      { ticker: "EAST.CA", name: "Eastern Company", base: 24.30 },
      { ticker: "SWDY.CA", name: "Elsewedy Electric", base: 46.50 },
      { ticker: "HRHO.CA", name: "EFG Holding", base: 18.20 },
      { ticker: "MFPC.CA", name: "MOPCO Fertilizers", base: 52.00 },
      { ticker: "ABUK.CA", name: "Abu Qir Fertilizers", base: 63.40 },
      { ticker: "ESRS.CA", name: "Ezz Steel", base: 95.00 },
      { ticker: "ORAS.CA", name: "Orascom Construction", base: 260.00 },
      { ticker: "AMOC.CA", name: "Alexandria Mineral Oils", base: 9.80 },
      { ticker: "SKPC.CA", name: "Sidi Kerir Petrochemicals", base: 28.50 },
      { ticker: "CIEB.CA", name: "Credit Agricole Egypt", base: 21.00 },
      { ticker: "ISPH.CA", name: "Ibnsina Pharma", base: 3.45 },
      { ticker: "JUFO.CA", name: "Juhayna Food Industries", base: 18.90 },
      { ticker: "HELI.CA", name: "Heliopolis Housing", base: 11.20 },
      { ticker: "MNHD.CA", name: "Madinet Masr Housing", base: 4.15 },
      { ticker: "EKHO.CA", name: "Egypt Kuwait Holding", base: 1.18 },
      { ticker: "CCAP.CA", name: "Qalaa Holdings", base: 3.65 }
    ];

    const marketState = {};
    let selectedTicker = "COMI.CA";
    let timerId = null;

    function initMarketHistories() {
      const now = new Date();
      EGX_STOCKS.forEach((s, idx) => {
        const candles = [];
        let p = s.base * 0.88;
        for (let i = 120; i >= 1; i--) {
          const d = new Date(now);
          d.setDate(d.getDate() - i);
          if (d.getDay() === 5 || d.getDay() === 6) continue; // Skip Fri/Sat on EGX
          const drift = (idx % 3 === 0) ? 0.0025 : 0.0005;
          const change = (Math.random() - 0.48 + drift) * 0.032;
          const close = Math.max(0.5, +(p * (1 + change)).toFixed(2));
          const high = +(Math.max(p, close) * (1 + Math.random() * 0.015)).toFixed(2);
          const low = +(Math.min(p, close) * (1 - Math.random() * 0.015)).toFixed(2);
          const open = +p.toFixed(2);
          const vol = Math.floor(150000 + Math.random() * 650000);
          candles.push({ date: d.toISOString().split('T')[0], open, high, low, close, volume: vol });
          p = close;
        }
        const last = candles[candles.length - 1];
        const prev = candles[candles.length - 2];
        marketState[s.ticker] = {
          ...s,
          candles,
          prevClose: prev.close,
          lastPrice: last.close,
          high: last.high,
          low: last.low,
          volume: last.volume,
          tradedValue: +(last.volume * last.close).toFixed(0)
        };
      });
    }

    // =========================================================================
    // 2. TECHNICAL INDICATORS ENGINE (EMA, SMA, RSI, MACD, ATR)
    // =========================================================================
    function calcEMA(values, period) {
      const k = 2 / (period + 1);
      let ema = [values[0]];
      for (let i = 1; i < values.length; i++) {
        ema.push(values[i] * k + ema[i - 1] * (1 - k));
      }
      return ema;
    }

    function calcSMA(values, period) {
      return values.map((_, idx) => {
        if (idx < period - 1) return values[idx];
        const slice = values.slice(idx - period + 1, idx + 1);
        return slice.reduce((a, b) => a + b, 0) / period;
      });
    }

    function calcRSI(closes, period = 14) {
      let gains = 0, losses = 0;
      for (let i = 1; i <= period && i < closes.length; i++) {
        const diff = closes[i] - closes[i - 1];
        if (diff >= 0) gains += diff; else losses -= diff;
      }
      let avgGain = gains / period;
      let avgLoss = losses / period;
      for (let i = period + 1; i < closes.length; i++) {
        const diff = closes[i] - closes[i - 1];
        avgGain = (avgGain * (period - 1) + (diff > 0 ? diff : 0)) / period;
        avgLoss = (avgLoss * (period - 1) + (diff < 0 ? -diff : 0)) / period;
      }
      if (avgLoss === 0) return 100;
      const rs = avgGain / avgLoss;
      return +(100 - (100 / (1 + rs))).toFixed(1);
    }

    function analyzeStock(stock) {
      const candles = stock.candles;
      const closes = candles.map(c => c.close);
      const highs = candles.map(c => c.high);
      const lows = candles.map(c => c.low);
      const vols = candles.map(c => c.volume);

      const ema9Arr = calcEMA(closes, 9);
      const ema21Arr = calcEMA(closes, 21);
      const sma50Arr = calcSMA(closes, 50);
      const ema12 = calcEMA(closes, 12);
      const ema26 = calcEMA(closes, 26);
      const macdLineArr = ema12.map((v, i) => v - ema26[i]);
      const sigLineArr = calcEMA(macdLineArr, 9);

      const n = closes.length - 1;
      const price = stock.lastPrice;
      const ema9 = ema9Arr[n];
      const ema21 = ema21Arr[n];
      const sma50 = sma50Arr[n];
      const rsi = calcRSI(closes, 14);
      const macdBull = macdLineArr[n] > sigLineArr[n];
      const macdCross = macdBull && (macdLineArr[n - 1] <= sigLineArr[n - 1]);

      const avgVol20 = vols.slice(-20).reduce((a, b) => a + b, 0) / 20;
      const rvol = +(stock.volume / avgVol20).toFixed(2);

      const swingHigh20 = Math.max(...highs.slice(-21, -1));
      const swingLow20 = Math.min(...lows.slice(-21, -1));
      const atr = +(price * 0.024).toFixed(2);

      const uptrend = price > sma50 && ema9 > ema21;
      const downtrend = price < sma50 && ema9 < ema21;
      const trend = uptrend ? "UPTREND" : downtrend ? "DOWNTREND" : "SIDEWAYS";

      let category = "NO_ACTIONABLE_SIGNAL";
      let reason = "Consolidating within normal technical range.";
      let score = 35;

      if (price > swingHigh20 * 1.002 && rvol >= 1.35 && rsi < 78) {
        category = "BULLISH_BREAKOUT";
        reason = `Cleared 20d resistance (${swingHigh20.toFixed(2)} EGP) with ${rvol}x Relative Volume.`;
        score = uptrend ? 88 : 76;
      } else if (macdCross && rsi >= 48 && rsi <= 68) {
        category = "BULLISH_MOMENTUM";
        reason = `Fresh MACD Signal Crossover with constructive RSI (${rsi}).`;
        score = 78;
      } else if (uptrend && Math.abs(price - ema21) / ema21 < 0.018 && rsi >= 40 && rsi <= 55) {
        category = "PULLBACK_UPTREND";
        reason = `Testing 21-EMA dynamic support (${ema21.toFixed(2)} EGP) in established uptrend.`;
        score = 74;
      } else if (rvol >= 2.1) {
        category = "UNUSUAL_VOLUME";
        reason = `Institutional volume expansion (${rvol}x 20-day average turnover).`;
        score = 68;
      } else if (price < swingLow20 && rvol >= 1.25) {
        category = "BEARISH_BREAKDOWN";
        reason = `Broke below 20-day structural support (${swingLow20.toFixed(2)} EGP) on elevated volume.`;
        score = 72;
      }

      const isBear = category === "BEARISH_BREAKDOWN";
      const stopLoss = isBear ? +(price + 1.5 * atr).toFixed(2) : +(price - 1.5 * atr).toFixed(2);
      const risk = Math.abs(price - stopLoss) || price * 0.02;
      const target1 = isBear ? +(price - 1.9 * risk).toFixed(2) : +(price + 1.9 * risk).toFixed(2);
      const target2 = isBear ? +(price - 3.0 * risk).toFixed(2) : +(price + 3.0 * risk).toFixed(2);
      const rr = +(Math.abs(target1 - price) / risk).toFixed(2);

      return {
        ...stock,
        chgPct: +(((price - stock.prevClose) / stock.prevClose) * 100).toFixed(2),
        rvol, rsi,
        macdStatus: macdBull ? "BULLISH" : "BEARISH",
        trend, category, reason, score,
        entryZone: `${(price * 0.996).toFixed(2)} - ${(price * 1.004).toFixed(2)}`,
        stopLoss, target1, target2, rr,
        ema9Arr, ema21Arr, sma50Arr, swingHigh20, swingLow20
      };
    }

    // =========================================================================
    // 3. REAL-TIME SIMULATION TICK & TABLE RENDERING
    // =========================================================================
    function simulateMicroTicks() {
      Object.values(marketState).forEach(st => {
        const shock = (Math.random() - 0.492) * 0.009;
        let nextP = +(st.lastPrice * (1 + shock)).toFixed(2);
        // EGX +/-20% daily limit guard
        nextP = Math.min(+(st.prevClose * 1.20).toFixed(2), Math.max(+(st.prevClose * 0.80).toFixed(2), nextP));
        st.lastPrice = nextP;
        st.high = Math.max(st.high, nextP);
        st.low = Math.min(st.low, nextP);
        const addVol = Math.floor(Math.random() * 14000);
        st.volume += addVol;
        st.tradedValue += Math.floor(addVol * nextP);
        st.candles[st.candles.length - 1].close = nextP;
        st.candles[st.candles.length - 1].high = st.high;
        st.candles[st.candles.length - 1].low = st.low;
      });
    }

    function getBadgeClass(cat) {
      if (cat.includes("BULLISH") || cat.includes("PULLBACK")) return "badge-bull";
      if (cat.includes("BEARISH")) return "badge-bear";
      if (cat.includes("VOLUME")) return "badge-vol";
      return "badge-neutral";
    }

    function runScannerSweep() {
      simulateMicroTicks();
      const minVal = parseFloat(document.getElementById("filter-val").value) || 0;
      const catFilter = document.getElementById("filter-category").value;

      const results = Object.values(marketState).map(analyzeStock);

      // Update Breadth Metrics
      const adv = results.filter(r => r.chgPct > 0.05).length;
      const dec = results.filter(r => r.chgPct < -0.05).length;
      const act = results.filter(r => r.category !== "NO_ACTIONABLE_SIGNAL").length;
      const totVal = results.reduce((acc, r) => acc + r.tradedValue, 0) / 1e6;

      document.getElementById("metric-adv").textContent = adv;
      document.getElementById("metric-dec").textContent = dec;
      document.getElementById("metric-sig").textContent = act;
      document.getElementById("metric-val").textContent = `${totVal.toFixed(1)}M EGP`;
      document.getElementById("last-scan-time").textContent = `Last Sweep: ${new Date().toLocaleTimeString()}`;

      const filtered = results.filter(r => {
        if (r.tradedValue < minVal) return false;
        if (catFilter === "ACTIONABLE") return r.category !== "NO_ACTIONABLE_SIGNAL";
        if (catFilter !== "ALL") return r.category === catFilter;
        return true;
      }).sort((a, b) => b.score - a.score);

      const tbody = document.getElementById("scanner-tbody");
      tbody.innerHTML = filtered.map(r => `
        <tr onclick="selectStock('${r.ticker}')" class="hover:bg-gray-800/70 cursor-pointer transition ${r.ticker === selectedTicker ? 'bg-gray-800/90' : ''}">
          <td class="py-2.5 px-3 font-bold text-white">${r.ticker}</td>
          <td class="py-2.5 px-3">${r.lastPrice.toFixed(2)}</td>
          <td class="py-2.5 px-3 ${r.chgPct >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${r.chgPct >= 0 ? '+' : ''}${r.chgPct.toFixed(2)}%</td>
          <td class="py-2.5 px-3 ${r.rvol >= 1.5 ? 'text-amber-400 font-bold' : ''}">${r.rvol}x</td>
          <td class="py-2.5 px-3">${r.rsi}</td>
          <td class="py-2.5 px-3 ${r.macdStatus === 'BULLISH' ? 'text-emerald-400' : 'text-rose-400'}">${r.macdStatus}</td>
          <td class="py-2.5 px-3">${r.trend}</td>
          <td class="py-2.5 px-3"><span class="px-2 py-0.5 rounded text-[10px] font-semibold ${getBadgeClass(r.category)}">${r.category}</span></td>
          <td class="py-2.5 px-3 text-gray-300">${r.entryZone}</td>
          <td class="py-2.5 px-3 text-rose-400">${r.stopLoss.toFixed(2)}</td>
          <td class="py-2.5 px-3 text-emerald-400">${r.target1.toFixed(2)}</td>
          <td class="py-2.5 px-3">${r.rr}</td>
          <td class="py-2.5 px-3 font-bold text-sky-400">${r.score}/100</td>
        </tr>
      `).join("");

      renderStockAnalysis(selectedTicker);
    }

    // =========================================================================
    // 4. INTERACTIVE PLOTLY CHART & BACKTESTER
    // =========================================================================
    function selectStock(ticker) {
      selectedTicker = ticker;
      renderStockAnalysis(ticker);
    }

    function renderStockAnalysis(ticker) {
      const analyzed = analyzeStock(marketState[ticker]);
      document.getElementById("chart-title").textContent = `${analyzed.ticker} — ${analyzed.name} (${analyzed.lastPrice.toFixed(2)} EGP)`;
      document.getElementById("chart-reason").textContent = `[${analyzed.category}] ${analyzed.reason}`;

      const dates = analyzed.candles.map(c => c.date);
      const traceCandle = {
        x: dates,
        open: analyzed.candles.map(c => c.open),
        high: analyzed.candles.map(c => c.high),
        low: analyzed.candles.map(c => c.low),
        close: analyzed.candles.map(c => c.close),
        type: 'candlestick',
        name: 'OHLC',
        increasing: { line: { color: '#10b981' } },
        decreasing: { line: { color: '#ef4444' } }
      };

      const traceEma9 = { x: dates, y: analyzed.ema9Arr, type: 'scatter', mode: 'lines', name: 'EMA 9', line: { color: '#38bdf8', width: 1.2 } };
      const traceEma21 = { x: dates, y: analyzed.ema21Arr, type: 'scatter', mode: 'lines', name: 'EMA 21', line: { color: '#c084fc', width: 1.2 } };
      const traceSma50 = { x: dates, y: analyzed.sma50Arr, type: 'scatter', mode: 'lines', name: 'SMA 50', line: { color: '#fb923c', width: 1.5 } };

      const layout = {
        paper_bgcolor: '#111827',
        plot_bgcolor: '#111827',
        font: { color: '#9ca3af', size: 11 },
        margin: { l: 40, r: 20, t: 10, b: 30 },
        xaxis: { rangeslider: { visible: false }, gridcolor: '#1f2937' },
        yaxis: { gridcolor: '#1f2937' },
        shapes: [
          { type: 'line', x0: dates[0], x1: dates[dates.length - 1], y0: analyzed.stopLoss, y1: analyzed.stopLoss, line: { color: '#ef4444', width: 1, dash: 'dash' } },
          { type: 'line', x0: dates[0], x1: dates[dates.length - 1], y0: analyzed.target1, y1: analyzed.target1, line: { color: '#10b981', width: 1, dash: 'dash' } }
        ],
        legend: { orientation: 'h', y: 1.08 }
      };

      Plotly.react('plotly-chart', [traceCandle, traceEma9, traceEma21, traceSma50], layout, { responsive: true, displayModeBar: false });

      // Setup Box
      document.getElementById("setup-details").innerHTML = `
        <div class="flex justify-between"><span>Signal Category:</span><span class="font-bold text-white">${analyzed.category}</span></div>
        <div class="flex justify-between"><span>Entry Range:</span><span class="font-mono text-sky-400">${analyzed.entryZone} EGP</span></div>
        <div class="flex justify-between"><span>Technical Stop-Loss:</span><span class="font-mono text-rose-400">${analyzed.stopLoss.toFixed(2)} EGP</span></div>
        <div class="flex justify-between"><span>Target 1 / Target 2:</span><span class="font-mono text-emerald-400">${analyzed.target1.toFixed(2)} / ${analyzed.target2.toFixed(2)} EGP</span></div>
        <div class="flex justify-between"><span>Risk-to-Reward Ratio:</span><span class="font-mono font-bold text-white">1 : ${analyzed.rr}</span></div>
        <div class="flex justify-between"><span>20d Resistance / Support:</span><span class="font-mono">${analyzed.swingHigh20.toFixed(2)} / ${analyzed.swingLow20.toFixed(2)}</span></div>
      `;

      // Run Walk-Forward Backtest on this stock
      let wins = 0, losses = 0, netReturnPct = 0;
      const c = analyzed.candles;
      for (let i = 22; i < c.length - 5; i++) {
        const prevHigh = Math.max(...c.slice(i - 20, i).map(x => x.high));
        if (c[i].close > prevHigh) {
          const entry = c[i + 1].open * 1.001;
          const exit = c[i + 5].close * 0.999;
          const tradeRet = ((exit - entry) / entry) - 0.004; // Deduct 0.4% round-trip EGX fees
          netReturnPct += tradeRet * 100;
          if (tradeRet > 0) wins++; else losses++;
          i += 5;
        }
      }
      const totalTrades = wins + losses;
      const winRate = totalTrades > 0 ? ((wins / totalTrades) * 100).toFixed(1) : "0.0";

      document.getElementById("backtest-metrics").innerHTML = `
        <div class="bg-gray-800/60 p-2.5 rounded"><div class="text-gray-400">Total Trades</div><div class="text-base font-bold text-white">${totalTrades}</div></div>
        <div class="bg-gray-800/60 p-2.5 rounded"><div class="text-gray-400">Win Rate</div><div class="text-base font-bold text-emerald-400">${winRate}%</div></div>
        <div class="bg-gray-800/60 p-2.5 rounded"><div class="text-gray-400">Net Cumulative Return</div><div class="text-base font-bold ${netReturnPct >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${netReturnPct >= 0 ? '+' : ''}${netReturnPct.toFixed(2)}%</div></div>
        <div class="bg-gray-800/60 p-2.5 rounded"><div class="text-gray-400">Avg Holding Period</div><div class="text-base font-bold text-white">5 Sessions</div></div>
      `;
    }

    // Cairo Timezone & Session Clock
    function updateCairoClock() {
      const now = new Date();
      const cairoStr = now.toLocaleString("en-US", { timeZone: "Africa/Cairo", hour12: false });
      const cairoDate = new Date(cairoStr);
      const day = cairoDate.getDay(); // 0=Sun .. 4=Thu, 5=Fri, 6=Sat
      const hr = cairoDate.getHours() + cairoDate.getMinutes() / 60;
      const isTradingDay = (day >= 0 && day <= 4);
      const isOpen = isTradingDay && (hr >= 10.0 && hr <= 14.5);

      document.getElementById("cairo-clock").textContent = `${cairoDate.toLocaleTimeString()} (${isOpen ? 'OPEN' : 'CLOSED'})`;
      document.getElementById("session-dot").className = `w-2.5 h-2.5 rounded-full ${isOpen ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`;
    }

    // Boot
    initMarketHistories();
    updateCairoClock();
    setInterval(updateCairoClock, 1000);
    runScannerSweep();

    document.getElementById("filter-category").addEventListener("change", runScannerSweep);
    document.getElementById("filter-val").addEventListener("change", runScannerSweep);
    document.getElementById("scan-speed").addEventListener("change", (e) => {
      clearInterval(timerId);
      timerId = setInterval(runScannerSweep, parseInt(e.target.value));
    });
    timerId = setInterval(runScannerSweep, 3000);
  </script>
</body>
</html>
