"""
Ingestion des prix quotidiens (yfinance) -> table DuckDB raw.prices_daily.

Exemples (depuis la racine du projet, venv activé) :
    python ingestion/ingest_prices.py                      # 60 titres du TSX 60 depuis 2010
    python ingestion/ingest_prices.py --start 2024-01-01   # mise à jour récente
    python ingestion/ingest_prices.py --tickers RY.TO TD.TO
"""
from __future__ import annotations

import argparse

import pandas as pd
import yfinance as yf

from common import DB_PATH, connect, load_tickers, run_sql_file

COLUMNS = ["ticker", "date", "open", "high", "low", "close",
           "adj_close", "volume", "dividends", "splits"]


def download_prices(tickers: list[str], start: str, end: str | None) -> pd.DataFrame:
    """Télécharge OHLCV + dividendes/splits et renvoie un DataFrame long (1 ligne = ticker x date)."""
    wide = yf.download(
        tickers,
        start=start,
        end=end,
        auto_adjust=False,   # on garde le prix brut ET Adj Close (l'ajustement se fera en couche clean)
        actions=True,        # ajoute les colonnes Dividends et Stock Splits
        group_by="column",
        multi_level_index=True,  # colonnes (champ, ticker) même pour 1 seul titre
        progress=False,
        threads=True,
    )
    if wide.empty:
        raise RuntimeError("yfinance n'a renvoyé aucune donnée (tickers ou réseau ?)")

    # Colonnes MultiIndex (champ, ticker) -> format long
    long = wide.stack(level=1, future_stack=True).reset_index()
    long = long.rename(columns={
        "Date": "date", "Ticker": "ticker",
        "Open": "open", "High": "high", "Low": "low", "Close": "close",
        "Adj Close": "adj_close", "Volume": "volume",
        "Dividends": "dividends", "Stock Splits": "splits",
    })
    for col in COLUMNS:                       # au cas où une colonne manquerait
        if col not in long.columns:
            long[col] = 0.0 if col in ("dividends", "splits") else None

    long = long[COLUMNS].dropna(subset=["close"])  # jours sans cotation pour ce titre
    long["date"] = pd.to_datetime(long["date"]).dt.date
    long["volume"] = long["volume"].fillna(0).astype("int64")
    long[["dividends", "splits"]] = long[["dividends", "splits"]].fillna(0.0)
    return long


def load_to_duckdb(df: pd.DataFrame) -> None:
    with connect() as con:
        run_sql_file(con, "00_create_raw.sql")
        con.register("staging", df)
        # Upsert : relancer le script ne crée pas de doublons (clé primaire ticker+date)
        con.execute(f"""
            INSERT OR REPLACE INTO raw.prices_daily ({", ".join(COLUMNS)})
            SELECT {", ".join(COLUMNS)} FROM staging
        """)
        summary = con.execute("""
            SELECT ticker, COUNT(*) AS n_jours, MIN(date) AS debut, MAX(date) AS fin
            FROM raw.prices_daily GROUP BY ticker ORDER BY debut DESC, ticker
        """).df()
    print(f"Base : {DB_PATH}")
    print(summary.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion des prix TSX -> DuckDB (raw)")
    parser.add_argument("--tickers", nargs="+", default=None,
                        help="par défaut : tous les titres de ingestion/tickers_tsx60.csv")
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--end", default=None, help="exclu ; par défaut aujourd'hui")
    args = parser.parse_args()
    tickers = args.tickers or load_tickers()

    print(f"Téléchargement de {len(tickers)} titres depuis {args.start}...")
    df = download_prices(tickers, args.start, args.end)
    missing = sorted(set(tickers) - set(df["ticker"]))
    print(f"{len(df):,} lignes reçues pour {df['ticker'].nunique()} titres")
    if missing:
        print(f"ATTENTION, aucun prix pour : {', '.join(missing)}")
    load_to_duckdb(df)


if __name__ == "__main__":
    main()
