-- OpenClaw Supabase Schema
-- Run this in: Supabase Dashboard → SQL Editor → New Query

-- Jobs table: all scraped + scored jobs from all tiers
CREATE TABLE IF NOT EXISTS jobs (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title          TEXT NOT NULL,
  company        TEXT,
  url            TEXT UNIQUE NOT NULL,
  source         TEXT,
  location       TEXT,
  description    TEXT,
  score          INTEGER,
  graph_overlap  INTEGER,
  matched_skills TEXT[],
  reason         TEXT,
  posted_date    TEXT,
  tab            TEXT,        -- 'tier1' | 'tier2' | 'tier3' | 'posts'
  status         TEXT DEFAULT 'scored',
  logged_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS jobs_score_idx     ON jobs (score DESC);
CREATE INDEX IF NOT EXISTS jobs_tab_idx       ON jobs (tab);
CREATE INDEX IF NOT EXISTS jobs_source_idx    ON jobs (source);
CREATE INDEX IF NOT EXISTS jobs_logged_at_idx ON jobs (logged_at DESC);

-- Resumes table: Supabase Storage URLs for generated PDFs
CREATE TABLE IF NOT EXISTS resumes (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id            UUID REFERENCES jobs(id) ON DELETE CASCADE,
  resume_url        TEXT,
  cover_letter_url  TEXT,
  generated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS resumes_job_id_idx     ON resumes (job_id);
CREATE INDEX IF NOT EXISTS resumes_generated_idx  ON resumes (generated_at DESC);

-- Run logs: tracks explore run history for the UI
CREATE TABLE IF NOT EXISTS run_logs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  command     TEXT NOT NULL,   -- 'explore' | 'explore-tier2' | 'explore-tier3' | 'explore-posts'
  status      TEXT DEFAULT 'running',   -- running | done | failed
  started_at  TIMESTAMPTZ DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  jobs_found  INTEGER DEFAULT 0,
  message     TEXT
);

CREATE INDEX IF NOT EXISTS run_logs_started_idx ON run_logs (started_at DESC);

-- Enable Row Level Security (open read for the anon key used by frontend)
ALTER TABLE jobs      ENABLE ROW LEVEL SECURITY;
ALTER TABLE resumes   ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_logs  ENABLE ROW LEVEL SECURITY;

-- Allow frontend (anon key) to read all rows
CREATE POLICY "public read jobs"     ON jobs      FOR SELECT USING (true);
CREATE POLICY "public read resumes"  ON resumes   FOR SELECT USING (true);
CREATE POLICY "public read run_logs" ON run_logs  FOR SELECT USING (true);

-- Only the service role (backend) can write
CREATE POLICY "service write jobs"     ON jobs      FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "service write resumes"  ON resumes   FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "service write run_logs" ON run_logs  FOR ALL USING (auth.role() = 'service_role');

-- Storage bucket for resume PDFs
-- Run separately in Dashboard → Storage → New bucket: "resumes" (public)
