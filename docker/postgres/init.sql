-- Initialisation script run once when the postgres container is first created.
-- Creates the `finance` schema used for testing.

CREATE SCHEMA IF NOT EXISTS finance;
