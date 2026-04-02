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

# ─── Fetch ASX Data ───────────────────────────────────────────────────────────
def fetch_asx_data():
    print("Fetching ASX 200 data...")
    ticker = yf.Ticker("^AXJO")
    df = ticker.history(period="2y", interval="1d")
    df = df[["Close"]].copy()
    df.dropna(inplace=True)
    df.index = pd.to_datetime(df.index)
    print(f"  Got {len(df)} days. Latest: {df.index[-1].date()} = {df['Close'].iloc[-1]:.0f}")
    return df

# ─── Fetch ETF Data ───────────────────────────────────────────────────────────
def fetch_etf_data():
    print("Fetching GEAR and BBOZ data...")
    gear_df, bboz_df = None, None
    try:
        g = yf.Ticker("GEAR.AX")
        gear_df = g.history(period="2y", interval="1d")[["Close"]].copy()
        gear_df.dropna(inplace=True)
        gear_df.index = pd.to_datetime(gear_df.index).tz_localize(None)
        print(f"  GEAR: {len(gear_df)} days")
    except Exception as e:
        print(f"  GEAR fetch failed: {e}")
    try:
        b = yf.Ticker("BBOZ.AX")
        bboz_df = b.history(period="2y", interval="1d")[["Close"]].copy()
        bboz_df.dropna(inplace=True)
        bboz_df.index = pd.to_datetime(bboz_df.index).tz_localize(None)
        print(f"  BBOZ: {len(bboz_df)} days")
    except Exception as e:
        print(f"  BBOZ fetch failed: {e}")
    return gear_df, bboz_df

# ─── Build Backtest ───────────────────────────────────────────────────────────
def run_backtest(df):
    closes = df["Close"]
    sma20 = calc_sma(closes, 20)
    sma250 = calc_sma(closes, 250)
    rsi = calc_rsi(closes, 14)
    bb_upper, bb_mid, bb_lower = calc_bb(closes, 20)

    portfolio = 100000.0
    bh_base = closes.iloc[0]
    equity, bh_equity, dd_series, labels = [], [], [], []
    peak = 100000.0
    wins = losses = 0
    prev_val = 100000.0
    regime_stats = {}
    regime_transitions = []
    prev_regime = None

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

        if regime != prev_regime and prev_regime is not None:
            regime_transitions.append({
                "date": closes.index[i].strftime("%d %b %Y"),
                "from": prev_regime,
                "to": regime,
                "price": round(float(price))
            })
        prev_regime = regime

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
        regime_rows.append({"regime": r, "days": s["days"],
                             "sys_ret": round(sys_ret, 1), "asx_ret": round(asx_ret, 1)})

    return {
        "total_return": round(total_return, 1), "cagr": round(cagr, 1),
        "max_dd": round(max_dd, 1), "win_rate": round(win_rate, 0),
        "sharpe": round(sharpe, 2), "labels": labels,
        "equity": equity, "bh_equity": bh_equity, "drawdowns": dd_series,
        "regime_rows": regime_rows, "regime_transitions": regime_transitions[-10:]
    }

# ─── Build Chart Data ─────────────────────────────────────────────────────────
def build_chart_data(df, gear_df, bboz_df, n=120):
    closes = df["Close"]
    sma20 = calc_sma(closes, 20)
    sma250 = calc_sma(closes, 250)
    rsi = calc_rsi(closes, 14)
    bb_upper, bb_mid, bb_lower = calc_bb(closes, 20)

    def fmt(series):
        return [round(v, 2) if not pd.isna(v) else None for v in series.iloc[-n:]]

    labels = [d.strftime("%d %b") for d in closes.index[-n:]]
    asx_dates = closes.index[-n:]

    # Normalise ASX to 100
    raw_closes = fmt(closes)
    base_asx = next((v for v in raw_closes if v), 1)
    asx_norm = [round(v / base_asx * 100, 2) if v else None for v in raw_closes]

    def get_etf_norm(etf_df):
        if etf_df is None or len(etf_df) == 0:
            return []
        vals = []
        for d in asx_dates:
            d_n = d.tz_localize(None) if hasattr(d, 'tz_localize') and d.tzinfo else d
            m = etf_df.index[etf_df.index <= d_n]
            vals.append(round(float(etf_df.loc[m[-1], "Close"]), 2) if len(m) > 0 else None)
        base = next((v for v in vals if v), None)
        if not base:
            return []
        return [round(v / base * 100, 2) if v else None for v in vals]

    return {
        "labels": labels,
        "closes": raw_closes,
        "sma20": fmt(sma20),
        "sma250": fmt(sma250),
        "rsi": fmt(rsi),
        "asx_norm": asx_norm,
        "gear_norm": get_etf_norm(gear_df),
        "bboz_norm": get_etf_norm(bboz_df),
    }

