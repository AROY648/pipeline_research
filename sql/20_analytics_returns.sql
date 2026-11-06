-- Couche ANALYTICS (1/2) : rendements.
-- Tous les rendements utilisent adj_close (ajusté dividendes + splits) = rendement total.
CREATE SCHEMA IF NOT EXISTS analytics;

-- 1) Rendements quotidiens + volatilité glissante 60 jours
CREATE OR REPLACE TABLE analytics.returns_daily AS
WITH r AS (
    SELECT
        ticker,
        date,
        adj_close,
        close,
        adj_close / LAG(adj_close) OVER w - 1 AS ret_1d
    FROM clean.prices_daily
    WINDOW w AS (PARTITION BY ticker ORDER BY date)
)
SELECT
    ticker,
    date,
    adj_close,
    close,
    ret_1d,
    -- Écart-type des 60 derniers rendements quotidiens (jour courant inclus), annualisé (x racine de 252)
    STDDEV_SAMP(ret_1d) OVER w60 * SQRT(252) AS vol_60d,
    COUNT(ret_1d)       OVER w60             AS n_obs_60d       -- pour exiger 60 observations complètes
FROM r
WINDOW w60 AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW);

-- 2) Une ligne par titre et par mois : la DERNIÈRE séance du mois
CREATE OR REPLACE TABLE analytics.returns_monthly AS
WITH month_end AS (
    SELECT
        ticker,
        DATE_TRUNC('month', date)::DATE AS month,     -- ex. 2024-03-01 = mars 2024
        date                            AS month_end_date,
        adj_close,
        close,
        CASE WHEN n_obs_60d = 60 THEN vol_60d END AS vol_60d
    FROM analytics.returns_daily
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, DATE_TRUNC('month', date) ORDER BY date DESC) = 1
)
SELECT
    ticker,
    month,
    month_end_date,
    adj_close,
    close,
    vol_60d,
    adj_close / LAG(adj_close) OVER w - 1  AS ret_1m,           -- rendement du mois écoulé
    -- Mois incomplet (mois en cours) : exclu du backtest
    month < DATE_TRUNC('month', current_date) AS is_complete_month
FROM month_end
WINDOW w AS (PARTITION BY ticker ORDER BY month);
