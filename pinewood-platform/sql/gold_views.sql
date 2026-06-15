-- 1. Monthly occupancy rate by community ------------------------------
CREATE OR REPLACE VIEW gold.vw_monthly_occupancy AS
SELECT o.community_id,
       c.community_name,
       c.state,
       c.region,
       o.month_start,
       o.total_units,
       o.occupied_units,
       o.occupancy_rate
FROM gold.fact_occupancy_monthly o
JOIN gold.dim_community c USING (community_id)
ORDER BY o.community_id, o.month_start;

-- 2. Average length of stay by care level, residents discharged in the
--    last 12 months ---------------------------------------------------
CREATE OR REPLACE VIEW gold.vw_avg_los_by_care_level AS
SELECT COALESCE(care_level, 'Unknown')      AS care_level,
       COUNT(*)                             AS discharges,
       ROUND(AVG(los_days), 1)              AS avg_los_days,
       ROUND(MIN(los_days), 0)              AS min_los_days,
       ROUND(MAX(los_days), 0)              AS max_los_days
FROM gold.fact_moveouts
WHERE move_out_date BETWEEN DATE '2024-07-01' AND DATE '2025-06-30'
  AND los_days IS NOT NULL
GROUP BY 1
ORDER BY avg_los_days DESC;

-- 3. Top three move-out reasons by community, trailing 12 months,
--    as a percentage of total move-outs ------------------------------
CREATE OR REPLACE VIEW gold.vw_top3_moveout_reasons AS
WITH base AS (
    SELECT community_id, COALESCE(move_out_reason, 'Unknown') AS reason
    FROM gold.fact_moveouts
    WHERE move_out_date >= DATE '2024-07-01'
), counts AS (
    SELECT community_id,
           reason,
           COUNT(*)                                            AS move_outs,
           SUM(COUNT(*)) OVER (PARTITION BY community_id)       AS total_move_outs
    FROM base
    GROUP BY community_id, reason
), ranked AS (
    SELECT community_id, reason, move_outs,
           ROUND(100.0 * move_outs / total_move_outs, 1)        AS pct_of_total,
           ROW_NUMBER() OVER (PARTITION BY community_id ORDER BY move_outs DESC) AS rn
    FROM counts
)
SELECT community_id, reason, move_outs, pct_of_total
FROM ranked
WHERE rn <= 3
ORDER BY community_id, move_outs DESC;

-- 4. Labor cost per resident-day by community by month ----------------
CREATE OR REPLACE VIEW gold.vw_labor_cost_per_resident_day AS
WITH labor AS (
    SELECT community_id, month_start,
           SUM(labor_cost) AS labor_cost,
           SUM(hours_worked) AS hours_worked
    FROM gold.fact_labor_monthly
    GROUP BY community_id, month_start
), days AS (
    SELECT community_id, month_start, SUM(resident_days) AS resident_days
    FROM gold.fact_census_monthly
    GROUP BY community_id, month_start
)
SELECT l.community_id,
       l.month_start,
       ROUND(l.labor_cost, 2)                                      AS labor_cost,
       d.resident_days,
       ROUND(l.labor_cost / NULLIF(d.resident_days, 0), 2)         AS labor_cost_per_resident_day
FROM labor l
JOIN days d USING (community_id, month_start)
ORDER BY l.community_id, l.month_start;

-- 5. Incident rate per 100 resident-days by community and care level --
CREATE OR REPLACE VIEW gold.vw_incident_rate AS
WITH inc AS (
    SELECT community_id, care_level, COUNT(*) AS incidents
    FROM gold.fact_incidents
    GROUP BY community_id, care_level
), days AS (
    SELECT community_id, care_level, SUM(resident_days) AS resident_days
    FROM gold.fact_census_monthly
    GROUP BY community_id, care_level
)
SELECT d.community_id,
       d.care_level,
       COALESCE(i.incidents, 0)                                              AS incidents,
       d.resident_days,
       ROUND(100.0 * COALESCE(i.incidents, 0) / NULLIF(d.resident_days, 0), 3)
                                                                             AS incidents_per_100_resident_days
FROM days d
LEFT JOIN inc i USING (community_id, care_level)
ORDER BY d.community_id, d.care_level;

-- 6. Residents whose acuity increased by >= 2 points within a 90-day
--    window — candidate list for a care-level review -----------------
CREATE OR REPLACE VIEW gold.vw_acuity_escalation_candidates AS
SELECT DISTINCT
       a1.resident_id,
       a1.community_id,
       a1.month_start                       AS from_month,
       a1.acuity_score                      AS from_acuity,
       a2.month_start                       AS to_month,
       a2.acuity_score                      AS to_acuity,
       (a2.acuity_score - a1.acuity_score)  AS acuity_increase
FROM gold.fact_resident_acuity_monthly a1
JOIN gold.fact_resident_acuity_monthly a2
  ON a1.resident_id = a2.resident_id
 AND a2.month_start >  a1.month_start
 AND a2.month_start <= a1.month_start + INTERVAL '90' DAY
WHERE (a2.acuity_score - a1.acuity_score) >= 2
ORDER BY acuity_increase DESC, resident_id;
