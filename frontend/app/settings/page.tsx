"use client"

import { useEffect, useState } from "react"

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

export default function SettingsPage() {
  const [keySet, setKeySet] = useState<boolean | null>(null)
  const [keyPreview, setKeyPreview] = useState("")
  const [newKey, setNewKey] = useState("")
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle")
  const [errorMsg, setErrorMsg] = useState("")

  useEffect(() => {
    fetch(`${API}/settings`)
      .then((r) => r.json())
      .then((d) => {
        setKeySet(d.apify_key_set)
        setKeyPreview(d.apify_key_preview)
      })
      .catch(() => setKeySet(false))
  }, [])

  async function handleSave() {
    if (!newKey.trim()) return
    setStatus("saving")
    setErrorMsg("")
    try {
      const res = await fetch(`${API}/settings/apify-key`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: newKey.trim() }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setKeySet(true)
      setKeyPreview(data.preview)
      setNewKey("")
      setStatus("saved")
    } catch (e: unknown) {
      setErrorMsg(e instanceof Error ? e.message : "Unknown error")
      setStatus("error")
    }
  }

  return (
    <div className="max-w-lg flex flex-col gap-6">
      <h1 className="text-lg font-semibold text-white">Settings</h1>

      {/* Apify Key Card */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 flex flex-col gap-4">
        <div>
          <h2 className="text-sm font-semibold text-gray-200">Apify API Key</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Used for LinkedIn post scraping. Each $5 credit top-up gives a new key — paste it here.
          </p>
        </div>

        <div className="flex items-center gap-2 text-xs">
          <span className="text-gray-400">Current key:</span>
          {keySet === null ? (
            <span className="text-gray-600">Loading…</span>
          ) : keySet ? (
            <span className="text-green-400 font-mono">{keyPreview}</span>
          ) : (
            <span className="text-red-400">Not set</span>
          )}
        </div>

        <div className="flex flex-col gap-2">
          <input
            type="text"
            placeholder="Paste new Apify API key here"
            value={newKey}
            onChange={(e) => { setNewKey(e.target.value); setStatus("idle") }}
            className="bg-gray-800 border border-gray-700 text-gray-200 text-sm rounded px-3 py-2 placeholder-gray-600 focus:outline-none focus:border-blue-600 font-mono"
          />
          <button
            onClick={handleSave}
            disabled={!newKey.trim() || status === "saving"}
            className="self-start text-sm bg-blue-700 hover:bg-blue-600 disabled:opacity-40 text-white px-4 py-1.5 rounded transition-colors"
          >
            {status === "saving" ? "Saving…" : "Save Key"}
          </button>
        </div>

        {status === "saved" && (
          <p className="text-xs text-green-400">Key updated — active immediately, persisted to .env.</p>
        )}
        {status === "error" && (
          <p className="text-xs text-red-400">{errorMsg || "Failed to update key."}</p>
        )}
      </div>

      <p className="text-xs text-gray-600">
        Get your Apify key at{" "}
        <a
          href="https://console.apify.com/account/integrations"
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-500 hover:underline"
        >
          console.apify.com → Integrations
        </a>
      </p>
    </div>
  )
}
