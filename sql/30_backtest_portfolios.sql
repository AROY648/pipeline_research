-- BACKTEST (1/2) : portefeuilles long-short mensuels, équipondérés.
--
-- Chaque fin de mois t, pour chaque facteur :
--   - on ACHÈTE le quintile "attractif" et on VEND À DÉCOUVERT le quintile opposé ;
--   - on détient jusqu'à la fin du mois t+1 et on encaisse fwd_ret_1m.
-- Sens des facteurs (hypothèses de la littérature) :
--   momentum   : long Q5 (gagnants)        / short Q1 (perdants)
--   value      : long Q5 (B/M élevé)       / short Q1 (B/M faible)
--   volatility : long Q1 (peu volatils)    / short Q5 (très volatils)  -> anomalie "low volatility"
CREATE SCHEMA IF NOT EXISTS analytics;

-- 1) Positions : une ligne = facteur x mois de formation x titre détenu
CREATE OR REPLACE TABLE analytics.backtest_holdings AS
WITH eligible AS (
    SELECT * FROM analytics.factors_monthly
    WHERE is_complete_month
      AND fwd_ret_1m IS NOT NULL            -- le mois suivant doit être connu (et complet)
),
legs AS (
    SELECT 'momentum'   AS factor, month, ticker, fwd_ret_1m,
           CASE q_momentum   WHEN 5 THEN 'long' WHEN 1 THEN 'short' END AS side
    FROM eligible
    UNION ALL
    SELECT 'value', month, ticker, fwd_ret_1m,
           CASE q_value      WHEN 5 THEN 'long' WHEN 1 THEN 'short' END
    FROM eligible
    UNION ALL
    SELECT 'volatility', month, ticker, fwd_ret_1m,
           CASE q_volatility WHEN 1 THEN 'long' WHEN 5 THEN 'short' END
    FROM eligible
)
SELECT
    factor,
    month                                         AS formation_month,
    ticker,
    side,
    1.0 / COUNT(*) OVER (PARTITION BY factor, month, side) AS weight,   -- équipondéré dans chaque jambe
    fwd_ret_1m
FROM legs
WHERE side IS NOT NULL;

-- 2) Rendement mensuel de chaque stratégie + turnover
CREATE OR REPLACE TABLE analytics.backtest_returns AS
WITH leg_returns AS (
    SELECT
        factor,
        formation_month,
        SUM(CASE WHEN side = 'long'  THEN weight * fwd_ret_1m END) AS long_ret,
        SUM(CASE WHEN side = 'short' THEN weight * fwd_ret_1m END) AS short_ret,
        COUNT_IF(side = 'long')  AS n_long,
        COUNT_IF(side = 'short') AS n_short
    FROM analytics.backtest_holdings
    GROUP BY factor, formation_month
),
weight_changes AS (
    -- Turnover = somme des |poids(t) - poids(t-1)| / 2, par jambe (0 = rien changé, 1 = tout changé).
    -- FULL OUTER JOIN : un titre entré (absent en t-1) ou sorti (absent en t) compte aussi.
    SELECT
        COALESCE(cur.factor, prev.factor)                                   AS factor,
        COALESCE(cur.formation_month, prev.formation_month + INTERVAL 1 MONTH)::DATE AS formation_month,
        COALESCE(cur.side, prev.side)                                       AS side,
        ABS(COALESCE(cur.weight, 0) - COALESCE(prev.weight, 0))             AS abs_change
    FROM analytics.backtest_holdings cur
    FULL OUTER JOIN analytics.backtest_holdings prev
      ON  prev.factor = cur.factor
      AND prev.ticker = cur.ticker
      AND prev.side   = cur.side
      AND prev.formation_month + INTERVAL 1 MONTH = cur.formation_month
),
turnover AS (
    SELECT factor, formation_month,
           SUM(abs_change) / 4 AS turnover        -- /2 par jambe, puis moyenne des 2 jambes
    FROM weight_changes
    GROUP BY factor, formation_month
)
SELECT
    l.factor,
    l.formation_month,
    CAST(l.formation_month + INTERVAL 1 MONTH AS DATE) AS return_month,   -- mois où le rendement est réalisé
    l.long_ret,
    l.short_ret,
    l.long_ret - l.short_ret                           AS ls_ret,         -- long-short (autofinancé)
    l.n_long,
    l.n_short,
    -- Premier mois de chaque facteur : on construit tout le portefeuille, pas de turnover "réel"
    CASE WHEN ROW_NUMBER() OVER (PARTITION BY l.factor ORDER BY l.formation_month) = 1
         THEN NULL ELSE t.turnover END                 AS turnover
FROM leg_returns l
LEFT JOIN turnover t USING (factor, formation_month)
WHERE l.n_long > 0 AND l.n_short > 0;
