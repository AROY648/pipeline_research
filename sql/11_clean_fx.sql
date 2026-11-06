-- Couche CLEAN : taux USD/CAD quotidien.
CREATE SCHEMA IF NOT EXISTS clean;

CREATE OR REPLACE TABLE clean.fx_usdcad_daily AS
SELECT date, rate AS usdcad          -- nombre de CAD pour 1 USD
FROM raw.fx_daily
WHERE pair = 'USDCAD'
  AND rate > 0;
