"""Fonctions partagées par les scripts d'ingestion."""
from __future__ import annotations

import csv
import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]          # dossier pipeline_research/
load_dotenv(ROOT / ".env")

DB_PATH = Path(os.getenv("DUCKDB_PATH", ROOT / "data" / "market.duckdb"))
if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_PATH
SQL_DIR = ROOT / "sql"
TICKERS_FILE = ROOT / "ingestion" / "tickers_tsx60.csv"


def load_tickers() -> list[str]:
    """Liste des tickers Yahoo (ex. RY.TO, TECK-B.TO) depuis tickers_tsx60.csv."""
    with open(TICKERS_FILE, encoding="utf-8-sig", newline="") as f:
        return [row["yahoo_ticker"] for row in csv.DictReader(f)]


def connect() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH))


def run_sql_file(con: duckdb.DuckDBPyConnection, filename: str) -> None:
    """Exécute un fichier du dossier sql/ (CREATE ... IF NOT EXISTS)."""
    path = SQL_DIR / filename
    sql = path.read_text(encoding="utf-8-sig")  # -sig : ignore un éventuel BOM Windows
    if "CREATE" not in sql.upper():
        raise RuntimeError(f"{path} est vide ou incomplet : vérifie qu'il est bien enregistré.")
    con.execute(sql)
