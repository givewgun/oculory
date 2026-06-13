-- Dedicated least-privilege monitoring role for postgres-exporter.
-- Run once against gunvest-db, e.g.:
--   sudo docker exec -i gunvest-db psql -U <DB_USER> -d <DB_NAME> < create-pg-monitor.sql
-- Then set PG_EXPORTER_DSN in .env to use oculory_ro with this password.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'oculory_ro') THEN
    CREATE ROLE oculory_ro LOGIN PASSWORD 'CHANGE_ME';
  END IF;
END
$$;

-- pg_monitor grants read access to all the pg_stat_* views the exporter needs.
GRANT pg_monitor TO oculory_ro;
GRANT CONNECT ON DATABASE gunvest TO oculory_ro;