# ─── Generate HTML ────────────────────────────────────────────────────────────
def generate_html(signal_data, chart_data, backtest_data, build_time, tz_name):
    bt = backtest_data
    cd = chart_data
    sd = signal_data

    # Regime colours
    regime = sd["regime"]
    if "STRONG UP" in regime:
        rc, rbg, rborder = "#15803d", "#f0fdf4", "#bbf7d0"
    elif "UP" in regime:
        rc, rbg, rborder = "#1d4ed8", "#eff6ff", "#bfdbfe"
    elif "STRONG DOWN" in regime:
        rc, rbg, rborder = "#b91c1c", "#fef2f2", "#fecaca"
    elif "DOWN" in regime:
        rc, rbg, rborder = "#c2410c", "#fff7ed", "#fed7aa"
    else:
        rc, rbg, rborder = "#475569", "#f8fafc", "#e2e8f0"

    action_display = sd["action"]
    if "GEAR" in action_display:
        action_display = action_display.replace("GEAR", '<span style="color:#1d4ed8;font-weight:700">GEAR</span>')
    elif "BBOZ" in action_display:
        action_display = action_display.replace("BBOZ", '<span style="color:#b91c1c;font-weight:700">BBOZ</span>')

    # Regime table rows
    bt_regime_rows = ""
    for r in bt["regime_rows"]:
        etf = "GEAR" if "UP" in r["regime"] else "BBOZ" if "DOWN" in r["regime"] else "CASH"
        bc = "badge-up" if "UP" in r["regime"] else "badge-dn" if "DOWN" in r["regime"] else "badge-nu"
        sc = "pos" if r["sys_ret"] >= 0 else "neg"
        ac = "pos" if r["asx_ret"] >= 0 else "neg"
        res = "result-win\">Outperformed" if r["sys_ret"] >= r["asx_ret"] else "result-lose\">Underperformed"
        bt_regime_rows += f"""<tr>
          <td>{r["regime"]}</td><td>{r["days"]}d</td>
          <td><span class="badge {bc}">{etf}</span></td>
          <td class="{sc}">{r["sys_ret"]:+.1f}%</td>
          <td class="{ac}">{r["asx_ret"]:+.1f}%</td>
          <td><span class="result {res}</span></td></tr>"""

    # Transition rows
    trans_rows = ""
    for t in reversed(bt.get("regime_transitions", [])):
        fc = "badge-up" if "UP" in t["from"] else "badge-dn" if "DOWN" in t["from"] else "badge-nu"
        tc = "badge-up" if "UP" in t["to"] else "badge-dn" if "DOWN" in t["to"] else "badge-nu"
        trans_rows += f"""<tr>
          <td style="font-family:'DM Mono',monospace;font-size:12px">{t["date"]}</td>
          <td><span class="badge {fc}">{t["from"]}</span></td>
          <td style="color:#94a3b8;font-size:16px">→</td>
          <td><span class="badge {tc}">{t["to"]}</span></td>
          <td style="font-family:'DM Mono',monospace">{t["price"]:,}</td></tr>"""
    if not trans_rows:
        trans_rows = '<tr><td colspan="5" style="color:#94a3b8;text-align:center;padding:20px;font-family:DM Mono,monospace;font-size:12px">No transitions recorded yet</td></tr>'

    bt_rc = "#15803d" if bt["total_return"] >= 0 else "#b91c1c"
    bt_cc = "#15803d" if bt["cagr"] >= 0 else "#b91c1c"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jun Yadnap Trade System</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Fraunces:ital,opsz,wght@0,9..144,600;1,9..144,400&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#f1f5f9;--bg2:#ffffff;--bg3:#f8fafc;
  --border:#e2e8f0;--border2:#cbd5e1;
  --text:#0f172a;--text2:#475569;--text3:#94a3b8;
  --green:#15803d;--green-bg:#f0fdf4;
  --red:#b91c1c;--red-bg:#fef2f2;
  --blue:#1d4ed8;--blue-bg:#eff6ff;
  --amber:#b45309;--amber-bg:#fffbeb;
  --radius:12px;--radius-sm:8px;
  --shadow:0 1px 3px rgba(0,0,0,0.07),0 1px 2px rgba(0,0,0,0.04);
  --shadow-md:0 4px 12px rgba(0,0,0,0.08);
}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;font-size:14px;min-height:100vh}}

