-- ---------- Dimensions ------------------------------------------------

-- One row per community. Region is derived from state
-- (OR=Pacific Northwest, AZ=Southwest, TX=South).
CREATE TABLE dim_community (
    community_id   VARCHAR PRIMARY KEY,
    community_name VARCHAR NOT NULL,
    city           VARCHAR,
    state          VARCHAR NOT NULL,   -- OR | AZ | TX
    region         VARCHAR NOT NULL    -- Pacific Northwest | Southwest | South
);

-- One row per calendar day in the analysis window. month_start is the
-- conformed monthly key used by the monthly fact tables.
CREATE TABLE dim_date (
    date_key    INTEGER PRIMARY KEY,   -- YYYYMMDD
    date        DATE NOT NULL,
    year        SMALLINT,
    month       SMALLINT,
    month_name  VARCHAR,
    quarter     SMALLINT,
    day         SMALLINT,
    month_start DATE
);

-- Canonical care levels and their short codes.
CREATE TABLE dim_care_level (
    care_level VARCHAR PRIMARY KEY,    -- Independent Living | Assisted Living | Memory Care
    care_code  VARCHAR NOT NULL        -- IL | AL | MC
);

-- One row per unit (latest snapshot). care_level conformed from unit_type.
CREATE TABLE dim_unit (
    unit_key     INTEGER PRIMARY KEY,
    unit_id      VARCHAR UNIQUE NOT NULL,
    community_id VARCHAR NOT NULL REFERENCES dim_community(community_id),
    unit_type    VARCHAR,              -- IL | AL | MC
    care_level   VARCHAR REFERENCES dim_care_level(care_level),
    monthly_rent DOUBLE
);

-- One current row per resident (latest snapshot attributes).
CREATE TABLE dim_resident (
    resident_key       INTEGER PRIMARY KEY,
    resident_id        VARCHAR UNIQUE NOT NULL,
    community_id       VARCHAR NOT NULL REFERENCES dim_community(community_id),
    first_name         VARCHAR,
    last_name          VARCHAR,
    dob                DATE,
    gender             VARCHAR,
    admit_date         DATE,
    discharge_date     DATE,
    current_care_level VARCHAR REFERENCES dim_care_level(care_level)
);

-- SCD TYPE 2 on resident care level. A resident who moves Assisted Living
-- -> Memory Care produces TWO rows with non-overlapping effective windows.
-- end_date NULL + is_current TRUE marks the active row.
CREATE TABLE dim_resident_care_scd2 (
    care_sk        INTEGER PRIMARY KEY,
    resident_id    VARCHAR NOT NULL REFERENCES dim_resident(resident_id),
    community_id   VARCHAR NOT NULL REFERENCES dim_community(community_id),
    care_level     VARCHAR NOT NULL REFERENCES dim_care_level(care_level),
    effective_date DATE NOT NULL,
    end_date       DATE,               -- NULL = open / current
    is_current     BOOLEAN NOT NULL
    -- Grain: one row per resident per continuous care-level period.
);

-- ---------- Fact tables ----------------------------------------------

-- Grain: one row per community per month.
-- Measures: total_units (capacity), occupied_units, occupancy_rate.
CREATE TABLE fact_occupancy_monthly (
    community_id   VARCHAR NOT NULL REFERENCES dim_community(community_id),
    month_start    DATE    NOT NULL,
    total_units    INTEGER,
    occupied_units INTEGER,
    occupancy_rate DOUBLE,
    PRIMARY KEY (community_id, month_start)
);

-- Grain: one row per community per care_level per month.
-- Measures: resident_days, distinct_residents, avg_daily_census.
-- resident_days is the conformed denominator for labor-cost-per-day and
-- incident-rate metrics.
CREATE TABLE fact_census_monthly (
    community_id       VARCHAR NOT NULL REFERENCES dim_community(community_id),
    care_level         VARCHAR REFERENCES dim_care_level(care_level),
    month_start        DATE    NOT NULL,
    resident_days      INTEGER,
    distinct_residents INTEGER,
    days_in_month      INTEGER,
    avg_daily_census   DOUBLE,
    PRIMARY KEY (community_id, care_level, month_start)
);

-- Grain: one row per community per role per month.
-- Measures: shift_count, hours_worked, labor_cost.
CREATE TABLE fact_labor_monthly (
    community_id VARCHAR NOT NULL REFERENCES dim_community(community_id),
    month_start  DATE    NOT NULL,
    role         VARCHAR NOT NULL,
    shift_count  INTEGER,
    hours_worked DOUBLE,
    labor_cost   DOUBLE,
    PRIMARY KEY (community_id, month_start, role)
);

-- Grain: one row per incident.
CREATE TABLE fact_incidents (
    incident_id   VARCHAR PRIMARY KEY,
    resident_id   VARCHAR REFERENCES dim_resident(resident_id),
    community_id  VARCHAR NOT NULL REFERENCES dim_community(community_id),
    incident_date DATE,
    month_start   DATE,
    incident_type VARCHAR,
    severity      INTEGER,             -- 1..5
    care_level    VARCHAR REFERENCES dim_care_level(care_level), -- as of incident date (SCD2)
    reported_by   VARCHAR
);

-- Grain: one row per completed lease (move-out recorded).
CREATE TABLE fact_moveouts (
    lease_id        VARCHAR PRIMARY KEY,
    resident_id     VARCHAR REFERENCES dim_resident(resident_id),
    community_id    VARCHAR NOT NULL REFERENCES dim_community(community_id),
    unit_id         VARCHAR REFERENCES dim_unit(unit_id),
    move_in_date    DATE,
    move_out_date   DATE,
    move_out_month  DATE,
    move_out_reason VARCHAR,
    los_days        INTEGER,           -- length of stay
    care_level      VARCHAR REFERENCES dim_care_level(care_level)
);

-- Grain: one row per Google review.
CREATE TABLE fact_reviews (
    review_id    VARCHAR PRIMARY KEY,
    community_id VARCHAR NOT NULL REFERENCES dim_community(community_id),
    review_date  DATE,
    month_start  DATE,
    rating       INTEGER,             -- 1..5
    has_response BOOLEAN
);

-- Grain: one row per HubSpot sales lead.
CREATE TABLE fact_leads (
    lead_id       VARCHAR PRIMARY KEY,
    community_id  VARCHAR NOT NULL REFERENCES dim_community(community_id),
    lead_source   VARCHAR,
    created_date  DATE,
    created_month DATE,
    tour_date     DATE,
    deposit_date  DATE,
    move_in_date  DATE,
    status        VARCHAR,             -- Won | Lost | Open
    lost_reason   VARCHAR,
    toured        BOOLEAN,
    deposited     BOOLEAN
);

-- Grain: one row per resident per month (acuity time series).
-- Feeds the acuity-escalation candidate view.
CREATE TABLE fact_resident_acuity_monthly (
    resident_id   VARCHAR NOT NULL REFERENCES dim_resident(resident_id),
    community_id  VARCHAR NOT NULL REFERENCES dim_community(community_id),
    month_start   DATE NOT NULL,
    acuity_score  INTEGER,            -- 1..10
    PRIMARY KEY (resident_id, month_start)
);
