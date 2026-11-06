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
FACTOR_COLORS = {"momentum": "#2a78d6", "value": "#eb6834", "volatility": "#1baf7a"}
LEG_COLORS = {"Jambe longue": "#6250d6", "Jambe courte": "#eda100"}
FACTOR_LABELS = {
    "momentum": "Momentum 12-1 (long gagnants / short perdants)",
    "value": "Value B/M (long bon marché / short cher)",
    "volatility": "Low volatility (long peu volatils / short très volatils)",
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
last = query("""
    SELECT f.ticker, f.month, f.mom_12_1, f.vol_60d, f.book_to_market, f.q_momentum, f.q_volatility, f.q_value
    FROM analytics.factors_monthly f
    WHERE f.month = (SELECT MAX(month) FROM analytics.factors_monthly WHERE is_complete_month)
""").merge(names, left_on="ticker", right_on="yahoo_ticker", how="left")
pos_factor = st.selectbox("Facteur", factors, format_func=lambda f: FACTOR_LABELS[f])
qcol, vcol, fmt = {"momentum": ("q_momentum", "mom_12_1", "{:+.1%}"),
                   "value": ("q_value", "book_to_market", "{:.2f}"),
                   "volatility": ("q_volatility", "vol_60d", "{:.1%}")}[pos_factor]
long_q, short_q = (1, 5) if pos_factor == "volatility" else (5, 1)
st.caption(f"Portefeuille formé fin {last['month'].iloc[0]:%m/%Y}, détenu le mois suivant.")
cl, cs = st.columns(2)
for col, title, q in ((cl, "Achat (long)", long_q), (cs, "Vente à découvert (short)", short_q)):
    leg = last[last[qcol] == q].sort_values(vcol, ascending=(q == 1))
    leg = leg.assign(valeur=leg[vcol].map(fmt.format))[["ticker", "company", "sector", "valeur"]]
    col.markdown(f"**{title}** · {len(leg)} titres")
    col.dataframe(leg.rename(columns={"company": "société", "sector": "secteur", "valeur": vcol}),
                  hide_index=True, width="stretch")

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