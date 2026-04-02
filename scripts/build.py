#!/usr/bin/env python3
"""
Jun Yadnap Trade System — Daily Build Script
Fetches ASX 200 data, calculates indicators, generates dashboard HTML.
"""

import json
import math
import os
from datetime import datetime, timezone, timedelta
import yfinance as yf
import pandas as pd
import numpy as np

# ─── Timezone ────────────────────────────────────────────────────────────────
def get_aest_now():
    """Return current Melbourne time with DST awareness."""
    utc_now = datetime.now(timezone.utc)
    year = utc_now.year
    apr1 = datetime(year, 4, 1)
    apr_sun = apr1 + timedelta(days=(6 - apr1.weekday()) % 7)
    oct1 = datetime(year, 10, 1)
    oct_sun = oct1 + timedelta(days=(6 - oct1.weekday()) % 7)
    utc_naive = utc_now.replace(tzinfo=None)
    if apr_sun <= utc_naive < oct_sun:
        offset = timedelta(hours=10)
        tz_name = "AEST"
    else:
        offset = timedelta(hours=11)
        tz_name = "AEDT"
    local = utc_now + offset
    return local, tz_name

# ─── Indicators ──────────────────────────────────────────────────────────────
def calc_sma(series, period):
    return series.rolling(window=period, min_periods=period).mean()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 0.001)
    return 100 - (100 / (1 + rs))

