-- Couche RAW : taux de change quotidiens (yfinance).
CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.fx_daily (
    pair       VARCHAR NOT NULL,   -- ex. 'USDCAD' = nombre de CAD pour 1 USD
    date       DATE    NOT NULL,
    rate       DOUBLE,             -- clôture du jour
    loaded_at  TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (pair, date)
);
