import type { Metadata } from "next";
import { IBM_Plex_Mono, Inter, Space_Grotesk } from "next/font/google";
import { Header } from "@/components/Header";
import { SessionProvider } from "@/components/SessionProvider";
import "./globals.css";

// Inter and Space Grotesk are variable fonts, so they load their whole weight axis and
// must not be given a `weight`. IBM Plex Mono is static and has to name its weights.
const sans = Inter({ subsets: ["latin"], variable: "--font-sans-face", display: "swap" });

const display = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-display-face",
  display: "swap",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono-face",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ImpactGraph — Verifiable impact",
  description: "Inspect the chain of trust behind real-world impact.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${sans.variable} ${display.variable} ${mono.variable}`}>
      <body>
        <SessionProvider>
          <a className="skipLink" href="#main">
            Skip to content
          </a>
          <Header />
          <main id="main">{children}</main>
          <footer>
            ImpactGraph · Transparent provenance for real-world impact
            <span>
              This is demonstration data. You can read everything here without an
              account — that&rsquo;s deliberate.
            </span>
          </footer>
        </SessionProvider>
      </body>
    </html>
  );
}
