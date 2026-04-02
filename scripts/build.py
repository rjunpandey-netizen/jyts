#!/usr/bin/env python3
"""
Jun Yadnap Trade System — Daily Build Script
Fetches ASX 200 data, calculates indicators, generates dashboard HTML.
v2 — Upgraded with ASX Stocks tab, new theme, auto-price fetch, learning tips, P&L fix.
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

def calc_atr(df, period=14):
    hi = df['High']; lo = df['Low']; cl = df['Close']
    prev_cl = cl.shift(1)
    tr = pd.concat([hi - lo, (hi - prev_cl).abs(), (lo - prev_cl).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

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
        print(f"  GEAR: {len(gear_df)} days, last price: ${gear_df['Close'].iloc[-1]:.2f}")
    except Exception as e:
        print(f"  GEAR fetch failed: {e}")
    try:
        b = yf.Ticker("BBOZ.AX")
        bboz_df = b.history(period="2y", interval="1d")[["Close"]].copy()
        bboz_df.dropna(inplace=True)
        bboz_df.index = pd.to_datetime(bboz_df.index).tz_localize(None)
        print(f"  BBOZ: {len(bboz_df)} days, last price: ${bboz_df['Close'].iloc[-1]:.2f}")
    except Exception as e:
        print(f"  BBOZ fetch failed: {e}")
    return gear_df, bboz_df

# ─── Fetch ETF Last Prices ────────────────────────────────────────────────────
def fetch_etf_prices(gear_df, bboz_df):
    gear_price = round(float(gear_df['Close'].iloc[-1]), 2) if gear_df is not None and len(gear_df) > 0 else None
    bboz_price = round(float(bboz_df['Close'].iloc[-1]), 2) if bboz_df is not None and len(bboz_df) > 0 else None
    return gear_price, bboz_price

# ─── Fetch ASX Stocks for Swing Trade Tab ─────────────────────────────────────
# Universe: 25 curated large/mid-cap ASX stocks
# Selection criteria:
#   - Large/mid cap only (lower risk, high liquidity — suitable for $500–$10K)
#   - Aligned to current world macro trends:
#       * AI & digital infrastructure (data centres, cloud, software)
#       * Defence & security spending (global rearmament trend)
#       * Healthcare & ageing population (structural demand)
#       * Resources tied to energy transition (copper, rare earths, uranium)
#       * Domestic financials (rate cycle beneficiaries)
#   - All have sufficient daily volume for easy entry/exit
#   - Avoid highly speculative small caps and high-ATR stocks
ASX_UNIVERSE = [
    # 🏦 Financials — large, liquid, rate-sensitive
    "CBA.AX",   # Commonwealth Bank — largest ASX stock, low risk
    "MQG.AX",   # Macquarie — global infrastructure & asset management
    "NAB.AX",   # National Australia Bank — solid dividend + rate play

    # 💊 Healthcare — defensive growth, ageing population tailwind
    "CSL.AX",   # CSL — plasma & vaccines, world-class compounder
    "RMD.AX",   # ResMed — sleep apnoea devices, US-exposed growth
    "COH.AX",   # Cochlear — hearing implants, premium medical device
    "PME.AX",   # Pro Medicus — AI radiology software, high momentum

    # 🤖 Technology — AI infrastructure & software
    "WTC.AX",   # WiseTech Global — global logistics software, strong moat
    "XRO.AX",   # Xero — cloud accounting, NZ/UK/AU exposure
    "TNE.AX",   # TechnologyOne — enterprise SaaS, consistent grower
    "GMG.AX",   # Goodman Group — data centre REIT, AI infrastructure play

    # ⚒️ Resources — energy transition metals (copper, rare earths, uranium)
    "BHP.AX",   # BHP — copper + iron ore, world's largest miner
    "RIO.AX",   # Rio Tinto — copper + aluminium, transition metals
    "LYC.AX",   # Lynas — rare earth elements, strategic global supply
    "PDN.AX",   # Paladin Energy — uranium, nuclear energy demand
    "WHC.AX",   # Whitehaven Coal — thermal coal, Asian energy demand

    # ⚡ Energy — oil & gas, domestic supply
    "WDS.AX",   # Woodside Energy — LNG, global energy security play
    "STO.AX",   # Santos — LNG + oil, Asia-Pacific demand

    # 🛡️ Defence & Industrial — global rearmament trend
    "WES.AX",   # Wesfarmers — diversified industrials + retail, low risk
    "AMC.AX",   # Amcor — global packaging, defensive earnings
    "ORI.AX",   # Orica — mining explosives, infrastructure spending

    # 🏠 Consumer & Digital Economy — domestic resilience
    "REA.AX",   # REA Group — property listings, digital duopoly
    "SEK.AX",   # Seek — jobs platform, economic activity indicator
    "JBH.AX",   # JB Hi-Fi — consumer electronics, domestic spend
    "WOW.AX",   # Woolworths — defensive grocery, consistent earnings
]

# Sector labels for display
STOCK_SECTORS = {
    "CBA": "Financials", "MQG": "Financials", "NAB": "Financials",
    "CSL": "Healthcare", "RMD": "Healthcare", "COH": "Healthcare", "PME": "Healthcare",
    "WTC": "Technology", "XRO": "Technology", "TNE": "Technology", "GMG": "Tech/REIT",
    "BHP": "Resources", "RIO": "Resources", "LYC": "Rare Earths", "PDN": "Uranium", "WHC": "Energy",
    "WDS": "Energy", "STO": "Energy",
    "WES": "Industrials", "AMC": "Industrials", "ORI": "Industrials",
    "REA": "Digital", "SEK": "Digital", "JBH": "Consumer", "WOW": "Consumer",
}

# ── ASX Swing Catalyst — 4-Strategy Scoring Engine ──────────────────────────
# Hard filters (ALL must pass):
#   1. Price > 200-day SMA (long-term trend safety switch)
#   2. Average daily $ volume >= $500K (liquidity for $10K entries)
# 4 strategies: Oversold Bounce | Momentum Continuation | SMA20 Pullback | Volume Breakout
# Risk/Reward: minimum 1:3 enforced
# Position sizing: $10K / 3 = $3,333 per trade, brokerage $3 flat

STRATEGY_BADGES = {
    "Oversold Bounce": "OB",
    "Momentum": "MC",
    "SMA20 Pullback": "PB",
    "Volume Breakout": "VB",
}

def score_stock(ticker_sym):
    """Score a stock using the ASX Swing Catalyst rules. Returns dict or None."""
    try:
        t = yf.Ticker(ticker_sym)
        # Need 1y for SMA200
        df = t.history(period="1y", interval="1d")
        if df is None or len(df) < 210:
            return None
        df = df[["Open","High","Low","Close","Volume"]].copy()
        df.dropna(inplace=True)
        if len(df) < 210:
            return None

        closes = df["Close"]
        price  = float(closes.iloc[-1])
        prev   = float(closes.iloc[-2])
        change_pct = (price - prev) / prev * 100

        # ── Indicators ───────────────────────────────────────
        sma20  = float(calc_sma(closes, 20).iloc[-1])
        sma50  = float(calc_sma(closes, 50).iloc[-1])
        sma200 = float(calc_sma(closes, 200).iloc[-1])
        rsi_val = float(calc_rsi(closes).iloc[-1])

        vol_series = df["Volume"]
        vol_avg20  = float(vol_series.rolling(20).mean().iloc[-1])
        vol_now    = float(vol_series.iloc[-1])
        vol_ratio  = vol_now / vol_avg20 if vol_avg20 > 0 else 0

        # Dollar volume liquidity check (~last close × avg volume)
        dollar_vol = price * vol_avg20
        high20 = float(df["High"].rolling(20).max().iloc[-1])

        # ATR for stop loss
        atr_series = calc_atr(df)
        atr = float(atr_series.iloc[-1])
        atr_pct = atr / price * 100

        # Returns
        ret_1m = (price - float(closes.iloc[-21])) / float(closes.iloc[-21]) * 100 if len(closes) >= 21 else 0
        ret_3m = (price - float(closes.iloc[-63])) / float(closes.iloc[-63]) * 100 if len(closes) >= 63 else 0

        if any(pd.isna(x) for x in [sma20, sma50, sma200, rsi_val]):
            return None

        # ── HARD FILTERS ─────────────────────────────────────
        # 1. Must be above 200-day SMA (safety switch)
        if price <= sma200:
            return None
        # 2. Must have ≥$500K avg daily dollar volume
        if dollar_vol < 500_000:
            return None

        ext20  = (price - sma20)  / sma20  * 100
        ext50  = (price - sma50)  / sma50  * 100
        ext200 = (price - sma200) / sma200 * 100

        above_sma20 = price > sma20
        above_sma50 = price > sma50

        # ── STRATEGY DETECTION (pick best match) ─────────────
        strategies_triggered = []
        score = 0

        # Strategy 1 — Oversold Bounce
        # Rules: price > SMA50, RSI < 35
        # Goal: snap-back to SMA20
        if above_sma50 and rsi_val < 35:
            strategies_triggered.append("Oversold Bounce")
            score += 30
            # Stop: 1× ATR below entry
            stop_ob = price - atr
            target_ob = sma20  # snap back to SMA20
            rr_ob = (target_ob - price) / (price - stop_ob) if (price - stop_ob) > 0 else 0
            if rr_ob >= 3:
                score += 20

        # Strategy 2 — Momentum Continuation
        # Rules: price > SMA20 and SMA50, RSI 40-65
        # Goal: ride the trend before overbought
        if above_sma20 and above_sma50 and 40 <= rsi_val <= 65:
            strategies_triggered.append("Momentum")
            score += 25
            score += 10  # RSI sweet spot bonus

        # Strategy 3 — SMA20 Pullback
        # Rules: within 3% of SMA20, price > SMA50
        # Goal: buy the dip in an uptrend
        if -3 <= ext20 <= 3 and above_sma50:
            strategies_triggered.append("SMA20 Pullback")
            score += 30
            if -1 <= ext20 <= 1:
                score += 10  # very tight pullback = higher conviction

        # Strategy 4 — Volume Breakout
        # Rules: price at 20-day high, volume > 1.3× avg
        # Goal: catch confirmed breakout move
        is_20d_high = price >= high20 * 0.995  # within 0.5% of 20d high
        if is_20d_high and vol_ratio >= 1.3:
            strategies_triggered.append("Volume Breakout")
            score += 30
            score += min(int((vol_ratio - 1.3) * 20), 15)  # extra for higher volume

        # No strategy triggered
        if not strategies_triggered:
            return None

        # ── RISK / REWARD CHECK (must be ≥ 1:3) ─────────────
        # Stop = 1× ATR below entry OR close below SMA20
        stop_atr   = price - atr
        stop_sma20 = sma20  # exit if closes below SMA20
        stop       = max(stop_atr, stop_sma20)  # use tighter stop
        risk_dollar = price - stop

        # Target: trailing stop logic — use 3× risk as minimum target
        target_3r  = price + (risk_dollar * 3)
        rr_ratio   = (target_3r - price) / risk_dollar if risk_dollar > 0 else 0

        if rr_ratio < 3:
            return None  # hard filter: must be 1:3 minimum

        risk_pct    = risk_dollar / price * 100
        reward_pct  = risk_dollar * 3 / price * 100

        # ── POSITION SIZING ($10K / 3 positions) ─────────────
        capital_per_trade = 3333
        shares_suggested  = int(capital_per_trade / price) if price > 0 else 0
        max_loss_dollar   = round(shares_suggested * risk_dollar, 2)

        # ── CONVICTION TEXT ───────────────────────────────────
        conviction_parts = []
        if len(strategies_triggered) > 1:
            conviction_parts.append(f"{len(strategies_triggered)} strategies confluent")
        if rsi_val < 35:
            conviction_parts.append(f"RSI {rsi_val:.0f} — deeply oversold")
        elif 40 <= rsi_val <= 65:
            conviction_parts.append(f"RSI {rsi_val:.0f} — momentum sweet spot")
        if vol_ratio >= 1.3:
            conviction_parts.append(f"Vol {vol_ratio:.1f}× avg")
        if -1 <= ext20 <= 1:
            conviction_parts.append("Tight SMA20 touch")
        if ext200 < 5:
            conviction_parts.append("Near SMA200 — low risk zone")
        conviction = " · ".join(conviction_parts[:3])

        # Exit signal based on strategy
        primary_strat = strategies_triggered[0]
        if primary_strat == "Oversold Bounce":
            exit_signal = "Close above SMA20 (target) or trailing 5% from peak"
        elif primary_strat == "Momentum":
            exit_signal = "RSI > 80 or close below SMA20"
        elif primary_strat == "SMA20 Pullback":
            exit_signal = "Close below SMA20"
        else:  # Volume Breakout
            exit_signal = "Trailing 5% from peak or close below SMA20"

        ticker_short = ticker_sym.replace(".AX", "")
        sector = STOCK_SECTORS.get(ticker_short, "Other")

        # Macro bonus (for tiebreaking, not filtering)
        macro_bonus_map = {
            "Technology": 8, "Tech/REIT": 8,
            "Healthcare": 6, "Rare Earths": 6, "Uranium": 6,
            "Energy": 4, "Resources": 3,
        }
        score += macro_bonus_map.get(sector, 0)

        # Penalty: too volatile (ATR > 5% means wide stops, harder to hit 1:3)
        if atr_pct > 5:
            score -= 10

        return {
            "ticker":           ticker_short,
            "sector":           sector,
            "price":            round(price, 2),
            "change":           round(change_pct, 2),
            "strategy":         primary_strat,
            "strategies_all":   strategies_triggered,
            "badge":            STRATEGY_BADGES.get(primary_strat, "—"),
            "entry":            round(price, 2),
            "stop":             round(stop, 2),
            "target":           round(target_3r, 2),
            "exit_signal":      exit_signal,
            "conviction":       conviction,
            "rsi":              round(rsi_val, 1),
            "sma20":            round(sma20, 2),
            "sma50":            round(sma50, 2),
            "sma200":           round(sma200, 2),
            "ext200":           round(ext200, 1),
            "vol_ratio":        round(vol_ratio, 1),
            "dollar_vol_k":     round(dollar_vol / 1000, 0),
            "atr":              round(atr, 2),
            "atr_pct":          round(atr_pct, 1),
            "rr_ratio":         round(rr_ratio, 1),
            "risk_pct":         round(risk_pct, 1),
            "reward_pct":       round(reward_pct, 1),
            "ret_1m":           round(ret_1m, 1),
            "ret_3m":           round(ret_3m, 1),
            "shares_suggested": shares_suggested,
            "max_loss_dollar":  max_loss_dollar,
            "score":            score,
        }
    except Exception as e:
        return None

def fetch_top_stocks():
    """
    Phase 1: Scan ASX universe — find up to 10 candidates passing all 4 strategies + hard filters.
    Phase 2: Filter to top 4 High Conviction setups (1:3 R/R already enforced in score_stock).
    """
    print("Running ASX Swing Catalyst scan...")
    candidates = []
    for sym in ASX_UNIVERSE:
        s = score_stock(sym)
        if s is not None:
            candidates.append(s)

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Phase 1: up to 10 research candidates
    research_10 = candidates[:10]
    # Phase 2: top 4 high conviction (highest score, already 1:3 enforced)
    high_conviction_4 = candidates[:4]

    tickers_10 = [s["ticker"] for s in research_10]
    tickers_4  = [s["ticker"] for s in high_conviction_4]
    print(f"  Passed filters: {len(candidates)} stocks")
    print(f"  Research phase (10): {tickers_10}")
    print(f"  High conviction (4): {tickers_4}")
    return research_10, high_conviction_4

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

        if portfolio > peak: peak = portfolio
        dd = (portfolio - peak) / peak * 100
        bh_val = 100000 * (price / bh_base)

        if regime not in regime_stats:
            regime_stats[regime] = {"days": 0, "sys_start": portfolio, "asx_start": price}
        regime_stats[regime]["days"] += 1
        regime_stats[regime]["sys_end"] = portfolio
        regime_stats[regime]["asx_end"] = price

        if i % 20 == 0:
            if portfolio > prev_val: wins += 1
            elif portfolio < prev_val: losses += 1
            prev_val = portfolio

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

# ─── Learning Tips ────────────────────────────────────────────────────────────
LEARNING_TIPS = [
    ("📈 What is SMA250?", "SMA250 is the 250-day average price of the ASX 200. Think of it as the market's 'long-term mood'. When the ASX is above it → markets are healthy (GEAR). Below it → markets are weak (BBOZ)."),
    ("📊 What is RSI?", "RSI (Relative Strength Index) measures how fast prices are moving. Above 70 = overbought (price may fall soon). Below 30 = oversold (price may bounce). Between 40–60 = normal momentum."),
    ("💡 Why GEAR and BBOZ?", "GEAR rises 2× when ASX rises. BBOZ rises 2× when ASX falls. These let you profit in both up and down markets — but losses are also amplified, so position sizing matters."),
    ("⚠️ Draw downs are normal.", "Even good strategies lose money for months. Malik sat through 30% draw downs. The key is conviction in your rules. Never exit a strategy during a draw down unless your rules say to."),
    ("🎯 Your edge: SMA250.", "If the ASX is above SMA250 and you hold GEAR — over the long run, you capture the uptrend. This simple rule has beaten buy-and-hold for decades. Simple edges outlast complex ones."),
    ("📐 Position sizing matters more than entry.", "Being 80% in GEAR vs 40% in GEAR makes an enormous difference over time. The system tells you exactly how much to hold. Follow the allocation, not your gut."),
    ("🔄 Mean reversion vs momentum.", "Momentum = buy what's already going up. Mean reversion = buy when it's pulled back. Both work. Your system combines them — momentum for the trend, mean reversion for entries within the trend."),
    ("🕐 Paper trade first, always.", "Malik paper traded for years before going big. Your brain needs to experience draw downs emotionally before risking real money. 3 months minimum — aim for 6."),
    ("📉 What is a draw down?", "A draw down is how much your portfolio has fallen from its peak. If you had $110K and it drops to $88K, that's a -20% draw down. They are temporary if your strategy is sound."),
    ("🧠 Play your own game.", "Malik's key insight: don't copy others blindly. Build conviction in rules you understand. If you don't understand why a rule works, you'll abandon it when it matters most."),
]

def get_daily_tip(build_time_str):
    """Pick a tip based on the day of the year."""
    day = datetime.now().timetuple().tm_yday
    return LEARNING_TIPS[day % len(LEARNING_TIPS)]

# ─── Generate HTML ────────────────────────────────────────────────────────────
def generate_html(signal_data, chart_data, backtest_data, build_time, tz_name,
                  gear_price, bboz_price, research_10, high_conviction_4):
    bt = backtest_data
    cd = chart_data
    sd = signal_data

    # Regime colours — updated to match new blue theme
    regime = sd["regime"]
    if "STRONG UP" in regime:
        rc, rbg, rborder = "#1a3a5c", "#e8f0f8", "#90b8d4"
    elif "UP" in regime:
        rc, rbg, rborder = "#2563a8", "#eef4fb", "#b8d4e8"
    elif "STRONG DOWN" in regime:
        rc, rbg, rborder = "#b91c1c", "#fef2f2", "#fecaca"
    elif "DOWN" in regime:
        rc, rbg, rborder = "#c2410c", "#fff7ed", "#fed7aa"
    else:
        rc, rbg, rborder = "#475569", "#f4f6f9", "#d0dce8"

    action_display = sd["action"]
    if "GEAR" in action_display:
        action_display = action_display.replace("GEAR", '<span style="color:#1a3a5c;font-weight:700">GEAR</span>')
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
          <td style="color:#90b8d4;font-size:16px">→</td>
          <td><span class="badge {tc}">{t["to"]}</span></td>
          <td style="font-family:'DM Mono',monospace">{t["price"]:,}</td></tr>"""
    if not trans_rows:
        trans_rows = '<tr><td colspan="5" style="color:#90b8d4;text-align:center;padding:20px;font-family:DM Mono,monospace;font-size:12px">No transitions recorded yet</td></tr>'

    bt_rc = "#1a3a5c" if bt["total_return"] >= 0 else "#b91c1c"
    bt_cc = "#1a3a5c" if bt["cagr"] >= 0 else "#b91c1c"

    # ETF price JS values
    gear_js = str(gear_price) if gear_price else "null"
    bboz_js = str(bboz_price) if bboz_price else "null"

    # Learning tip of the day
    tip_title, tip_body = get_daily_tip(build_time)

    # ── Stocks tab HTML ───────────────────────────────────────────────
    # ── ASX Swing Catalyst HTML ──────────────────────────────────────────────

    # Live Pulse banner (research 10 — scrolling ticker strip)
    pulse_items = ""
    for s in research_10:
        chg_col  = "#22c55e" if s["change"] >= 0 else "#ef4444"
        chg_arrow = "▲" if s["change"] >= 0 else "▼"
        badge_col = {
            "OB": "#f59e0b", "MC": "#3b82f6",
            "PB": "#8b5cf6", "VB": "#10b981"
        }.get(s["badge"], "#6b7280")
        pulse_items += f'''<span class="pulse-item">
          <span class="pulse-ticker">{s["ticker"]}.ASX</span>
          <span class="pulse-price" style="color:{chg_col}">{chg_arrow} ${s["price"]:.2f} ({s["change"]:+.1f}%)</span>
          <span class="pulse-badge" style="background:{badge_col}">{s["badge"]}</span>
        </span>'''

    # High Conviction table rows (top 4)
    hc_rows = ""
    for s in high_conviction_4:
        chg_col   = "pos" if s["change"] >= 0 else "neg"
        chg_arrow = "▲" if s["change"] >= 0 else "▼"
        strat_colours = {
            "Oversold Bounce":  ("#f59e0b", "#fffbeb"),
            "Momentum":         ("#3b82f6", "#eff6ff"),
            "SMA20 Pullback":   ("#8b5cf6", "#f5f3ff"),
            "Volume Breakout":  ("#10b981", "#ecfdf5"),
        }
        sc, sbg = strat_colours.get(s["strategy"], ("#6b7280","#f9fafb"))
        multi = f' <span style="font-size:9px;color:#90b8d4">+{len(s["strategies_all"])-1}</span>' if len(s["strategies_all"]) > 1 else ""
        hc_rows += f'''<tr>
          <td>
            <div style="font-weight:700;font-size:15px;color:#e2e8f0">{s["ticker"]}.ASX</div>
            <div style="font-size:10px;color:#90b8d4;margin-top:2px">{s["sector"]}</div>
          </td>
          <td>
            <span style="display:inline-block;background:{sbg};color:{sc};border:1px solid {sc};
              border-radius:20px;padding:3px 10px;font-size:11px;font-weight:600;font-family:'DM Mono',monospace">
              {s["strategy"]}{multi}
            </span>
          </td>
          <td style="font-family:'DM Mono',monospace;font-weight:600;color:#e2e8f0">${s["entry"]:.2f}</td>
          <td style="font-family:'DM Mono',monospace;color:#ef4444">${s["stop"]:.2f}
            <div style="font-size:10px;color:#6b7280">−{s["risk_pct"]:.1f}% · ATR ${s["atr"]:.2f}</div>
          </td>
          <td style="font-family:'DM Mono',monospace;color:#22c55e">${s["target"]:.2f}
            <div style="font-size:10px;color:#6b7280">R/R {s["rr_ratio"]:.1f}:1</div>
          </td>
          <td style="font-size:11px;color:#94a3b8;max-width:160px">{s["exit_signal"]}</td>
          <td style="font-size:11px;color:#94a3b8;max-width:180px">{s["conviction"]}</td>
        </tr>'''

    if not hc_rows:
        hc_rows = '<tr><td colspan="7" style="text-align:center;padding:32px;color:#475569;font-family:DM Mono,monospace;font-size:12px">No high conviction setups today — all candidates failed 1:3 R/R or hard filters.</td></tr>'

    # Research phase table (10 candidates — light detail)
    research_rows = ""
    for i, s in enumerate(research_10, 1):
        chg_col = "pos-dark" if s["change"] >= 0 else "neg-dark"
        strat_badges = " ".join(
            f'<span class="strat-mini-badge strat-{b.lower()}">{b}</span>'
            for b in [STRATEGY_BADGES.get(st,"?") for st in s["strategies_all"]]
        )
        research_rows += f'''<tr>
          <td style="color:#64748b;font-family:'DM Mono',monospace;font-size:11px">{i}</td>
          <td>
            <span style="font-weight:600;color:#e2e8f0">{s["ticker"]}</span>
            <span style="font-size:10px;color:#475569;margin-left:6px">{s["sector"]}</span>
          </td>
          <td style="font-family:'DM Mono',monospace;color:#e2e8f0">${s["price"]:.2f}</td>
          <td class="{chg_col}" style="font-family:'DM Mono',monospace">{"▲" if s["change"]>=0 else "▼"} {abs(s["change"]):.1f}%</td>
          <td>{strat_badges}</td>
          <td style="font-family:'DM Mono',monospace;color:{"#f59e0b" if s["rsi"]<35 else "#22c55e" if s["rsi"]<=65 else "#94a3b8"}">{s["rsi"]:.0f}</td>
          <td style="font-family:'DM Mono',monospace;color:#64748b">${s["sma200"]:.2f} <span style="color:{"#22c55e" if s["ext200"]>0 else "#ef4444"};font-size:10px">+{s["ext200"]:.1f}%</span></td>
          <td style="font-family:'DM Mono',monospace;color:#64748b">{s["vol_ratio"]:.1f}× <span style="font-size:10px;color:#475569">${s["dollar_vol_k"]:.0f}K/day</span></td>
          <td style="font-family:'DM Mono',monospace;color:#ef4444">${s["stop"]:.2f}</td>
          <td style="font-family:'DM Mono',monospace;color:#22c55e">${s["target"]:.2f}</td>
        </tr>'''

    if not research_rows:
        research_rows = '<tr><td colspan="10" style="text-align:center;padding:24px;color:#475569;font-family:DM Mono,monospace;font-size:12px">No candidates passed hard filters today (above SMA200 + $500K+ liquidity + 1:3 R/R).</td></tr>'

    # Position sizing for high conviction 4
    pos_rows = ""
    for s in high_conviction_4:
        cap = 3333
        shares = s["shares_suggested"]
        cost   = round(shares * s["entry"], 2)
        max_loss = s["max_loss_dollar"]
        brokerage = 3.00
        net_risk  = round(max_loss + brokerage, 2)
        free_ride_target = round(s["entry"] * 1.10, 2)
        pos_rows += f'''<tr>
          <td style="font-weight:600;color:#e2e8f0">{s["ticker"]}</td>
          <td style="font-family:'DM Mono',monospace;color:#94a3b8">${cap:,}</td>
          <td style="font-family:'DM Mono',monospace;color:#e2e8f0">{shares}</td>
          <td style="font-family:'DM Mono',monospace;color:#e2e8f0">${cost:,.2f}</td>
          <td style="font-family:'DM Mono',monospace;color:#ef4444">−${net_risk:.2f}</td>
          <td style="font-family:'DM Mono',monospace;color:#f59e0b">${free_ride_target:.2f}</td>
          <td style="font-family:'DM Mono',monospace;color:#22c55e">${s["target"]:.2f} ({s["rr_ratio"]:.1f}:1)</td>
        </tr>'''

    if not pos_rows:
        pos_rows = '<tr><td colspan="7" style="text-align:center;padding:20px;color:#475569;font-family:DM Mono,monospace;font-size:12px">No positions to size today.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jun Yadnap Trade System</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  /* ── New neutral blue theme ── */
  --bg:#f4f6f9;
  --bg2:#ffffff;
  --bg3:#eef2f7;
  --border:#d8e4ee;
  --border2:#c4d4e2;
  --text:#1e2a38;
  --text2:#4a5f72;
  --text3:#8aa4b8;
  --accent:#90b8d4;
  --accent-dark:#1a3a5c;
  --accent-mid:#2563a8;
  --green:#15803d;--green-bg:#f0fdf4;
  --red:#b91c1c;--red-bg:#fef2f2;
  --amber:#b45309;--amber-bg:#fffbeb;
  --radius:12px;--radius-sm:8px;
  --shadow:0 1px 4px rgba(26,58,92,0.07),0 1px 2px rgba(26,58,92,0.04);
  --shadow-md:0 4px 16px rgba(26,58,92,0.10);
}}
body{{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;font-size:14px;min-height:100vh}}