def calc_bb(series, period=20, std_dev=2):
    mid = calc_sma(series, period)
    std = series.rolling(window=period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, mid, lower

def get_regime(price, sma250, rsi):
    if pd.isna(sma250) or pd.isna(price):
        return "NEUTRAL"
    ext = (price - sma250) / sma250 * 100
    if price > sma250 and ext > 2 and rsi > 55:
        return "STRONG UPTREND"
    if price > sma250 and ext > 1:
        return "UPTREND"
    if price < sma250 and ext < -2 and rsi < 40:
        return "STRONG DOWNTREND"
    if price < sma250:
        return "DOWNTREND"
    return "NEUTRAL"

def get_signal(regime, price, sma20, sma250, bb_upper, bb_lower, rsi):
    if any(pd.isna(x) for x in [sma20, sma250, bb_upper, bb_lower]):
        return dict(action="HOLD CASH", gear=0, bboz=0, cash=100,
                    reason="Insufficient data. Holding cash.")
    ext20 = (price - sma20) / sma20 * 100
    ext250 = (price - sma250) / sma250 * 100

    if regime == "STRONG UPTREND":
        if -1.5 < ext20 < 1.5:
            return dict(action="BUY GEAR", gear=80, bboz=0, cash=20,
                        reason=f"Strong uptrend. ASX near SMA20 (ext: {ext20:+.1f}%). Momentum Long + MR Long 1 active.")
        if price < bb_lower and price > sma250:
            return dict(action="BUY GEAR", gear=70, bboz=0, cash=30,
                        reason=f"Deep pullback in strong uptrend. Below lower BB but above SMA250. MR Long 2 triggered.")
        return dict(action="BUY GEAR", gear=65, bboz=0, cash=35,
                    reason=f"Strong uptrend confirmed. ASX {ext250:+.1f}% above SMA250. Holding GEAR.")

    if regime == "UPTREND":
        if -1 < ext20 < 1:
            return dict(action="BUY GEAR", gear=50, bboz=0, cash=50,
                        reason=f"Uptrend + SMA20 pullback (ext: {ext20:+.1f}%). MR Long 1 active.")
        if 0 < ext250 < 3:
            return dict(action="BUY GEAR", gear=40, bboz=0, cash=60,
                        reason=f"Near SMA250 bounce zone ({ext250:+.1f}%). MR Long 3 watching for confirmation.")
        if ext20 > 4 and price > bb_upper:
            return dict(action="BUY BBOZ", gear=0, bboz=20, cash=80,
                        reason=f"Overextended {ext20:+.1f}% above SMA20 and above upper BB. MR Short triggered.")
        return dict(action="BUY GEAR", gear=40, bboz=0, cash=60,
                    reason=f"Uptrend. ASX {ext250:+.1f}% above SMA250. Standard GEAR position.")

    if regime == "DOWNTREND":
        if price < bb_lower:
            return dict(action="BUY BBOZ", gear=0, bboz=30, cash=70,
                        reason=f"Below SMA250 and lower BB. Momentum Short 1 active. Downtrend confirmed.")
        if rsi > 50 and ext20 < 0:
            return dict(action="BUY BBOZ", gear=0, bboz=25, cash=75,
                        reason=f"Failed bounce below SMA20 in downtrend. Momentum Short 2 active.")
        return dict(action="BUY BBOZ", gear=0, bboz=25, cash=75,
                    reason=f"Downtrend. ASX {ext250:+.1f}% below SMA250. Holding BBOZ.")

    if regime == "STRONG DOWNTREND":
        return dict(action="BUY BBOZ", gear=0, bboz=60, cash=40,
                    reason=f"Strong downtrend. ASX {ext250:+.1f}% below SMA250, RSI {rsi:.0f}. Max BBOZ allocation.")

    return dict(action="HOLD CASH", gear=0, bboz=0, cash=100,
                reason=f"Neutral zone. ASX within 1% of SMA250 ({ext250:+.1f}%). Waiting for clear direction.")

# ─── Fetch Data ───────────────────────────────────────────────────────────────
def fetch_asx_data():
    print("Fetching ASX 200 data...")
    ticker = yf.Ticker("^AXJO")
    df = ticker.history(period="2y", interval="1d")
    df = df[["Close"]].copy()
    df.dropna(inplace=True)
    df.index = pd.to_datetime(df.index)
    print(f"  Got {len(df)} days of data. Latest: {df.index[-1].date()} = {df['Close'].iloc[-1]:.0f}")
    return df

# ─── Build Backtest ───────────────────────────────────────────────────────────
def run_backtest(df):
    closes = df["Close"]
    sma20 = calc_sma(closes, 20)
    sma250 = calc_sma(closes, 250)
    rsi = calc_rsi(closes, 14)
    bb_upper, bb_mid, bb_lower = calc_bb(closes, 20)

    portfolio = 100000.0
    bh_base = closes.iloc[0]
    equity = []
    bh_equity = []
    dd_series = []
    peak = 100000.0
    wins = losses = 0
    prev_val = 100000.0
    regime_stats = {}
    labels = []

    for i in range(250, len(closes)):
        price = closes.iloc[i]
        prev_price = closes.iloc[i-1]
        s20 = sma20.iloc[i]
        s250 = sma250.iloc[i]
        r = rsi.iloc[i]
        bbu = bb_upper.iloc[i]
        bbl = bb_lower.iloc[i]

        regime = get_regime(price, s250, r)
        sig = get_signal(regime, price, s20, s250, bbu, bbl, r)

        daily_ret = (price - prev_price) / prev_price
        port_ret = 0.0
        if sig["gear"] > 0:
            port_ret = daily_ret * 2 * (sig["gear"]/100) - (0.35/252 * sig["gear"]/100)
        elif sig["bboz"] > 0:
            port_ret = -daily_ret * 2 * (sig["bboz"]/100) - (0.56/252 * sig["bboz"]/100)
        portfolio *= (1 + port_ret)

        if i % 20 == 0:
            if portfolio > prev_val: wins += 1
            elif portfolio < prev_val: losses += 1
            prev_val = portfolio

        if portfolio > peak: peak = portfolio
        dd = (portfolio - peak) / peak * 100

        bh_val = 100000 * (price / bh_base)

        if regime not in regime_stats:
            regime_stats[regime] = {"days": 0, "sys_start": portfolio, "asx_start": price}
        regime_stats[regime]["days"] += 1
        regime_stats[regime]["sys_end"] = portfolio
        regime_stats[regime]["asx_end"] = price

        if i % 5 == 0:
            labels.append(closes.index[i].strftime("%b '%y"))
            equity.append(round(portfolio))
            bh_equity.append(round(bh_val))
            dd_series.append(round(dd, 2))

    total_return = (portfolio - 100000) / 100000 * 100
    years = len(closes) / 252
    cagr = (math.pow(portfolio / 100000, 1/years) - 1) * 100
    max_dd = min(dd_series) if dd_series else 0
    win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
    sharpe = cagr / abs(max_dd or 1) * 0.8

    regime_rows = []
    for r, s in regime_stats.items():
        sys_ret = (s["sys_end"] - s["sys_start"]) / s["sys_start"] * 100
        asx_ret = (s["asx_end"] - s["asx_start"]) / s["asx_start"] * 100
        regime_rows.append({
            "regime": r, "days": s["days"],
            "sys_ret": round(sys_ret, 1), "asx_ret": round(asx_ret, 1)
        })

    return {
        "total_return": round(total_return, 1),
        "cagr": round(cagr, 1),
        "max_dd": round(max_dd, 1),
        "win_rate": round(win_rate, 0),
        "sharpe": round(sharpe, 2),
        "labels": labels,
        "equity": equity,
        "bh_equity": bh_equity,
        "drawdowns": dd_series,
        "regime_rows": regime_rows
    }

# ─── Build chart data ─────────────────────────────────────────────────────────
def build_chart_data(df, n=120):
    closes = df["Close"]
    sma20 = calc_sma(closes, 20)
    sma250 = calc_sma(closes, 250)
    rsi = calc_rsi(closes, 14)
    bb_upper, bb_mid, bb_lower = calc_bb(closes, 20)

    def fmt(series):
        return [round(v, 2) if not pd.isna(v) else None for v in series.iloc[-n:]]

    labels = [d.strftime("%d %b") for d in closes.index[-n:]]
    return {
        "labels": labels,
        "closes": fmt(closes),
        "sma20": fmt(sma20),
        "sma250": fmt(sma250),
        "rsi": fmt(rsi),
        "bb_upper": fmt(bb_upper),
        "bb_mid": fmt(bb_mid),
        "bb_lower": fmt(bb_lower),
    }

# ─── Generate HTML ────────────────────────────────────────────────────────────
def generate_html(signal_data, chart_data, backtest_data, build_time, tz_name):
    bt = backtest_data
    cd = chart_data
    sd = signal_data

    bt_regime_rows = ""
    for r in bt["regime_rows"]:
        etf = "GEAR" if "UP" in r["regime"] else "BBOZ" if "DOWN" in r["regime"] else "CASH"
        badge = "badge-g" if "UP" in r["regime"] else "badge-r" if "DOWN" in r["regime"] else "badge-n"
        sys_cls = "pnl-g" if r["sys_ret"] >= 0 else "pnl-r"
        asx_cls = "pnl-g" if r["asx_ret"] >= 0 else "pnl-r"
        better = "badge-g\">Outperformed" if r["sys_ret"] >= r["asx_ret"] else "badge-r\">Underperformed"
        bt_regime_rows += f"""<tr>
          <td>{r["regime"]}</td><td>{r["days"]}d</td>
          <td><span class="badge {badge}">{etf}</span></td>
          <td class="{sys_cls}">{r["sys_ret"]:+.1f}%</td>
          <td class="{asx_cls}">{r["asx_ret"]:+.1f}%</td>
          <td><span class="badge {better}</span></td>
        </tr>"""

    action_html = sd["action"]
    if "GEAR" in sd["action"]:
        action_html = sd["action"].replace("GEAR", '<span class="ticker-g">GEAR</span>')
    elif "BBOZ" in sd["action"]:
        action_html = sd["action"].replace("BBOZ", '<span class="ticker-r">BBOZ</span>')

    gear_color = "#00d4a0" if sd["gear"] > 0 else "#333"
    bboz_color = "#ff5e5e" if sd["bboz"] > 0 else "#333"
    regime_accent = "#00d4a0" if "UP" in sd["regime"] else "#ff5e5e" if "DOWN" in sd["regime"] else "#8a95a8"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jun Yadnap Trade System</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Playfair+Display:wght@600&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root {{
  --bg:#0a0e14;--bg2:#111720;--bg3:#161d28;
  --border:rgba(255,255,255,0.07);--border2:rgba(255,255,255,0.12);
  --text:#e8edf5;--text2:#8a95a8;--text3:#4a5568;
  --green:#00d4a0;--green2:#00a37a;--green-dim:rgba(0,212,160,0.12);
  --red:#ff5e5e;--red-dim:rgba(255,94,94,0.12);
  --amber:#f0a832;--amber-dim:rgba(240,168,50,0.12);
  --blue:#4a9eff;--blue-dim:rgba(74,158,255,0.1);
  --radius:12px;--radius-sm:8px;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;font-size:14px;min-height:100vh}}
#lock-screen{{position:fixed;inset:0;background:var(--bg);z-index:999;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:24px}}
#lock-screen.hidden{{display:none}}
.lock-logo{{font-family:'Playfair Display',serif;font-size:28px;text-align:center;line-height:1.3}}
.lock-logo span{{color:var(--green)}}
.lock-sub{{font-size:11px;color:var(--text3);font-family:'DM Mono',monospace;letter-spacing:2px;text-transform:uppercase}}
.lock-form{{display:flex;flex-direction:column;gap:12px;width:280px}}
.lock-input{{background:var(--bg3);border:1px solid var(--border2);border-radius:var(--radius-sm);padding:12px 16px;color:var(--text);font-size:14px;font-family:'DM Sans',sans-serif;outline:none;transition:border-color .15s}}
.lock-input:focus{{border-color:var(--green)}}
.lock-btn{{background:var(--green);color:#000;border:none;padding:12px;border-radius:var(--radius-sm);font-size:14px;font-weight:500;cursor:pointer;transition:background .15s}}
.lock-btn:hover{{background:var(--green2)}}
.lock-error{{font-size:12px;color:var(--red);text-align:center;display:none;font-family:'DM Mono',monospace}}
.app{{display:grid;grid-template-columns:220px 1fr;min-height:100vh}}
.sidebar{{background:var(--bg2);border-right:1px solid var(--border);padding:0;display:flex;flex-direction:column;position:sticky;top:0;height:100vh;overflow-y:auto}}
.main{{padding:28px 32px;overflow-y:auto}}
.logo-area{{padding:24px 20px 20px;border-bottom:1px solid var(--border)}}
.logo-name{{font-family:'Playfair Display',serif;font-size:16px;color:var(--text);line-height:1.3}}
.logo-sub{{font-size:10px;color:var(--text3);letter-spacing:1.5px;text-transform:uppercase;margin-top:4px;font-family:'DM Mono',monospace}}
.logo-dot{{width:8px;height:8px;border-radius:50%;background:var(--green);display:inline-block;margin-right:6px;animation:blink 2s ease-in-out infinite}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:0.3}}}}
.signal-pill{{margin:16px 12px;padding:12px;background:var(--bg3);border:1px solid var(--border);border-radius:var(--radius)}}
.signal-pill-label{{font-size:10px;color:var(--text3);letter-spacing:1px;text-transform:uppercase;font-family:'DM Mono',monospace;margin-bottom:6px}}
.signal-pill-action{{font-size:15px;font-weight:500;color:{regime_accent};font-family:'DM Mono',monospace}}
.signal-pill-regime{{font-size:11px;color:var(--text2);margin-top:3px}}
.nav-section{{padding:16px 12px 8px}}
.nav-label{{font-size:10px;color:var(--text3);letter-spacing:1.5px;text-transform:uppercase;font-family:'DM Mono',monospace;padding:0 8px;margin-bottom:6px}}
.nav-item{{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:var(--radius-sm);cursor:pointer;color:var(--text2);font-size:13px;transition:all .15s;margin-bottom:2px}}
.nav-item:hover{{background:rgba(255,255,255,0.05);color:var(--text)}}
.nav-item.active{{background:var(--green-dim);color:var(--green)}}
.nav-icon{{font-size:14px;width:18px;text-align:center}}
.sidebar-footer{{margin-top:auto;padding:16px 12px;border-top:1px solid var(--border);font-size:11px;color:var(--text3);line-height:1.8}}
.tab{{display:none}}.tab.active{{display:block}}
.page-header{{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:24px;flex-wrap:wrap;gap:12px}}
.page-title{{font-family:'Playfair Display',serif;font-size:26px;color:var(--text)}}
.page-sub{{font-size:12px;color:var(--text3);margin-top:4px;font-family:'DM Mono',monospace}}
.card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:20px}}
.card-title{{font-size:11px;color:var(--text3);letter-spacing:1.5px;text-transform:uppercase;font-family:'DM Mono',monospace;margin-bottom:14px}}
.signal-main{{background:var(--bg2);border:1px solid var(--border);border-top:2px solid {regime_accent};border-radius:var(--radius);padding:24px;margin-bottom:20px}}
.signal-regime{{font-size:11px;color:var(--text3);letter-spacing:1.5px;text-transform:uppercase;font-family:'DM Mono',monospace;margin-bottom:8px}}
.signal-action{{font-size:32px;font-weight:300;color:var(--text);letter-spacing:-0.5px}}
.ticker-g{{color:var(--green);font-weight:500}}.ticker-r{{color:var(--red);font-weight:500}}
.signal-reason{{font-size:13px;color:var(--text2);margin-top:10px;line-height:1.7;max-width:580px}}
.alloc-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}}
.alloc-box{{background:var(--bg3);border:1px solid var(--border);border-radius:var(--radius-sm);padding:14px 16px}}
.alloc-name{{font-size:11px;color:var(--text3);font-family:'DM Mono',monospace;margin-bottom:6px;letter-spacing:1px}}
.alloc-pct{{font-size:28px;font-weight:300;letter-spacing:-1px}}
.alloc-pct.g{{color:var(--green)}}.alloc-pct.r{{color:var(--red)}}.alloc-pct.w{{color:var(--text2)}}
.alloc-bar{{height:3px;background:var(--bg);border-radius:2px;margin-top:8px}}
.alloc-fill{{height:100%;border-radius:2px}}
.metrics-row{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px}}
.metric-box{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:16px}}
.metric-label{{font-size:10px;color:var(--text3);letter-spacing:1.5px;text-transform:uppercase;font-family:'DM Mono',monospace;margin-bottom:8px}}
.metric-val{{font-size:22px;font-weight:300;color:var(--text);font-family:'DM Mono',monospace;letter-spacing:-0.5px}}
.metric-val.g{{color:var(--green)}}.metric-val.r{{color:var(--red)}}.metric-val.a{{color:var(--amber)}}
.metric-sub{{font-size:11px;color:var(--text3);margin-top:4px}}
.chart-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:20px}}
.chart-header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px}}
.chart-title{{font-size:11px;color:var(--text3);letter-spacing:1.5px;text-transform:uppercase;font-family:'DM Mono',monospace}}
.legend{{display:flex;gap:16px;flex-wrap:wrap}}
.legend-item{{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--text2)}}
.legend-dot{{width:8px;height:8px;border-radius:2px;flex-shrink:0}}
.data-table{{width:100%;border-collapse:collapse;font-size:13px}}
.data-table th{{font-size:10px;color:var(--text3);text-align:left;padding:8px 12px;border-bottom:1px solid var(--border);text-transform:uppercase;letter-spacing:1px;font-weight:400;font-family:'DM Mono',monospace}}
.data-table td{{padding:10px 12px;border-bottom:1px solid var(--border);color:var(--text);vertical-align:middle}}
.data-table tr:last-child td{{border-bottom:none}}
.data-table tr:hover td{{background:rgba(255,255,255,0.02)}}
.buy-tag{{color:var(--green);font-weight:500;font-family:'DM Mono',monospace;font-size:11px}}
.sell-tag{{color:var(--red);font-weight:500;font-family:'DM Mono',monospace;font-size:11px}}
.pnl-g{{color:var(--green);font-family:'DM Mono',monospace}}
.pnl-r{{color:var(--red);font-family:'DM Mono',monospace}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:500;font-family:'DM Mono',monospace;letter-spacing:0.5px}}
.badge-g{{background:var(--green-dim);color:var(--green)}}
.badge-r{{background:var(--red-dim);color:var(--red)}}
.badge-a{{background:var(--amber-dim);color:var(--amber)}}
.badge-n{{background:rgba(255,255,255,0.06);color:var(--text2)}}
.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}}
.form-group{{display:flex;flex-direction:column;gap:6px}}
.form-label{{font-size:11px;color:var(--text3);font-family:'DM Mono',monospace;letter-spacing:1px;text-transform:uppercase}}
.form-input,.form-select{{background:var(--bg3);border:1px solid var(--border2);border-radius:var(--radius-sm);padding:9px 12px;color:var(--text);font-size:13px;font-family:'DM Sans',sans-serif;width:100%;outline:none;transition:border-color .15s}}
.form-input:focus,.form-select:focus{{border-color:var(--green)}}
.form-select option{{background:var(--bg2)}}
.btn-action{{background:var(--green);color:#000;border:none;padding:10px 20px;border-radius:var(--radius-sm);font-size:13px;cursor:pointer;font-weight:500;transition:all .15s}}
.btn-action:hover{{background:var(--green2)}}
.btn-outline{{background:transparent;border:1px solid var(--border2);color:var(--text2);padding:10px 20px;border-radius:var(--radius-sm);font-size:13px;cursor:pointer;transition:all .15s;margin-left:8px}}
.btn-outline:hover{{border-color:var(--text2);color:var(--text)}}
.strat-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:20px}}
.strat-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:18px;transition:border-color .15s}}
.strat-card:hover{{border-color:var(--border2)}}
.strat-header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}}
.strat-name{{font-size:13px;font-weight:500;color:var(--text)}}
.strat-body{{font-size:12px;color:var(--text2);line-height:1.7}}
.strat-rule{{margin-top:8px;font-size:11px;font-family:'DM Mono',monospace;color:var(--text3)}}
.bt-summary{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px}}
.bt-metric{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:16px;text-align:center}}
.bt-metric-val{{font-size:24px;font-weight:300;font-family:'DM Mono',monospace;letter-spacing:-1px}}
.bt-metric-label{{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:1.5px;margin-top:6px}}
.info-box{{background:var(--blue-dim);border:1px solid rgba(74,158,255,0.2);border-radius:var(--radius-sm);padding:12px 16px;font-size:12px;color:var(--text2);line-height:1.7;margin-bottom:16px}}
.warn-box{{background:var(--amber-dim);border:1px solid rgba(240,168,50,0.2);border-radius:var(--radius-sm);padding:12px 16px;font-size:12px;color:var(--amber);line-height:1.7;margin-bottom:16px}}
.updated-badge{{font-size:11px;color:var(--text3);font-family:'DM Mono',monospace;background:var(--bg3);border:1px solid var(--border);padding:6px 12px;border-radius:var(--radius-sm)}}
::-webkit-scrollbar{{width:4px}}
::-webkit-scrollbar-thumb{{background:var(--border2);border-radius:2px}}
@media(max-width:768px){{
  .app{{grid-template-columns:1fr}}
  .sidebar{{position:fixed;bottom:0;left:0;right:0;height:auto;flex-direction:row;z-index:100;border-right:none;border-top:1px solid var(--border);overflow-x:auto}}
  .logo-area,.signal-pill,.sidebar-footer,.nav-label{{display:none}}
  .nav-section{{display:flex;flex-direction:row;padding:8px}}
  .nav-item{{flex-direction:column;gap:4px;font-size:10px;padding:8px 12px}}
  .main{{padding:16px;padding-bottom:80px}}
  .metrics-row,.bt-summary{{grid-template-columns:repeat(2,1fr)}}
  .strat-grid,.form-grid{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>

<!-- Lock Screen -->
<div id="lock-screen">
  <div class="lock-logo">Jun <span>Yadnap</span><br>Trade System</div>
  <div class="lock-sub">ASX Systematic Trading</div>
  <div class="lock-form">
    <input class="lock-input" type="password" id="lock-pw" placeholder="Enter password" onkeydown="if(event.key==='Enter')unlock()">
    <button class="lock-btn" onclick="unlock()">Unlock Dashboard</button>
    <div class="lock-error" id="lock-error">Incorrect password. Try again.</div>
  </div>
</div>

<div class="app" id="app-content" style="display:none">
<!-- SIDEBAR -->
<aside class="sidebar">
  <div class="logo-area">
    <div class="logo-name">Jun Yadnap<br>Trade System</div>
    <div class="logo-sub"><span class="logo-dot"></span>ASX Auto-Updated</div>
  </div>
  <div class="signal-pill">
    <div class="signal-pill-label">Current signal</div>
    <div class="signal-pill-action">{sd["action"]}</div>
    <div class="signal-pill-regime">{sd["regime"]}</div>
  </div>
  <div class="nav-section">
    <div class="nav-label">Navigation</div>
    <div class="nav-item active" onclick="showTab('dashboard',this)"><span class="nav-icon">◈</span> Dashboard</div>
    <div class="nav-item" onclick="showTab('trades',this)"><span class="nav-icon">◎</span> Paper Trades</div>
    <div class="nav-item" onclick="showTab('backtest',this)"><span class="nav-icon">◷</span> Backtest</div>
    <div class="nav-item" onclick="showTab('strategies',this)"><span class="nav-icon">◇</span> Strategies</div>
    <div class="nav-item" onclick="showTab('guide',this)"><span class="nav-icon">◌</span> How to Use</div>
  </div>
  <div class="sidebar-footer">
    Built: {build_time} {tz_name}<br>
    Auto-updates 4:15pm daily<br><br>
    Not financial advice.<br>
    Paper trading only.
  </div>
</aside>

<!-- MAIN -->
<main class="main">

<!-- DASHBOARD -->
<div id="tab-dashboard" class="tab active">
  <div class="page-header">
    <div>
      <div class="page-title">Dashboard</div>
      <div class="page-sub">Last updated: {build_time} {tz_name}</div>
    </div>
    <div class="updated-badge">Auto-updates 4:15pm AEST/AEDT</div>
  </div>

  <div class="signal-main">
    <div class="signal-regime">REGIME: {sd["regime"]}</div>
    <div class="signal-action">{action_html}</div>
    <div class="signal-reason">{sd["reason"]}</div>
    <div class="alloc-grid">
      <div class="alloc-box">
        <div class="alloc-name">GEAR — Long</div>
        <div class="alloc-pct g">{sd["gear"]}%</div>
        <div class="alloc-bar"><div class="alloc-fill" style="width:{sd["gear"]}%;background:var(--green)"></div></div>
      </div>
      <div class="alloc-box">
        <div class="alloc-name">BBOZ — Short</div>
        <div class="alloc-pct r">{sd["bboz"]}%</div>
        <div class="alloc-bar"><div class="alloc-fill" style="width:{sd["bboz"]}%;background:var(--red)"></div></div>
      </div>
      <div class="alloc-box">
        <div class="alloc-name">Cash</div>
        <div class="alloc-pct w">{sd["cash"]}%</div>
        <div class="alloc-bar"><div class="alloc-fill" style="width:{sd["cash"]}%;background:var(--text3)"></div></div>
      </div>
    </div>
  </div>

  <div class="metrics-row" style="grid-template-columns:repeat(4,1fr)">
    <div class="metric-box">
      <div class="metric-label">ASX 200</div>
      <div class="metric-val">{sd["price"]:,.0f}</div>
      <div class="metric-sub" style="color:{'var(--green)' if sd['change'] >= 0 else 'var(--red)'}">{sd["change"]:+.2f}% today</div>
    </div>
    <div class="metric-box">
      <div class="metric-label">RSI (14)</div>
      <div class="metric-val {'g' if sd['rsi'] > 65 else 'r' if sd['rsi'] < 35 else ''}">{sd["rsi"]:.1f}</div>
      <div class="metric-sub">{"Overbought" if sd["rsi"] > 70 else "Oversold" if sd["rsi"] < 30 else "Neutral"}</div>
    </div>
    <div class="metric-box">
      <div class="metric-label">vs SMA 250</div>
      <div class="metric-val {'g' if sd['ext250'] >= 0 else 'r'}">{sd["ext250"]:+.1f}%</div>
      <div class="metric-sub">SMA250: {sd["sma250"]:,.0f}</div>
    </div>
    <div class="metric-box">
      <div class="metric-label">vs SMA 20</div>
      <div class="metric-val {'g' if sd['ext20'] >= 0 else 'r'}">{sd["ext20"]:+.1f}%</div>
      <div class="metric-sub">SMA20: {sd["sma20"]:,.0f}</div>
    </div>
  </div>

  <div class="chart-card">
    <div class="chart-header">
      <div class="chart-title">ASX 200 — Price with SMA20 & SMA250</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#00d4a0"></div>ASX 200</div>
        <div class="legend-item"><div class="legend-dot" style="background:#f0a832"></div>SMA 20</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ff5e5e"></div>SMA 250</div>
      </div>
    </div>
    <div style="position:relative;height:280px"><canvas id="priceChart"></canvas></div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
    <div class="chart-card" style="margin-bottom:0">
      <div class="chart-header"><div class="chart-title">RSI (14)</div></div>
      <div style="position:relative;height:160px"><canvas id="rsiChart"></canvas></div>
    </div>
    <div class="chart-card" style="margin-bottom:0">
      <div class="chart-header"><div class="chart-title">Bollinger Bands</div></div>
      <div style="position:relative;height:160px"><canvas id="bbChart"></canvas></div>
    </div>
  </div>
</div>

<!-- PAPER TRADES -->
<div id="tab-trades" class="tab">
  <div class="page-header">
    <div><div class="page-title">Paper Trades</div><div class="page-sub">Starting capital: $100,000 AUD</div></div>
  </div>
  <div class="metrics-row">
    <div class="metric-box"><div class="metric-label">Portfolio</div><div class="metric-val" id="pt-portfolio">$100,000</div><div class="metric-sub">cash + open positions</div></div>
    <div class="metric-box"><div class="metric-label">Open Value</div><div class="metric-val a" id="pt-openval">$0</div><div class="metric-sub">at avg buy price</div></div>
    <div class="metric-box"><div class="metric-label">Realised P&amp;L</div><div class="metric-val" id="pt-return">0.0%</div><div class="metric-sub">closed trades only</div></div>
    <div class="metric-box"><div class="metric-label">Win rate</div><div class="metric-val" id="pt-winrate">—</div></div>
    <div class="metric-box"><div class="metric-label">Total trades</div><div class="metric-val" id="pt-count">0</div></div>
  </div>
  <div class="card" style="margin-bottom:20px">
    <div class="card-title">Log paper trade</div>
    <div class="form-grid">
      <div class="form-group"><label class="form-label">Date</label><input class="form-input" type="date" id="t-date"></div>
      <div class="form-group"><label class="form-label">Ticker</label><select class="form-select" id="t-ticker"><option>GEAR</option><option>BBOZ</option><option>CASH</option></select></div>
      <div class="form-group"><label class="form-label">Action</label><select class="form-select" id="t-action"><option>BUY</option><option>SELL</option><option>HOLD</option></select></div>
      <div class="form-group"><label class="form-label">Price ($)</label><input class="form-input" type="number" id="t-price" placeholder="e.g. 42.50" step="0.01"></div>
      <div class="form-group"><label class="form-label">Shares</label><input class="form-input" type="number" id="t-shares" placeholder="e.g. 500"></div>
      <div class="form-group"><label class="form-label">Strategy</label>
        <select class="form-select" id="t-strat">
          <option>Momentum Long</option><option>MR Long 1 (SMA20 Pullback)</option>
          <option>MR Long 2 (Deep Pullback)</option><option>MR Long 3 (SMA250 Bounce)</option>
          <option>MR Short (Overextension)</option><option>Momentum Short 1 (Breakdown)</option>
          <option>Momentum Short 2 (Failed Bounce)</option><option>Cash / No signal</option>
        </select>
      </div>
    </div>
    <button class="btn-action" onclick="logTrade()">Log Trade</button>
    <button class="btn-outline" onclick="clearTrades()">Clear All</button>
  </div>
  <div class="card">
    <div class="card-title">Trade log</div>
    <div style="overflow-x:auto">
      <table class="data-table">
        <thead><tr><th>#</th><th>Date</th><th>Ticker</th><th>Action</th><th>Shares</th><th>Price</th><th>Value</th><th>Strategy</th></tr></thead>
        <tbody id="trade-tbody"><tr><td colspan="8" style="color:var(--text3);padding:24px 12px;text-align:center;font-family:'DM Mono',monospace;font-size:12px;">No trades logged yet</td></tr></tbody>
      </table>
    </div>
  </div>
</div>

<!-- BACKTEST -->
<div id="tab-backtest" class="tab">
  <div class="page-header">
    <div><div class="page-title">Backtest 2022–2024</div><div class="page-sub">ASX 200 historical simulation — pre-calculated at build time</div></div>
  </div>
  <div class="info-box">Results use real ASX 200 daily closes. GEAR modelled as 2× daily return minus 0.35% MER/year. BBOZ modelled as −2× daily return minus 0.56% MER/year. Does not account for brokerage (~$9.50/trade on SelfWealth).</div>
  <div class="bt-summary">
    <div class="bt-metric"><div class="bt-metric-val {'g' if bt['total_return'] >= 0 else 'r'}">{bt["total_return"]:+.1f}%</div><div class="bt-metric-label">Total Return</div></div>
    <div class="bt-metric"><div class="bt-metric-val {'g' if bt['cagr'] >= 0 else 'r'}">{bt["cagr"]:+.1f}%</div><div class="bt-metric-label">CAGR</div></div>
    <div class="bt-metric"><div class="bt-metric-val r">{bt["max_dd"]:.1f}%</div><div class="bt-metric-label">Max Drawdown</div></div>
    <div class="bt-metric"><div class="bt-metric-val {'g' if bt['win_rate'] >= 50 else 'a'}">{bt["win_rate"]:.0f}%</div><div class="bt-metric-label">Win Rate</div></div>
    <div class="bt-metric"><div class="bt-metric-val {'g' if bt['sharpe'] >= 1 else 'a' if bt['sharpe'] >= 0 else 'r'}">{bt["sharpe"]:.2f}</div><div class="bt-metric-label">Sharpe Ratio</div></div>
  </div>
  <div class="chart-card">
    <div class="chart-header">
      <div class="chart-title">Equity curve — System vs Buy &amp; Hold ASX 200</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#00d4a0"></div>JYTS System</div>
        <div class="legend-item"><div class="legend-dot" style="background:#4a9eff"></div>ASX 200 B&amp;H</div>
      </div>
    </div>
    <div style="position:relative;height:300px"><canvas id="btEquityChart"></canvas></div>
  </div>
  <div class="chart-card">
    <div class="chart-header"><div class="chart-title">Drawdown</div></div>
    <div style="position:relative;height:160px"><canvas id="btDDChart"></canvas></div>
  </div>
  <div class="card">
    <div class="card-title">Regime performance breakdown</div>
    <table class="data-table">
      <thead><tr><th>Regime</th><th>Days</th><th>ETF</th><th>System Return</th><th>ASX Return</th><th>Result</th></tr></thead>
      <tbody>{bt_regime_rows}</tbody>
    </table>
  </div>
  <div class="warn-box" style="margin-top:16px">Past performance does not guarantee future results.</div>
</div>

<!-- STRATEGIES -->
<div id="tab-strategies" class="tab">
  <div class="page-header"><div><div class="page-title">7 Strategies</div><div class="page-sub">ASX 200 adapted — GEAR (long) &amp; BBOZ (short)</div></div></div>
  <div class="strat-grid">
    <div class="strat-card"><div class="strat-header"><div class="strat-name">Momentum Long</div><span class="badge badge-g">GEAR</span></div><div class="strat-body">ASX 200 breaks above upper Bollinger Band while above SMA20. Ride strong upward momentum.</div><div class="strat-rule">Entry: price &gt; upper BB + above SMA20<br>Exit: close below SMA20 · Regime: Uptrend</div></div>
    <div class="strat-card"><div class="strat-header"><div class="strat-name">MR Long 1 — SMA20 Pullback</div><span class="badge badge-g">GEAR</span></div><div class="strat-body">ASX 200 pulls back to within 1% of SMA20. Buy the dip expecting a bounce.</div><div class="strat-rule">Entry: within 1% of SMA20<br>Exit: extends 3%+ above SMA20 · Regime: Uptrend</div></div>
    <div class="strat-card"><div class="strat-header"><div class="strat-name">MR Long 2 — Deep Pullback</div><span class="badge badge-g">GEAR</span></div><div class="strat-body">ASX drops below lower BB but stays above SMA250. Aggressive dip buy.</div><div class="strat-rule">Entry: price &lt; lower BB AND &gt; SMA250<br>Exit: reclaims middle BB · Regime: Uptrend</div></div>
    <div class="strat-card"><div class="strat-header"><div class="strat-name">MR Long 3 — SMA250 Bounce</div><span class="badge badge-g">GEAR</span></div><div class="strat-body">ASX approaches SMA250 from above with a bullish candle. Catch the long-term support bounce.</div><div class="strat-rule">Entry: within 3% of SMA250 + bullish candle<br>Exit: 5%+ above SMA20 · Regime: Near SMA250</div></div>
    <div class="strat-card"><div class="strat-header"><div class="strat-name">MR Short — Overextension</div><span class="badge badge-r">BBOZ</span></div><div class="strat-body">ASX is 4%+ above SMA20 AND above upper BB. Fade extreme overextension.</div><div class="strat-rule">Entry: ext20 &gt; 4% AND price &gt; upper BB<br>Exit: reverts to SMA20 · Regime: Uptrend</div></div>
    <div class="strat-card"><div class="strat-header"><div class="strat-name">Momentum Short 1 — Breakdown</div><span class="badge badge-r">BBOZ</span></div><div class="strat-body">ASX closes below SMA250 AND lower BB. Major bearish signal. Size scales with depth.</div><div class="strat-rule">Entry: price &lt; SMA250 + lower BB<br>Exit: reclaims SMA250 · Regime: Downtrend</div></div>
    <div class="strat-card" style="grid-column:span 2"><div class="strat-header"><div class="strat-name">Momentum Short 2 — Failed Bounce</div><span class="badge badge-r">BBOZ</span></div><div class="strat-body">In a downtrend, ASX bounces to SMA20 then closes back below it. Classic bull trap — short the failure.</div><div class="strat-rule">Entry: bounce to SMA20 then close below it in downtrend · Exit: above SMA20 for 3 days</div></div>
  </div>
  <div class="card">
    <div class="card-title">Regime playbook — $100K capital</div>
    <table class="data-table">
      <thead><tr><th>Regime</th><th>Condition</th><th>ETF</th><th>Allocation</th><th>$ Amount</th></tr></thead>
      <tbody>
        <tr><td>Strong uptrend</td><td>ASX &gt;2% above SMA250, RSI &gt;55</td><td><span class="badge badge-g">GEAR</span></td><td>80%</td><td class="pnl-g">$80,000</td></tr>
        <tr><td>Uptrend</td><td>ASX above SMA250</td><td><span class="badge badge-g">GEAR</span></td><td>50%</td><td class="pnl-g">$50,000</td></tr>
        <tr><td>Neutral</td><td>Within 1% of SMA250</td><td><span class="badge badge-n">CASH</span></td><td>100%</td><td style="color:var(--text3)">$100,000</td></tr>
        <tr><td>Downtrend</td><td>ASX below SMA250</td><td><span class="badge badge-r">BBOZ</span></td><td>30%</td><td class="pnl-r">$30,000</td></tr>
        <tr><td>Strong downtrend</td><td>ASX &gt;2% below SMA250, RSI &lt;40</td><td><span class="badge badge-r">BBOZ</span></td><td>60%</td><td class="pnl-r">$60,000</td></tr>
      </tbody>
    </table>
  </div>
</div>

<!-- HOW TO USE -->
<div id="tab-guide" class="tab">
  <div class="page-header"><div><div class="page-title">How to Use</div><div class="page-sub">Daily routine and trading rules</div></div></div>
  <div class="card" style="margin-bottom:20px">
    <div class="card-title">Daily routine — 10 minutes</div>
    <table class="data-table">
      <thead><tr><th>Time (AEST/AEDT)</th><th>Action</th><th>Where</th></tr></thead>
      <tbody>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:00 pm</td><td>ASX closes</td><td style="color:var(--text3)">—</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:15 pm</td><td>Open your GitHub Pages URL — dashboard auto-updated</td><td style="color:var(--text3)">Browser</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:16 pm</td><td>Enter password → read Signal Card</td><td style="color:var(--text3)">Dashboard tab</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:18 pm</td><td>Check GEAR or BBOZ closing price</td><td style="color:var(--text3)">SelfWealth</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:20 pm</td><td>Log your paper trade</td><td style="color:var(--text3)">Paper Trades tab</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:25 pm</td><td>Done. See you tomorrow.</td><td style="color:var(--text3)">—</td></tr>
      </tbody>
    </table>
  </div>
  <div class="card" style="margin-bottom:20px">
    <div class="card-title">Stop loss rules</div>
    <table class="data-table">
      <thead><tr><th>Rule</th><th>Trigger</th><th>Action</th></tr></thead>
      <tbody>
        <tr><td style="color:var(--amber)">Regime change</td><td>ASX 200 crosses SMA250</td><td>Exit all positions immediately</td></tr>
        <tr><td style="color:var(--red)">Hard stop</td><td>Position down 7%</td><td>Exit regardless of signal</td></tr>
        <tr><td style="color:var(--green)">Profit trail</td><td>Position up 10%</td><td>Move stop to breakeven</td></tr>
        <tr><td style="color:var(--green)">Full trail</td><td>Position up 15%</td><td>Trail stop at 1.5× ATR</td></tr>
      </tbody>
    </table>
  </div>
  <div class="warn-box">Not financial advice. Paper trade for at least 3 months before using real money. GEAR and BBOZ are complex leveraged products — losses can be amplified significantly.</div>
</div>

</main>
</div>

<script>
// ─── Password ─────────────────────────────────────────────────────
function unlock() {{
  const pw = document.getElementById('lock-pw').value;
  if (pw === 'Youarewhoyouthinkyouare') {{
    document.getElementById('lock-screen').classList.add('hidden');
    document.getElementById('app-content').style.display = 'grid';
    sessionStorage.setItem('jyts_auth', '1');
  }} else {{
    document.getElementById('lock-error').style.display = 'block';
    document.getElementById('lock-pw').value = '';
  }}
}}
if (sessionStorage.getItem('jyts_auth') === '1') {{
  document.getElementById('lock-screen').classList.add('hidden');
  document.getElementById('app-content').style.display = 'grid';
}}

// ─── Nav ──────────────────────────────────────────────────────────
function showTab(name, el) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (el) el.classList.add('active');
}}

// ─── Charts (pre-baked data) ──────────────────────────────────────
const CD = {json.dumps(cd)};
const BT = {json.dumps(bt)};
const chartOpts = {{
  responsive: true, maintainAspectRatio: false,
  plugins: {{ legend: {{ display: false }} }},
  scales: {{
    x: {{ ticks: {{ maxTicksLimit: 8, font: {{ size: 10 }}, color: '#4a5568' }}, grid: {{ color: 'rgba(255,255,255,0.04)' }}, border: {{ display: false }} }},
    y: {{ ticks: {{ font: {{ size: 10 }}, color: '#4a5568', callback: v => v.toLocaleString() }}, grid: {{ color: 'rgba(255,255,255,0.04)' }}, border: {{ display: false }} }}
  }}
}};

new Chart(document.getElementById('priceChart'), {{
  type: 'line',
  data: {{ labels: CD.labels, datasets: [
    {{ data: CD.closes, borderColor: '#00d4a0', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false }},
    {{ data: CD.sma20, borderColor: '#f0a832', borderWidth: 1, pointRadius: 0, borderDash: [4,3], tension: 0.3, fill: false }},
    {{ data: CD.sma250, borderColor: '#ff5e5e', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false }}
  ]}},
  options: chartOpts
}});

new Chart(document.getElementById('rsiChart'), {{
  type: 'line',
  data: {{ labels: CD.labels, datasets: [{{ data: CD.rsi, borderColor: '#4a9eff', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false }}] }},
  options: {{ ...chartOpts, scales: {{ ...chartOpts.scales, y: {{ min: 0, max: 100, ticks: {{ font: {{ size: 10 }}, color: '#4a5568', stepSize: 25 }}, grid: {{ color: 'rgba(255,255,255,0.04)' }}, border: {{ display: false }} }} }} }}
}});

new Chart(document.getElementById('bbChart'), {{
  type: 'line',
  data: {{ labels: CD.labels, datasets: [
    {{ data: CD.closes, borderColor: '#00d4a0', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false }},
    {{ data: CD.bb_upper, borderColor: 'rgba(240,168,50,0.5)', borderWidth: 1, pointRadius: 0, borderDash: [3,3], tension: 0.3, fill: false }},
    {{ data: CD.bb_lower, borderColor: 'rgba(240,168,50,0.5)', borderWidth: 1, pointRadius: 0, borderDash: [3,3], tension: 0.3, fill: false }},
    {{ data: CD.bb_mid, borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1, pointRadius: 0, tension: 0.3, fill: false }}
  ]}},
  options: chartOpts
}});

new Chart(document.getElementById('btEquityChart'), {{
  type: 'line',
  data: {{ labels: BT.labels, datasets: [
    {{ label: 'JYTS System', data: BT.equity, borderColor: '#00d4a0', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false }},
    {{ label: 'ASX 200 B&H', data: BT.bh_equity, borderColor: '#4a9eff', borderWidth: 1.5, pointRadius: 0, borderDash: [4,3], tension: 0.3, fill: false }}
  ]}},
  options: {{ ...chartOpts, plugins: {{ legend: {{ display: true, labels: {{ color: '#8a95a8', font: {{ size: 11 }}, boxWidth: 10 }} }} }}, scales: {{ ...chartOpts.scales, y: {{ ticks: {{ font: {{ size: 10 }}, color: '#4a5568', callback: v => '$' + (v/1000).toFixed(0) + 'K' }}, grid: {{ color: 'rgba(255,255,255,0.04)' }}, border: {{ display: false }} }} }} }}
}});

new Chart(document.getElementById('btDDChart'), {{
  type: 'line',
  data: {{ labels: BT.labels, datasets: [{{ data: BT.drawdowns, borderColor: '#ff5e5e', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: {{ target: 'origin', above: 'rgba(255,94,94,0.08)' }} }}] }},
  options: {{ ...chartOpts, scales: {{ ...chartOpts.scales, y: {{ ticks: {{ font: {{ size: 10 }}, color: '#4a5568', callback: v => v.toFixed(0) + '%' }}, grid: {{ color: 'rgba(255,255,255,0.04)' }}, border: {{ display: false }} }} }} }}
}});

// ─── Paper trades ─────────────────────────────────────────────────
let trades = JSON.parse(localStorage.getItem('jyts_trades') || '[]');
document.getElementById('t-date').value = new Date().toISOString().split('T')[0];
renderTrades();

function logTrade() {{
  const date = document.getElementById('t-date').value;
  const ticker = document.getElementById('t-ticker').value;
  const action = document.getElementById('t-action').value;
  const price = parseFloat(document.getElementById('t-price').value);
  const shares = parseInt(document.getElementById('t-shares').value);
  const strat = document.getElementById('t-strat').value;
  if (!date || !price || !shares) {{ alert('Please fill in date, price and shares.'); return; }}
  trades.push({{ date, ticker, action, price, shares, value: parseFloat((price*shares).toFixed(2)), strat }});
  localStorage.setItem('jyts_trades', JSON.stringify(trades));
  renderTrades();
  document.getElementById('t-price').value = '';
  document.getElementById('t-shares').value = '';
}}

function clearTrades() {{
  if (!confirm('Clear all paper trades?')) return;
  trades = [];
  localStorage.setItem('jyts_trades', JSON.stringify(trades));
  renderTrades();
}}

function renderTrades() {{
  let openPositions = {{}};
  let realisedPnL = 0;
  let wins = 0, losses = 0;

  trades.forEach(t => {{
    if (t.action === 'BUY') {{
      if (!openPositions[t.ticker]) openPositions[t.ticker] = {{ shares: 0, cost: 0 }};
      openPositions[t.ticker].shares += t.shares;
      openPositions[t.ticker].cost += t.value;
    }}
    if (t.action === 'SELL') {{
      const pos = openPositions[t.ticker];
      if (pos && pos.shares > 0) {{
        const avgCost = pos.cost / pos.shares;
        const pnl = (t.price - avgCost) * t.shares;
        realisedPnL += pnl;
        if (pnl >= 0) wins++; else losses++;
        pos.shares -= t.shares;
        pos.cost = pos.shares * avgCost;
        if (pos.shares <= 0) delete openPositions[t.ticker];
      }}
    }}
  }});

  // Open position value — use last known buy price as proxy
  let openValue = 0;
  const tickersSeen = {{}};
  trades.slice().reverse().forEach(t => {{
    if (openPositions[t.ticker] && !tickersSeen[t.ticker] && t.action === 'BUY') {{
      openValue += openPositions[t.ticker].shares * t.price;
      tickersSeen[t.ticker] = true;
    }}
  }});

  const portfolioVal = 100000 + realisedPnL;
  const ret = realisedPnL / 100000 * 100;

  document.getElementById('pt-portfolio').textContent = '$' + portfolioVal.toLocaleString('en-AU', {{ maximumFractionDigits: 0 }});
  document.getElementById('pt-openval').textContent = '$' + openValue.toLocaleString('en-AU', {{ maximumFractionDigits: 0 }});
  const retEl = document.getElementById('pt-return');
  retEl.textContent = (ret >= 0 ? '+' : '') + ret.toFixed(2) + '%';
  retEl.className = 'metric-val ' + (ret >= 0 ? 'g' : 'r');
  document.getElementById('pt-count').textContent = trades.length;
  document.getElementById('pt-winrate').textContent = (wins + losses) > 0 ? Math.round(wins / (wins + losses) * 100) + '%' : '—';

  const tbody = document.getElementById('trade-tbody');
  if (!trades.length) {{
    tbody.innerHTML = '<tr><td colspan="8" style="color:var(--text3);padding:24px 12px;text-align:center;font-family:\\'DM Mono\\',monospace;font-size:12px;">No trades logged yet</td></tr>';
    return;
  }}
  tbody.innerHTML = trades.slice().reverse().map((t, i) =>
    `<tr>
      <td style="color:var(--text3);font-family:'DM Mono',monospace">${{trades.length - i}}</td>
      <td style="font-family:'DM Mono',monospace;font-size:12px">${{t.date}}</td>
      <td><span class="badge ${{t.ticker==='GEAR'?'badge-g':t.ticker==='BBOZ'?'badge-r':'badge-n'}}">${{t.ticker}}</span></td>
      <td class="${{t.action==='BUY'?'buy-tag':'sell-tag'}}">${{t.action}}</td>
      <td style="font-family:'DM Mono',monospace">${{t.shares.toLocaleString()}}</td>
      <td style="font-family:'DM Mono',monospace">$${{t.price.toFixed(2)}}</td>
      <td style="font-family:'DM Mono',monospace">$${{t.value.toLocaleString('en-AU',{{maximumFractionDigits:0}})}}</td>
      <td style="font-size:11px;color:var(--text3)">${{t.strat}}</td>
    </tr>`
  ).join('');
}}
</script>
</body>
</html>"""

def hash_password(password):
    import ctypes
    h = ctypes.c_int32(0)
    for c in password:
        h = ctypes.c_int32(31 * h.value + ord(c))
    val = h.value
    chars = '0123456789abcdefghijklmnopqrstuvwxyz'
    negative = val < 0
    n = abs(val)
    if n == 0: return '0'
    result = ''
    while n:
        result = chars[n % 36] + result
        n //= 36
    return ('-' if negative else '') + result

generate_html.__doc__ = "patched"

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=== Jun Yadnap Trade System — Build Script ===")
    local_time, tz_name = get_aest_now()
    build_time = local_time.strftime("%d %b %Y %I:%M %p")
    print(f"Build time: {build_time} {tz_name}")

    df = fetch_asx_data()

    closes = df["Close"]
    sma20 = calc_sma(closes, 20)
    sma250 = calc_sma(closes, 250)
    rsi = calc_rsi(closes, 14)
    bb_upper, bb_mid, bb_lower = calc_bb(closes, 20)

    price = float(closes.iloc[-1])
    prev_price = float(closes.iloc[-2])
    s20 = float(sma20.iloc[-1])
    s250 = float(sma250.iloc[-1])
    r = float(rsi.iloc[-1])
    bbu = float(bb_upper.iloc[-1])
    bbl = float(bb_lower.iloc[-1])

    regime = get_regime(price, s250, r)
    signal = get_signal(regime, price, s20, s250, bbu, bbl, r)

    signal_data = {
        **signal,
        "regime": regime,
        "price": price,
        "change": (price - prev_price) / prev_price * 100,
        "rsi": r,
        "sma20": s20,
        "sma250": s250,
        "ext20": (price - s20) / s20 * 100,
        "ext250": (price - s250) / s250 * 100,
    }

    print(f"  Regime: {regime} | Signal: {signal['action']}")

    chart_data = build_chart_data(df)
    print("  Chart data built.")

    backtest_data = run_backtest(df)
    print(f"  Backtest: Return={backtest_data['total_return']:+.1f}% | MaxDD={backtest_data['max_dd']:.1f}% | WinRate={backtest_data['win_rate']:.0f}%")

    pw_hash = hash_password("Youarewhoyouthinkyouare")

    html = generate_html(signal_data, chart_data, backtest_data, build_time, tz_name)
    html = html.replace("'{hash_password(\"Youarewhoyouthinkyouare\")}'", f"'{pw_hash}'")

    os.makedirs("docs", exist_ok=True)
    out_path = "docs/index.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Dashboard written to {out_path}")
    print("=== Build complete ===")

if __name__ == "__main__":
    main()
 
