"""
BACKTEST (2/2) : métriques de performance des stratégies long-short.

Prérequis : python sql/run_sql.py   (construit analytics.* et sql/30_backtest_portfolios.sql)

    python backtest/run_backtest.py
    python backtest/run_backtest.py --cost-bps 20     # coûts de transaction plus élevés
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
from common import connect  # noqa: E402

COMMON_START = "2023-01-01"   # 1er mois où les 3 facteurs existent : comparaison à périmètre égal


def max_drawdown(returns: pd.Series) -> float:
    """Pire baisse, en %, entre un sommet et un creux de la courbe de richesse."""
    wealth = (1 + returns).cumprod()
    return float((wealth / wealth.cummax() - 1).min())


def metrics(r: pd.Series, turnover: pd.Series) -> dict:
    n = len(r)
    mean, std = r.mean(), r.std(ddof=1)
    return {
        "debut": r.index.min().strftime("%Y-%m"),
        "fin": r.index.max().strftime("%Y-%m"),
        "mois": n,
        "rend_annualise": (1 + r).prod() ** (12 / n) - 1,       # rendement géométrique
        "vol_annualisee": std * np.sqrt(12),
        # Long-short autofinancé : le taux sans risque s'annule entre la jambe longue et la courte
        "sharpe": mean / std * np.sqrt(12) if std > 0 else np.nan,
        "t_stat": mean / (std / np.sqrt(n)) if std > 0 else np.nan,  # |t| > 2 ~ significatif à 5 %
        "max_drawdown": max_drawdown(r),
        "pct_mois_positifs": (r > 0).mean(),
        "turnover_mensuel": turnover.mean(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Métriques du backtest long-short")
    parser.add_argument("--cost-bps", type=float, default=10.0,
                        help="coût de transaction par transaction, en points de base (défaut 10 = 0,10 %%)")
    args = parser.parse_args()
    cost = args.cost_bps / 10_000

    with connect() as con:
        df = con.execute("SELECT * FROM analytics.backtest_returns ORDER BY factor, return_month").df()
    if df.empty:
        raise SystemExit("analytics.backtest_returns est vide : lance d'abord python sql/run_sql.py")
    df["return_month"] = pd.to_datetime(df["return_month"])
    # Coûts : montant échangé = 2 x turnover par jambe, 2 jambes -> 4 x turnover x coût.
    # 1er mois : construction complète (achat de 100 % de chaque jambe) = 2 x coût -> turnover 0,5.
    df["ls_ret_net"] = df["ls_ret"] - 4 * df["turnover"].fillna(0.5) * cost

    rows, curves = [], []
    for factor, g in df.groupby("factor"):
        g = g.set_index("return_month")
        for periode, sub in (("complete", g), (f"commune {COMMON_START[:4]}+", g[g.index >= COMMON_START])):
            if len(sub) < 6:
                continue
            for version, col in (("brut", "ls_ret"), ("net", "ls_ret_net")):
                rows.append({"facteur": factor, "periode": periode, "version": version,
                             **metrics(sub[col], sub["turnover"])})
            if periode == "complete":
                rows.append({"facteur": factor, "periode": periode, "version": "jambe longue",
                             **metrics(sub["long_ret"], sub["turnover"])})
                rows.append({"facteur": factor, "periode": periode, "version": "jambe courte",
                             **metrics(sub["short_ret"], sub["turnover"])})
        curves.append(pd.DataFrame({
            "factor": factor, "return_month": g.index,
            "ls_ret": g["ls_ret"].values, "ls_ret_net": g["ls_ret_net"].values,
            "wealth_gross": (1 + g["ls_ret"]).cumprod().values,
            "wealth_net": (1 + g["ls_ret_net"]).cumprod().values,
        }))

    summary = pd.DataFrame(rows)
    curve = pd.concat(curves, ignore_index=True)
    summary["cost_bps"] = args.cost_bps

    # Sauvegarde pour le tableau de bord Streamlit
    with connect() as con:
        con.register("summary_df", summary)
        con.register("curve_df", curve)
        con.execute("CREATE OR REPLACE TABLE analytics.backtest_summary AS SELECT * FROM summary_df")
        con.execute("CREATE OR REPLACE TABLE analytics.backtest_cumulative AS SELECT * FROM curve_df")

    show = summary.copy()
    for col in ("rend_annualise", "vol_annualisee", "max_drawdown", "pct_mois_positifs", "turnover_mensuel"):
        show[col] = (show[col] * 100).round(1)
    show[["sharpe", "t_stat"]] = show[["sharpe", "t_stat"]].round(2)
    show = show.drop(columns="cost_bps")
    pd.set_option("display.width", 220)
    print(f"Coûts de transaction : {args.cost_bps:g} pb   (rendements, vol, drawdown, turnover en %)\n")
    print(show.to_string(index=False))
    print("\nTables enregistrées : analytics.backtest_summary, analytics.backtest_cumulative")


if __name__ == "__main__":
    main()
