"use client"

import { useEffect, useState, useCallback } from "react"
import { fetchJobs } from "@/lib/api"
import type { Job } from "@/lib/api"
import JobCard from "@/components/JobCard"
import ExploreControls from "@/components/ExploreControls"
import RunStatus from "@/components/RunStatus"

const TABS = ["", "tier1", "tier2", "tier3", "posts"] as const
const TAB_LABELS: Record<string, string> = {
  "": "All",
  tier1: "Tier 1+2",
  tier2: "Tier 2",
  tier3: "Companies",
  posts: "Posts",
}

const PAGE_SIZE = 30

export default function ExplorePage() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<string>("")
  const [minScore, setMinScore] = useState(0)
  const [keyword, setKeyword] = useState("")
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(true)
  const [runRefresh, setRunRefresh] = useState(0)

  const load = useCallback(
    async (reset: boolean) => {
      setLoading(true)
      try {
        const next = reset ? 0 : offset
        const data = await fetchJobs({
          tab: tab || undefined,
          min_score: minScore || undefined,
          keyword: keyword.trim() || undefined,
          limit: PAGE_SIZE,
          offset: next,
        })
        setJobs(reset ? data : (prev) => [...prev, ...data])
        setOffset(next + data.length)
        setHasMore(data.length === PAGE_SIZE)
      } catch {
        setJobs([])
      } finally {
        setLoading(false)
      }
    },
    [tab, minScore, keyword, offset]
  )

  useEffect(() => {
    setOffset(0)
    setHasMore(true)
    load(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, minScore, keyword])

  return (
    <div className="flex flex-col gap-5">
      <ExploreControls onStarted={() => setRunRefresh((n) => n + 1)} />
      <RunStatus refresh={runRefresh} />

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex rounded overflow-hidden border border-gray-700">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-3 py-1.5 text-xs transition-colors ${
                tab === t ? "bg-blue-700 text-white" : "bg-gray-900 text-gray-400 hover:text-white"
              }`}
            >
              {TAB_LABELS[t]}
            </button>
          ))}
        </div>

        <select
          value={minScore}
          onChange={(e) => setMinScore(Number(e.target.value))}
          className="bg-gray-900 border border-gray-700 text-gray-300 text-xs rounded px-2 py-1.5"
        >
          <option value={0}>Any score</option>
          <option value={50}>50+</option>
          <option value={70}>70+</option>
          <option value={80}>80+</option>
          <option value={85}>85+</option>
          <option value={90}>90+</option>
        </select>

        <input
          type="text"
          placeholder="Search title..."
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          className="bg-gray-900 border border-gray-700 text-gray-300 text-xs rounded px-3 py-1.5 w-48 placeholder-gray-600"
        />

        <span className="text-xs text-gray-500">{jobs.length} jobs shown</span>
      </div>

      {/* Job grid */}
      {loading && jobs.length === 0 ? (
        <div className="text-gray-500 text-sm py-12 text-center">Loading jobs...</div>
      ) : jobs.length === 0 ? (
        <div className="text-gray-500 text-sm py-12 text-center">
          No jobs found — run an explore to populate the database.
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {jobs.map((job) => (
              <JobCard key={job.id} job={job} />
            ))}
          </div>
          {hasMore && (
            <button
              onClick={() => load(false)}
              disabled={loading}
              className="mx-auto text-xs text-gray-400 hover:text-white border border-gray-700 px-4 py-2 rounded transition-colors disabled:opacity-40"
            >
              {loading ? "Loading…" : "Load more"}
            </button>
          )}
        </>
      )}
    </div>
  )
}
