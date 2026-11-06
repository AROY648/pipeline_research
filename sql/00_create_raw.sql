-- Couche RAW : données telles que reçues de la source (yfinance), non ajustées.
-- Idempotent : peut être relancé sans risque.
CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.prices_daily (
    ticker     VARCHAR   NOT NULL,   -- ex. RY.TO
    date       DATE      NOT NULL,   -- date de cotation
    open       DOUBLE,
    high       DOUBLE,
    low        DOUBLE,
    close      DOUBLE,               -- prix de clôture brut (non ajusté)
    adj_close  DOUBLE,               -- clôture ajustée dividendes + splits (fournie par Yahoo)
    volume     BIGINT,
    dividends  DOUBLE,               -- dividende versé ce jour (0 sinon)
    splits     DOUBLE,               -- ratio de split ce jour (0 sinon)
    source     VARCHAR   DEFAULT 'yfinance',
    loaded_at  TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (ticker, date)
);
