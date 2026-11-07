"""
Tableau de bord Streamlit : résultats du backtest factoriel TSX 60.

Prérequis : python sql/run_sql.py  puis  python backtest/run_backtest.py
Lancement  : streamlit run dashboard/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
from common import DB_PATH, TICKERS_FILE  # noqa: E402

st.set_page_config(page_title="Facteurs TSX 60", layout="wide")

# Couleurs fixes par entité (palette validée daltonisme). Ne jamais réattribuer selon le filtre.
FACTOR_COLORS = {"momentum": "#2a78d6", "value": "#eb6834", "volatility": "#1baf7a", "reversal": "#eda100"}
LEG_COLORS = {"Jambe longue": "#6250d6", "Jambe courte": "#e87ba4"}
FACTOR_LABELS = {
    "momentum": "Momentum 12-1 (long gagnants / short perdants)",
    "value": "Value B/M (long bon marché / short cher)",
    "volatility": "Low volatility (long peu volatils / short très volatils)",
    "reversal": "Reversal 1-1 (long retournements / short tendance)",
    "reversal": "Retournement 1 mois (long perdants du mois / short gagnants)",
}


@st.cache_data(ttl=600)
def query(sql: str) -> pd.DataFrame:
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        return con.execute(sql).df()


try:
    returns = query("SELECT * FROM analytics.backtest_returns ORDER BY factor, return_month")
    summary = query("SELECT * FROM analytics.backtest_summary")
    freshness = query("SELECT MAX(date) AS last_price FROM clean.prices_daily")
except duckdb.Error as exc:
    st.error(f"Base introuvable ou incomplète ({exc}). Lance d'abord sql/run_sql.py puis backtest/run_backtest.py.")
    st.stop()

returns["return_month"] = pd.to_datetime(returns["return_month"])
cost_bps = float(summary["cost_bps"].iloc[0])

# ---------------------------------------------------------------- en-tête + filtres
st.title("Recherche factorielle : TSX 60")
st.caption(
    f"Portefeuilles long-short mensuels équipondérés (quintile 5 − quintile 1). "
    f"Dernier prix : {freshness['last_price'].iloc[0]:%Y-%m-%d}. Coûts : {cost_bps:g} pb par transaction."
)

f1, f2, f3 = st.columns([2, 1, 1])
factors = f1.multiselect("Facteurs", list(FACTOR_COLORS), default=list(FACTOR_COLORS))
periode = f2.radio("Période", ["complete", "commune 2023+"], horizontal=True,
                   help="« commune 2023+ » : les 3 facteurs sur la même période (value n'existe qu'à partir de 2023).")
version = f3.radio("Rendements", ["net", "brut"], horizontal=True, help="Net = après coûts de transaction.")
if not factors:
    st.info("Choisis au moins un facteur.")
    st.stop()

# ---------------------------------------------------------------- indicateurs clés
sel = summary[(summary["periode"] == periode) & (summary["version"] == version) & summary["facteur"].isin(factors)]
cols = st.columns(len(factors))
for col, factor in zip(cols, factors):
    row = sel[sel["facteur"] == factor]
    with col:
        st.markdown(f"**{factor.capitalize()}**")
        if row.empty:
            st.write("Pas assez de données sur cette période.")
            continue
        r = row.iloc[0]
        a, b = st.columns(2)
        a.metric("Rendement annualisé", f"{r['rend_annualise']:+.1%}")
        b.metric("Sharpe", f"{r['sharpe']:.2f}")
        a.metric("t-stat", f"{r['t_stat']:.2f}",
                 help="|t| > 2 : résultat statistiquement significatif (≈ 5 %). Sinon, indiscernable du hasard.")
        b.metric("Drawdown max", f"{r['max_drawdown']:.1%}")
        st.caption(f"{r['debut']} → {r['fin']} · {r['mois']} mois · turnover {r['turnover_mensuel']:.0%}/mois")

# ---------------------------------------------------------------- courbe cumulée
st.subheader("Valeur de 1 $ investi dans chaque stratégie long-short")
ret_col = "ls_ret_net" if version == "net" else "ls_ret"
r = returns[returns["factor"].isin(factors)].copy()
r["ls_ret_net"] = r["ls_ret"] - 4 * r["turnover"].fillna(0.5) * cost_bps / 10_000
if periode != "complete":
    r = r[r["return_month"] >= "2023-01-01"]
r = r.sort_values(["factor", "return_month"])
r["valeur"] = (1 + r[ret_col]).groupby(r["factor"]).cumprod()
r["rendement_mois"] = r[ret_col]

color = alt.Color("factor:N", title="Facteur",
                  scale=alt.Scale(domain=list(FACTOR_COLORS), range=list(FACTOR_COLORS.values())))
nearest = alt.selection_point(nearest=True, on="pointerover", fields=["return_month"], empty=False)
base = alt.Chart(r).encode(
    x=alt.X("return_month:T", title=None, axis=alt.Axis(format="%Y", tickCount="year")),
    y=alt.Y("valeur:Q", title="Valeur (départ = 1 $)", scale=alt.Scale(zero=False)),
    color=color,
)
lines = base.mark_line(strokeWidth=2)
points = base.mark_point(size=60, filled=True).encode(
    opacity=alt.condition(nearest, alt.value(1), alt.value(0)),
    tooltip=[alt.Tooltip("factor:N", title="Facteur"),
             alt.Tooltip("return_month:T", title="Mois", format="%Y-%m"),
             alt.Tooltip("valeur:Q", title="Valeur", format=".2f"),
             alt.Tooltip("rendement_mois:Q", title="Rendement du mois", format="+.1%")],
).add_params(nearest)
rule = alt.Chart(r).mark_rule(color="gray", strokeWidth=1).encode(x="return_month:T").transform_filter(nearest)
baseline = alt.Chart(pd.DataFrame({"y": [1.0]})).mark_rule(strokeDash=[4, 4], color="gray").encode(y="y:Q")
st.altair_chart((baseline + lines + rule + points).properties(height=380), width="stretch")
st.caption("Sous la ligne pointillée : la stratégie a perdu de l'argent depuis le début de la période.")

# ---------------------------------------------------------------- jambes
st.subheader("D'où vient le résultat ? Rendement annualisé de chaque jambe")
legs = summary[(summary["periode"] == "complete") & summary["version"].isin(["jambe longue", "jambe courte"])
               & summary["facteur"].isin(factors)].copy()
legs["jambe"] = legs["version"].str.replace("jambe ", "Jambe ", regex=False)
legs["label_x"] = legs["rend_annualise"].clip(lower=0)      # étiquette à droite de zéro, même si négatif
bars = alt.Chart(legs).mark_bar(cornerRadiusEnd=4, size=22).encode(
    y=alt.Y("facteur:N", title=None, sort=factors),
    yOffset=alt.YOffset("jambe:N", sort=list(LEG_COLORS)),
    x=alt.X("rend_annualise:Q", title="Rendement annualisé", axis=alt.Axis(format="%")),
    color=alt.Color("jambe:N", title="Jambe",
                    scale=alt.Scale(domain=list(LEG_COLORS), range=list(LEG_COLORS.values()))),
    tooltip=[alt.Tooltip("facteur:N", title="Facteur"), alt.Tooltip("jambe:N", title="Jambe"),
             alt.Tooltip("rend_annualise:Q", title="Rendement annualisé", format="+.1%"),
             alt.Tooltip("sharpe:Q", title="Sharpe", format=".2f")],
)
labels = bars.mark_text(align="left", dx=4).encode(
    x=alt.X("label_x:Q"), text=alt.Text("rend_annualise:Q", format="+.1%"), color=alt.value("gray"))
st.altair_chart((bars + labels).properties(height=90 * len(factors)), width="stretch")
st.caption(
    "Les deux jambes gagnent beaucoup pour tous les facteurs : c'est la signature du biais du survivant "
    "(liste actuelle du TSX 60 appliquée au passé). C'est l'écart entre les jambes qui mesure le facteur."
)

# ---------------------------------------------------------------- tableau complet
with st.expander("Tableau complet des métriques"):
    table = summary[summary["facteur"].isin(factors)].copy()
    pct = ["rend_annualise", "vol_annualisee", "max_drawdown", "pct_mois_positifs", "turnover_mensuel"]
    table[pct] = (table[pct] * 100).round(1)
    table[["sharpe", "t_stat"]] = table[["sharpe", "t_stat"]].round(2)
    st.dataframe(table.drop(columns="cost_bps"), hide_index=True, width="stretch")
    st.caption("Rendements, volatilité, drawdown, % de mois positifs et turnover en %.")

# ---------------------------------------------------------------- positions du dernier mois
st.subheader("Positions actuelles")
names = pd.read_csv(TICKERS_FILE)[["yahoo_ticker", "company", "sector"]]
last = query("""SELECT f.ticker, f.month, f.mom_12_1, f.vol_60d, f.book_to_market, f.rev_1m,
           f.q_momentum, f.q_volatility, f.q_value, f.q_reversal
