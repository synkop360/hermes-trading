from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import webbrowser
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ccxt.async_support as ccxt
import yaml

from hermes_trading.market_data import timeframe_interval
from hermes_trading.viewer_data import (
    Candle,
    asset_strategy_config,
    compute_indicator_series,
    load_closed_trades,
    load_open_position,
)

ALLOWED_TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
MAX_CANDLES = 720
INDICATOR_WARMUP = 30


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def configured_symbols(state_dir: Path) -> list[str]:
    goal = _load_yaml(state_dir / "goal.yaml")
    assets = goal.get("assets")
    if isinstance(assets, list) and assets:
        return [str(asset) for asset in assets]
    asset = goal.get("asset")
    return [str(asset)] if asset else []


async def fetch_candles(exchange_id: str, symbol: str, timeframe: str, limit: int) -> tuple[Candle, ...]:
    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    try:
        rows = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    finally:
        await exchange.close()
    return tuple(
        Candle(
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
        )
        for row in rows
    )


def build_symbol_payload(
    state_dir: Path, exchange_id: str, symbol: str, timeframe: str, hours: float
) -> dict[str, Any]:
    interval_seconds = timeframe_interval(timeframe).total_seconds()
    candle_count = math.ceil(hours * 3600 / interval_seconds) + INDICATOR_WARMUP
    limit = max(INDICATOR_WARMUP + 1, min(candle_count, MAX_CANDLES))

    candles = asyncio.run(fetch_candles(exchange_id, symbol, timeframe, limit))
    strategy = _load_yaml(state_dir / "strategy.yaml")
    asset_strategy = asset_strategy_config(strategy, symbol)
    indicators = compute_indicator_series(candles, asset_strategy)

    window_start = datetime.now(UTC) - timedelta(hours=hours)
    trades = load_closed_trades(state_dir, symbol, window_start)
    open_position = load_open_position(state_dir, symbol)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": [
            {
                "t": candle.timestamp.isoformat(),
                "o": float(candle.open),
                "h": float(candle.high),
                "l": float(candle.low),
                "c": float(candle.close),
            }
            for candle in candles
        ],
        "rsi": [float(value) if value is not None else None for value in indicators.rsi],
        "atr": [float(value) if value is not None else None for value in indicators.atr],
        "entry": {"indicator": indicators.entry_indicator, "threshold": _optional_float(indicators.entry_threshold)},
        "exit": {"indicator": indicators.exit_indicator, "threshold": _optional_float(indicators.exit_threshold)},
        "trades": [
            {
                "position_id": trade.get("position_id"),
                "opened_at": trade.get("opened_at"),
                "closed_at": trade.get("closed_at"),
                "entry_price": _optional_float_str(trade.get("entry_price")),
                "exit_price": _closed_trade_exit_price(trade),
                "stop_price": _optional_float_str(trade.get("stop_price")),
                "realized_pnl": _optional_float_str(trade.get("realized_pnl")),
            }
            for trade in trades
        ],
        "open_position": (
            {
                "position_id": open_position.position_id,
                "entry_price": float(open_position.entry_price),
                "stop_price": float(open_position.stop_price),
                "take_profit_price": _optional_float(open_position.take_profit_price),
                "quantity": float(open_position.quantity),
                "opened_at": open_position.opened_at.isoformat(),
            }
            if open_position is not None
            else None
        ),
    }


def _closed_trade_exit_price(trade: dict[str, Any]) -> float | None:
    entry_price = trade.get("entry_price")
    quantity = trade.get("quantity")
    realized_pnl = trade.get("realized_pnl")
    fees = trade.get("fees")
    if None in (entry_price, quantity, realized_pnl, fees):
        return None
    try:
        entry = Decimal(str(entry_price))
        qty = Decimal(str(quantity))
        if qty == 0:
            return None
        return float(entry + (Decimal(str(realized_pnl)) + Decimal(str(fees))) / qty)
    except Exception:
        return None


