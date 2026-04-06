-- Initialisation script run once when the postgres container is first created.
-- Creates the `costs` schema used for testing.

CREATE SCHEMA IF NOT EXISTS costs;
