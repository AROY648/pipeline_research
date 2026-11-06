"""
Ingestion du taux de change USD/CAD (yfinance, symbole CAD=X) -> raw.fx_daily.
Sert à convertir en CAD les états financiers publiés en USD.

    python ingestion/ingest_fx.py
    python ingestion/ingest_fx.py --start 2024-01-01
"""
from __future__ import annotations

import argparse

import pandas as pd
import yfinance as yf

from common import connect, run_sql_file

PAIRS = {"USDCAD": "CAD=X"}   # CAD=X sur Yahoo = nombre de CAD pour 1 USD


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion des taux de change -> DuckDB (raw)")
    parser.add_argument("--start", default="2010-01-01")
    args = parser.parse_args()

    frames = []
    for pair, symbol in PAIRS.items():
        data = yf.download(symbol, start=args.start, auto_adjust=False,
                           multi_level_index=False, progress=False)
        if data.empty:
            raise RuntimeError(f"Aucune donnée pour {symbol}")
        df = data[["Close"]].rename(columns={"Close": "rate"}).dropna().reset_index()
        df = df.rename(columns={"Date": "date"})
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df["pair"] = pair
        frames.append(df[["pair", "date", "rate"]])
    fx = pd.concat(frames, ignore_index=True)

    with connect() as con:
        run_sql_file(con, "02_create_raw_fx.sql")
        con.register("fx_stg", fx)
        con.execute("INSERT OR REPLACE INTO raw.fx_daily (pair, date, rate) "
                    "SELECT pair, date, rate FROM fx_stg")
        print(con.execute("""
            SELECT pair, COUNT(*) AS n_jours, MIN(date) AS debut, MAX(date) AS fin,
                   ROUND(arg_max(rate, date), 4) AS dernier_taux
            FROM raw.fx_daily GROUP BY pair
        """).df().to_string(index=False))


if __name__ == "__main__":
    main()
