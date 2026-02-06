-- Add temporary patient fields
ALTER TABLE carteirinhas
ADD COLUMN is_temporary BOOLEAN DEFAULT FALSE,
ADD COLUMN expires_at TIMESTAMP WITH TIME ZONE;

-- Index for faster cleanup queries
CREATE INDEX idx_carteirinhas_temp_expiry ON carteirinhas(is_temporary, expires_at);
