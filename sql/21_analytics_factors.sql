-- Couche ANALYTICS (2/2) : facteurs mensuels, prêts pour le backtest.
-- Une ligne = un titre x une fin de mois t.
-- Règle d'or : toutes les colonnes "facteur" n'utilisent QUE de l'information connue à la clôture
-- du dernier jour du mois t. Seule fwd_ret_1m (rendement du mois t+1) regarde vers le futur :
-- c'est la variable à expliquer, jamais un signal.
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE OR REPLACE TABLE analytics.factors_monthly AS
WITH monthly AS (
    SELECT
        *,
        LAG(adj_close, 1)  OVER w AS adj_close_m1,     -- fin du mois t-1
        LAG(adj_close, 12) OVER w AS adj_close_m12,    -- fin du mois t-12
        LAG(month, 12)     OVER w AS month_m12,
        LEAD(ret_1m)       OVER w AS fwd_ret_1m,       -- rendement du mois t+1 (cible du backtest)
        LEAD(is_complete_month) OVER w AS fwd_month_complete
    FROM analytics.returns_monthly
    WINDOW w AS (PARTITION BY ticker ORDER BY month)
),
fund_pit AS (
    -- Fondamentaux "point-in-time" : dernière période PUBLIÉE à la fin du mois t
    -- (available_date <= date de fin de mois) et qui a bien une valeur comptable.
    SELECT
        m.ticker,
        m.month,
        f.period_end      AS fund_period_end,
        f.frequency       AS fund_frequency,
        f.book_equity_cad,
        f.shares_outstanding
    FROM monthly m
    JOIN clean.fundamentals f
      ON  f.ticker = m.ticker
      AND f.available_date <= m.month_end_date
      AND f.book_equity_cad IS NOT NULL
      AND f.shares_outstanding > 0
    QUALIFY ROW_NUMBER() OVER (PARTITION BY m.ticker, m.month
                               ORDER BY f.period_end DESC,
                                        CASE f.frequency WHEN 'quarterly' THEN 1 ELSE 2 END) = 1
),
eps_pit AS (
    -- BPA annuel publié le plus récent (le trimestriel n'a pas assez d'historique pour un BPA 12 mois)
    SELECT m.ticker, m.month, f.eps_cad AS eps_annual_cad
    FROM monthly m
    JOIN clean.fundamentals f
      ON  f.ticker = m.ticker
      AND f.frequency = 'annual'
      AND f.available_date <= m.month_end_date
      AND f.eps_cad IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY m.ticker, m.month ORDER BY f.period_end DESC) = 1
),
raw_factors AS (
    SELECT
        m.ticker,
        m.month,
        m.month_end_date,
        m.is_complete_month,
        m.ret_1m,
        CASE WHEN m.fwd_month_complete THEN m.fwd_ret_1m END AS fwd_ret_1m,
        -- Momentum 12-1 : rendement de t-12 à t-1. On saute le dernier mois (effet de retournement court terme).
        CASE WHEN m.month_m12 = m.month - INTERVAL 12 MONTH       -- 12 mois d'historique sans trou
             THEN m.adj_close_m1 / m.adj_close_m12 - 1 END        AS mom_12_1,
        m.vol_60d,
                -- Retournement court terme : rendement du mois t lui-même (le mois que le momentum 12-1 exclut)
        m.ret_1m                                                   AS rev_1m,
        -- Capitalisation = prix affiché x nombre d'actions de la dernière période publiée
        m.close * p.shares_outstanding                             AS market_cap_cad,
        p.book_equity_cad / NULLIF(m.close * p.shares_outstanding, 0) AS book_to_market,
        e.eps_annual_cad / NULLIF(m.close, 0)                      AS earnings_yield,
        p.fund_period_end,
        p.fund_frequency
    FROM monthly m
    LEFT JOIN fund_pit p USING (ticker, month)
    LEFT JOIN eps_pit  e USING (ticker, month)
)
SELECT
    *,
    -- Quintiles par mois (1 = plus faible, 5 = plus élevé). NULL si le facteur est manquant.
    -- PARTITION BY ... (x IS NULL) : les titres sans valeur ne sont pas classés avec les autres.
    CASE WHEN mom_12_1 IS NOT NULL THEN
        NTILE(5) OVER (PARTITION BY month, mom_12_1 IS NULL ORDER BY mom_12_1) END             AS q_momentum,
    CASE WHEN vol_60d IS NOT NULL THEN
        NTILE(5) OVER (PARTITION BY month, vol_60d IS NULL ORDER BY vol_60d) END               AS q_volatility,
    CASE WHEN book_to_market IS NOT NULL THEN
        NTILE(5) OVER (PARTITION BY month, book_to_market IS NULL ORDER BY book_to_market) END AS q_value,
    CASE WHEN rev_1m IS NOT NULL THEN
        NTILE(5) OVER (PARTITION BY month, rev_1m IS NULL ORDER BY rev_1m) END                 AS q_reversal,
    CASE WHEN book_to_market IS NOT NULL THEN
        PERCENT_RANK() OVER (PARTITION BY month, book_to_market IS NULL ORDER BY book_to_market) END AS value_rank
FROM raw_factors;
