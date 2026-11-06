-- Diagnostic : où manquent les capitaux propres dans clean.fundamentals ?
-- rang 1 = période la plus récente du titre.
WITH ranked AS (
    SELECT
        f.*,
        ROW_NUMBER() OVER (PARTITION BY ticker, frequency ORDER BY period_end DESC) AS rang
    FROM clean.fundamentals f
),
postes AS (
    -- nombre de postes reçus de Yahoo pour chaque période
    SELECT ticker, frequency, period_end, COUNT(*) AS n_postes,
           MAX(CASE WHEN item ILIKE '%equity%' THEN 1 ELSE 0 END) AS a_un_poste_equity
    FROM raw.financial_statements
    WHERE statement = 'balance_sheet'
    GROUP BY ALL
)
SELECT
    r.frequency,
    r.rang,
    COUNT(*)                                         AS periodes,
    CAST(COUNT_IF(r.flag_missing_book) AS INTEGER)   AS sans_book,
    CAST(ROUND(AVG(p.n_postes)) AS INTEGER)          AS postes_bilan_moy,
    CAST(COUNT_IF(r.flag_missing_book AND p.n_postes IS NULL) AS INTEGER) AS sans_aucun_bilan,
    STRING_AGG(CASE WHEN r.flag_missing_book AND p.n_postes IS NOT NULL THEN r.ticker END, ', ')
                                                     AS titres_bilan_sans_equity
FROM ranked r
LEFT JOIN postes p USING (ticker, frequency, period_end)
GROUP BY ALL
ORDER BY r.frequency, r.rang;
