# pipeline_research : recherche factorielle sur le TSX 60

Pipeline de données de bout en bout qui ingère les prix et fondamentaux des 60 titres du S&P/TSX 60,
les transforme en SQL (couches **raw → clean → analytics**) et backteste trois facteurs classiques
(momentum, value, low volatility) avec des portefeuilles long-short mensuels, sans biais d'anticipation.
Résultats présentés dans un tableau de bord Streamlit.

**Stack :** Python · yfinance · DuckDB (développement local) · SQL (CTE, fonctions de fenêtre, `QUALIFY`) ·
pandas · Streamlit · Snowflake (déploiement).

## Architecture

```
yfinance ──► ingestion/*.py ──► raw.*  ──► sql/1x_*.sql ──► clean.*  ──► sql/2x_*.sql ──► analytics.*
 (prix,        (Python,          (brut,       (nettoyage,       (point-in-time,  (rendements,      │
 fondamentaux,  upsert            tel que      contrôles         en CAD)          facteurs)         │
 USD/CAD)       idempotent)       reçu)        qualité)                                             ▼
                                                                       sql/30_*.sql + backtest/run_backtest.py
                                                                                     │
                                                                       dashboard/app.py (Streamlit)
```

| Couche | Tables | Contenu |
|---|---|---|
| raw | `prices_daily`, `fundamentals_snapshot`, `financial_statements`, `fx_daily` | Données brutes, clé primaire + upsert |
| clean | `prices_daily`, `fx_usdcad_daily`, `fundamentals` | Séance en cours exclue, anomalies signalées, fondamentaux datés à leur publication et convertis en CAD |
| analytics | `returns_daily`, `returns_monthly`, `factors_monthly` | Rendements, momentum 12-1, volatilité 60 j, book-to-market, quintiles |
| backtest | `backtest_holdings`, `backtest_returns`, `backtest_summary`, `backtest_cumulative` | Positions, rendements long-short, turnover, métriques |

## Méthode

- **Univers** : 60 titres du S&P/TSX 60 (composition au 31 janvier 2026), prix quotidiens depuis 2010.
- **Facteurs** (calculés à la fin de chaque mois t) :
  - *Momentum 12-1* : rendement de t−12 à t−1 (le dernier mois est exclu, effet de retournement court terme).
  - *Low volatility* : écart-type de 60 rendements quotidiens, annualisé.
  - *Value* : capitaux propres / capitalisation (book-to-market).
- **Portefeuilles** : quintiles mensuels ; long du quintile attractif, short du quintile opposé,
  ~12 titres par jambe, équipondérés ; détention d'un mois.
- **Pas de look-ahead** :
  - signal mesuré fin t, rendement réalisé en t+1 ;
  - fondamentaux utilisés seulement après leur date limite de publication
    (Règlement 51-102 : 90 jours après l'exercice, 45 jours après un trimestre) ;
  - séance et mois en cours exclus.
- **Métriques** : rendement annualisé, volatilité, Sharpe, t-stat, drawdown max, turnover,
  brut et net de coûts (10 pb par transaction par défaut).

## Résultats (novembre 2026, brut)

| Facteur | Période | Rend. annualisé | Sharpe | t-stat | Drawdown max | Turnover/mois |
|---|---|---|---|---|---|---|
| Momentum 12-1 | 2011–2026 (189 mois) | +5,3 % | 0,34 | 1,37 | −58,5 % | 23 % |
| Value (B/M) | 2023–2026 (45 mois) | +4,3 % | 0,38 | 0,74 | −11,4 % | 10 % |
| Low volatility | 2010–2026 (199 mois) | −11,2 % | −0,38 | −1,56 | −92,8 % | 19 % |

**Lecture :**
- Aucun facteur n'est statistiquement significatif (|t| < 2) sur cet univers de 60 grandes capitalisations.
- **Momentum** : prime positive mais instable, avec de sévères « momentum crashes » lors des rebonds de marché ;
  le turnover élevé ronge le rendement (+2,4 %/an à 25 pb de coûts).
- **Value** : légèrement positif, mais 45 mois ne permettent aucune conclusion.
- **Low volatility** : la jambe peu volatile a un meilleur Sharpe (1,32 contre 0,89), donc l'anomalie
  existe en rendement *ajusté du risque*. Le long-short à montants égaux perd néanmoins, car il n'est pas
  neutre en bêta et parce que le biais du survivant gonfle les titres volatils encore présents dans l'indice.

## Limites

- **Biais du survivant** : la composition actuelle est appliquée au passé (rendement moyen de l'univers ≈ 18 %/an).
  Une composition historique de l'indice (donnée payante) corrigerait ce biais.
- **Petit univers** : 12 titres par quintile, résultats sensibles à quelques titres.
- **Fondamentaux gratuits limités** : ~4 ans d'états annuels via yfinance, donc value testable depuis 2023 seulement.
- Vente à découvert supposée toujours possible ; coûts de transaction simplifiés ; pas de neutralisation sectorielle ni de bêta.

## Reproduire

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env

python ingestion/ingest_prices.py          # prix 60 titres depuis 2010
python ingestion/ingest_fundamentals.py    # fondamentaux
python ingestion/ingest_fx.py              # USD/CAD
python sql/run_sql.py                      # clean + analytics + portefeuilles
python backtest/run_backtest.py            # métriques
streamlit run dashboard/app.py             # tableau de bord
```

Explorer la base : `python sql/query.py "SELECT * FROM analytics.factors_monthly LIMIT 10"`.

## Structure

```
ingestion/   scripts d'ingestion (yfinance → DuckDB raw) + liste des tickers
sql/         00-02 : tables raw · 1x : clean · 2x : analytics · 30 : backtest · checks/ : diagnostics
backtest/    calcul des métriques de performance
dashboard/   application Streamlit
```