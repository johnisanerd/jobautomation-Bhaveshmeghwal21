const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

export interface Job {
  id: string
  title: string
  company: string
  url: string
  source: string
  location: string
  description: string
  score: number
  graph_overlap: number
  matched_skills: string[]
  reason: string
  posted_date: string
  tab: string
  status: string
  logged_at: string
}

export interface Resume {
  id: string
  job_id: string
  resume_url: string
  cover_letter_url: string
  generated_at: string
  jobs?: { title: string; company: string; url: string; score: number }
}

export interface Run {
  id: string
  command: string
  status: "running" | "done" | "failed"
  started_at: string
  finished_at: string | null
  jobs_found: number
  message: string
}

export async function fetchJobs(params: {
  tab?: string
  min_score?: number
  source?: string
  keyword?: string
  limit?: number
  offset?: number
}): Promise<Job[]> {
  const q = new URLSearchParams()
  if (params.tab) q.set("tab", params.tab)
  if (params.min_score) q.set("min_score", String(params.min_score))
  if (params.source) q.set("source", params.source)
  if (params.keyword) q.set("keyword", params.keyword)
  if (params.limit) q.set("limit", String(params.limit))
  if (params.offset) q.set("offset", String(params.offset))
  const res = await fetch(`${API}/jobs?${q}`, { cache: "no-store" })
  if (!res.ok) throw new Error("Failed to fetch jobs")
  return res.json()
}

export async function fetchRuns(limit = 10): Promise<Run[]> {
  const res = await fetch(`${API}/runs?limit=${limit}`, { cache: "no-store" })
  if (!res.ok) return []
  return res.json()
}

export async function fetchResumes(limit = 50): Promise<Resume[]> {
  const res = await fetch(`${API}/resumes?limit=${limit}`, { cache: "no-store" })
  if (!res.ok) return []
  return res.json()
}

export async function startExplore(tier: string): Promise<{ run_id: string; status: string }> {
  const res = await fetch(`${API}/explore/${tier}`, { method: "POST" })
  if (!res.ok) throw new Error("Failed to start explore")
  return res.json()
}

export async function generateResume(jobId: string): Promise<{
  status: string
  resume_url?: string
  cover_letter_url?: string
}> {
  const res = await fetch(`${API}/resume/${jobId}`, { method: "POST" })
  if (!res.ok) throw new Error("Failed to request resume generation")
  return res.json()
}

export async function pollResume(jobId: string): Promise<{
  status: string
  resume_url?: string
  cover_letter_url?: string
}> {
  const res = await fetch(`${API}/resume/${jobId}`, { cache: "no-store" })
  if (!res.ok) return { status: "not_generated" }
  return res.json()
}
