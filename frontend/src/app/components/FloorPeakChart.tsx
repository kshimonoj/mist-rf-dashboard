"use client";

import {
  Bar, BarChart, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { FloorPeakMeta, FloorPeakRow, floorPeakModelColor } from "@/lib/api";

/**
 * フロア別ピーク時点の棒グラフ（案A: AP 名を左に**完全な形で**表示する）。
 *
 * - AP 名は省略・truncate しない。`STASW-05F-AP63-0181` と
 *   `STASW-05F-AP63E-0187` が並んだときに桁がずれないよう等幅フォントを使う。
 * - 棒の色はモデルで分ける。**色の定義はバックエンド（meta.model_colors）**を使い、
 *   ここで定義し直さない。辞書に無いモデルは灰色に落ちる。
 * - 凡例はグラフの上に自前で並べる（recharts の Legend は系列単位なので、
 *   1 系列をモデルで塗り分けたこのグラフではモデル名を出せない）。
 */

/** 1 行あたりの高さ（px）。AP 名が読める最小限 */
const ROW_HEIGHT = 26;
/** 軸・余白のぶん */
const CHART_PADDING = 48;
/** 等幅フォントの 1 文字あたりの目安幅（px）。AP 名を切らないために使う */
const CHAR_WIDTH = 7.2;
/** Y 軸の最小幅（px） */
const MIN_AXIS_WIDTH = 150;

const AXIS_FONT = {
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
  fontSize: 11,
};

function ChartTooltip({ active, payload }: {
  active?: boolean;
  payload?: { payload: FloorPeakRow }[];
}) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div
      className="border rounded-lg px-3 py-2 text-xs"
      style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
    >
      <p className="font-mono" style={{ color: "var(--text-primary)" }}>{row.ap_name}</p>
      <p style={{ color: "var(--text-secondary)" }}>
        {row.model || "(モデル不明)"} / {row.status || "-"}
      </p>
      <p style={{ color: "var(--cyan)" }}>接続端末数 {row.num_clients}（フロア内 {row.rank_in_floor} 位）</p>
      <p className="font-mono text-[10px]" style={{ color: "var(--text-muted)" }}>{row.mac}</p>
    </div>
  );
}

export default function FloorPeakChart({
  rows, meta,
}: {
  /** 表示するフロアの上位行（並び順は API が返した rank_in_floor の昇順） */
  rows: FloorPeakRow[];
  meta: FloorPeakMeta;
}) {
  if (rows.length === 0) {
    return (
      <div
        className="border rounded-lg py-12 text-center text-sm"
        style={{
          borderColor: "var(--border-cyan)",
          backgroundColor: "var(--bg-card)",
          color: "var(--text-muted)",
        }}
      >
        このフロアに AP がありません
      </div>
    );
  }

  // AP 名を切らないよう、最長の名前に合わせて軸の幅を決める（案A の要）
  const longest = rows.reduce((n, r) => Math.max(n, r.ap_name.length), 0);
  const axisWidth = Math.max(MIN_AXIS_WIDTH, Math.round(longest * CHAR_WIDTH) + 12);
  const models = Array.from(new Set(rows.map((r) => r.model)));

  return (
    <div
      className="border rounded-lg p-4"
      style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
    >
      {/* 凡例（このグラフに出ているモデルだけ） */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-3">
        <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>モデル:</span>
        {models.map((model) => (
          <span key={model} className="flex items-center gap-1.5 text-xs">
            <span
              className="inline-block w-3 h-3 rounded-sm"
              style={{ backgroundColor: floorPeakModelColor(model, meta) }}
            />
            <span style={{ color: "var(--text-secondary)" }}>{model || "(不明)"}</span>
          </span>
        ))}
      </div>

      <ResponsiveContainer width="100%" height={rows.length * ROW_HEIGHT + CHART_PADDING}>
        <BarChart
          data={rows}
          layout="vertical"
          margin={{ top: 4, right: 48, bottom: 4, left: 4 }}
        >
          <XAxis
            type="number"
            allowDecimals={false}
            tick={{ fill: "var(--text-muted)", fontSize: 10 }}
            axisLine={{ stroke: "var(--chart-grid)" }}
            tickLine={false}
          />
          <YAxis
            type="category"
            dataKey="ap_name"
            width={axisWidth}
            interval={0}
            tick={{ fill: "var(--text-primary)", ...AXIS_FONT }}
            axisLine={{ stroke: "var(--chart-grid)" }}
            tickLine={false}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--bg-hover)" }} />
          <Bar dataKey="num_clients" isAnimationActive={false} barSize={ROW_HEIGHT - 10}>
            {rows.map((row) => (
              <Cell key={row.mac || row.ap_name} fill={floorPeakModelColor(row.model, meta)} />
            ))}
            <LabelList
              dataKey="num_clients"
              position="right"
              style={{ fill: "var(--text-primary)", fontSize: 11 }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