/* ── Lock Screen ── */
#lock-screen{{position:fixed;inset:0;background:#0d1f2d;z-index:999;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:28px}}
#lock-screen.hidden{{display:none}}
.lock-logo{{font-family:'Inter',sans-serif;font-size:28px;font-weight:700;color:#f4f6f9;text-align:center;line-height:1.3;letter-spacing:-0.5px}}
.lock-logo em{{color:#90b8d4;font-style:normal}}
.lock-sub{{font-size:10px;color:#2a4a62;font-family:'DM Mono',monospace;letter-spacing:2px;text-transform:uppercase}}
.lock-tip{{background:#112233;border:1px solid #1e3a5c;border-radius:var(--radius);padding:18px 22px;width:340px;max-width:90vw}}
.lock-tip-title{{font-size:12px;color:#90b8d4;font-weight:600;margin-bottom:6px;font-family:'DM Mono',monospace}}
.lock-tip-body{{font-size:12px;color:#8aa4b8;line-height:1.7}}
.lock-form{{display:flex;flex-direction:column;gap:12px;width:300px;max-width:90vw}}
.lock-input{{background:#1a3a5c;border:1px solid #2a5070;border-radius:var(--radius-sm);padding:13px 16px;color:#f4f6f9;font-size:14px;outline:none;transition:border-color .15s;font-family:'Inter',sans-serif}}
.lock-input:focus{{border-color:#90b8d4}}
.lock-input::placeholder{{color:#2a5070}}
.lock-btn{{background:#1a3a5c;color:#f4f6f9;border:none;padding:13px;border-radius:var(--radius-sm);font-size:14px;font-weight:600;cursor:pointer;transition:background .15s;font-family:'Inter',sans-serif}}
.lock-btn:hover{{background:#2563a8}}
.lock-error{{font-size:12px;color:#f87171;text-align:center;display:none;font-family:'DM Mono',monospace}}

/* ── Layout ── */
.app{{display:grid;grid-template-columns:240px 1fr;min-height:100vh}}
.sidebar{{background:#0d1f2d;display:flex;flex-direction:column;position:sticky;top:0;height:100vh;overflow-y:auto}}
.main{{padding:32px 40px;overflow-y:auto}}

/* ── Sidebar ── */
.logo-area{{padding:28px 20px 22px;border-bottom:1px solid #1a3a5c}}
.logo-name{{font-family:'Inter',sans-serif;font-size:17px;font-weight:700;color:#f4f6f9;line-height:1.4;letter-spacing:-0.3px}}
.logo-name em{{color:#90b8d4;font-style:normal}}
.logo-sub{{font-size:10px;color:#2a4a62;letter-spacing:1.5px;text-transform:uppercase;margin-top:6px;font-family:'DM Mono',monospace}}
.dot{{width:7px;height:7px;border-radius:50%;background:#22c55e;display:inline-block;margin-right:6px;animation:pulse 2s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:0.3;transform:scale(0.75)}}}}
.sig-pill{{margin:16px 12px;padding:14px 16px;background:#112233;border-radius:var(--radius);border:1px solid #1a3a5c}}
.sig-pill-label{{font-size:10px;color:#2a5070;letter-spacing:1px;text-transform:uppercase;font-family:'DM Mono',monospace;margin-bottom:8px}}
.sig-pill-action{{font-size:15px;font-weight:700;color:{rc};font-family:'DM Mono',monospace}}
.sig-pill-regime{{font-size:11px;color:#4a6a82;margin-top:3px}}
.nav-section{{padding:16px 12px 8px}}
.nav-label{{font-size:10px;color:#1a3a5c;letter-spacing:1.5px;text-transform:uppercase;font-family:'DM Mono',monospace;padding:0 8px;margin-bottom:8px}}
.nav-item{{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:var(--radius-sm);cursor:pointer;color:#4a6a82;font-size:13px;transition:all .15s;margin-bottom:2px;font-family:'Inter',sans-serif}}
.nav-item:hover{{background:#1a3a5c;color:#d0e4f4}}
.nav-item.active{{background:#1a3a5c;color:#90b8d4;font-weight:600}}
.nav-icon{{font-size:13px;width:18px;text-align:center}}
.sb-footer{{margin-top:auto;padding:16px;border-top:1px solid #1a3a5c;font-size:11px;color:#2a4a62;line-height:1.9;font-family:'DM Mono',monospace}}

/* ── Tabs ── */
.tab{{display:none}}.tab.active{{display:block}}

/* ── Page header ── */
.page-header{{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:28px;flex-wrap:wrap;gap:12px}}
.page-title{{font-family:'Inter',sans-serif;font-size:26px;font-weight:700;color:var(--text);letter-spacing:-0.5px}}
.page-sub{{font-size:12px;color:var(--text3);margin-top:4px;font-family:'DM Mono',monospace}}
.build-badge{{font-size:11px;color:var(--text3);font-family:'DM Mono',monospace;background:var(--bg2);border:1px solid var(--border);padding:6px 14px;border-radius:20px;box-shadow:var(--shadow)}}

/* ── Signal card ── */
.signal-card{{background:{rbg};border:1px solid {rborder};border-left:4px solid {rc};border-radius:var(--radius);padding:24px 28px;margin-bottom:24px;box-shadow:var(--shadow)}}
.signal-regime-tag{{font-size:11px;color:{rc};letter-spacing:2px;text-transform:uppercase;font-family:'DM Mono',monospace;font-weight:600;margin-bottom:8px}}
.signal-action{{font-family:'Inter',sans-serif;font-size:34px;font-weight:700;color:var(--text);line-height:1;margin-bottom:10px;letter-spacing:-1px}}
.signal-reason{{font-size:13px;color:var(--text2);line-height:1.7;max-width:600px}}
.alloc-row{{display:flex;gap:10px;margin-top:20px;flex-wrap:wrap}}
.alloc-chip{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px 16px;min-width:105px;box-shadow:var(--shadow)}}
.alloc-chip-name{{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:1px;font-family:'DM Mono',monospace;margin-bottom:4px}}
.alloc-chip-val{{font-size:22px;font-weight:700;font-family:'DM Mono',monospace}}
.av-gear{{color:var(--accent-dark)}}.av-bboz{{color:var(--red)}}.av-cash{{color:var(--text2)}}

/* ── Metric cards ── */
.metrics-row{{display:grid;gap:14px;margin-bottom:24px}}
.m5{{grid-template-columns:repeat(5,1fr)}}.m4{{grid-template-columns:repeat(4,1fr)}}.m3{{grid-template-columns:repeat(3,1fr)}}
.metric-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:18px 20px;box-shadow:var(--shadow)}}
.metric-label{{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:1.5px;font-family:'DM Mono',monospace;margin-bottom:10px}}
.metric-val{{font-size:24px;font-weight:700;color:var(--text);font-family:'DM Mono',monospace;letter-spacing:-0.5px}}
.metric-val.pos{{color:var(--green)}}.metric-val.neg{{color:var(--red)}}.metric-val.neu{{color:var(--amber)}}
.metric-sub{{font-size:11px;color:var(--text3);margin-top:5px}}

/* ── Chart cards ── */
.card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:22px;margin-bottom:24px;box-shadow:var(--shadow)}}
.card-header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;flex-wrap:wrap;gap:8px}}
.card-title{{font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:1.5px;font-family:'DM Mono',monospace;font-weight:500}}
.legend{{display:flex;gap:14px;flex-wrap:wrap}}
.legend-item{{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--text2)}}
.legend-line{{width:14px;height:2px;border-radius:2px}}
.section-title{{font-family:'Inter',sans-serif;font-size:17px;font-weight:700;color:var(--text);margin-bottom:16px;margin-top:4px}}

/* ── Perf summary ── */
.perf-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:24px}}
.perf-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:18px 20px;box-shadow:var(--shadow)}}
.perf-label{{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:1px;font-family:'DM Mono',monospace;margin-bottom:6px}}
.perf-val{{font-size:20px;font-weight:700;font-family:'DM Mono',monospace}}
.perf-val.pos{{color:var(--green)}}.perf-val.neg{{color:var(--red)}}

/* ── Tables ── */
.data-table{{width:100%;border-collapse:collapse;font-size:13px}}
.data-table th{{font-size:10px;color:var(--text3);text-align:left;padding:9px 12px;border-bottom:2px solid var(--border);text-transform:uppercase;letter-spacing:1px;font-weight:500;font-family:'DM Mono',monospace}}
.data-table td{{padding:11px 12px;border-bottom:1px solid var(--border);color:var(--text);vertical-align:middle}}
.data-table tr:last-child td{{border-bottom:none}}
.data-table tr:hover td{{background:var(--bg3)}}
.pos{{color:var(--green);font-family:'DM Mono',monospace}}
.neg{{color:var(--red);font-family:'DM Mono',monospace}}

/* ── Badges ── */
.badge{{display:inline-block;padding:3px 9px;border-radius:20px;font-size:10px;font-weight:600;font-family:'DM Mono',monospace;letter-spacing:.5px}}
.badge-up{{background:#dbeafe;color:#1e40af}}
.badge-dn{{background:#fee2e2;color:#991b1b}}
.badge-nu{{background:#f1f5f9;color:#475569}}
.badge-mid{{background:#e8f0f8;color:#1a3a5c}}
.badge-bounce{{background:#fef3c7;color:#92400e}}
.result{{display:inline-block;padding:3px 9px;border-radius:20px;font-size:10px;font-weight:600;font-family:'DM Mono',monospace}}
.result-win{{background:#dcfce7;color:#166534}}
.result-lose{{background:#fee2e2;color:#991b1b}}
.buy-tag{{color:var(--accent-mid);font-weight:600;font-family:'DM Mono',monospace;font-size:11px}}
.sell-tag{{color:var(--red);font-weight:600;font-family:'DM Mono',monospace;font-size:11px}}
.hold-tag{{color:var(--text3);font-weight:600;font-family:'DM Mono',monospace;font-size:11px}}

/* ── Signal chips ── */
.sig-chip{{display:inline-block;background:var(--bg3);border:1px solid var(--border);border-radius:20px;padding:2px 8px;font-size:10px;color:var(--text2);font-family:'DM Mono',monospace;margin-right:3px;margin-bottom:2px}}

/* ── Form ── */
.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px}}
.form-group{{display:flex;flex-direction:column;gap:6px}}
.form-label{{font-size:11px;color:var(--text2);font-family:'DM Mono',monospace;letter-spacing:1px;text-transform:uppercase;font-weight:500}}
.form-input,.form-select{{background:var(--bg);border:1px solid var(--border2);border-radius:var(--radius-sm);padding:10px 13px;color:var(--text);font-size:13px;font-family:'Inter',sans-serif;width:100%;outline:none;transition:border-color .15s}}
.form-input:focus,.form-select:focus{{border-color:#90b8d4;box-shadow:0 0 0 3px rgba(144,184,212,.15)}}
.btn-primary{{background:var(--accent-dark);color:#fff;border:none;padding:11px 22px;border-radius:var(--radius-sm);font-size:13px;cursor:pointer;font-weight:600;transition:background .15s;font-family:'Inter',sans-serif}}
.btn-primary:hover{{background:#2563a8}}
.btn-ghost{{background:transparent;border:1px solid var(--border2);color:var(--text2);padding:11px 22px;border-radius:var(--radius-sm);font-size:13px;cursor:pointer;transition:all .15s;margin-left:8px;font-family:'Inter',sans-serif}}
.btn-ghost:hover{{border-color:var(--accent);color:var(--text)}}
.btn-del{{background:none;border:none;cursor:pointer;color:var(--text3);font-size:15px;padding:4px 7px;border-radius:6px;transition:all .15s;line-height:1}}
.btn-del:hover{{color:var(--red);background:var(--red-bg)}}
.btn-fetch{{background:var(--bg3);border:1px solid var(--border2);color:var(--accent-dark);padding:9px 16px;border-radius:var(--radius-sm);font-size:12px;cursor:pointer;transition:all .15s;font-family:'DM Mono',monospace;font-weight:500}}
.btn-fetch:hover{{background:var(--accent-dark);color:#fff}}

/* ── Info / warn ── */
.info-box{{background:#eef4fb;border:1px solid #b8d4e8;border-radius:var(--radius-sm);padding:12px 16px;font-size:12px;color:#1a3a5c;line-height:1.7;margin-bottom:16px}}
.warn-box{{background:#fffbeb;border:1px solid #fde68a;border-radius:var(--radius-sm);padding:12px 16px;font-size:12px;color:var(--amber);line-height:1.7;margin-bottom:16px}}
.tip-box{{background:var(--bg2);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:var(--radius-sm);padding:14px 18px;font-size:12px;color:var(--text2);line-height:1.8;margin-bottom:20px}}
.tip-box strong{{color:var(--accent-dark);display:block;margin-bottom:5px;font-size:12px;text-transform:uppercase;letter-spacing:1px;font-family:'DM Mono',monospace}}

/* ── Strat cards ── */
.strat-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:20px}}
.strat-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:18px;transition:box-shadow .15s}}
.strat-card:hover{{box-shadow:var(--shadow-md)}}
.strat-header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}}
.strat-name{{font-size:13px;font-weight:600;color:var(--text)}}
.strat-body{{font-size:12px;color:var(--text2);line-height:1.7}}
.strat-rule{{margin-top:8px;font-size:11px;font-family:'DM Mono',monospace;color:var(--text3);line-height:1.6}}

/* ── Stock cards ── */
.stock-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:16px;box-shadow:var(--shadow);transition:box-shadow .15s}}
.stock-card:hover{{box-shadow:var(--shadow-md)}}
.stock-card-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}}
.stock-rank{{font-size:11px;font-weight:600;color:var(--accent);font-family:'DM Mono',monospace;margin-right:6px}}
.stock-ticker{{font-size:16px;font-weight:700;color:var(--accent-dark)}}
.stock-signals{{font-size:11px;color:var(--text3);margin-bottom:12px;line-height:1.7}}
.stock-stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px}}
.sstat{{background:var(--bg3);border-radius:8px;padding:10px;text-align:center}}
.sstat-label{{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:1px;font-family:'DM Mono',monospace;margin-bottom:4px}}
.sstat-val{{font-size:15px;font-weight:700;font-family:'DM Mono',monospace;color:var(--text)}}
.stock-rr{{background:var(--bg3);border-radius:8px;padding:12px}}
.etf-price-badge{{background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:10px 16px;display:inline-block;margin-right:10px;margin-bottom:10px}}
.etf-price-label{{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:1px;font-family:'DM Mono',monospace}}
.etf-price-val{{font-size:18px;font-weight:700;font-family:'DM Mono',monospace;color:var(--accent-dark)}}

/* ── Pulse Banner ── */
.pulse-banner{{background:#0a1929;border-bottom:1px solid #1a3a5c;padding:10px 20px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:24px;border-radius:var(--radius)}}
.pulse-label{{font-size:10px;font-weight:700;color:#90b8d4;font-family:'DM Mono',monospace;letter-spacing:2px;text-transform:uppercase;white-space:nowrap}}
.pulse-track{{flex:1;overflow:hidden;min-width:0}}
.pulse-scroll{{display:flex;gap:24px;animation:pulse-scroll 30s linear infinite;width:max-content}}
@keyframes pulse-scroll{{0%{{transform:translateX(0)}}100%{{transform:translateX(-50%)}}}}
.pulse-item{{display:flex;align-items:center;gap:8px;white-space:nowrap}}
.pulse-ticker{{font-family:'DM Mono',monospace;font-size:12px;font-weight:700;color:#e2e8f0}}
.pulse-price{{font-family:'DM Mono',monospace;font-size:12px}}
.pulse-badge{{font-size:9px;font-weight:700;font-family:'DM Mono',monospace;padding:2px 6px;border-radius:4px;color:#fff}}
.pulse-legend{{display:flex;gap:12px;flex-wrap:wrap;font-size:10px;font-family:'DM Mono',monospace;white-space:nowrap}}
.pulse-legend-item{{opacity:0.8}}

/* ── High Conviction Section ── */
.hc-section{{background:#0d1f2d;border:1px solid #1a3a5c;border-radius:var(--radius);padding:24px;margin-bottom:20px}}
.hc-header{{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:12px}}
.hc-title{{font-family:'Inter',sans-serif;font-size:16px;font-weight:700;color:#e2e8f0;letter-spacing:-0.3px}}
.hc-sub{{font-size:11px;color:#475569;font-family:'DM Mono',monospace;margin-top:4px}}
.hc-stat-pill{{background:#112233;border:1px solid #1a3a5c;border-radius:8px;padding:8px 14px;text-align:center}}
.hc-stat-label{{font-size:9px;color:#475569;text-transform:uppercase;letter-spacing:1px;font-family:'DM Mono',monospace;display:block;margin-bottom:3px}}
.hc-stat-val{{font-size:15px;font-weight:700;font-family:'DM Mono',monospace;color:#90b8d4}}

/* ── HC Table (dark) ── */
.hc-table{{width:100%;border-collapse:collapse;font-size:13px}}
.hc-table th{{font-size:10px;color:#475569;text-align:left;padding:9px 12px;border-bottom:1px solid #1a3a5c;text-transform:uppercase;letter-spacing:1px;font-weight:500;font-family:'DM Mono',monospace}}
.hc-table td{{padding:13px 12px;border-bottom:1px solid #112233;vertical-align:middle}}
.hc-table tr:last-child td{{border-bottom:none}}
.hc-table tr:hover td{{background:#112233}}
.pos-dark{{color:#22c55e;font-family:'DM Mono',monospace}}
.neg-dark{{color:#ef4444;font-family:'DM Mono',monospace}}

/* ── Strategy mini badges ── */
.strat-mini-badge{{display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700;font-family:'DM Mono',monospace;margin-right:3px}}
.strat-ob{{background:#78350f;color:#fbbf24}}
.strat-mc{{background:#1e3a8a;color:#93c5fd}}
.strat-pb{{background:#3b0764;color:#c4b5fd}}
.strat-vb{{background:#064e3b;color:#6ee7b7}}

/* ── HC Rules ── */
.hc-rules{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:20px}}
.hc-rule-item{{display:flex;align-items:flex-start;gap:12px;background:#112233;border-radius:8px;padding:14px}}
.hc-rule-icon{{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}}
.hc-rule-title{{font-size:12px;font-weight:600;color:#e2e8f0;margin-bottom:4px}}
.hc-rule-body{{font-size:11px;color:#64748b;line-height:1.6}}

/* ── Dark metrics ── */
.dark-metric{{background:#112233;border:1px solid #1a3a5c;border-radius:8px;padding:14px 18px}}
.dark-metric-label{{font-size:10px;color:#475569;text-transform:uppercase;letter-spacing:1px;font-family:'DM Mono',monospace;margin-bottom:6px}}
.dark-metric-val{{font-size:20px;font-weight:700;font-family:'DM Mono',monospace;color:#e2e8f0}}

/* ── Dark form inputs ── */
.dark-input{{background:#112233;border:1px solid #1a3a5c;border-radius:8px;padding:10px 13px;color:#e2e8f0;font-size:13px;font-family:'Inter',sans-serif;width:100%;outline:none;transition:border-color .15s}}
.dark-input:focus{{border-color:#90b8d4}}
.dark-input::placeholder{{color:#1e3a5c}}
.btn-dark-add{{background:#1a3a5c;color:#90b8d4;border:1px solid #2563a8;padding:10px 18px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif;white-space:nowrap;transition:background .15s;align-self:flex-end}}
.btn-dark-add:hover{{background:#2563a8;color:#fff}}
.btn-dark-del{{background:none;border:none;cursor:pointer;color:#475569;font-size:14px;padding:4px 8px;border-radius:6px;transition:all .15s}}
.btn-dark-del:hover{{color:#ef4444;background:rgba(239,68,68,0.1)}}

::-webkit-scrollbar{{width:5px}}
::-webkit-scrollbar-track{{background:var(--bg3)}}
::-webkit-scrollbar-thumb{{background:var(--border2);border-radius:3px}}

@media(max-width:768px){{
  .app{{grid-template-columns:1fr}}
  .sidebar{{position:fixed;bottom:0;left:0;right:0;height:auto;flex-direction:row;z-index:100;border-top:1px solid #1a3a5c;overflow-x:auto}}
  .logo-area,.sig-pill,.sb-footer,.nav-label{{display:none}}
  .nav-section{{display:flex;flex-direction:row;padding:6px}}
  .nav-item{{flex-direction:column;gap:3px;font-size:9px;padding:8px 10px}}
  .main{{padding:16px;padding-bottom:80px}}
  .m5,.m4,.metrics-row{{grid-template-columns:repeat(2,1fr)}}
  .perf-grid,.strat-grid,.form-grid{{grid-template-columns:1fr}}
  .stock-stats{{grid-template-columns:repeat(2,1fr)}}
}}
</style>
</head>
<body>

<!-- ── Lock Screen ── -->
<div id="lock-screen">
  <div class="lock-logo">Jun <em>Yadnap</em><br>Trade System</div>
  <div class="lock-sub">ASX Systematic Trading</div>
  <div class="lock-tip">
    <div class="lock-tip-title">💡 Today's Trading Reminder</div>
    <div class="lock-tip-body"><strong>{tip_title}</strong><br>{tip_body}</div>
  </div>
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
    <div class="nav-item" onclick="showTab('stocks',this)"><span class="nav-icon">◉</span> ASX Stocks</div>
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

  <div class="tip-box">
    <strong>{tip_title}</strong>
    {tip_body}
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

  <!-- ETF price badges -->
  <div style="margin-bottom:20px">
    <div class="etf-price-badge">
      <div class="etf-price-label">GEAR.AX Last Close</div>
      <div class="etf-price-val">${gear_price if gear_price else '—'}</div>
    </div>
    <div class="etf-price-badge">
      <div class="etf-price-label">BBOZ.AX Last Close</div>
      <div class="etf-price-val">${bboz_price if bboz_price else '—'}</div>
    </div>
  </div>

  <div class="card">
    <div class="card-header">
      <div class="card-title">ASX 200 — Price with SMA20 &amp; SMA250</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-line" style="background:#1e2a38"></div>ASX 200</div>
        <div class="legend-item"><div class="legend-line" style="background:#90b8d4;border-top:2px dashed #90b8d4;background:none"></div>SMA 20</div>
        <div class="legend-item"><div class="legend-line" style="background:#1a3a5c"></div>SMA 250</div>
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
        <div class="legend-item"><div class="legend-line" style="background:#90b8d4"></div>ASX 200</div>
        <div class="legend-item"><div class="legend-line" style="background:#1a3a5c"></div>GEAR.AX</div>
      </div>
    </div>
    <div style="position:relative;height:220px"><canvas id="gearChart"></canvas></div>
  </div>

  <div class="card">
    <div class="card-header">
      <div class="card-title">Equity Curve vs BBOZ Benchmark (Normalised to 100)</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-line" style="background:#90b8d4"></div>ASX 200</div>
        <div class="legend-item"><div class="legend-line" style="background:#b91c1c"></div>BBOZ.AX</div>
      </div>
    </div>
    <div style="position:relative;height:220px"><canvas id="bbozChart"></canvas></div>
  </div>
</div>

<!-- ══════════ PAPER TRADES ══════════ -->
<div id="tab-trades" class="tab">
  <div class="page-header">
    <div><div class="page-title">Paper Trades</div><div class="page-sub">Starting capital: $100,000 AUD · Realised P&amp;L only</div></div>
  </div>

  <div class="tip-box">
    <strong>💡 How P&amp;L is calculated</strong>
    Portfolio shows <strong>cash remaining + realised P&amp;L</strong> from closed trades only. "Open Value" shows the cost of any open positions. P&amp;L only appears when you SELL. When you BUY, cash is reserved but no loss is recorded.
  </div>

  <!-- ETF price auto-fill strip -->
  <div class="card" style="margin-bottom:20px;padding:16px 20px">
    <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
      <div>
        <div class="form-label" style="margin-bottom:6px">Today's Prices (auto-filled from last close)</div>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <div class="etf-price-badge" style="cursor:pointer" onclick="prefillPrice('GEAR',GEAR_PRICE)" title="Click to use for GEAR trade">
            <div class="etf-price-label">GEAR.AX ↓ click to prefill</div>
            <div class="etf-price-val" id="gear-price-badge">${gear_price if gear_price else '—'}</div>
          </div>
          <div class="etf-price-badge" style="cursor:pointer" onclick="prefillPrice('BBOZ',BBOZ_PRICE)" title="Click to use for BBOZ trade">
            <div class="etf-price-label">BBOZ.AX ↓ click to prefill</div>
            <div class="etf-price-val" id="bboz-price-badge">${bboz_price if bboz_price else '—'}</div>
          </div>
        </div>
      </div>
      <div style="font-size:11px;color:var(--text3);font-family:'DM Mono',monospace;max-width:200px">Prices from last market close. Click a badge to pre-fill the price field below.</div>
    </div>
  </div>

  <div class="metrics-row m5">
    <div class="metric-card">
      <div class="metric-label">Portfolio (Cash)</div>
      <div class="metric-val" id="pt-portfolio">$100,000</div>
      <div class="metric-sub">starting $100K + closed P&amp;L</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Open Position Value</div>
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
        <select class="form-select" id="t-ticker" onchange="onTickerChange()">
          <option>GEAR</option><option>BBOZ</option><option>CASH</option>
        </select></div>
      <div class="form-group"><label class="form-label">Action</label>
        <select class="form-select" id="t-action"><option>BUY</option><option>SELL</option><option>HOLD</option></select></div>
      <div class="form-group">
        <label class="form-label">Price ($) <span id="price-hint" style="font-size:10px;color:var(--accent);text-transform:none;letter-spacing:0;font-weight:400"></span></label>
        <input class="form-input" type="number" id="t-price" placeholder="e.g. 42.50" step="0.01">
      </div>
      <div class="form-group"><label class="form-label">Shares</label>
        <input class="form-input" type="number" id="t-shares" placeholder="e.g. 500" oninput="calcValue()"></div>
      <div class="form-group"><label class="form-label">Estimated Value</label>
        <input class="form-input" type="text" id="t-value-display" placeholder="$0.00" readonly style="color:var(--text3)"></div>
      <div class="form-group" style="grid-column:1/-1"><label class="form-label">Strategy</label>
        <select class="form-select" id="t-strat">
          <option>Momentum Long</option><option>MR Long 1 (SMA20 Pullback)</option>
          <option>MR Long 2 (Deep Pullback)</option><option>MR Long 3 (SMA250 Bounce)</option>
          <option>MR Short (Overextension)</option><option>Momentum Short 1 (Breakdown)</option>
          <option>Momentum Short 2 (Failed Bounce)</option><option>Cash / No signal</option>
        </select></div>
      <div class="form-group" style="grid-column:1/-1"><label class="form-label">Notes (optional)</label>
        <input class="form-input" type="text" id="t-notes" placeholder="e.g. RSI pulled back to 45, entered on SMA20 bounce"></div>
    </div>
    <button class="btn-primary" onclick="logTrade()">Log Trade</button>
    <button class="btn-ghost" onclick="clearTrades()">Clear All</button>
  </div>

  <div class="card">
    <div class="card-header"><div class="card-title">Trade Log</div></div>
    <div style="overflow-x:auto">
      <table class="data-table">
        <thead><tr><th>#</th><th>Date</th><th>Ticker</th><th>Action</th><th>Shares</th><th>Price</th><th>Value</th><th>Strategy</th><th>Notes</th><th></th></tr></thead>
        <tbody id="trade-tbody">
          <tr><td colspan="10" style="color:var(--text3);padding:28px;text-align:center;font-family:'DM Mono',monospace;font-size:12px">No trades logged yet</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- ══════════ ASX STOCKS ══════════ -->
<div id="tab-stocks" class="tab">

  <!-- ── LIVE PULSE BANNER ── -->
  <div class="pulse-banner">
    <div class="pulse-label">LIVE PULSE</div>
    <div class="pulse-track">
      <div class="pulse-scroll">
        {pulse_items}{pulse_items}
      </div>
    </div>
    <div class="pulse-legend">
      <span class="pulse-legend-item" style="color:#f59e0b">OB Oversold Bounce</span>
      <span class="pulse-legend-item" style="color:#3b82f6">MC Momentum</span>
      <span class="pulse-legend-item" style="color:#8b5cf6">PB Pullback</span>
      <span class="pulse-legend-item" style="color:#10b981">VB Vol Breakout</span>
    </div>
  </div>

  <!-- ── HIGH CONVICTION COMMAND CENTRE ── -->
  <div class="hc-section">
    <div class="hc-header">
      <div>
        <div class="hc-title">High Conviction Setups</div>
        <div class="hc-sub">Top 4 · 1:3 R/R enforced · Above SMA200 · $500K+ daily liquidity · Updated {build_time} {tz_name}</div>
      </div>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <div class="hc-stat-pill"><span class="hc-stat-label">Capital</span><span class="hc-stat-val">$10,000</span></div>
        <div class="hc-stat-pill"><span class="hc-stat-label">Per Trade</span><span class="hc-stat-val">$3,333</span></div>
        <div class="hc-stat-pill"><span class="hc-stat-label">Brokerage</span><span class="hc-stat-val">$3.00</span></div>
        <div class="hc-stat-pill"><span class="hc-stat-label">Max Positions</span><span class="hc-stat-val">3</span></div>
      </div>
    </div>

    <div style="overflow-x:auto">
      <table class="hc-table">
        <thead><tr>
          <th>Ticker</th>
          <th>Strategy</th>
          <th>Entry</th>
          <th>Stop Loss</th>
          <th>Target (3R)</th>
          <th>Exit Signal (Trailing)</th>
          <th>Why High Conviction?</th>
        </tr></thead>
        <tbody id="hc-tbody">{hc_rows}</tbody>
      </table>
    </div>

    <div class="hc-rules">
      <div class="hc-rule-item">
        <div class="hc-rule-icon" style="background:#1a3a5c">⚡</div>
        <div><div class="hc-rule-title">Safety Switch</div><div class="hc-rule-body">All stocks must be above SMA200. If any holding drops below its SMA200 — exit immediately, no exceptions.</div></div>
      </div>
      <div class="hc-rule-item">
        <div class="hc-rule-icon" style="background:#15803d">🆓</div>
        <div><div class="hc-rule-title">The Free Ride</div><div class="hc-rule-body">Once a position is up 10%, move your stop loss to entry price. You now have a zero-risk trade — let it run.</div></div>
      </div>
      <div class="hc-rule-item">
        <div class="hc-rule-icon" style="background:#b91c1c">🛡️</div>
        <div><div class="hc-rule-title">Risk Cap</div><div class="hc-rule-body">Maximum $100 risk per trade (1% of $10K). If the stop distance is too wide, reduce shares — never widen the stop.</div></div>
      </div>
    </div>
  </div>

  <!-- ── ACTIVE PORTFOLIO TRACKER ── -->
  <div class="hc-section" style="margin-bottom:24px">
    <div class="hc-header">
      <div class="hc-title">Active Portfolio Tracker</div>
      <div style="font-size:11px;color:#475569;font-family:'DM Mono',monospace">Log positions below · P/L calculated from entry</div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px">
      <div class="dark-metric"><div class="dark-metric-label">Total Value</div><div class="dark-metric-val" id="sc-total">$10,000</div></div>
      <div class="dark-metric"><div class="dark-metric-label">Open P/L</div><div class="dark-metric-val" id="sc-pnl" style="color:#22c55e">+$0</div></div>
      <div class="dark-metric"><div class="dark-metric-label">Open Positions</div><div class="dark-metric-val" id="sc-positions">0 / 3</div></div>
      <div class="dark-metric"><div class="dark-metric-label">Brokerage Paid</div><div class="dark-metric-val" id="sc-brokerage" style="color:#f59e0b">$0</div></div>
    </div>

    <!-- Position entry form -->
    <div style="display:grid;grid-template-columns:repeat(5,1fr) auto;gap:10px;margin-bottom:16px;align-items:end">
      <div class="form-group">
        <label class="form-label" style="color:#64748b">Ticker</label>
        <input class="dark-input" type="text" id="sc-ticker" placeholder="e.g. TLX" style="text-transform:uppercase">
      </div>
      <div class="form-group">
        <label class="form-label" style="color:#64748b">Entry $</label>
        <input class="dark-input" type="number" id="sc-entry" placeholder="0.00" step="0.01">
      </div>
      <div class="form-group">
        <label class="form-label" style="color:#64748b">Shares</label>
        <input class="dark-input" type="number" id="sc-shares" placeholder="0">
      </div>
      <div class="form-group">
        <label class="form-label" style="color:#64748b">Current $</label>
        <input class="dark-input" type="number" id="sc-current" placeholder="0.00" step="0.01">
      </div>
      <div class="form-group">
        <label class="form-label" style="color:#64748b">Stop $</label>
        <input class="dark-input" type="number" id="sc-stop-input" placeholder="0.00" step="0.01">
      </div>
      <button class="btn-dark-add" onclick="addPosition()">+ Add</button>
    </div>

    <div style="overflow-x:auto">
      <table class="hc-table" style="font-size:12px">
        <thead><tr>
          <th>Ticker</th><th>Entry</th><th>Current</th><th>Shares</th>
          <th>Cost</th><th>Value</th><th>P/L $</th><th>P/L %</th>
          <th>Stop</th><th>Free Ride?</th><th></th>
        </tr></thead>
        <tbody id="sc-tbody">
          <tr><td colspan="11" style="text-align:center;padding:24px;color:#475569;font-family:'DM Mono',monospace;font-size:12px">No active positions. Add one above.</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- ── RESEARCH PHASE — 10 CANDIDATES ── -->
  <div class="hc-section" style="margin-bottom:24px">
    <div class="hc-header">
      <div>
        <div class="hc-title">Research Phase — All Candidates</div>
        <div class="hc-sub">All stocks passing hard filters today (above SMA200 + $500K liquidity + triggered a strategy)</div>
      </div>
    </div>
    <div style="overflow-x:auto">
      <table class="hc-table" style="font-size:12px">
        <thead><tr>
          <th>#</th><th>Stock</th><th>Price</th><th>Today</th>
          <th>Strategies</th><th>RSI</th><th>SMA200</th><th>Volume</th>
          <th>Stop</th><th>Target</th>
        </tr></thead>
        <tbody>{research_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- ── POSITION SIZING ── -->
  <div class="hc-section" style="margin-bottom:24px">
    <div class="hc-header">
      <div>
        <div class="hc-title">Position Sizing — $10K Portfolio</div>
        <div class="hc-sub">$3,333 per trade · Max 3 concurrent · $3 brokerage · Free Ride at +10%</div>
      </div>
    </div>
    <div style="overflow-x:auto">
      <table class="hc-table">
        <thead><tr>
          <th>Stock</th><th>Capital</th><th>Shares</th><th>Cost</th>
          <th>Max Loss (incl. $3 brok)</th><th>Free Ride Trigger (+10%)</th><th>Target (3R)</th>
        </tr></thead>
        <tbody>{pos_rows}</tbody>
      </table>
    </div>
    <div style="margin-top:16px;padding:14px 18px;background:#112233;border:1px solid #1a3a5c;border-radius:8px;font-size:12px;color:#64748b;line-height:1.9;font-family:'DM Mono',monospace">
      ⚠️ Not financial advice. Paper trade on nabtrade virtual portfolio first. Stake/CMC recommended for $3 flat brokerage when ready for live trading.
    </div>
  </div>

</div>

<!-- ══════════ BACKTEST ══════════ -->
<div id="tab-backtest" class="tab">
  <div class="page-header">
    <div><div class="page-title">Backtest</div><div class="page-sub">ASX 200 historical simulation — pre-calculated at build time</div></div>
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
        <div class="legend-item"><div class="legend-line" style="background:#1a3a5c"></div>JYTS System</div>
        <div class="legend-item"><div class="legend-line" style="background:#90b8d4;border-top:2px dashed #90b8d4;background:none"></div>ASX 200 B&amp;H</div>
      </div>
    </div>
    <div style="position:relative;height:280px"><canvas id="btEquityChart"></canvas></div>
  </div>

  <div class="card">
    <div class="card-header"><div class="card-title">Drawdown Chart</div></div>
    <div style="position:relative;height:180px"><canvas id="btDDChart"></canvas></div>
  </div>

  <div class="card">
    <div class="card-header"><div class="card-title">Regime Performance</div></div>
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
</div>

<!-- ══════════ STRATEGIES ══════════ -->
<div id="tab-strategies" class="tab">
  <div class="page-header"><div><div class="page-title">7 Strategies</div><div class="page-sub">ASX 200 adapted — GEAR (long) &amp; BBOZ (short)</div></div></div>

  <div class="strat-grid">
    <div class="strat-card">
      <div class="strat-header"><div class="strat-name">Momentum Long</div><span class="badge badge-up">GEAR</span></div>
      <div class="strat-body">ASX 200 breaks above upper Bollinger Band while above SMA20. Ride strong upward momentum.</div>
      <div class="strat-rule">Entry: price &gt; upper BB + above SMA20<br>Exit: close below SMA20 · Regime: Uptrend</div>
    </div>
    <div class="strat-card">
      <div class="strat-header"><div class="strat-name">MR Long 1 — SMA20 Pullback</div><span class="badge badge-up">GEAR</span></div>
      <div class="strat-body">ASX 200 pulls back to within 1% of SMA20. Buy the dip expecting a bounce.</div>
      <div class="strat-rule">Entry: within 1% of SMA20<br>Exit: extends 3%+ above SMA20 · Regime: Uptrend</div>
    </div>
    <div class="strat-card">
      <div class="strat-header"><div class="strat-name">MR Long 2 — Deep Pullback</div><span class="badge badge-up">GEAR</span></div>
      <div class="strat-body">ASX drops below lower BB but stays above SMA250. Aggressive dip buy.</div>
      <div class="strat-rule">Entry: price &lt; lower BB AND &gt; SMA250<br>Exit: reclaims middle BB · Regime: Uptrend</div>
    </div>
    <div class="strat-card">
      <div class="strat-header"><div class="strat-name">MR Long 3 — SMA250 Bounce</div><span class="badge badge-up">GEAR</span></div>
      <div class="strat-body">ASX approaches SMA250 from above with a bullish candle. Catch the long-term support bounce.</div>
      <div class="strat-rule">Entry: within 3% of SMA250 + bullish candle<br>Exit: 5%+ above SMA20 · Regime: Near SMA250</div>
    </div>
    <div class="strat-card">
      <div class="strat-header"><div class="strat-name">MR Short — Overextension</div><span class="badge badge-dn">BBOZ</span></div>
      <div class="strat-body">ASX is 4%+ above SMA20 AND above upper BB. Fade extreme overextension.</div>
      <div class="strat-rule">Entry: ext20 &gt; 4% AND price &gt; upper BB<br>Exit: reverts to SMA20 · Regime: Uptrend</div>
    </div>
    <div class="strat-card">
      <div class="strat-header"><div class="strat-name">Momentum Short 1 — Breakdown</div><span class="badge badge-dn">BBOZ</span></div>
      <div class="strat-body">ASX closes below SMA250 AND lower BB. Major bearish signal. Size scales with depth.</div>
      <div class="strat-rule">Entry: price &lt; SMA250 + lower BB<br>Exit: reclaims SMA250 · Regime: Downtrend</div>
    </div>
    <div class="strat-card">
      <div class="strat-header"><div class="strat-name">Momentum Short 2 — Failed Bounce</div><span class="badge badge-dn">BBOZ</span></div>
      <div class="strat-body">In a downtrend, ASX bounces to SMA20 then closes back below it. Classic bull trap — short the failure.</div>
      <div class="strat-rule">Entry: bounce to SMA20 then close below it in downtrend<br>Exit: above SMA20 for 3 days</div>
    </div>
  </div>

  <div class="section-title">Regime Playbook — $100K Capital</div>
  <div class="card">
    <table class="data-table">
      <thead><tr><th>Regime</th><th>Condition</th><th>ETF</th><th>Allocation</th><th>$ Amount</th></tr></thead>
      <tbody>
        <tr><td>Strong uptrend</td><td>ASX &gt;2% above SMA250, RSI &gt;55</td><td><span class="badge badge-up">GEAR</span></td><td>80%</td><td class="pos">$80,000</td></tr>
        <tr><td>Uptrend</td><td>ASX above SMA250</td><td><span class="badge badge-up">GEAR</span></td><td>50%</td><td class="pos">$50,000</td></tr>
        <tr><td>Neutral</td><td>Within 1% of SMA250</td><td><span class="badge badge-nu">CASH</span></td><td>100%</td><td>$100,000</td></tr>
        <tr><td>Downtrend</td><td>ASX below SMA250</td><td><span class="badge badge-dn">BBOZ</span></td><td>30%</td><td class="neg">$30,000</td></tr>
        <tr><td>Strong downtrend</td><td>ASX &gt;2% below SMA250, RSI &lt;40</td><td><span class="badge badge-dn">BBOZ</span></td><td>60%</td><td class="neg">$60,000</td></tr>
      </tbody>
    </table>
  </div>
</div>

<!-- ══════════ HOW TO USE ══════════ -->
<div id="tab-guide" class="tab">
  <div class="page-header"><div><div class="page-title">How to Use</div><div class="page-sub">Daily routine and trading rules</div></div></div>

  <div class="section-title">Learning Refreshers</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:28px">
    {''.join(f'<div class="tip-box" style="margin-bottom:0"><strong>{t[0]}</strong>{t[1]}</div>' for t in LEARNING_TIPS)}
  </div>

  <div class="card" style="margin-bottom:20px">
    <div class="card-header"><div class="card-title">Daily Routine — 10 Minutes</div></div>
    <table class="data-table">
      <thead><tr><th>Time (AEST/AEDT)</th><th>Action</th><th>Where</th></tr></thead>
      <tbody>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:00 pm</td><td>ASX closes</td><td style="color:var(--text3)">—</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:15 pm</td><td>Open dashboard — auto-updated</td><td style="color:var(--text3)">This page</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:16 pm</td><td>Read Signal Card + Today's Tip</td><td style="color:var(--text3)">Dashboard tab</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:18 pm</td><td>Click ETF price badge → pre-fills price field</td><td style="color:var(--text3)">Paper Trades tab</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:20 pm</td><td>Log your paper trade</td><td style="color:var(--text3)">Paper Trades tab</td></tr>
        <tr><td style="color:var(--green);font-family:'DM Mono',monospace">4:22 pm</td><td>Check ASX Stocks for swing ideas</td><td style="color:var(--text3)">ASX Stocks tab</td></tr>
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
// ─── Constants ─────────────────────────────────────────────────────
const GEAR_PRICE = {gear_js};
const BBOZ_PRICE = {bboz_js};

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

// ─── Auto-fill price ───────────────────────────────────────────────
function prefillPrice(ticker, price) {{
  if (!price) return;
  document.getElementById('t-ticker').value = ticker;
  document.getElementById('t-price').value = price.toFixed(2);
  updatePriceHint();
  calcValue();
  document.getElementById('t-shares').focus();
}}

function onTickerChange() {{
  const ticker = document.getElementById('t-ticker').value;
  const price = ticker === 'GEAR' ? GEAR_PRICE : ticker === 'BBOZ' ? BBOZ_PRICE : null;
  if (price) {{
    document.getElementById('t-price').value = price.toFixed(2);
    calcValue();
  }} else {{
    document.getElementById('t-price').value = '';
    document.getElementById('t-value-display').value = '';
  }}
  updatePriceHint();
}}

function updatePriceHint() {{
  const ticker = document.getElementById('t-ticker').value;
  const hint = document.getElementById('price-hint');
  const price = ticker === 'GEAR' ? GEAR_PRICE : ticker === 'BBOZ' ? BBOZ_PRICE : null;
  hint.textContent = price ? `(last close: $${{price.toFixed(2)}})` : '';
}}

function calcValue() {{
  const price = parseFloat(document.getElementById('t-price').value);
  const shares = parseInt(document.getElementById('t-shares').value);
  if (price && shares) {{
    document.getElementById('t-value-display').value = '$' + (price * shares).toLocaleString('en-AU', {{minimumFractionDigits:2,maximumFractionDigits:2}});
  }}
}}

document.getElementById('t-price').addEventListener('input', calcValue);

// ─── Chart data ────────────────────────────────────────────────────
const CD = {json.dumps(cd)};
const BT = {json.dumps(bt)};

const baseOpts = {{
  responsive: true, maintainAspectRatio: false,
  plugins: {{ legend: {{ display: false }} }},
  scales: {{
    x: {{ ticks: {{ maxTicksLimit: 8, font: {{ size: 10, family: "'DM Mono', monospace" }}, color: '#8aa4b8' }}, grid: {{ color: 'rgba(26,58,92,0.06)' }}, border: {{ display: false }} }},
    y: {{ ticks: {{ font: {{ size: 10, family: "'DM Mono', monospace" }}, color: '#8aa4b8', callback: v => v.toLocaleString() }}, grid: {{ color: 'rgba(26,58,92,0.06)' }}, border: {{ display: false }} }}
  }}
}};

// ASX Price chart
new Chart(document.getElementById('priceChart'), {{
  type: 'line',
  data: {{ labels: CD.labels, datasets: [
    {{ data: CD.closes, borderColor: '#1e2a38', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false }},
    {{ data: CD.sma20, borderColor: '#90b8d4', borderWidth: 1.5, pointRadius: 0, borderDash: [5,3], tension: 0.3, fill: false }},
    {{ data: CD.sma250, borderColor: '#1a3a5c', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false }}
  ]}}, options: baseOpts
}});

// RSI
new Chart(document.getElementById('rsiChart'), {{
  type: 'line',
  data: {{ labels: CD.labels, datasets: [{{ data: CD.rsi, borderColor: '#2563a8', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false }}] }},
  options: {{ ...baseOpts, scales: {{ ...baseOpts.scales,
    y: {{ min: 0, max: 100, ticks: {{ font: {{ size: 10, family: "'DM Mono', monospace" }}, color: '#8aa4b8', stepSize: 25 }}, grid: {{ color: 'rgba(26,58,92,0.06)' }}, border: {{ display: false }} }} }} }}
}});

// GEAR benchmark
const gearData = CD.gear_norm && CD.gear_norm.some(v => v !== null);
if (gearData) {{
  new Chart(document.getElementById('gearChart'), {{
    type: 'line',
    data: {{ labels: CD.labels, datasets: [
      {{ label: 'ASX 200', data: CD.asx_norm, borderColor: '#90b8d4', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false, borderDash: [5,3] }},
      {{ label: 'GEAR.AX', data: CD.gear_norm, borderColor: '#1a3a5c', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false }}
    ]}},
    options: {{ ...baseOpts,
      plugins: {{ legend: {{ display: true, labels: {{ color: '#4a5f72', font: {{ size: 11, family: "'DM Mono', monospace" }}, boxWidth: 14, padding: 16 }} }} }},
      scales: {{ ...baseOpts.scales, y: {{ ticks: {{ font: {{ size: 10, family: "'DM Mono', monospace" }}, color: '#8aa4b8', callback: v => v.toFixed(0) }}, grid: {{ color: 'rgba(26,58,92,0.06)' }}, border: {{ display: false }} }} }} }}
  }});
}} else {{
  document.getElementById('gearChart').parentElement.innerHTML = '<p style="text-align:center;color:#8aa4b8;padding:40px;font-size:12px;font-family:DM Mono,monospace">GEAR.AX data unavailable</p>';
}}

// BBOZ benchmark
const bbozData = CD.bboz_norm && CD.bboz_norm.some(v => v !== null);
if (bbozData) {{
  new Chart(document.getElementById('bbozChart'), {{
    type: 'line',
    data: {{ labels: CD.labels, datasets: [
      {{ label: 'ASX 200', data: CD.asx_norm, borderColor: '#90b8d4', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false, borderDash: [5,3] }},
      {{ label: 'BBOZ.AX', data: CD.bboz_norm, borderColor: '#b91c1c', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false }}
    ]}},
    options: {{ ...baseOpts,
      plugins: {{ legend: {{ display: true, labels: {{ color: '#4a5f72', font: {{ size: 11, family: "'DM Mono', monospace" }}, boxWidth: 14, padding: 16 }} }} }},
      scales: {{ ...baseOpts.scales, y: {{ ticks: {{ font: {{ size: 10, family: "'DM Mono', monospace" }}, color: '#8aa4b8', callback: v => v.toFixed(0) }}, grid: {{ color: 'rgba(26,58,92,0.06)' }}, border: {{ display: false }} }} }} }}
  }});
}} else {{
  document.getElementById('bbozChart').parentElement.innerHTML = '<p style="text-align:center;color:#8aa4b8;padding:40px;font-size:12px;font-family:DM Mono,monospace">BBOZ.AX data unavailable</p>';
}}

// Backtest equity
new Chart(document.getElementById('btEquityChart'), {{
  type: 'line',
  data: {{ labels: BT.labels, datasets: [
    {{ label: 'JYTS System', data: BT.equity, borderColor: '#1a3a5c', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false }},
    {{ label: 'ASX 200 B&H', data: BT.bh_equity, borderColor: '#90b8d4', borderWidth: 1.5, pointRadius: 0, borderDash: [5,3], tension: 0.3, fill: false }}
  ]}},
  options: {{ ...baseOpts,
    plugins: {{ legend: {{ display: true, labels: {{ color: '#4a5f72', font: {{ size: 11, family: "'DM Mono', monospace" }}, boxWidth: 14, padding: 16 }} }} }},
    scales: {{ ...baseOpts.scales, y: {{ ticks: {{ font: {{ size: 10, family: "'DM Mono', monospace" }}, color: '#8aa4b8', callback: v => '$' + (v/1000).toFixed(0) + 'K' }}, grid: {{ color: 'rgba(26,58,92,0.06)' }}, border: {{ display: false }} }} }} }}
}});

// Drawdown
new Chart(document.getElementById('btDDChart'), {{
  type: 'line',
  data: {{ labels: BT.labels, datasets: [{{ data: BT.drawdowns, borderColor: '#ef4444', borderWidth: 1.5, pointRadius: 0, tension: 0.3,
    fill: {{ target: 'origin', above: 'rgba(239,68,68,0.08)' }} }}] }},
  options: {{ ...baseOpts, scales: {{ ...baseOpts.scales, y: {{ ticks: {{ font: {{ size: 10, family: "'DM Mono', monospace" }}, color: '#8aa4b8', callback: v => v.toFixed(0)+'%' }}, grid: {{ color: 'rgba(26,58,92,0.06)' }}, border: {{ display: false }} }} }} }}
}});

// ─── Paper Trades ──────────────────────────────────────────────────
let trades = JSON.parse(localStorage.getItem('jyts_trades') || '[]');
document.getElementById('t-date').value = new Date().toISOString().split('T')[0];
updatePriceHint();
renderTrades();

function logTrade() {{
  const date = document.getElementById('t-date').value;
  const ticker = document.getElementById('t-ticker').value;
  const action = document.getElementById('t-action').value;
  const price = parseFloat(document.getElementById('t-price').value);
  const shares = parseInt(document.getElementById('t-shares').value);
  const strat = document.getElementById('t-strat').value;
  const notes = document.getElementById('t-notes').value;
  if (!date || !price || !shares) {{ alert('Please fill in date, price and shares.'); return; }}
  trades.push({{ id: Date.now(), date, ticker, action, price, shares, value: parseFloat((price*shares).toFixed(2)), strat, notes }});
  localStorage.setItem('jyts_trades', JSON.stringify(trades));
  renderTrades();
  document.getElementById('t-price').value = '';
  document.getElementById('t-shares').value = '';
  document.getElementById('t-value-display').value = '';
  document.getElementById('t-notes').value = '';
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
  // ── P&L: only realised on SELL ──────────────────────────────────
  let openPos={{}}, realPnL=0, wins=0, losses=0, cashDeployed=0;
  trades.forEach(t => {{
    if (t.action==='BUY') {{
      if (!openPos[t.ticker]) openPos[t.ticker]={{shares:0,cost:0}};
      openPos[t.ticker].shares+=t.shares;
      openPos[t.ticker].cost+=t.value;
      cashDeployed+=t.value;
    }}
    if (t.action==='SELL') {{
      const pos=openPos[t.ticker];
      if (pos&&pos.shares>0) {{
        const avg=pos.cost/pos.shares, pnl=(t.price-avg)*Math.min(t.shares,pos.shares);
        realPnL+=pnl;
        if(pnl>=0) wins++; else losses++;
        pos.shares-=t.shares; pos.cost=pos.shares*avg;
        if(pos.shares<=0) delete openPos[t.ticker];
      }}
    }}
  }});

  // Open position value (at avg buy price)
  let openValue=0;
  for (const [ticker, pos] of Object.entries(openPos)) {{
    if (pos.shares > 0) openValue += pos.cost;
  }}

  // Portfolio = starting cash - cash still in open positions + realised P&L
  const portfolio = 100000 + realPnL;
  const ret = realPnL / 100000 * 100;
  const wkPnL = getWeekPnL();
  const total = wins + losses;

  document.getElementById('pt-portfolio').textContent='$'+(portfolio).toLocaleString('en-AU',{{maximumFractionDigits:0}});
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
    tbody.innerHTML='<tr><td colspan="10" style="color:var(--text3);padding:28px;text-align:center;font-family:DM Mono,monospace;font-size:12px">No trades logged yet</td></tr>';
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
      <td style="font-size:11px;color:var(--text3);max-width:150px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${{t.notes||'—'}}</td>
      <td><button class="btn-del" onclick="deleteTrade(${{tid}})" title="Delete">🗑</button></td>
    </tr>`;
  }}).join('');
}}

// ─── ASX Swing Catalyst Portfolio Tracker ────────────────────────
let scPositions = JSON.parse(localStorage.getItem('jyts_sc_positions') || '[]');
renderSCPortfolio();

function addPosition() {{
  const ticker  = (document.getElementById('sc-ticker').value || '').toUpperCase().trim();
  const entry   = parseFloat(document.getElementById('sc-entry').value);
  const shares  = parseInt(document.getElementById('sc-shares').value);
  const current = parseFloat(document.getElementById('sc-current').value);
  const stop    = parseFloat(document.getElementById('sc-stop-input').value);
  if (!ticker || !entry || !shares || !current) {{ alert('Fill in Ticker, Entry, Shares and Current price.'); return; }}
  scPositions.push({{ id: Date.now(), ticker, entry, shares, current, stop: stop||0, added: new Date().toISOString().split('T')[0] }});
  localStorage.setItem('jyts_sc_positions', JSON.stringify(scPositions));
  renderSCPortfolio();
  ['sc-ticker','sc-entry','sc-shares','sc-current','sc-stop-input'].forEach(id => document.getElementById(id).value = '');
}}

function removeSCPosition(id) {{
  scPositions = scPositions.filter(p => p.id !== id);
  localStorage.setItem('jyts_sc_positions', JSON.stringify(scPositions));
  renderSCPortfolio();
}}

function renderSCPortfolio() {{
  const tbody = document.getElementById('sc-tbody');
  if (!scPositions.length) {{
    tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;padding:24px;color:#475569;font-family:\'DM Mono\',monospace;font-size:12px">No active positions. Add one above.</td></tr>';
    document.getElementById('sc-total').textContent = '$10,000';
    document.getElementById('sc-pnl').textContent = '+$0';
    document.getElementById('sc-pnl').style.color = '#22c55e';
    document.getElementById('sc-positions').textContent = '0 / 3';
    document.getElementById('sc-brokerage').textContent = '$' + (scPositions.length * 3).toFixed(2);
    return;
  }}
  let totalPnL = 0, totalCost = 0, totalValue = 0;
  const rows = scPositions.map(p => {{
    const cost    = p.entry * p.shares;
    const value   = p.current * p.shares;
    const pnl     = value - cost;
    const pnlPct  = (pnl / cost) * 100;
    totalPnL  += pnl; totalCost += cost; totalValue += value;
    const pnlCol  = pnl >= 0 ? '#22c55e' : '#ef4444';
    const freeRide = pnlPct >= 10;
    const stopPct = p.stop > 0 ? ((p.current - p.stop) / p.current * 100).toFixed(1) : '—';
    return `<tr>
      <td style="font-weight:700;color:#e2e8f0">${{p.ticker}}</td>
      <td style="font-family:'DM Mono',monospace;color:#94a3b8">$${{p.entry.toFixed(2)}}</td>
      <td style="font-family:'DM Mono',monospace;color:#e2e8f0">$${{p.current.toFixed(2)}}</td>
      <td style="font-family:'DM Mono',monospace">${{p.shares}}</td>
      <td style="font-family:'DM Mono',monospace;color:#94a3b8">$${{cost.toLocaleString('en-AU',{{maximumFractionDigits:0}})}}</td>
      <td style="font-family:'DM Mono',monospace;color:#e2e8f0">$${{value.toLocaleString('en-AU',{{maximumFractionDigits:0}})}}</td>
      <td style="font-family:'DM Mono',monospace;color:${{pnlCol}}">${{pnl>=0?'+':''}}$${{Math.abs(pnl).toFixed(2)}}</td>
      <td style="font-family:'DM Mono',monospace;color:${{pnlCol}}">${{pnl>=0?'+':''}}${{pnlPct.toFixed(1)}}%</td>
      <td style="font-family:'DM Mono',monospace;color:#ef4444">${{p.stop>0?'$'+p.stop.toFixed(2):'—'}}</td>
      <td style="font-size:11px">${{freeRide?'<span style="color:#22c55e;font-weight:600">✓ Move stop to entry</span>':'<span style="color:#475569">'+pnlPct.toFixed(1)+'% of 10%</span>'}}</td>
      <td><button class="btn-dark-del" onclick="removeSCPosition(${{p.id}})">✕</button></td>
    </tr>`;
  }});
  tbody.innerHTML = rows.join('');
  const totalPort = 10000 - totalCost + totalValue;
  const pnlEl = document.getElementById('sc-pnl');
  pnlEl.textContent = (totalPnL >= 0 ? '+$' : '-$') + Math.abs(totalPnL).toFixed(2);
  pnlEl.style.color = totalPnL >= 0 ? '#22c55e' : '#ef4444';
  document.getElementById('sc-total').textContent = '$' + totalPort.toLocaleString('en-AU',{{maximumFractionDigits:0}});
  document.getElementById('sc-positions').textContent = scPositions.length + ' / 3';
  document.getElementById('sc-brokerage').textContent = '$' + (scPositions.length * 3).toFixed(2);
}}
</script>
</body>
</html>"""

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=== Jun Yadnap Trade System — Build Script v2 ===")
    local_time, tz_name = get_aest_now()
    build_time = local_time.strftime("%d %b %Y %I:%M %p")
    print(f"Build time: {build_time} {tz_name}")

    df = fetch_asx_data()
    gear_df, bboz_df = fetch_etf_data()
    gear_price, bboz_price = fetch_etf_prices(gear_df, bboz_df)
    print(f"  GEAR price: ${gear_price} | BBOZ price: ${bboz_price}")

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

    research_10, high_conviction_4 = fetch_top_stocks()

    html = generate_html(signal_data, chart_data, backtest_data, build_time, tz_name,
                         gear_price, bboz_price, research_10, high_conviction_4)

    os.makedirs("docs", exist_ok=True)
    out_path = "docs/index.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Dashboard written to {out_path}")
    print("=== Build complete ===")

if __name__ == "__main__":
    main()
