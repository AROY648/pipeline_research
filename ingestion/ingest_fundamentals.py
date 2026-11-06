"""
Ingestion des fondamentaux (yfinance) -> DuckDB :
  - raw.fundamentals_snapshot : capitalisation, BPA, valeur comptable... du jour
  - raw.financial_statements  : bilans et comptes de résultats publiés (annuels + trimestriels)

Exemples (depuis la racine du projet, venv activé) :
    python ingestion/ingest_fundamentals.py
    python ingestion/ingest_fundamentals.py --tickers RY.TO TD.TO
"""
from __future__ import annotations

import argparse
import time
from datetime import date

import pandas as pd
import yfinance as yf

from common import DB_PATH, connect, load_tickers, run_sql_file

# Champs de Ticker.info -> colonnes de raw.fundamentals_snapshot
INFO_FIELDS = {
    "marketCap": "market_cap",
    "sharesOutstanding": "shares_outstanding",
    "bookValue": "book_value_per_share",
    "priceToBook": "price_to_book",
    "trailingEps": "trailing_eps",
    "currency": "currency",
    "financialCurrency": "financial_currency",
    "sector": "sector",
    "industry": "industry",
}
SNAPSHOT_COLUMNS = ["ticker", "snapshot_date", *INFO_FIELDS.values()]

# (attribut yfinance, statement, frequency)
STATEMENTS = [
    ("balance_sheet", "balance_sheet", "annual"),
    ("quarterly_balance_sheet", "balance_sheet", "quarterly"),
    ("income_stmt", "income_stmt", "annual"),
    ("quarterly_income_stmt", "income_stmt", "quarterly"),
]
STATEMENT_COLUMNS = ["ticker", "statement", "frequency", "period_end", "item", "value"]


def fetch_one(ticker: str) -> tuple[dict, list[pd.DataFrame]]:
    t = yf.Ticker(ticker)

    info = t.info or {}
    snapshot = {"ticker": ticker, "snapshot_date": date.today()}
    snapshot.update({col: info.get(key) for key, col in INFO_FIELDS.items()})

    frames = []
    for attr, statement, frequency in STATEMENTS:
        wide = getattr(t, attr)              # index = postes, colonnes = dates de fin de période
        if wide is None or wide.empty:
            continue
        long = (wide.rename_axis(index="item", columns="period_end")
                    .stack(future_stack=True)
                    .rename("value")
                    .reset_index()
                    .dropna(subset=["value"]))
        long["ticker"], long["statement"], long["frequency"] = ticker, statement, frequency
        long["period_end"] = pd.to_datetime(long["period_end"]).dt.date
        long["value"] = pd.to_numeric(long["value"], errors="coerce")
        frames.append(long[STATEMENT_COLUMNS])
    return snapshot, frames


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion des fondamentaux TSX -> DuckDB (raw)")
    parser.add_argument("--tickers", nargs="+", default=None,
                        help="par défaut : tous les titres de ingestion/tickers_tsx60.csv")
    parser.add_argument("--pause", type=float, default=0.5,
                        help="secondes entre deux titres (évite d'être limité par Yahoo)")
    args = parser.parse_args()
    tickers = args.tickers or load_tickers()

    snapshots, statement_frames, failed = [], [], []
    for i, ticker in enumerate(tickers, 1):
        try:
            snap, frames = fetch_one(ticker)
            snapshots.append(snap)
            statement_frames.extend(frames)
            print(f"[{i}/{len(tickers)}] {ticker} : ok ({sum(len(f) for f in frames)} postes)")
        except Exception as exc:              # un titre en échec ne bloque pas les autres
            failed.append(ticker)
            print(f"[{i}/{len(tickers)}] {ticker} : ÉCHEC ({exc})")
        time.sleep(args.pause)

    if not snapshots:
        raise RuntimeError("Aucune donnée reçue (réseau ou limite Yahoo ?)")

    snap_df = pd.DataFrame(snapshots, columns=SNAPSHOT_COLUMNS)
    stmt_df = (pd.concat(statement_frames, ignore_index=True) if statement_frames
               else pd.DataFrame(columns=STATEMENT_COLUMNS))

    with connect() as con:
        run_sql_file(con, "01_create_raw_fundamentals.sql")
        con.register("snap_stg", snap_df)
        con.register("stmt_stg", stmt_df)
        con.execute(f"""
            INSERT OR REPLACE INTO raw.fundamentals_snapshot ({", ".join(SNAPSHOT_COLUMNS)})
            SELECT {", ".join(SNAPSHOT_COLUMNS)} FROM snap_stg
        """)
        con.execute(f"""
            INSERT OR REPLACE INTO raw.financial_statements ({", ".join(STATEMENT_COLUMNS)})
            SELECT {", ".join(STATEMENT_COLUMNS)} FROM stmt_stg
        """)
        resume = con.execute("""
            SELECT statement, frequency,
                   COUNT(DISTINCT ticker)     AS n_titres,
                   COUNT(DISTINCT period_end) AS n_periodes,
                   MIN(period_end) AS premiere, MAX(period_end) AS derniere
            FROM raw.financial_statements
            GROUP BY ALL ORDER BY ALL
        """).df()
        devises = con.execute("""
            SELECT financial_currency, COUNT(*) AS n_titres
            FROM raw.fundamentals_snapshot
            WHERE snapshot_date = current_date
            GROUP BY ALL ORDER BY n_titres DESC
        """).df()

    print(f"\nBase : {DB_PATH}")
    print(resume.to_string(index=False))
    print("\nDevise des états financiers :")
    print(devises.to_string(index=False))
    if failed:
        print(f"\nATTENTION, échec pour : {', '.join(failed)} (relancer avec --tickers ...)")


if __name__ == "__main__":
    main()
