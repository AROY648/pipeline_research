"""
Pairs trading (divergence / retour à la moyenne) sur des paires du TSX 60 choisies pour leur logique économique.

Méthode (inspirée de Gatev, Goetzmann et Rouwenhorst, 2006), répétée chaque fin de mois t :
  1. FORMATION (252 dernières séances jusqu'à t) : pour chaque paire candidate (A, B)
       - régression ln(A) = alpha + beta * ln(B)  ->  spread = ln(A) - alpha - beta * ln(B)
       - écart-type du spread, corrélation des rendements quotidiens, demi-vie du retour à la moyenne
       - on garde les paires qui reviennent vite à la moyenne (demi-vie de 2 à 40 jours),
         puis les N plus corrélées.
  2. TRADING (séances des H mois suivants, H = --hold-months), avec les paramètres FIGÉS de la formation :
       - z = spread / écart-type
       - z > +2 : le spread est "trop haut" -> on vend A et on achète B ; z < -2 : l'inverse
       - sortie quand z repasse par 0 (convergence), si |z| > 4 (stop-loss) ou en fin de période
       - le signal est calculé à la clôture du jour d ; la position rapporte à partir du jour d+1.
  3. Capital égal par paire sélectionnée ; une paire inactive rapporte 0 (approche "capital engagé", prudente).
  4. Avec H > 1, une nouvelle "cohorte" démarre chaque mois : H cohortes se chevauchent, chacune reçoit 1/H du capital
     (comme Gatev et al., qui utilisent H = 6).

Prérequis : python sql/run_sql.py
Lancement  : python backtest/pairs_trading.py                  # période de trading = 1 mois
             python backtest/pairs_trading.py --hold-months 6  # version Gatev et al.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import connect           # noqa: E402
from run_backtest import metrics     # noqa: E402  (mêmes métriques que les facteurs)

# Paires candidates : même secteur, même modèle d'affaires. Définies AVANT de regarder les résultats.
GROUPS = {
    "Banques": ["RY.TO", "TD.TO", "BNS.TO", "BMO.TO", "CM.TO", "NA.TO"],
    "Assureurs vie": ["MFC.TO", "SLF.TO"],
    "Chemins de fer": ["CNR.TO", "CP.TO"],
    "Télécoms": ["BCE.TO", "T.TO", "RCI-B.TO"],
    "Pipelines": ["ENB.TO", "TRP.TO", "PPL.TO"],
    "Pétrole intégré": ["CNQ.TO", "SU.TO", "CVE.TO", "IMO.TO"],
    "Mines d'or": ["AEM.TO", "ABX.TO", "K.TO"],
    "Redevances métaux": ["FNV.TO", "WPM.TO"],
    "Services publics": ["FTS.TO", "EMA.TO", "H.TO"],
    "Épiciers": ["L.TO", "MRU.TO", "WN.TO"],
}
CANDIDATES = [(a, b, g) for g, tks in GROUPS.items() for i, a in enumerate(tks) for b in tks[i + 1:]]

FORMATION_DAYS = 252
MIN_HALF_LIFE, MAX_HALF_LIFE = 2, 40
STOP_Z = 4.0


def formation_stats(la: np.ndarray, lb: np.ndarray) -> dict | None:
    """Statistiques d'une paire sur la fenêtre de formation (log-prix)."""
    beta, alpha = np.polyfit(lb, la, 1)
    if not 0.2 <= beta <= 5:                      # couverture absurde -> paire rejetée
        return None
    spread = la - alpha - beta * lb
    sd = spread.std(ddof=1)
    # Demi-vie : régression de la variation du spread sur son niveau de la veille (processus AR(1))
    slope = np.polyfit(spread[:-1], np.diff(spread), 1)[0]
    if slope >= 0:                                # aucun retour à la moyenne
        return None
    half_life = -np.log(2) / np.log(1 + slope)
    corr = np.corrcoef(np.diff(la), np.diff(lb))[0, 1]
    return {"alpha": alpha, "beta": beta, "sd": sd, "half_life": half_life, "corr": corr}


