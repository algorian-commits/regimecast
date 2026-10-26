import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Nav } from "@/components/nav";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "RegimeCast", template: "%s · RegimeCast" },
  description: "Explainable crypto risk co-pilot for BTC, ETH and SOL perpetual futures. Paper trading only.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

// Runs before first paint so the saved (or system) theme never flashes the wrong colours.
const themeScript = `try{var t=localStorage.getItem("theme");if(t==="dark"||(!t&&matchMedia("(prefers-color-scheme: dark)").matches))document.documentElement.classList.add("dark")}catch(e){}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-full flex flex-col font-sans">
        <Nav />
        <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:py-8">{children}</main>
        <footer className="border-t border-border px-4 py-4 text-center text-xs text-muted">
          RegimeCast · Final Year Project, SZABIST Islamabad · Paper trading only. Not financial advice.
        </footer>
      </body>
    </html>
  );
}
