import type { Metadata, Viewport } from "next";
import { Barlow, Barlow_Condensed } from "next/font/google";
import "./globals.css";
import { Header } from "@/components/Header";

const barlow = Barlow({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-barlow",
  display: "swap",
});
const condensed = Barlow_Condensed({
  subsets: ["latin"],
  weight: ["600", "700"],
  variable: "--font-barlow-condensed",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "Matchplan — FPL decisions from your team ID", template: "%s · Matchplan" },
  description:
    "Weekly Fantasy Premier League recommendations from public data: transfers, lineup, captain and chips, with the uncertainty shown.",
};

export const viewport: Viewport = { themeColor: "#136f43", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-GB" className={`${barlow.variable} ${condensed.variable}`}>
      <body className="flex min-h-dvh flex-col">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-paper focus:px-3 focus:py-2"
        >
          Skip to content
        </a>
        <Header />
        <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 pb-16 pt-6 sm:px-6">
          {children}
        </main>
        <footer className="border-t border-line bg-paper">
          <div className="mx-auto max-w-6xl px-4 py-6 text-sm text-slate sm:px-6">
            <p>
              Matchplan reads FPL&apos;s public data only. It never signs in to FPL or changes your team.
              Not affiliated with the Premier League or Fantasy Premier League.
            </p>
            <p className="mt-1">
              Forecasts are probabilities, not promises: football is uncertain and no plan guarantees
              points or rank.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
