#!/usr/bin/env python3
"""Shared symbol → NSE sectoral-index map (ADR-010 decision 5).

Extracted from `7_sector_macro_regime.py` exactly when ADR-010 said to: a
*second* consumer arrived (stage 4 reads the same ticker out of the same
`uvest-market-context/1` bundle for its 20-session sectoral correlation).
Until then the map lived as a module constant in stage 7 alone — the stage-0
`OPTIONS_TYPES` precedent of domain data in code, pinned by tests, with no new
file and no I/O.

Conservative by design: a symbol whose membership is not pinned here is
*unmapped* and waives the consuming stage — never a guessed index.
"""

from __future__ import annotations

SECTOR_INDEX_MAP = {
    # NIFTY ENERGY
    "RELIANCE": "^CNXENERGY", "ONGC": "^CNXENERGY", "IOC": "^CNXENERGY",
    "BPCL": "^CNXENERGY", "GAIL": "^CNXENERGY",
    # NIFTY BANK
    "HDFCBANK": "^NSEBANK", "ICICIBANK": "^NSEBANK", "SBIN": "^NSEBANK",
    "KOTAKBANK": "^NSEBANK", "AXISBANK": "^NSEBANK", "INDUSINDBK": "^NSEBANK",
    # NIFTY IT
    "INFY": "^CNXIT", "TCS": "^CNXIT", "HCLTECH": "^CNXIT", "WIPRO": "^CNXIT",
    "TECHM": "^CNXIT", "LTIM": "^CNXIT",
    # NIFTY AUTO
    "MARUTI": "^CNXAUTO", "TATAMOTORS": "^CNXAUTO", "M&M": "^CNXAUTO",
    "BAJAJ-AUTO": "^CNXAUTO", "HEROMOTOCO": "^CNXAUTO",
    "EICHERMOT": "^CNXAUTO", "ASHOKLEY": "^CNXAUTO",
    # NIFTY PHARMA
    "SUNPHARMA": "^CNXPHARMA", "DRREDDY": "^CNXPHARMA", "CIPLA": "^CNXPHARMA",
    "DIVISLAB": "^CNXPHARMA", "LUPIN": "^CNXPHARMA", "TORNTPHARM": "^CNXPHARMA",
    # NIFTY FMCG
    "HINDUNILVR": "^CNXFMCG", "ITC": "^CNXFMCG", "NESTLEIND": "^CNXFMCG",
    "BRITANNIA": "^CNXFMCG", "DABUR": "^CNXFMCG", "MARICO": "^CNXFMCG",
    "TATACONSUM": "^CNXFMCG", "GODREJCP": "^CNXFMCG",
    # NIFTY METAL
    "TATASTEEL": "^CNXMETAL", "JSWSTEEL": "^CNXMETAL", "HINDALCO": "^CNXMETAL",
    "VEDL": "^CNXMETAL", "JINDALSTEL": "^CNXMETAL",
    # NIFTY REALTY
    "DLF": "^CNXREALTY", "GODREJPROP": "^CNXREALTY", "OBEROIRLTY": "^CNXREALTY",
    # NIFTY PSU BANK
    "BANKBARODA": "^CNXBANK", "PNB": "^CNXBANK", "CANBK": "^CNXBANK",
    # NIFTY INFRA (thematic). Membership pinned — not guessed — from four
    # constituent lists read 2026-10-14: TradingView NSE:CNXINFRA components,
    # Economic Times' Nifty Infrastructure constituents, Investing.com
    # cnx-infrastructure components, and Yahoo's ^CNXINFRA quote/history
    # (the live Yahoo ticker this map addresses). Both LT and ADANIPORTS
    # appear in all four.
    "LT": "^CNXINFRA", "ADANIPORTS": "^CNXINFRA",
}