def trade_pair(la, lb, ra, rb, stats, entry_z, cost):
    """Simule une paire sur la période de trading. Renvoie les rendements quotidiens et la liste des trades."""
    z = (la - stats["alpha"] - stats["beta"] * lb) / stats["sd"]
    wa = 1 / (1 + stats["beta"])                  # exposition brute totale = 1, neutre en dollars pondérés par beta
    wb = stats["beta"] / (1 + stats["beta"])
    n = len(z)
    pos = np.zeros(n)                             # position décidée à la clôture du jour d
    trades, current, opened = [], 0, None
    for d in range(n):
        if current == 0:
            if d == n - 1:                        # pas d'ouverture le dernier jour de la période
                pass
            elif entry_z < z[d] < STOP_Z:         # on n'entre pas si l'écart dépasse déjà le stop-loss
                current, opened = -1, d           # spread trop haut : short A / long B
            elif -STOP_Z < z[d] < -entry_z:
                current, opened = 1, d            # spread trop bas : long A / short B
        else:
            reason = None
            if np.sign(z[d]) != np.sign(z[opened]):
                reason = "convergence"
            elif abs(z[d]) > STOP_Z:
                reason = "stop-loss"
            elif d == n - 1:
                reason = "fin de période"
            if reason:
                trades.append((opened, d, current, reason))
                current = 0
        pos[d] = current
    if current != 0:
        trades.append((opened, n - 1, current, "fin de période"))
        pos[n - 1] = 0                            # on ferme à la dernière clôture de la période
    held = np.concatenate([[0.0], pos[:-1]])      # position détenue PENDANT le jour d = décidée la veille
    gross = held * (wa * ra - wb * rb)
    costs = np.abs(np.diff(np.concatenate([[0.0], pos]))) * cost   # coût à chaque ouverture / fermeture
    return gross - costs, trades, z


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest pairs trading")
    parser.add_argument("--entry", type=float, default=2.0, help="seuil d'entrée en écarts-types (défaut 2)")
    parser.add_argument("--top", type=int, default=10, help="nombre max de paires par mois (défaut 10)")
    parser.add_argument("--cost-bps", type=float, default=10.0, help="coût par transaction en pb (défaut 10)")
    parser.add_argument("--hold-months", type=int, default=1, help="durée de la période de trading en mois (défaut 1)")
    args = parser.parse_args()
    hold = args.hold_months
    cost = args.cost_bps / 10_000

    tickers = sorted({t for a, b, _ in CANDIDATES for t in (a, b)})
    with connect() as con:
        px = con.execute(f"""
            SELECT date, ticker, adj_close FROM clean.prices_daily
            WHERE ticker IN ({", ".join(f"'{t}'" for t in tickers)})
        """).df()
    prices = px.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    prices.index = pd.to_datetime(prices.index)
    logp, rets = np.log(prices), prices.pct_change()
    month_ends = prices.groupby(prices.index.to_period("M")).tail(1).index
    current_month = pd.Timestamp.today().to_period("M")

    daily_rows, trade_rows, sel_rows = [], [], []
    for i, t_end in enumerate(month_ends):
        if i + hold >= len(month_ends):
            break
        t_last = month_ends[i + hold]                        # dernière séance de la période de trading
        if t_last.to_period("M") >= current_month:          # période de trading incomplète : exclue
            break
        hist = logp.loc[:t_end].tail(FORMATION_DAYS)
        trade_days = logp.loc[t_end:t_last].index[1:]       # séances des mois t+1 ... t+H
        if len(hist) < FORMATION_DAYS:
            continue
        # 1) formation
        cands = []
        for a, b, g in CANDIDATES:
            if a not in hist or b not in hist or hist[[a, b]].isna().any().any():
                continue
            s = formation_stats(hist[a].values, hist[b].values)
            if s and MIN_HALF_LIFE <= s["half_life"] <= MAX_HALF_LIFE:
                cands.append({"pair": f"{a.removesuffix('.TO')}/{b.removesuffix('.TO')}",
                              "a": a, "b": b, "group": g, **s})
        selected = sorted(cands, key=lambda c: -c["corr"])[: args.top]
        if not selected:
            continue
        # 2) trading
        port = np.zeros(len(trade_days))
        for s in selected:
            la = logp.loc[trade_days, s["a"]].values
            lb = logp.loc[trade_days, s["b"]].values
            ra = rets.loc[trade_days, s["a"]].fillna(0).values
            rb = rets.loc[trade_days, s["b"]].fillna(0).values
            if np.isnan(la).any() or np.isnan(lb).any():
                continue
            r, trades, _ = trade_pair(la, lb, ra, rb, s, args.entry, cost)
            port += r / len(selected)                       # capital égal par paire sélectionnée
            for o, c, direction, reason in trades:
                trade_rows.append({
                    "pair": s["pair"], "group": s["group"], "formation_month": t_end.date(),
                    "open_date": trade_days[o].date(), "close_date": trade_days[c].date(),
                    "direction": "long A / short B" if direction == 1 else "short A / long B",
                    "days": c - o, "ret": float(np.prod(1 + r[o: c + 1]) - 1), "exit": reason,   # coûts inclus
                })
            sel_rows.append({"formation_month": t_end.date(), "pair": s["pair"], "group": s["group"],
                             "beta": s["beta"], "half_life": s["half_life"], "corr": s["corr"]})
        daily_rows.append(pd.DataFrame({"date": trade_days, "cohort": t_end.date(), "ret": port}))

    if not daily_rows:
        raise SystemExit("Aucune paire sélectionnée : vérifie clean.prices_daily (python sql/run_sql.py).")
    cohorts = pd.concat(daily_rows, ignore_index=True)
    # Rendement du portefeuille = moyenne des cohortes actives ce jour-là (chacune ~1/H du capital)
    daily = cohorts.groupby("date", as_index=False)["ret"].mean()
    daily["hold_months"] = hold
    monthly = (daily.set_index("date")["ret"].add(1).groupby(lambda d: d.to_period("M")).prod().sub(1))
    monthly.index = monthly.index.to_timestamp()
    trades = pd.DataFrame(trade_rows).assign(hold_months=hold)
    selections = pd.DataFrame(sel_rows).assign(hold_months=hold)

    m = metrics(monthly, pd.Series(dtype=float))
    m.pop("turnover_mensuel")
    by_group = (trades.groupby("group")
                .agg(trades=("ret", "size"), rend_moyen=("ret", "mean"), pct_gagnants=("ret", lambda x: (x > 0).mean()),
                     duree_moy_j=("days", "mean"))
                .sort_values("trades", ascending=False))
    exits = trades["exit"].value_counts(normalize=True)

    with connect() as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.register("daily_df", daily)
        con.register("trades_df", trades)
        con.register("sel_df", selections)
        # Une version par durée de trading : on remplace seulement les lignes de cette durée
        for table, df_name in (("pairs_daily", "daily_df"), ("pairs_trades", "trades_df"),
                               ("pairs_selections", "sel_df")):
            try:
                con.execute(f"SELECT hold_months FROM analytics.{table} LIMIT 0")
            except duckdb.Error:                            # table absente ou ancienne version sans hold_months
                con.execute(f"DROP TABLE IF EXISTS analytics.{table}")
                con.execute(f"CREATE TABLE analytics.{table} AS SELECT * FROM {df_name} LIMIT 0")
            con.execute(f"DELETE FROM analytics.{table} WHERE hold_months = {hold}")
            con.execute(f"INSERT INTO analytics.{table} BY NAME SELECT * FROM {df_name}")

    pd.set_option("display.width", 200)
    print(f"Paires candidates : {len(CANDIDATES)} · seuil d'entrée |z| > {args.entry:g} · "
          f"max {args.top} paires/cohorte · trading {hold} mois · coûts {args.cost_bps:g} pb\n")
    print("== Performance du portefeuille (net de coûts) ==")
    for k, v in m.items():
        pct = k in ("rend_annualise", "vol_annualisee", "max_drawdown", "pct_mois_positifs")
        print(f"  {k:<20} {v:.1%}" if pct else f"  {k:<20} {v:.2f}" if isinstance(v, float) else f"  {k:<20} {v}")
    print(f"\n== Trades : {len(trades)} au total, {len(trades) / len(monthly):.1f} ouverts par mois, "
          f"durée moyenne {trades['days'].mean():.1f} jours ==")
    print("Sorties : " + ", ".join(f"{k} {v:.0%}" for k, v in exits.items()))
    out = by_group.copy()
    out["rend_moyen"] = (out["rend_moyen"] * 100).round(2)
    out["pct_gagnants"] = (out["pct_gagnants"] * 100).round(0)
    out["duree_moy_j"] = out["duree_moy_j"].round(1)
    print("\n== Par groupe (rendement moyen par trade en %) ==")
    print(out.to_string())
    print("\n== Paires les plus souvent sélectionnées ==")
    print(selections["pair"].value_counts().head(10).to_string())
    print("\nTables enregistrées : analytics.pairs_daily, analytics.pairs_trades, analytics.pairs_selections")


if __name__ == "__main__":
    main()