/* Lock */
#lock-screen{{position:fixed;inset:0;background:#0f172a;z-index:999;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:28px}}
#lock-screen.hidden{{display:none}}
.lock-logo{{font-family:'Fraunces',serif;font-size:34px;color:#f8fafc;text-align:center;line-height:1.2}}
.lock-logo em{{color:#93c5fd;font-style:italic}}
.lock-sub{{font-size:10px;color:#475569;font-family:'DM Mono',monospace;letter-spacing:2px;text-transform:uppercase}}
.lock-form{{display:flex;flex-direction:column;gap:12px;width:300px}}
.lock-input{{background:#1e293b;border:1px solid #334155;border-radius:var(--radius-sm);padding:13px 16px;color:#f1f5f9;font-size:14px;outline:none;transition:border-color .15s;font-family:'DM Sans',sans-serif}}
.lock-input:focus{{border-color:#93c5fd}}
.lock-btn{{background:#2563eb;color:#fff;border:none;padding:13px;border-radius:var(--radius-sm);font-size:14px;font-weight:600;cursor:pointer;transition:background .15s}}
.lock-btn:hover{{background:#1d4ed8}}
.lock-error{{font-size:12px;color:#f87171;text-align:center;display:none;font-family:'DM Mono',monospace}}

/* Layout */
.app{{display:grid;grid-template-columns:240px 1fr;min-height:100vh}}
.sidebar{{background:#0f172a;display:flex;flex-direction:column;position:sticky;top:0;height:100vh;overflow-y:auto}}
.main{{padding:32px 40px;overflow-y:auto}}

/* Sidebar */
.logo-area{{padding:28px 20px 22px;border-bottom:1px solid #1e293b}}
.logo-name{{font-family:'Fraunces',serif;font-size:18px;color:#f8fafc;line-height:1.4}}
.logo-name em{{color:#93c5fd;font-style:italic}}
.logo-sub{{font-size:10px;color:#334155;letter-spacing:1.5px;text-transform:uppercase;margin-top:6px;font-family:'DM Mono',monospace}}
.dot{{width:7px;height:7px;border-radius:50%;background:#22c55e;display:inline-block;margin-right:6px;animation:pulse 2s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:0.3;transform:scale(0.75)}}}}
.sig-pill{{margin:16px 12px;padding:14px 16px;background:#1e293b;border-radius:var(--radius);border:1px solid #334155}}
.sig-pill-label{{font-size:10px;color:#475569;letter-spacing:1px;text-transform:uppercase;font-family:'DM Mono',monospace;margin-bottom:8px}}
.sig-pill-action{{font-size:15px;font-weight:600;color:{rc};font-family:'DM Mono',monospace}}
.sig-pill-regime{{font-size:11px;color:#64748b;margin-top:3px}}
.nav-section{{padding:16px 12px 8px}}
.nav-label{{font-size:10px;color:#1e293b;letter-spacing:1.5px;text-transform:uppercase;font-family:'DM Mono',monospace;padding:0 8px;margin-bottom:8px}}
.nav-item{{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:var(--radius-sm);cursor:pointer;color:#475569;font-size:13px;transition:all .15s;margin-bottom:2px}}
.nav-item:hover{{background:#1e293b;color:#e2e8f0}}
.nav-item.active{{background:#1e3a5f;color:#93c5fd;font-weight:500}}
.nav-icon{{font-size:13px;width:18px;text-align:center}}
.sb-footer{{margin-top:auto;padding:16px;border-top:1px solid #1e293b;font-size:11px;color:#334155;line-height:1.9;font-family:'DM Mono',monospace}}

/* Tabs */
.tab{{display:none}}.tab.active{{display:block}}

/* Page header */
.page-header{{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:28px;flex-wrap:wrap;gap:12px}}
.page-title{{font-family:'Fraunces',serif;font-size:28px;color:var(--text)}}
.page-sub{{font-size:12px;color:var(--text3);margin-top:4px;font-family:'DM Mono',monospace}}
.build-badge{{font-size:11px;color:var(--text3);font-family:'DM Mono',monospace;background:var(--bg2);border:1px solid var(--border);padding:6px 14px;border-radius:20px;box-shadow:var(--shadow)}}

/* Signal card */
.signal-card{{background:{rbg};border:1px solid {rborder};border-left:4px solid {rc};border-radius:var(--radius);padding:24px 28px;margin-bottom:24px;box-shadow:var(--shadow)}}
.signal-regime-tag{{font-size:11px;color:{rc};letter-spacing:2px;text-transform:uppercase;font-family:'DM Mono',monospace;font-weight:500;margin-bottom:8px}}
.signal-action{{font-family:'Fraunces',serif;font-size:36px;color:var(--text);line-height:1;margin-bottom:10px}}
.signal-reason{{font-size:13px;color:var(--text2);line-height:1.7;max-width:600px}}
.alloc-row{{display:flex;gap:10px;margin-top:20px;flex-wrap:wrap}}
.alloc-chip{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px 16px;min-width:105px;box-shadow:var(--shadow)}}
.alloc-chip-name{{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:1px;font-family:'DM Mono',monospace;margin-bottom:4px}}
.alloc-chip-val{{font-size:22px;font-weight:700;font-family:'DM Mono',monospace}}
.av-gear{{color:var(--blue)}}.av-bboz{{color:var(--red)}}.av-cash{{color:var(--text2)}}

/* Metric cards */
.metrics-row{{display:grid;gap:14px;margin-bottom:24px}}
.m5{{grid-template-columns:repeat(5,1fr)}}.m4{{grid-template-columns:repeat(4,1fr)}}.m3{{grid-template-columns:repeat(3,1fr)}}
.metric-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:18px 20px;box-shadow:var(--shadow)}}
.metric-label{{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:1.5px;font-family:'DM Mono',monospace;margin-bottom:10px}}
.metric-val{{font-size:24px;font-weight:700;color:var(--text);font-family:'DM Mono',monospace;letter-spacing:-0.5px}}
.metric-val.pos{{color:var(--green)}}.metric-val.neg{{color:var(--red)}}.metric-val.neu{{color:var(--amber)}}
.metric-sub{{font-size:11px;color:var(--text3);margin-top:5px}}

/* Chart cards */
.card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:22px;margin-bottom:24px;box-shadow:var(--shadow)}}
.card-header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;flex-wrap:wrap;gap:8px}}
.card-title{{font-size:12px;color:var(--text2);text-transform:uppercase;letter-spacing:1.5px;font-family:'DM Mono',monospace;font-weight:500}}
.legend{{display:flex;gap:14px;flex-wrap:wrap}}
.legend-item{{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--text2)}}
.legend-line{{width:14px;height:2px;border-radius:2px}}

/* Section title */
.section-title{{font-family:'Fraunces',serif;font-size:19px;color:var(--text);margin-bottom:16px;margin-top:4px}}

/* Perf summary */
.perf-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:24px}}
.perf-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:18px 20px;box-shadow:var(--shadow)}}
.perf-label{{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:1px;font-family:'DM Mono',monospace;margin-bottom:6px}}
.perf-val{{font-size:20px;font-weight:700;font-family:'DM Mono',monospace}}
.perf-val.pos{{color:var(--green)}}.perf-val.neg{{color:var(--red)}}

/* Tables */
.data-table{{width:100%;border-collapse:collapse;font-size:13px}}
.data-table th{{font-size:10px;color:var(--text3);text-align:left;padding:9px 12px;border-bottom:2px solid var(--border);text-transform:uppercase;letter-spacing:1px;font-weight:500;font-family:'DM Mono',monospace}}
.data-table td{{padding:11px 12px;border-bottom:1px solid var(--border);color:var(--text);vertical-align:middle}}
.data-table tr:last-child td{{border-bottom:none}}
.data-table tr:hover td{{background:var(--bg3)}}
.pos{{color:var(--green);font-family:'DM Mono',monospace}}
.neg{{color:var(--red);font-family:'DM Mono',monospace}}

/* Badges */
.badge{{display:inline-block;padding:3px 9px;border-radius:20px;font-size:10px;font-weight:600;font-family:'DM Mono',monospace;letter-spacing:.5px}}
.badge-up{{background:#dbeafe;color:#1e40af}}
.badge-dn{{background:#fee2e2;color:#991b1b}}
.badge-nu{{background:#f1f5f9;color:#475569}}
.result{{display:inline-block;padding:3px 9px;border-radius:20px;font-size:10px;font-weight:600;font-family:'DM Mono',monospace}}
.result-win{{background:#dcfce7;color:#166534}}
.result-lose{{background:#fee2e2;color:#991b1b}}

/* Action tags */
.buy-tag{{color:var(--blue);font-weight:600;font-family:'DM Mono',monospace;font-size:11px}}
.sell-tag{{color:var(--red);font-weight:600;font-family:'DM Mono',monospace;font-size:11px}}
.hold-tag{{color:var(--text3);font-weight:600;font-family:'DM Mono',monospace;font-size:11px}}

/* Form */
.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px}}
.form-group{{display:flex;flex-direction:column;gap:6px}}
.form-label{{font-size:11px;color:var(--text2);font-family:'DM Mono',monospace;letter-spacing:1px;text-transform:uppercase;font-weight:500}}
.form-input,.form-select{{background:var(--bg);border:1px solid var(--border2);border-radius:var(--radius-sm);padding:10px 13px;color:var(--text);font-size:13px;font-family:'DM Sans',sans-serif;width:100%;outline:none;transition:border-color .15s}}
.form-input:focus,.form-select:focus{{border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.1)}}
.btn-primary{{background:#0f172a;color:#fff;border:none;padding:11px 22px;border-radius:var(--radius-sm);font-size:13px;cursor:pointer;font-weight:600;transition:background .15s}}
.btn-primary:hover{{background:#1e293b}}
.btn-ghost{{background:transparent;border:1px solid var(--border2);color:var(--text2);padding:11px 22px;border-radius:var(--radius-sm);font-size:13px;cursor:pointer;transition:all .15s;margin-left:8px}}
.btn-ghost:hover{{border-color:var(--text);color:var(--text)}}
.btn-del{{background:none;border:none;cursor:pointer;color:var(--text3);font-size:15px;padding:4px 7px;border-radius:6px;transition:all .15s;line-height:1}}
.btn-del:hover{{color:var(--red);background:var(--red-bg)}}

/* Info / warn */
.info-box{{background:#eff6ff;border:1px solid #bfdbfe;border-radius:var(--radius-sm);padding:12px 16px;font-size:12px;color:#1e40af;line-height:1.7;margin-bottom:16px}}
.warn-box{{background:#fffbeb;border:1px solid #fde68a;border-radius:var(--radius-sm);padding:12px 16px;font-size:12px;color:var(--amber);line-height:1.7;margin-bottom:16px}}

/* Strat cards */
.strat-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:20px}}
.strat-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:18px;transition:box-shadow .15s}}
.strat-card:hover{{box-shadow:var(--shadow-md)}}
.strat-header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}}
.strat-name{{font-size:13px;font-weight:600;color:var(--text)}}
.strat-body{{font-size:12px;color:var(--text2);line-height:1.7}}
.strat-rule{{margin-top:8px;font-size:11px;font-family:'DM Mono',monospace;color:var(--text3);line-height:1.6}}

::-webkit-scrollbar{{width:5px}}
::-webkit-scrollbar-track{{background:var(--bg3)}}
::-webkit-scrollbar-thumb{{background:var(--border2);border-radius:3px}}

@media(max-width:768px){{
  .app{{grid-template-columns:1fr}}
  .sidebar{{position:fixed;bottom:0;left:0;right:0;height:auto;flex-direction:row;z-index:100;border-top:1px solid #1e293b;overflow-x:auto}}
  .logo-area,.sig-pill,.sb-footer,.nav-label{{display:none}}
  .nav-section{{display:flex;flex-direction:row;padding:6px}}
  .nav-item{{flex-direction:column;gap:3px;font-size:9px;padding:8px 10px}}
  .main{{padding:16px;padding-bottom:80px}}
  .m5,.m4,.metrics-row{{grid-template-columns:repeat(2,1fr)}}
  .perf-grid,.strat-grid,.form-grid{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>

<!-- Lock -->
<div id="lock-screen">
  <div class="lock-logo">Jun <em>Yadnap</em><br>Trade System</div>
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
    <div class="logo-name">Jun <em>Yadnap</em><br>Trade System</div>
    <div class="logo-sub"><span class="dot"></span>ASX Auto-Updated</div>
  </div>
  <div class="sig-pill">
    <div class="sig-pill-label">Current Signal</div>
    <div class="sig-pill-action">{sd["action"]}</div>
    <div class="sig-pill-regime">{sd["regime"]}</div>
  </div>
  <div class="nav-section">
    <div class="nav-label">Navigation</div>
    <div class="nav-item active" onclick="showTab('dashboard',this)"><span class="nav-icon">◈</span> Dashboard</div>
    <div class="nav-item" onclick="showTab('trades',this)"><span class="nav-icon">◎</span> Paper Trades</div>
    <div class="nav-item" onclick="showTab('backtest',this)"><span class="nav-icon">◷</span> Backtest</div>
    <div class="nav-item" onclick="showTab('strategies',this)"><span class="nav-icon">◇</span> Strategies</div>
    <div class="nav-item" onclick="showTab('guide',this)"><span class="nav-icon">◌</span> How to Use</div>
  </div>
  <div class="sb-footer">
    Built: {build_time} {tz_name}<br>
    Auto-updates 4:15pm daily<br><br>
    Not financial advice.<br>
    Paper trading only.
  </div>
</aside>

<!-- MAIN -->
<main class="main">

<!-- ══════════ DASHBOARD ══════════ -->
<div id="tab-dashboard" class="tab active">
  <div class="page-header">
    <div>
      <div class="page-title">Dashboard</div>
      <div class="page-sub">Updated {build_time} {tz_name}</div>
    </div>
    <div class="build-badge">Auto-updates 4:15pm AEST/AEDT</div>
  </div>

  <div class="signal-card">
    <div class="signal-regime-tag">⬤ {sd["regime"]}</div>
    <div class="signal-action">{action_display}</div>
    <div class="signal-reason">{sd["reason"]}</div>
    <div class="alloc-row">
      <div class="alloc-chip"><div class="alloc-chip-name">GEAR — Long</div><div class="alloc-chip-val av-gear">{sd["gear"]}%</div></div>
      <div class="alloc-chip"><div class="alloc-chip-name">BBOZ — Short</div><div class="alloc-chip-val av-bboz">{sd["bboz"]}%</div></div>
      <div class="alloc-chip"><div class="alloc-chip-name">Cash</div><div class="alloc-chip-val av-cash">{sd["cash"]}%</div></div>
    </div>
  </div>

  <div class="metrics-row m4">
    <div class="metric-card">
      <div class="metric-label">ASX 200</div>
      <div class="metric-val">{sd["price"]:,.0f}</div>
      <div class="metric-sub" style="color:{'var(--green)' if sd['change']>=0 else 'var(--red)'}">{sd["change"]:+.2f}% today</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">RSI (14)</div>
      <div class="metric-val {'pos' if sd['rsi']>60 else 'neg' if sd['rsi']<40 else ''}">{sd["rsi"]:.1f}</div>
      <div class="metric-sub">{"Overbought" if sd["rsi"]>70 else "Oversold" if sd["rsi"]<30 else "Neutral"}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">vs SMA 250</div>
      <div class="metric-val {'pos' if sd['ext250']>=0 else 'neg'}">{sd["ext250"]:+.1f}%</div>
      <div class="metric-sub">SMA250: {sd["sma250"]:,.0f}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">vs SMA 20</div>
      <div class="metric-val {'pos' if sd['ext20']>=0 else 'neg'}">{sd["ext20"]:+.1f}%</div>
      <div class="metric-sub">SMA20: {sd["sma20"]:,.0f}</div>
    </div>
  </div>

  <div class="card">
    <div class="card-header">
      <div class="card-title">ASX 200 — Price with SMA20 &amp; SMA250</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-line" style="background:#0f172a"></div>ASX 200</div>
        <div class="legend-item"><div class="legend-line" style="background:#f59e0b;border-top:2px dashed #f59e0b;background:none"></div>SMA 20</div>
        <div class="legend-item"><div class="legend-line" style="background:#ef4444"></div>SMA 250</div>
      </div>
    </div>
    <div style="position:relative;height:280px"><canvas id="priceChart"></canvas></div>
  </div>

  <div class="card">
    <div class="card-header">
      <div class="card-title">RSI (14)</div>
      <div style="font-size:11px;color:var(--text3);font-family:'DM Mono',monospace">Overbought &gt;70 · Oversold &lt;30</div>
    </div>
    <div style="position:relative;height:160px"><canvas id="rsiChart"></canvas></div>
  </div>

  <div class="card">
    <div class="card-header">
      <div class="card-title">Equity Curve vs GEAR Benchmark (Normalised to 100)</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-line" style="background:#2563eb"></div>ASX 200</div>
        <div class="legend-item"><div class="legend-line" style="background:#15803d"></div>GEAR.AX</div>
      </div>
    </div>
    <div style="position:relative;height:220px"><canvas id="gearChart"></canvas></div>
  </div>

  <div class="card">
    <div class="card-header">
      <div class="card-title">Equity Curve vs BBOZ Benchmark (Normalised to 100)</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-line" style="background:#2563eb"></div>ASX 200</div>
        <div class="legend-item"><div class="legend-line" style="background:#b91c1c"></div>BBOZ.AX</div>
      </div>
    </div>
    <div style="position:relative;height:220px"><canvas id="bbozChart"></canvas></div>
  </div>
</div>

<!-- ══════════ PAPER TRADES ══════════ -->
<div id="tab-trades" class="tab">
  <div class="page-header">
    <div><div class="page-title">Paper Trades</div><div class="page-sub">Starting capital: $100,000 AUD</div></div>
  </div>

  <div class="metrics-row m5">
    <div class="metric-card">
      <div class="metric-label">Portfolio</div>
      <div class="metric-val" id="pt-portfolio">$100,000</div>
      <div class="metric-sub">cash + realised P&amp;L</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Open Value</div>
      <div class="metric-val neu" id="pt-openval">$0</div>
      <div class="metric-sub">at avg buy price</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Realised P&amp;L</div>
      <div class="metric-val" id="pt-return">+0.00%</div>
      <div class="metric-sub">closed trades only</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Win Rate</div>
      <div class="metric-val" id="pt-winrate">—</div>
      <div class="metric-sub" id="pt-winsub">no closed trades</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Total Trades</div>
      <div class="metric-val" id="pt-count">0</div>
    </div>
  </div>

  <div class="section-title">Performance Summary</div>
  <div class="perf-grid">
    <div class="perf-card">
      <div class="perf-label">This Week P&amp;L</div>
      <div class="perf-val" id="pt-week">$0</div>
    </div>
    <div class="perf-card">
      <div class="perf-label">Total Realised P&amp;L ($)</div>
      <div class="perf-val" id="pt-pnl-dollar">$0</div>
    </div>
    <div class="perf-card">
      <div class="perf-label">Wins / Losses</div>
      <div class="perf-val" id="pt-wins-losses">— / —</div>
    </div>
  </div>

  <div class="card" style="margin-bottom:24px">
    <div class="card-header"><div class="card-title">Log Paper Trade</div></div>
    <div class="form-grid">
      <div class="form-group"><label class="form-label">Date</label><input class="form-input" type="date" id="t-date"></div>
      <div class="form-group"><label class="form-label">Ticker</label>
        <select class="form-select" id="t-ticker"><option>GEAR</option><option>BBOZ</option><option>CASH</option></select></div>
      <div class="form-group"><label class="form-label">Action</label>
        <select class="form-select" id="t-action"><option>BUY</option><option>SELL</option><option>HOLD</option></select></div>
      <div class="form-group"><label class="form-label">Price ($)</label>
        <input class="form-input" type="number" id="t-price" placeholder="e.g. 42.50" step="0.01"></div>
      <div class="form-group"><label class="form-label">Shares</label>
        <input class="form-input" type="number" id="t-shares" placeholder="e.g. 500"></div>
      <div class="form-group"><label class="form-label">Strategy</label>
        <select class="form-select" id="t-strat">
          <option>Momentum Long</option><option>MR Long 1 (SMA20 Pullback)</option>
          <option>MR Long 2 (Deep Pullback)</option><option>MR Long 3 (SMA250 Bounce)</option>
          <option>MR Short (Overextension)</option><option>Momentum Short 1 (Breakdown)</option>
          <option>Momentum Short 2 (Failed Bounce)</option><option>Cash / No signal</option>
        </select></div>
    </div>
    <button class="btn-primary" onclick="logTrade()">Log Trade</button>
    <button class="btn-ghost" onclick="clearTrades()">Clear All</button>
  </div>

  <div class="card">
    <div class="card-header"><div class="card-title">Trade Log</div></div>
    <div style="overflow-x:auto">
      <table class="data-table">
        <thead><tr><th>#</th><th>Date</th><th>Ticker</th><th>Action</th><th>Shares</th><th>Price</th><th>Value</th><th>Strategy</th><th></th></tr></thead>
        <tbody id="trade-tbody">
          <tr><td colspan="9" style="color:var(--text3);padding:28px;text-align:center;font-family:'DM Mono',monospace;font-size:12px">No trades logged yet</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- ══════════ BACKTEST ══════════ -->
<div id="tab-backtest" class="tab">
  <div class="page-header">
    <div><div class="page-title">Backtest 2022–2024</div><div class="page-sub">ASX 200 historical simulation — pre-calculated at build time</div></div>
  </div>
  <div class="info-box">Results use real ASX 200 daily closes. GEAR modelled as 2× daily return minus 0.35% MER/year. BBOZ modelled as −2× daily return minus 0.56% MER/year. Does not account for brokerage (~$9.50/trade on SelfWealth).</div>

  <div class="metrics-row m5">
    <div class="metric-card"><div class="metric-label">Total Return</div><div class="metric-val" style="color:{bt_rc}">{bt["total_return"]:+.1f}%</div></div>
    <div class="metric-card"><div class="metric-label">CAGR</div><div class="metric-val" style="color:{bt_cc}">{bt["cagr"]:+.1f}%</div></div>
    <div class="metric-card"><div class="metric-label">Max Drawdown</div><div class="metric-val neg">{bt["max_dd"]:.1f}%</div></div>
    <div class="metric-card"><div class="metric-label">Win Rate</div><div class="metric-val {'pos' if bt['win_rate']>=50 else 'neu'}">{bt["win_rate"]:.0f}%</div></div>
    <div class="metric-card"><div class="metric-label">Sharpe Ratio</div><div class="metric-val {'pos' if bt['sharpe']>=1 else 'neu' if bt['sharpe']>=0 else 'neg'}">{bt["sharpe"]:.2f}</div></div>
  </div>

  <div class="card">
    <div class="card-header">
      <div class="card-title">Equity Curve — System vs Buy &amp; Hold ASX 200</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-line" style="background:#15803d"></div>JYTS System</div>
        <div class="legend-item"><div class="legend-line" style="background:#2563eb"></div>ASX 200 B&amp;H</div>
      </div>
    </div>
    <div style="position:relative;height:300px"><canvas id="btEquityChart"></canvas></div>
  </div>

  <div class="card">
    <div class="card-header"><div class="card-title">Drawdown</div></div>
    <div style="position:relative;height:160px"><canvas id="btDDChart"></canvas></div>
  </div>

  <div class="card" style="margin-bottom:24px">
    <div class="card-header"><div class="card-title">Performance by Regime</div></div>
    <table class="data-table">
      <thead><tr><th>Regime</th><th>Days</th><th>ETF</th><th>System Return</th><th>ASX Return</th><th>Result</th></tr></thead>
      <tbody>{bt_regime_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <div class="card-header"><div class="card-title">Recent Regime Transitions</div></div>
    <table class="data-table">
      <thead><tr><th>Date</th><th>From</th><th></th><th>To</th><th>ASX Price</th></tr></thead>
      <tbody>{trans_rows}</tbody>
    </table>
  </div>
  <div class="warn-box" style="margin-top:16px">Past performance does not guarantee future results.</div>
</div>

<!-- ══════════ STRATEGIES ══════════ -->
<div id="tab-strategies" class="tab">
  <div class="page-header"><div><div class="page-title">7 Strategies</div><div class="page-sub">ASX 200 adapted — GEAR (long) &amp; BBOZ (short)</div></div></div>
  <div class="strat-grid">
    <div class="strat-card"><div class="strat-header"><div class="strat-name">Momentum Long</div><span class="badge badge-up">GEAR</span></div><div class="strat-body">ASX 200 breaks above upper Bollinger Band while above SMA20. Ride strong upward momentum.</div><div class="strat-rule">Entry: price &gt; upper BB + above SMA20<br>Exit: close below SMA20 · Regime: Uptrend</div></div>
    <div class="strat-card"><div class="strat-header"><div class="strat-name">MR Long 1 — SMA20 Pullback</div><span class="badge badge-up">GEAR</span></div><div class="strat-body">ASX 200 pulls back to within 1% of SMA20. Buy the dip expecting a bounce.</div><div class="strat-rule">Entry: within 1% of SMA20<br>Exit: extends 3%+ above SMA20 · Regime: Uptrend</div></div>
    <div class="strat-card"><div class="strat-header"><div class="strat-name">MR Long 2 — Deep Pullback</div><span class="badge badge-up">GEAR</span></div><div class="strat-body">ASX drops below lower BB but stays above SMA250. Aggressive dip buy.</div><div class="strat-rule">Entry: price &lt; lower BB AND &gt; SMA250<br>Exit: reclaims middle BB · Regime: Uptrend</div></div>
    <div class="strat-card"><div class="strat-header"><div class="strat-name">MR Long 3 — SMA250 Bounce</div><span class="badge badge-up">GEAR</span></div><div class="strat-body">ASX approaches SMA250 from above with a bullish candle. Catch the long-term support bounce.</div><div class="strat-rule">Entry: within 3% of SMA250 + bullish candle<br>Exit: 5%+ above SMA20 · Regime: Near SMA250</div></div>
    <div class="strat-card"><div class="strat-header"><div class="strat-name">MR Short — Overextension</div><span class="badge badge-dn">BBOZ</span></div><div class="strat-body">ASX is 4%+ above SMA20 AND above upper BB. Fade extreme overextension.</div><div class="strat-rule">Entry: ext20 &gt; 4% AND price &gt; upper BB<br>Exit: reverts to SMA20 · Regime: Uptrend</div></div>
    <div class="strat-card"><div class="strat-header"><div class="strat-name">Momentum Short 1 — Breakdown</div><span class="badge badge-dn">BBOZ</span></div><div class="strat-body">ASX closes below SMA250 AND lower BB. Major bearish signal. Size scales with depth.</div><div class="strat-rule">Entry: price &lt; SMA250 + lower BB<br>Exit: reclaims SMA250 · Regime: Downtrend</div></div>
    <div class="strat-card" style="grid-column:span 2"><div class="strat-header"><div class="strat-name">Momentum Short 2 — Failed Bounce</div><span class="badge badge-dn">BBOZ</span></div><div class="strat-body">In a downtrend, ASX bounces to SMA20 then closes back below it. Classic bull trap — short the failure.</div><div class="strat-rule">Entry: bounce to SMA20 then close below it in downtrend · Exit: above SMA20 for 3 days</div></div>
  </div>
  <div class="card">
    <div class="card-header"><div class="card-title">Regime Playbook — $100K Capital</div></div>
    <table class="data-table">
      <thead><tr><th>Regime</th><th>Condition</th><th>ETF</th><th>Allocation</th><th>$ Amount</th></tr></thead>
      <tbody>
        <tr><td>Strong uptrend</td><td>ASX &gt;2% above SMA250, RSI &gt;55</td><td><span class="badge badge-up">GEAR</span></td><td>80%</td><td class="pos">$80,000</td></tr>
        <tr><td>Uptrend</td><td>ASX above SMA250</td><td><span class="badge badge-up">GEAR</span></td><td>50%</td><td class="pos">$50,000</td></tr>
        <tr><td>Neutral</td><td>Within 1% of SMA250</td><td><span class="badge badge-nu">CASH</span></td><td>100%</td><td style="color:var(--text3)">$100,000</td></tr>
        <tr><td>Downtrend</td><td>ASX below SMA250</td><td><span class="badge badge-dn">BBOZ</span></td><td>30%</td><td class="neg">$30,000</td></tr>
        <tr><td>Strong downtrend</td><td>ASX &gt;2% below SMA250, RSI &lt;40</td><td><span class="badge badge-dn">BBOZ</span></td><td>60%</td><td class="neg">$60,000</td></tr>
      </tbody>
    </table>
  </div>
</div>

<!-- ══════════ HOW TO USE ══════════ -->
<div id="tab-guide" class="tab">
  <div class="page-header"><div><div class="page-title">How to Use</div><div class="page-sub">Daily routine and trading rules</div></div></div>
  <div class="card" style="margin-bottom:20px">
    <div class="card-header"><div class="card-title">Daily Routine — 10 Minutes</div></div>
    <table class="data-table">
      <thead><tr><th>Time (AEST/AEDT)</th><th>Action</th><th>Where</th></tr></thead>
      <tbody>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:00 pm</td><td>ASX closes</td><td style="color:var(--text3)">—</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:15 pm</td><td>Open dashboard — auto-updated</td><td style="color:var(--text3)">This page</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:16 pm</td><td>Read Signal Card — note action &amp; regime</td><td style="color:var(--text3)">Dashboard tab</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:18 pm</td><td>Check GEAR or BBOZ closing price</td><td style="color:var(--text3)">SelfWealth</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:20 pm</td><td>Log your paper trade</td><td style="color:var(--text3)">Paper Trades tab</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:25 pm</td><td>Done. See you tomorrow.</td><td style="color:var(--text3)">—</td></tr>
      </tbody>
    </table>
  </div>
  <div class="card" style="margin-bottom:20px">
    <div class="card-header"><div class="card-title">Stop Loss Rules</div></div>
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
// ─── Password ──────────────────────────────────────────────────────
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

// ─── Nav ───────────────────────────────────────────────────────────
function showTab(name, el) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (el) el.classList.add('active');
}}

// ─── Chart data ────────────────────────────────────────────────────
const CD = {json.dumps(cd)};
const BT = {json.dumps(bt)};

const baseOpts = {{
  responsive: true, maintainAspectRatio: false,
  plugins: {{ legend: {{ display: false }} }},
  scales: {{
    x: {{ ticks: {{ maxTicksLimit: 8, font: {{ size: 10 }}, color: '#94a3b8' }}, grid: {{ color: 'rgba(15,23,42,0.05)' }}, border: {{ display: false }} }},
    y: {{ ticks: {{ font: {{ size: 10 }}, color: '#94a3b8', callback: v => v.toLocaleString() }}, grid: {{ color: 'rgba(15,23,42,0.05)' }}, border: {{ display: false }} }}
  }}
}};

// ASX Price chart
new Chart(document.getElementById('priceChart'), {{
  type: 'line',
  data: {{ labels: CD.labels, datasets: [
    {{ data: CD.closes, borderColor: '#0f172a', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false }},
    {{ data: CD.sma20, borderColor: '#f59e0b', borderWidth: 1.5, pointRadius: 0, borderDash: [5,3], tension: 0.3, fill: false }},
    {{ data: CD.sma250, borderColor: '#ef4444', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false }}
  ]}}, options: baseOpts
}});

// RSI
new Chart(document.getElementById('rsiChart'), {{
  type: 'line',
  data: {{ labels: CD.labels, datasets: [{{ data: CD.rsi, borderColor: '#2563eb', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false }}] }},
  options: {{ ...baseOpts, scales: {{ ...baseOpts.scales,
    y: {{ min: 0, max: 100, ticks: {{ font: {{ size: 10 }}, color: '#94a3b8', stepSize: 25 }}, grid: {{ color: 'rgba(15,23,42,0.05)' }}, border: {{ display: false }} }} }} }}
}});

// GEAR benchmark
const gearData = CD.gear_norm && CD.gear_norm.some(v => v !== null);
if (gearData) {{
  new Chart(document.getElementById('gearChart'), {{
    type: 'line',
    data: {{ labels: CD.labels, datasets: [
      {{ label: 'ASX 200', data: CD.asx_norm, borderColor: '#2563eb', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false, borderDash: [5,3] }},
      {{ label: 'GEAR.AX', data: CD.gear_norm, borderColor: '#15803d', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false }}
    ]}},
    options: {{ ...baseOpts,
      plugins: {{ legend: {{ display: true, labels: {{ color: '#475569', font: {{ size: 11 }}, boxWidth: 14, padding: 16 }} }} }},
      scales: {{ ...baseOpts.scales, y: {{ ticks: {{ font: {{ size: 10 }}, color: '#94a3b8', callback: v => v.toFixed(0) }}, grid: {{ color: 'rgba(15,23,42,0.05)' }}, border: {{ display: false }} }} }} }}
  }});
}} else {{
  document.getElementById('gearChart').parentElement.innerHTML = '<p style="text-align:center;color:#94a3b8;padding:40px;font-size:12px;font-family:DM Mono,monospace">GEAR.AX data unavailable</p>';
}}

// BBOZ benchmark
const bbozData = CD.bboz_norm && CD.bboz_norm.some(v => v !== null);
if (bbozData) {{
  new Chart(document.getElementById('bbozChart'), {{
    type: 'line',
    data: {{ labels: CD.labels, datasets: [
      {{ label: 'ASX 200', data: CD.asx_norm, borderColor: '#2563eb', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false, borderDash: [5,3] }},
      {{ label: 'BBOZ.AX', data: CD.bboz_norm, borderColor: '#b91c1c', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false }}
    ]}},
    options: {{ ...baseOpts,
      plugins: {{ legend: {{ display: true, labels: {{ color: '#475569', font: {{ size: 11 }}, boxWidth: 14, padding: 16 }} }} }},
      scales: {{ ...baseOpts.scales, y: {{ ticks: {{ font: {{ size: 10 }}, color: '#94a3b8', callback: v => v.toFixed(0) }}, grid: {{ color: 'rgba(15,23,42,0.05)' }}, border: {{ display: false }} }} }} }}
  }});
}} else {{
  document.getElementById('bbozChart').parentElement.innerHTML = '<p style="text-align:center;color:#94a3b8;padding:40px;font-size:12px;font-family:DM Mono,monospace">BBOZ.AX data unavailable</p>';
}}

// Backtest equity
new Chart(document.getElementById('btEquityChart'), {{
  type: 'line',
  data: {{ labels: BT.labels, datasets: [
    {{ label: 'JYTS System', data: BT.equity, borderColor: '#15803d', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false }},
    {{ label: 'ASX 200 B&H', data: BT.bh_equity, borderColor: '#2563eb', borderWidth: 1.5, pointRadius: 0, borderDash: [5,3], tension: 0.3, fill: false }}
  ]}},
  options: {{ ...baseOpts,
    plugins: {{ legend: {{ display: true, labels: {{ color: '#475569', font: {{ size: 11 }}, boxWidth: 14, padding: 16 }} }} }},
    scales: {{ ...baseOpts.scales, y: {{ ticks: {{ font: {{ size: 10 }}, color: '#94a3b8', callback: v => '$' + (v/1000).toFixed(0) + 'K' }}, grid: {{ color: 'rgba(15,23,42,0.05)' }}, border: {{ display: false }} }} }} }}
}});

// Drawdown
new Chart(document.getElementById('btDDChart'), {{
  type: 'line',
  data: {{ labels: BT.labels, datasets: [{{ data: BT.drawdowns, borderColor: '#ef4444', borderWidth: 1.5, pointRadius: 0, tension: 0.3,
    fill: {{ target: 'origin', above: 'rgba(239,68,68,0.08)' }} }}] }},
  options: {{ ...baseOpts, scales: {{ ...baseOpts.scales, y: {{ ticks: {{ font: {{ size: 10 }}, color: '#94a3b8', callback: v => v.toFixed(0)+'%' }}, grid: {{ color: 'rgba(15,23,42,0.05)' }}, border: {{ display: false }} }} }} }}
}});

// ─── Paper Trades ──────────────────────────────────────────────────
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
  trades.push({{ id: Date.now(), date, ticker, action, price, shares, value: parseFloat((price*shares).toFixed(2)), strat }});
  localStorage.setItem('jyts_trades', JSON.stringify(trades));
  renderTrades();
  document.getElementById('t-price').value = '';
  document.getElementById('t-shares').value = '';
}}

function deleteTrade(id) {{
  if (!confirm('Delete this trade?')) return;
  trades = trades.filter(t => t.id !== id);
  localStorage.setItem('jyts_trades', JSON.stringify(trades));
  renderTrades();
}}

function clearTrades() {{
  if (!confirm('Clear ALL trades? This cannot be undone.')) return;
  trades = [];
  localStorage.setItem('jyts_trades', JSON.stringify(trades));
  renderTrades();
}}

function getWeekPnL() {{
  const now = new Date();
  const sow = new Date(now); sow.setDate(now.getDate()-now.getDay()); sow.setHours(0,0,0,0);
  let openPos = {{}}, weekPnL = 0;
  trades.forEach(t => {{
    if (t.action==='BUY') {{
      if (!openPos[t.ticker]) openPos[t.ticker]={{shares:0,cost:0}};
      openPos[t.ticker].shares+=t.shares; openPos[t.ticker].cost+=t.value;
    }}
    if (t.action==='SELL') {{
      const pos=openPos[t.ticker];
      if (pos&&pos.shares>0) {{
        const avg=pos.cost/pos.shares, pnl=(t.price-avg)*t.shares;
        if (new Date(t.date)>=sow) weekPnL+=pnl;
        pos.shares-=t.shares; pos.cost=pos.shares*avg;
        if (pos.shares<=0) delete openPos[t.ticker];
      }}
    }}
  }});
  return weekPnL;
}}

function renderTrades() {{
  let openPos={{}}, realPnL=0, wins=0, losses=0;
  trades.forEach(t => {{
    if (t.action==='BUY') {{
      if (!openPos[t.ticker]) openPos[t.ticker]={{shares:0,cost:0}};
      openPos[t.ticker].shares+=t.shares; openPos[t.ticker].cost+=t.value;
    }}
    if (t.action==='SELL') {{
      const pos=openPos[t.ticker];
      if (pos&&pos.shares>0) {{
        const avg=pos.cost/pos.shares, pnl=(t.price-avg)*t.shares;
        realPnL+=pnl; if(pnl>=0) wins++; else losses++;
        pos.shares-=t.shares; pos.cost=pos.shares*avg;
        if(pos.shares<=0) delete openPos[t.ticker];
      }}
    }}
  }});

  let openValue=0; const seen={{}};
  trades.slice().reverse().forEach(t=>{{
    if(openPos[t.ticker]&&!seen[t.ticker]&&t.action==='BUY'){{
      openValue+=openPos[t.ticker].shares*t.price; seen[t.ticker]=true;
    }}
  }});

  const ret=realPnL/100000*100, wkPnL=getWeekPnL(), total=wins+losses;

  document.getElementById('pt-portfolio').textContent='$'+(100000+realPnL).toLocaleString('en-AU',{{maximumFractionDigits:0}});
  document.getElementById('pt-openval').textContent='$'+openValue.toLocaleString('en-AU',{{maximumFractionDigits:0}});

  const retEl=document.getElementById('pt-return');
  retEl.textContent=(ret>=0?'+':'')+ret.toFixed(2)+'%';
  retEl.className='metric-val '+(ret>=0?'pos':'neg');

  document.getElementById('pt-count').textContent=trades.length;
  document.getElementById('pt-winrate').textContent=total>0?Math.round(wins/total*100)+'%':'—';
  document.getElementById('pt-winsub').textContent=total>0?`${{wins}} wins / ${{losses}} losses`:'no closed trades';

  const wkEl=document.getElementById('pt-week');
  wkEl.textContent=(wkPnL>=0?'+$':'-$')+Math.abs(wkPnL).toLocaleString('en-AU',{{maximumFractionDigits:0}});
  wkEl.className='perf-val '+(wkPnL>=0?'pos':'neg');

  const pnlEl=document.getElementById('pt-pnl-dollar');
  pnlEl.textContent=(realPnL>=0?'+$':'-$')+Math.abs(realPnL).toLocaleString('en-AU',{{maximumFractionDigits:0}});
  pnlEl.className='perf-val '+(realPnL>=0?'pos':'neg');

  document.getElementById('pt-wins-losses').textContent=`${{wins}} / ${{losses}}`;

  const tbody=document.getElementById('trade-tbody');
  if (!trades.length) {{
    tbody.innerHTML='<tr><td colspan="9" style="color:var(--text3);padding:28px;text-align:center;font-family:DM Mono,monospace;font-size:12px">No trades logged yet</td></tr>';
    return;
  }}
  tbody.innerHTML=trades.slice().reverse().map((t,i)=>{{
    const ac=t.action==='BUY'?'buy-tag':t.action==='SELL'?'sell-tag':'hold-tag';
    const bc=t.ticker==='GEAR'?'badge-up':t.ticker==='BBOZ'?'badge-dn':'badge-nu';
    const tid=t.id||i;
    return `<tr>
      <td style="color:var(--text3);font-family:'DM Mono',monospace;font-size:12px">${{trades.length-i}}</td>
      <td style="font-family:'DM Mono',monospace;font-size:12px">${{t.date}}</td>
      <td><span class="badge ${{bc}}">${{t.ticker}}</span></td>
      <td class="${{ac}}">${{t.action}}</td>
      <td style="font-family:'DM Mono',monospace">${{t.shares.toLocaleString()}}</td>
      <td style="font-family:'DM Mono',monospace">$${{t.price.toFixed(2)}}</td>
      <td style="font-family:'DM Mono',monospace">$${{t.value.toLocaleString('en-AU',{{maximumFractionDigits:0}})}}</td>
      <td style="font-size:11px;color:var(--text3)">${{t.strat}}</td>
      <td><button class="btn-del" onclick="deleteTrade(${{tid}})" title="Delete">🗑</button></td>
    </tr>`;
  }}).join('');
}}
</script>
</body>
</html>"""

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=== Jun Yadnap Trade System — Build Script ===")
    local_time, tz_name = get_aest_now()
    build_time = local_time.strftime("%d %b %Y %I:%M %p")
    print(f"Build time: {build_time} {tz_name}")

    df = fetch_asx_data()
    gear_df, bboz_df = fetch_etf_data()

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

    chart_data = build_chart_data(df, gear_df, bboz_df)
    print("  Chart data built.")

    backtest_data = run_backtest(df)
    print(f"  Backtest: Return={backtest_data['total_return']:+.1f}% | MaxDD={backtest_data['max_dd']:.1f}% | WinRate={backtest_data['win_rate']:.0f}%")

    html = generate_html(signal_data, chart_data, backtest_data, build_time, tz_name)

    os.makedirs("docs", exist_ok=True)
    out_path = "docs/index.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Dashboard written to {out_path}")
    print("=== Build complete ===")

if __name__ == "__main__":
    main()
