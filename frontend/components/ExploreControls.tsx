"use client"

import { useState } from "react"
import { startExplore } from "@/lib/api"

const TIERS = [
  { id: "tier1", label: "Tier 1+2", desc: "LinkedIn, Naukri, Indeed + niche boards" },
  { id: "tier2", label: "Tier 2", desc: "Niche India boards only" },
  { id: "tier3", label: "Tier 3", desc: "Company career pages" },
  { id: "posts", label: "Posts", desc: "LinkedIn hiring posts" },
  { id: "all", label: "All", desc: "Full crawl — takes 30-60 min" },
]

export default function ExploreControls({ onStarted }: { onStarted: () => void }) {
  const [loading, setLoading] = useState<string | null>(null)
  const [lastRun, setLastRun] = useState<string | null>(null)

  async function handleExplore(tier: string) {
    setLoading(tier)
    try {
      const res = await startExplore(tier)
      setLastRun(`Started ${tier} run (ID: ${res.run_id.slice(0, 8)}…)`)
      onStarted()
    } catch {
      setLastRun(`Failed to start ${tier} run`)
    } finally {
      setLoading(null)
    }
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <h2 className="text-sm font-semibold text-gray-300 mb-3">Trigger Explore Run</h2>
      <div className="flex flex-wrap gap-2">
        {TIERS.map((t) => (
          <button
            key={t.id}
            onClick={() => handleExplore(t.id)}
            disabled={loading !== null}
            title={t.desc}
            className="text-xs px-3 py-1.5 rounded bg-gray-800 hover:bg-gray-700 disabled:opacity-40 transition-colors text-white"
          >
            {loading === t.id ? "Starting…" : t.label}
          </button>
        ))}
      </div>
      {lastRun && <p className="mt-2 text-xs text-gray-400">{lastRun}</p>}
    </div>
  )
}