def _optional_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _optional_float_str(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except Exception:
        return None


def _make_handler(state_dir: Path, exchange_id: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
                return
            if parsed.path == "/api/symbols":
                body = json.dumps(
                    {"symbols": configured_symbols(state_dir), "timeframes": list(ALLOWED_TIMEFRAMES)}
                ).encode("utf-8")
                self._send(200, "application/json", body)
                return
            if parsed.path == "/api/chart":
                self._handle_chart(parse_qs(parsed.query))
                return
            self._send(404, "text/plain", b"not found")

        def _handle_chart(self, query: dict[str, list[str]]) -> None:
            symbols = [value for value in query.get("symbols", [""])[0].split(",") if value]
            timeframe = query.get("timeframe", ["15m"])[0]
            if timeframe not in ALLOWED_TIMEFRAMES:
                self._send(400, "application/json", json.dumps({"error": "unsupported timeframe"}).encode("utf-8"))
                return
            try:
                hours = float(query.get("hours", ["24"])[0])
            except ValueError:
                hours = 24.0
            hours = max(1.0, min(hours, 24.0 * 30))

            payloads: dict[str, Any] = {}
            for symbol in symbols:
                try:
                    payloads[symbol] = build_symbol_payload(state_dir, exchange_id, symbol, timeframe, hours)
                except Exception as error:
                    payloads[symbol] = {"symbol": symbol, "error": str(error)}
            self._send(200, "application/json", json.dumps(payloads).encode("utf-8"))

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Hermes Position Viewer</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font-family: -apple-system, Segoe UI, sans-serif; background: #0f1115; color: #e6e6e6; }
  header { padding: 12px 16px; border-bottom: 1px solid #2a2d35; display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }
  header h1 { font-size: 15px; margin: 0 16px 0 0; font-weight: 600; color: #9fb4ff; }
  .control { display: flex; gap: 6px; align-items: center; font-size: 12px; }
  select, input[type=number] { background: #1a1d24; color: #e6e6e6; border: 1px solid #333; border-radius: 4px; padding: 3px 6px; }
  #symbols { display: flex; gap: 10px; flex-wrap: wrap; }
  #symbols label { font-size: 12px; display: flex; gap: 4px; align-items: center; }
  button { background: #2a4dff; color: white; border: none; border-radius: 4px; padding: 5px 12px; font-size: 12px; cursor: pointer; }
  button:hover { background: #3d5cff; }
  #panels { padding: 12px 16px; display: flex; flex-direction: column; gap: 18px; }
  .panel { border: 1px solid #23262e; border-radius: 6px; padding: 10px; background: #14161c; }
  .panel-title { font-size: 13px; font-weight: 600; margin-bottom: 6px; display: flex; justify-content: space-between; }
  .panel-title .pos { font-weight: 400; font-size: 11px; color: #9aa0aa; }
  canvas { display: block; width: 100%; }
  .legend { font-size: 10px; color: #9aa0aa; margin-top: 4px; display: flex; gap: 14px; flex-wrap: wrap; }
  .legend span.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }
  .error { color: #ff6b6b; font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>Hermes Position Viewer</h1>
  <div id="symbols" class="control"></div>
  <div class="control">
    <label>Timeframe</label>
    <select id="timeframe"></select>
  </div>
  <div class="control">
    <label>Hours</label>
    <input id="hours" type="number" value="24" min="1" max="720" step="1" style="width:60px">
  </div>
  <div class="control">
    <label><input type="checkbox" id="autoRefresh" checked> Auto-refresh (30s)</label>
  </div>
  <button id="refresh">Refresh</button>
</header>
<div id="panels"></div>
<script>
const panelsEl = document.getElementById('panels');
const symbolsEl = document.getElementById('symbols');
const timeframeEl = document.getElementById('timeframe');
const hoursEl = document.getElementById('hours');
let timer = null;

function fmt(n) {
  if (n === null || n === undefined) return '-';
  if (Math.abs(n) < 0.001) return n.toPrecision(4);
  return n.toFixed(Math.abs(n) < 10 ? 4 : 2);
}

async function loadConfig() {
  const res = await fetch('/api/symbols');
  const data = await res.json();
  symbolsEl.innerHTML = data.symbols.map((s, i) =>
    `<label><input type="checkbox" value="${s}" ${i < 4 ? 'checked' : ''}> ${s}</label>`
  ).join('');
  timeframeEl.innerHTML = data.timeframes.map(t => `<option value="${t}">${t}</option>`).join('');
  timeframeEl.value = '15m';
}

function selectedSymbols() {
  return Array.from(symbolsEl.querySelectorAll('input:checked')).map(el => el.value);
}

function drawPriceChart(canvas, payload) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  const candles = payload.candles;
  if (!candles.length) return;
  const lows = candles.map(c => c.l), highs = candles.map(c => c.h);
  let min = Math.min(...lows), max = Math.max(...highs);
  const openPos = payload.open_position;
  if (openPos) {
    min = Math.min(min, openPos.stop_price, openPos.take_profit_price ?? min);
    max = Math.max(max, openPos.stop_price, openPos.take_profit_price ?? max);
  }
  const pad = (max - min) * 0.08 || max * 0.01 || 1;
  min -= pad; max += pad;
  const n = candles.length;
  const cw = w / n;
  const y = v => h - ((v - min) / (max - min)) * h;

  ctx.strokeStyle = '#23262e';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const yy = (h / 4) * i;
    ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(w, yy); ctx.stroke();
  }

  candles.forEach((c, i) => {
    const x = i * cw + cw / 2;
    const up = c.c >= c.o;
    ctx.strokeStyle = ctx.fillStyle = up ? '#3ecf6f' : '#ff5c5c';
    ctx.beginPath(); ctx.moveTo(x, y(c.h)); ctx.lineTo(x, y(c.l)); ctx.stroke();
    const bodyTop = y(Math.max(c.o, c.c)), bodyBot = y(Math.min(c.o, c.c));
    ctx.fillRect(x - cw * 0.35, bodyTop, cw * 0.7, Math.max(1, bodyBot - bodyTop));
  });

  function line(value, color) {
    ctx.strokeStyle = color; ctx.setLineDash([4, 3]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, y(value)); ctx.lineTo(w, y(value)); ctx.stroke();
    ctx.setLineDash([]);
  }
  if (openPos) {
    line(openPos.entry_price, '#9fb4ff');
    line(openPos.stop_price, '#ff5c5c');
    if (openPos.take_profit_price !== null) line(openPos.take_profit_price, '#3ecf6f');
  }

  function timeIndex(iso) {
    const t = new Date(iso).getTime();
    let best = 0, bestDiff = Infinity;
    candles.forEach((c, i) => {
      const diff = Math.abs(new Date(c.t).getTime() - t);
      if (diff < bestDiff) { bestDiff = diff; best = i; }
    });
    return best;
  }

  function fmtTimestamp(iso) {
    const d = new Date(iso);
    const pad = n => String(n).padStart(2, '0');
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function drawTimestampLabel(x, yPos, iso, color, side) {
    const text = fmtTimestamp(iso);
    ctx.font = '9px monospace';
    const textWidth = ctx.measureText(text).width;
    const boxX = Math.min(Math.max(x - textWidth / 2, 2), w - textWidth - 2);
    const boxY = side === 'above' ? yPos - 20 : yPos + 20;
    ctx.fillStyle = 'rgba(10,12,16,0.85)';
    ctx.fillRect(boxX - 2, boxY - 8, textWidth + 4, 11);
    ctx.strokeStyle = color; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, yPos); ctx.lineTo(x, boxY + (side === 'above' ? 3 : -3)); ctx.stroke();
    ctx.fillStyle = color;
    ctx.fillText(text, boxX, boxY);
  }

  function drawEntryMarker(i, price, iso, color) {
    const x = i * cw + cw / 2;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(x, y(price) + 8);
    ctx.lineTo(x - 5, y(price) + 16);
    ctx.lineTo(x + 5, y(price) + 16);
    ctx.fill();
    drawTimestampLabel(x, y(price) + 16, iso, color, 'below');
  }

  function drawExitMarker(i, price, iso, color) {
    const x = i * cw + cw / 2;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(x, y(price) - 8);
    ctx.lineTo(x - 5, y(price) - 16);
    ctx.lineTo(x + 5, y(price) - 16);
    ctx.fill();
    drawTimestampLabel(x, y(price) - 16, iso, color, 'above');
  }

  payload.trades.forEach(trade => {
    if (trade.opened_at && trade.entry_price !== null) {
      drawEntryMarker(timeIndex(trade.opened_at), trade.entry_price, trade.opened_at, '#3ecf6f');
    }
    if (trade.closed_at && trade.exit_price !== null) {
      drawExitMarker(timeIndex(trade.closed_at), trade.exit_price, trade.closed_at, trade.realized_pnl >= 0 ? '#3ecf6f' : '#ff5c5c');
    }
  });

  if (openPos && openPos.opened_at) {
    drawEntryMarker(timeIndex(openPos.opened_at), openPos.entry_price, openPos.opened_at, '#9fb4ff');
  }
}

function drawRsiChart(canvas, payload) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  const rsi = payload.rsi;
  const n = rsi.length;
  if (!n) return;
  const y = v => h - (v / 100) * h;
  ctx.strokeStyle = '#23262e';
  [0, 25, 50, 75, 100].forEach(v => {
    ctx.beginPath(); ctx.moveTo(0, y(v)); ctx.lineTo(w, y(v)); ctx.stroke();
  });
  function line(value, color) {
    if (value === null || value === undefined) return;
    ctx.strokeStyle = color; ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(0, y(value)); ctx.lineTo(w, y(value)); ctx.stroke();
    ctx.setLineDash([]);
  }
  line(payload.entry.threshold, '#3ecf6f');
  line(payload.exit.threshold, '#ff9f43');

  ctx.strokeStyle = '#9fb4ff'; ctx.lineWidth = 1.5;
  ctx.beginPath();
  let started = false;
  rsi.forEach((v, i) => {
    if (v === null) return;
    const x = (i / (n - 1)) * w;
    if (!started) { ctx.moveTo(x, y(v)); started = true; } else { ctx.lineTo(x, y(v)); }
  });
  ctx.stroke();
}

function renderPanel(payload) {
  const panel = document.createElement('div');
  panel.className = 'panel';
  if (payload.error) {
    panel.innerHTML = `<div class="panel-title">${payload.symbol}</div><div class="error">${payload.error}</div>`;
    return panel;
  }
  const last = payload.candles.at(-1);
  const pos = payload.open_position;
  const posText = pos
    ? `open: entry ${fmt(pos.entry_price)} stop ${fmt(pos.stop_price)} target ${pos.take_profit_price !== null ? fmt(pos.take_profit_price) : '-'}`
    : 'no open position';
  panel.innerHTML = `
    <div class="panel-title"><span>${payload.symbol} <span class="pos">${payload.timeframe} last=${fmt(last?.c)}</span></span><span class="pos">${posText}</span></div>
    <canvas class="price" height="240"></canvas>
    <canvas class="rsi" height="90"></canvas>
    <div class="legend">
      <span><span class="dot" style="background:#9fb4ff"></span>entry price</span>
      <span><span class="dot" style="background:#ff5c5c"></span>stop</span>
      <span><span class="dot" style="background:#3ecf6f"></span>take-profit / entry marker</span>
      <span><span class="dot" style="background:#ff9f43"></span>rsi ${payload.exit.indicator ?? ''} exit threshold</span>
      <span>closed trades in window: ${payload.trades.length}</span>
    </div>
  `;
  return panel;
}

function drawPanel(panel, payload) {
  if (payload.error) return;
  const priceCanvas = panel.querySelector('canvas.price');
  const rsiCanvas = panel.querySelector('canvas.rsi');
  // Sized here (not via requestAnimationFrame) so charts still render when the
  // tab is backgrounded/unfocused, where browsers throttle rAF indefinitely --
  // this only needs the panel to already be in the DOM for clientWidth to be valid.
  priceCanvas.width = priceCanvas.clientWidth || 900;
  rsiCanvas.width = rsiCanvas.clientWidth || 900;
  drawPriceChart(priceCanvas, payload);
  drawRsiChart(rsiCanvas, payload);
}

async function refresh() {
  const symbols = selectedSymbols();
  if (!symbols.length) { panelsEl.innerHTML = '<div class="legend">select at least one symbol</div>'; return; }
  const url = `/api/chart?symbols=${encodeURIComponent(symbols.join(','))}&timeframe=${timeframeEl.value}&hours=${hoursEl.value}`;
  const res = await fetch(url);
  const data = await res.json();
  panelsEl.innerHTML = '';
  const rendered = symbols.filter(symbol => data[symbol]).map(symbol => ({
    payload: data[symbol],
    panel: renderPanel(data[symbol]),
  }));
  rendered.forEach(({ panel }) => panelsEl.appendChild(panel));
  rendered.forEach(({ panel, payload }) => drawPanel(panel, payload));
}

function scheduleAutoRefresh() {
  if (timer) clearInterval(timer);
  if (document.getElementById('autoRefresh').checked) {
    timer = setInterval(refresh, 30000);
  }
}

document.getElementById('refresh').addEventListener('click', refresh);
document.getElementById('autoRefresh').addEventListener('change', scheduleAutoRefresh);

loadConfig().then(refresh).then(scheduleAutoRefresh);
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Local viewer for positions, market data, and strategy indicators")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--exchange-id", default=os.getenv("EXCHANGE_ID", "kraken"))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    handler = _make_handler(state_dir, args.exchange_id)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Hermes position viewer serving {url} (state-dir={state_dir}, exchange={args.exchange_id})")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
