-- Migration: 0027_add_last_activity_to_users.sql
-- Description: Adds last_activity column to users table to enforce auto-logout after 20 minutes of inactivity.

ALTER TABLE users ADD COLUMN last_activity TIMESTAMP WITH TIME ZONE;
