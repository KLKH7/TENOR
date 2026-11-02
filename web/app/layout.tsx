import type { Metadata } from "next";
import { Inter, Newsreader } from "next/font/google";

import "./globals.css";

// Newsreader at low optical size gives the soft, low-contrast serif of Granola's titles.
const serif = Newsreader({ subsets: ["latin"], variable: "--font-serif-face", weight: ["400"] });
const sans = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Concentrate",
  description: "Compressed, cited, entity-linked memory over meeting notes (research prototype).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${serif.variable} ${sans.variable}`}>
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