""").merge(names, left_on="ticker", right_on="yahoo_ticker", how="left")
pos_factor = st.selectbox("Facteur", factors, format_func=lambda f: FACTOR_LABELS[f])
qcol, vcol, fmt = {"momentum": ("q_momentum", "mom_12_1", "{:+.1%}"),
                   "value": ("q_value", "book_to_market", "{:.2f}"),
                   "volatility": ("q_volatility", "vol_60d", "{:.1%}"),
                   "reversal": ("q_reversal", "rev_1m", "{:+.1%}")}[pos_factor]
long_q, short_q = (1, 5) if pos_factor in ("volatility", "reversal") else (5, 1)
st.caption(f"Portefeuille formé fin {last['month'].iloc[0]:%m/%Y}, détenu le mois suivant.")
cl, cs = st.columns(2)
for col, title, q in ((cl, "Achat (long)", long_q), (cs, "Vente à découvert (short)", short_q)):
    leg = last[last[qcol] == q].sort_values(vcol, ascending=(q == 1))
    leg = leg.assign(valeur=leg[vcol].map(fmt.format))[["ticker", "company", "sector", "valeur"]]
    col.markdown(f"**{title}** · {len(leg)} titres")
    col.dataframe(leg.rename(columns={"company": "société", "sector": "secteur", "valeur": vcol}),
                  hide_index=True, width="stretch")

# ---------------------------------------------------------------- pairs trading
st.subheader("Pairs trading")
try:
    pairs_daily = query("SELECT * FROM analytics.pairs_daily ORDER BY hold_months, date")
    pairs_trades = query("SELECT * FROM analytics.pairs_trades")
except duckdb.Error:
    pairs_daily = pd.DataFrame()
if pairs_daily.empty:
    st.info("Lance python backtest/pairs_trading.py (et --hold-months 6) pour afficher cette section.")
else:
    st.caption(
        "39 paires du même secteur ; formation sur 12 mois (régression des log-prix, demi-vie du spread), "
        "entrée si 2 < |z| < 4, sortie à la convergence (z = 0), au stop-loss (|z| > 4) ou en fin de période. "
        "Net de coûts."
    )
    pairs_daily["date"] = pd.to_datetime(pairs_daily["date"])
    pairs_daily["version"] = pairs_daily["hold_months"].map(lambda h: f"{h} mois")
    monthly_p = (pairs_daily.set_index("date").groupby("version")["ret"]
                 .resample("ME").apply(lambda x: (1 + x).prod() - 1).reset_index())
    pcols = st.columns(monthly_p["version"].nunique())
    for col, (ver, g) in zip(pcols, monthly_p.groupby("version")):
        rr = g["ret"]
        n = len(rr)
        wealth = (1 + rr).cumprod()
        with col:
            st.markdown(f"**Trading {ver}**")
            a, b = st.columns(2)
            a.metric("Rendement annualisé", f"{wealth.iloc[-1] ** (12 / n) - 1:+.1%}")
            b.metric("Sharpe", f"{rr.mean() / rr.std() * 12 ** 0.5:.2f}")
            a.metric("t-stat", f"{rr.mean() / (rr.std() / n ** 0.5):.2f}")
            b.metric("Drawdown max", f"{(wealth / wealth.cummax() - 1).min():.1%}")
    monthly_p["valeur"] = (1 + monthly_p["ret"]).groupby(monthly_p["version"]).cumprod()
    versions = sorted(monthly_p["version"].unique(), key=lambda v: int(v.split()[0]))
    pair_colors = ["#6250d6", "#e87ba4"][: len(versions)]
    p_chart = alt.Chart(monthly_p).mark_line(strokeWidth=2).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%Y", tickCount="year")),
        y=alt.Y("valeur:Q", title="Valeur (départ = 1 $)", scale=alt.Scale(zero=False)),
        color=alt.Color("version:N", title="Période de trading",
                        scale=alt.Scale(domain=versions, range=pair_colors)),
        tooltip=[alt.Tooltip("version:N", title="Version"), alt.Tooltip("date:T", title="Mois", format="%Y-%m"),
                 alt.Tooltip("valeur:Q", title="Valeur", format=".3f"),
                 alt.Tooltip("ret:Q", title="Rendement du mois", format="+.2%")],
    )
    st.altair_chart((baseline + p_chart).properties(height=300), width="stretch")

    ver_choice = st.radio("Détail des trades", versions, horizontal=True)
    t = pairs_trades[pairs_trades["hold_months"] == int(ver_choice.split()[0])]
    c1, c2 = st.columns([1, 2])
    exits = (t["exit"].value_counts(normalize=True).rename("part").reset_index()
             .rename(columns={"index": "exit"}))
    exits["part"] = (exits["part"] * 100).round(1)
    c1.markdown("**Comment les trades se terminent (%)**")
    c1.dataframe(exits.rename(columns={"exit": "sortie", "part": "% des trades"}), hide_index=True, width="stretch")
    by_group = (t.groupby("group").agg(trades=("ret", "size"), rend_moyen_pct=("ret", "mean"),
                                       gagnants_pct=("ret", lambda x: (x > 0).mean()),
                                       duree_moy_j=("days", "mean"))
                .sort_values("trades", ascending=False).reset_index())
    by_group["rend_moyen_pct"] = (by_group["rend_moyen_pct"] * 100).round(2)
    by_group["gagnants_pct"] = (by_group["gagnants_pct"] * 100).round(0)
    by_group["duree_moy_j"] = by_group["duree_moy_j"].round(1)
    c2.markdown("**Par groupe de paires**")
    c2.dataframe(by_group.rename(columns={"group": "groupe"}), hide_index=True, width="stretch")
    st.caption(
        "Beaucoup de stop-loss et peu de convergences : les écarts estimés sur les 12 derniers mois "
        "ne se referment pas dans les mois suivants. Un retour à la moyenne observé dans le passé (in-sample) "
        "ne garantit pas qu'il se reproduira (out-of-sample)."
    )

# ---------------------------------------------------------------- méthode
with st.expander("Méthode et limites"):
    st.markdown("""
- **Univers** : 60 titres du TSX 60 (composition du 31 janvier 2026), prix quotidiens yfinance depuis 2010.
- **Signaux** fin de mois t, **rendement** du mois t+1 (aucun look-ahead). Fondamentaux utilisés seulement
  après leur date limite de publication (90 jours après l'exercice, 45 jours après un trimestre), convertis en CAD.
- **Momentum 12-1** : rendement de t−12 à t−1. **Volatilité** : écart-type de 60 rendements quotidiens, annualisé.
  **Value** : capitaux propres / capitalisation.
- **Limites** : biais du survivant (composition actuelle appliquée au passé), 12 titres par quintile,
  value testable sur ~45 mois seulement, vente à découvert supposée toujours possible, coûts simplifiés.
""")