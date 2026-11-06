"""
Exécute les fichiers SQL de transformation dans l'ordre (10_, 11_, 12_, ...) puis affiche des contrôles.

    python sql/run_sql.py            # couches clean (1x_) puis analytics (2x_)
    python sql/run_sql.py 12         # seulement les fichiers qui commencent par 12
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
from common import SQL_DIR, connect, run_sql_file  # noqa: E402

CHECKS = {
    "Prix clean": """
        SELECT COUNT(*) AS lignes, COUNT(DISTINCT ticker) AS titres,
               MIN(date) AS debut, MAX(date) AS fin,
               CAST(COUNT_IF(flag_big_move) AS INTEGER) AS variations_40pct, CAST(COUNT_IF(flag_zero_volume) AS INTEGER) AS volume_nul
        FROM clean.prices_daily""",
    "Variations > 40 % en un jour (à vérifier)": """
        SELECT ticker, date, ROUND(adj_close, 2) AS adj_close, splits
        FROM clean.prices_daily WHERE flag_big_move ORDER BY date LIMIT 15""",
    "Taux USD/CAD": """
        SELECT COUNT(*) AS jours, MIN(date) AS debut, MAX(date) AS fin,
               ROUND(arg_max(usdcad, date), 4) AS dernier
        FROM clean.fx_usdcad_daily""",
    "Fondamentaux clean": """
        SELECT frequency, reporting_currency, COUNT(*) AS lignes, COUNT(DISTINCT ticker) AS titres,
               CAST(COUNT_IF(flag_missing_book) AS INTEGER) AS sans_book, CAST(COUNT_IF(flag_missing_fx) AS INTEGER) AS sans_fx
        FROM clean.fundamentals GROUP BY ALL ORDER BY ALL""",
    "Rendements mensuels": """
        SELECT COUNT(*) AS lignes, COUNT(DISTINCT ticker) AS titres, MIN(month) AS premier_mois,
               MAX(month) AS dernier_mois, ROUND(AVG(ret_1m) * 100, 2) AS rend_moyen_pct
        FROM analytics.returns_monthly WHERE is_complete_month""",
    "Couverture des facteurs (nb de titres classés par année, en décembre ou dernier mois)": """
        SELECT YEAR(month) AS annee, COUNT(*) AS titres,
               COUNT(mom_12_1) AS momentum, COUNT(vol_60d) AS volatilite, COUNT(book_to_market) AS value
        FROM analytics.factors_monthly
        WHERE month IN (SELECT MAX(month) FROM analytics.factors_monthly
                        WHERE is_complete_month GROUP BY YEAR(month))
        GROUP BY annee ORDER BY annee""",
    "Distribution des facteurs (dernier mois complet)": """
        SELECT ROUND(MEDIAN(mom_12_1) * 100, 1) AS mom_median_pct,
               ROUND(MEDIAN(vol_60d) * 100, 1)  AS vol_median_pct,
               ROUND(MEDIAN(book_to_market), 2) AS bm_median,
               ROUND(MIN(book_to_market), 2) AS bm_min, ROUND(MAX(book_to_market), 2) AS bm_max
        FROM analytics.factors_monthly
        WHERE month = (SELECT MAX(month) FROM analytics.factors_monthly WHERE is_complete_month)""",
}


def main() -> None:
    prefixes = sys.argv[1:] or ["1", "2"]          # 1x_ = clean, 2x_ = analytics
    files = sorted({p.name for pre in prefixes for p in SQL_DIR.glob(f"{pre}*.sql")})
    if not files:
        raise SystemExit(f"Aucun fichier sql/ commençant par {prefixes}")
    with connect() as con:
        for name in files:
            print(f"-> {name}")
            run_sql_file(con, name)
        for title, query in CHECKS.items():
            try:
                print(f"\n== {title} ==")
                print(con.execute(query).df().to_string(index=False))
            except Exception as exc:          # table pas encore créée, etc.
                print(f"(indisponible : {exc})")


if __name__ == "__main__":
    main()
