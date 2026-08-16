import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Custom-Mojo | Options Strategy Suite",
  description: "Indian options analytics and constraint-solving strategy discovery",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-bg text-slate-100">
        <header className="border-b border-border">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
            <div className="flex items-center gap-2">
              <span className="text-lg font-semibold tracking-tight">Custom-Mojo</span>
              <span className="rounded bg-panel px-2 py-0.5 text-xs text-muted">Options Suite</span>
            </div>
            <nav className="flex gap-1 rounded-lg border border-border bg-panel p-1 text-sm">
              <Link href="/research" className="rounded-md px-3 py-1.5 hover:bg-white/5">
                Research Mode
              </Link>
              <Link href="/strategy" className="rounded-md px-3 py-1.5 hover:bg-white/5">
                Strategy Command Mode
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-6 py-6">{children}</main>
      </body>
    </html>
  );
}
