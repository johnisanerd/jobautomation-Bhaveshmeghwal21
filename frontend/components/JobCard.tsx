"use client"

import { useState } from "react"
import { generateResume, pollResume } from "@/lib/api"
import type { Job } from "@/lib/api"

interface Props {
  job: Job
}

function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 85 ? "bg-green-600" : score >= 70 ? "bg-yellow-600" : "bg-gray-600"
  return (
    <span className={`${color} text-white text-xs font-bold px-2 py-0.5 rounded`}>
      {score}
    </span>
  )
}

export default function JobCard({ job }: Props) {
  const [resumeState, setResumeState] = useState<
    "idle" | "generating" | "ready" | "error"
  >("idle")
  const [resumeUrl, setResumeUrl] = useState<string>("")
  const [coverUrl, setCoverUrl] = useState<string>("")

  async function handleGenerate() {
    setResumeState("generating")
    try {
      const res = await generateResume(job.id)
      if (res.status === "already_exists" && res.resume_url) {
        setResumeUrl(res.resume_url)
        setCoverUrl(res.cover_letter_url || "")
        setResumeState("ready")
        return
      }
      // Poll until ready (max ~2 min)
      for (let i = 0; i < 24; i++) {
        await new Promise((r) => setTimeout(r, 5000))
        const poll = await pollResume(job.id)
        if (poll.status === "ready" && poll.resume_url) {
          setResumeUrl(poll.resume_url)
          setCoverUrl(poll.cover_letter_url || "")
          setResumeState("ready")
          return
        }
      }
      setResumeState("error")
    } catch {
      setResumeState("error")
    }
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-semibold text-white hover:text-blue-400 transition-colors line-clamp-1"
          >
            {job.title}
          </a>
          <div className="text-gray-400 text-sm">{job.company}</div>
        </div>
        <ScoreBadge score={job.score} />
      </div>

      <div className="flex items-center gap-3 text-xs text-gray-500 flex-wrap">
        {job.location && <span>{job.location}</span>}
        {job.source && <span className="capitalize">{job.source}</span>}
        {job.graph_overlap != null && job.graph_overlap >= 0 && (
          <span>Graph: {job.graph_overlap}%</span>
        )}
        {job.tab && (
          <span className="bg-gray-800 px-1.5 py-0.5 rounded capitalize">{job.tab}</span>
        )}
      </div>

      {job.reason && (
        <p className="text-gray-400 text-xs line-clamp-2">{job.reason}</p>
      )}

      {job.matched_skills?.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {job.matched_skills.slice(0, 6).map((s) => (
            <span
              key={s}
              className="bg-blue-900/40 text-blue-300 text-xs px-1.5 py-0.5 rounded"
            >
              {s}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 mt-1">
        {resumeState === "idle" && (
          <button
            onClick={handleGenerate}
            className="text-xs bg-blue-700 hover:bg-blue-600 text-white px-3 py-1 rounded transition-colors"
          >
            Generate Resume
          </button>
        )}
        {resumeState === "generating" && (
          <span className="text-xs text-gray-400 animate-pulse">Generating...</span>
        )}
        {resumeState === "ready" && (
          <>
            <a
              href={resumeUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs bg-green-700 hover:bg-green-600 text-white px-3 py-1 rounded transition-colors"
            >
              Resume PDF
            </a>
            {coverUrl && (
              <a
                href={coverUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs bg-gray-700 hover:bg-gray-600 text-white px-3 py-1 rounded transition-colors"
              >
                Cover Letter
              </a>
            )}
          </>
        )}
        {resumeState === "error" && (
          <span className="text-xs text-red-400">Generation failed — check server logs</span>
        )}
      </div>
    </div>
  )
}
