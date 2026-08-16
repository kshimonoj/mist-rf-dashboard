import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Mist RF Dashboard",
  description: "HPE Mist AP監視ダッシュボード",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <head>
        {/* テーマフラッシュ防止: ハイドレーション前にlocalStorageから読み込む */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){var t=localStorage.getItem('theme');document.documentElement.setAttribute('data-theme',t==='light'?'light':'dark');})()`,
          }}
        />
      </head>
      <body className="min-h-screen grid-bg" style={{ backgroundColor: "var(--bg-primary)" }}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
