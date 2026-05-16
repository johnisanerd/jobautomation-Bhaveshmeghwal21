"use client"

import { useEffect, useState } from "react"
import { fetchRuns } from "@/lib/api"
import type { Run } from "@/lib/api"

function statusColor(s: string) {
  if (s === "done") return "text-green-400"
  if (s === "failed") return "text-red-400"
  return "text-yellow-400"
}

function fmt(iso: string | null) {
  if (!iso) return "—"
  return new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })
}

export default function RunStatus({ refresh }: { refresh: number }) {
  const [runs, setRuns] = useState<Run[]>([])

  useEffect(() => {
    fetchRuns(5).then(setRuns).catch(() => {})
  }, [refresh])

  if (!runs.length) return null

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <h2 className="text-sm font-semibold text-gray-300 mb-3">Recent Runs</h2>
      <div className="flex flex-col gap-1.5">
        {runs.map((r) => (
          <div key={r.id} className="flex items-center gap-3 text-xs">
            <span className={`w-14 font-medium ${statusColor(r.status)}`}>{r.status}</span>
            <span className="text-gray-300 capitalize">{r.command.replace("-", " ")}</span>
            <span className="text-gray-500">{fmt(r.started_at)}</span>
            {r.jobs_found > 0 && (
              <span className="text-gray-400">{r.jobs_found} jobs</span>
            )}
            {r.message && r.status === "failed" && (
              <span className="text-red-400 truncate max-w-xs">{r.message}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
