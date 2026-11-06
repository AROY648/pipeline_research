"""
Exécute une requête SELECT (fichier .sql ou texte) et affiche le résultat. Pratique pour explorer.

    python sql/query.py sql/checks/missing_book.sql
    python sql/query.py "SELECT COUNT(*) FROM clean.prices_daily"
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
from common import connect  # noqa: E402

pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 200)
pd.set_option("display.max_columns", 30)

arg = " ".join(sys.argv[1:])
path = Path(arg)
sql = path.read_text(encoding="utf-8-sig") if path.suffix == ".sql" and path.exists() else arg
with connect() as con:
    print(con.execute(sql).df().to_string(index=False))
