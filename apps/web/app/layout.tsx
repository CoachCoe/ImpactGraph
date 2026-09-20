import type { Metadata } from "next";
import { Header } from "@/components/Header";
import { SessionProvider } from "@/components/SessionProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: "ImpactGraph — Verifiable impact",
  description: "Inspect the chain of trust behind real-world impact.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <SessionProvider>
          <Header />
          <main>{children}</main>
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
