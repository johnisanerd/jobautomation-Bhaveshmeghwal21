import type { Metadata } from "next"
import "./globals.css"

export const metadata: Metadata = {
  title: "OpenClaw",
  description: "Job automation dashboard",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-gray-950 text-gray-100 min-h-screen font-sans">
        <nav className="border-b border-gray-800 px-6 py-3 flex items-center gap-6">
          <span className="font-bold text-white text-lg">OpenClaw</span>
          <a href="/" className="text-gray-400 hover:text-white text-sm transition-colors">
            Explore
          </a>
          <a href="/resumes" className="text-gray-400 hover:text-white text-sm transition-colors">
            Resumes
          </a>
          <a href="/settings" className="text-gray-400 hover:text-white text-sm transition-colors">
            Settings
          </a>
        </nav>
        <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
      </body>
    </html>
  )
}
