-- Couche CLEAN : prix quotidiens prêts à l'emploi.
-- Source : raw.prices_daily. Recréée entièrement à chaque exécution.
CREATE SCHEMA IF NOT EXISTS clean;

CREATE OR REPLACE TABLE clean.prices_daily AS
WITH base AS (
    SELECT ticker, date, close, adj_close, volume, dividends, splits
    FROM raw.prices_daily
    WHERE date < current_date          -- la séance du jour peut être incomplète (script lancé pendant la bourse)
      AND close > 0
      AND adj_close > 0
),
with_prev AS (
    SELECT
        *,
        LAG(adj_close) OVER (PARTITION BY ticker ORDER BY date) AS prev_adj_close
    FROM base
)
SELECT
    ticker,
    date,
    close,                                   -- prix affiché (Yahoo l'ajuste déjà pour les splits, pas pour les dividendes)
    adj_close,                               -- prix ajusté dividendes + splits : à utiliser pour TOUS les rendements
    adj_close / close           AS adj_factor,   -- < 1 dans le passé : effet cumulé des dividendes réinvestis
    volume,
    dividends,
    splits,
    -- Contrôles qualité : on signale, on ne supprime pas (décision prise en aval)
    COALESCE(ABS(adj_close / prev_adj_close - 1) > 0.40, FALSE) AS flag_big_move,    -- variation > 40 % en un jour
    volume = 0                                                  AS flag_zero_volume
FROM with_prev;
