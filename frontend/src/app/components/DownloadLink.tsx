"use client";

import { AnchorHTMLAttributes, ReactNode } from "react";
import { useMask } from "@/app/providers";
import { DOWNLOAD_DISABLED_TITLE } from "@/lib/mask";

interface DownloadLinkProps extends Pick<AnchorHTMLAttributes<HTMLAnchorElement>, "download"> {
  href: string;
  className?: string;
  style?: React.CSSProperties;
  children: ReactNode;
  /** マスクとは別の理由での無効化（ファイル未生成など）。マスクと OR で効く */
  disabled?: boolean;
  /** 無効化されていないときの title */
  title?: string;
}

/**
 * ファイルをダウンロードする `<a>`。**マスク ON 中は一律で無効化する**
 * （29番: 壊れるものだけを止めると、押せるものと押せないものが混在して危険なため）。
 * ダウンロード導線は必ずこのコンポーネントを通すこと。
 */
export default function DownloadLink({
  href, className, style, download, children, disabled, title,
}: DownloadLinkProps) {
  const { masked } = useMask();
  const blocked = masked || disabled;

  if (blocked) {
    return (
      <span
        className={`${className ?? ""} pointer-events-none opacity-40 cursor-not-allowed`}
        style={style}
        title={masked ? DOWNLOAD_DISABLED_TITLE : title}
        aria-disabled="true"
      >
        {children}
      </span>
    );
  }

  return (
    <a href={href} download={download} className={className} style={style} title={title}>
      {children}
    </a>
  );
}
