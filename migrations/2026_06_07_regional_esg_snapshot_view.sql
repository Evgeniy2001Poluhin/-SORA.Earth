-- VIEW: API карты РФ (map_russia.py) читает regional_esg_snapshot,
-- агрегатор пишет в region_esg_scores. VIEW маппит колонки.
CREATE OR REPLACE VIEW regional_esg_snapshot AS
SELECT region_code,
       env_score    AS e_score,
       social_score AS s_score,
       gov_score    AS g_score,
       total_score  AS score,
       confidence,
       ARRAY[]::text[] AS sources_used,
       updated_at   AS computed_at
FROM region_esg_scores;
