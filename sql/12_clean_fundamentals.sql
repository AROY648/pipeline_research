-- Couche CLEAN : fondamentaux "point-in-time", en CAD.
-- Une ligne = un titre x une période comptable (annuelle ou trimestrielle).
--
-- Deux règles anti look-ahead :
--   1) available_date = date à partir de laquelle le chiffre était PUBLIC.
--      Règle canadienne (Règlement 51-102) : états annuels au plus tard 90 jours après la fin
--      de l'exercice, trimestriels au plus tard 45 jours. On prend ces délais maximaux (prudent).
--   2) La conversion USD -> CAD utilise le taux à la date de fin de période (taux de clôture du bilan).
CREATE SCHEMA IF NOT EXISTS clean;

CREATE OR REPLACE TABLE clean.fundamentals AS
WITH items AS (
    -- Format long -> une colonne par poste utile
    SELECT
        ticker,
        frequency,
        period_end,
        MAX(CASE WHEN item = 'Common Stock Equity'            THEN value END) AS common_equity,
        MAX(CASE WHEN item = 'Stockholders Equity'            THEN value END) AS stockholders_equity,
        MAX(CASE WHEN item = 'Ordinary Shares Number'         THEN value END) AS shares_ordinary,
        MAX(CASE WHEN item = 'Share Issued'                   THEN value END) AS shares_issued,
        MAX(CASE WHEN item = 'Diluted EPS'                    THEN value END) AS eps_diluted,
        MAX(CASE WHEN item = 'Basic EPS'                      THEN value END) AS eps_basic,
        MAX(CASE WHEN item = 'Net Income Common Stockholders' THEN value END) AS net_income_common
    FROM raw.financial_statements
    GROUP BY ticker, frequency, period_end
),
currency AS (
    -- Devise des états financiers : celle de la photo la plus récente
    SELECT ticker, financial_currency
    FROM raw.fundamentals_snapshot
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) = 1
),
with_fx AS (
    -- Dernier taux connu à la date de fin de période (fin de période parfois un week-end)
    SELECT
        p.*,
        COALESCE(c.financial_currency, 'CAD') AS reporting_currency,
        fx.usdcad
    FROM items p
    LEFT JOIN currency c
           ON c.ticker = p.ticker
    LEFT JOIN clean.fx_usdcad_daily fx
           ON fx.date BETWEEN p.period_end - 7 AND p.period_end
    QUALIFY ROW_NUMBER() OVER (PARTITION BY p.ticker, p.frequency, p.period_end
                               ORDER BY fx.date DESC) = 1
),
converted AS (
    SELECT
        *,
        CASE reporting_currency WHEN 'CAD' THEN 1.0
                                WHEN 'USD' THEN usdcad
        END AS fx_to_cad                           -- NULL si devise inconnue -> repéré par les contrôles
    FROM with_fx
)
SELECT
    ticker,
    frequency,
    period_end,
    CAST(period_end + CASE frequency WHEN 'annual' THEN 90 ELSE 45 END AS DATE) AS available_date,
    reporting_currency,
    fx_to_cad,
    COALESCE(common_equity, stockholders_equity) * fx_to_cad  AS book_equity_cad,   -- valeur comptable (actionnaires ordinaires)
    COALESCE(shares_ordinary, shares_issued)                  AS shares_outstanding,
    COALESCE(eps_diluted, eps_basic) * fx_to_cad              AS eps_cad,          -- BPA de la période (pas 12 mois glissants)
    net_income_common * fx_to_cad                             AS net_income_cad,
    -- Contrôles qualité
    COALESCE(common_equity, stockholders_equity) IS NULL      AS flag_missing_book,
    fx_to_cad IS NULL                                         AS flag_missing_fx
FROM converted;
