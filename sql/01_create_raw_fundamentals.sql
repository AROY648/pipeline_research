-- Couche RAW : fondamentaux bruts reçus de yfinance.
CREATE SCHEMA IF NOT EXISTS raw;

-- 1) Photo du jour (Ticker.info) : valeurs "actuelles" seulement.
--    On garde une ligne par jour d'extraction pour construire un historique au fil du temps.
CREATE TABLE IF NOT EXISTS raw.fundamentals_snapshot (
    ticker               VARCHAR NOT NULL,
    snapshot_date        DATE    NOT NULL,   -- date à laquelle on a pris la photo
    market_cap           DOUBLE,             -- capitalisation (devise de cotation : CAD)
    shares_outstanding   DOUBLE,
    book_value_per_share DOUBLE,             -- en devise des états financiers
    price_to_book        DOUBLE,
    trailing_eps         DOUBLE,             -- BPA 12 derniers mois
    currency             VARCHAR,            -- devise du prix (CAD)
    financial_currency   VARCHAR,            -- devise des états financiers (CAD ou USD !)
    sector               VARCHAR,
    industry             VARCHAR,
    loaded_at            TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (ticker, snapshot_date)
);

-- 2) États financiers publiés (bilan + résultats), annuels et trimestriels.
--    Format long : une ligne = un poste comptable pour une période.
--    Ex. ('RY.TO', 'balance_sheet', 'annual', 2025-10-31, 'Stockholders Equity', 1.3e11)
CREATE TABLE IF NOT EXISTS raw.financial_statements (
    ticker      VARCHAR NOT NULL,
    statement   VARCHAR NOT NULL,   -- 'balance_sheet' ou 'income_stmt'
    frequency   VARCHAR NOT NULL,   -- 'annual' ou 'quarterly'
    period_end  DATE    NOT NULL,   -- fin de période comptable (PAS la date de publication)
    item        VARCHAR NOT NULL,   -- nom du poste, tel que fourni par Yahoo
    value       DOUBLE,
    loaded_at   TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (ticker, statement, frequency, period_end, item)
);
