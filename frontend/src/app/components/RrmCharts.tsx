"use client";

import {
  Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  RrmClassification, RrmHourlyBucket, RrmMeta, rrmClassColor,
} from "@/lib/api";

/**
 * RRM / RADAR 分析のグラフ（recharts）。**新しいライブラリは足さない。**
 *
 * - 時間帯別のチャネル変更回数 / インパクトは **積み上げ棒**。3 分類の内訳が
 *   分かることが目的なので、合計だけの棒にしない。
 * - 棒の色は **バックエンド（meta.class_colors）** を使う。ここで定義し直さない。
 * - 凡例は recharts の `Legend`（系列＝分類なので標準の凡例で足りる）。
 */

const CHART_HEIGHT = 260;
const AXIS_TICK = { fill: "var(--text-muted)", fontSize: 10 };

const cardStyle = { borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" };

const CLASS_LABELS: Record<string, string> = {
  RADAR: "RADAR（レーダー起因）",
  POST_RADAR: "POST_RADAR（レーダー後処理）",
  RRM: "RRM（その他）",
};

function classifications(meta: RrmMeta): RrmClassification[] {
  return (meta.classifications ?? ["RADAR", "POST_RADAR", "RRM"]) as RrmClassification[];
}

/** 期間が 1 日に収まるなら `HH:00`、またがるなら `MM/DD HH:00` */
function bucketLabel(bucket: string, singleDay: boolean): string {
  const time = bucket.slice(11, 16);
  return singleDay ? time : `${bucket.slice(5, 10)} ${time}`;
}

function ChartCard({
  title, note, children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="border rounded-lg p-4" style={cardStyle}>
      <p className="text-xs mb-1" style={{ color: "var(--cyan)" }}>{title}</p>
      {note && (
        <p className="text-[10px] mb-2" style={{ color: "var(--text-muted)" }}>{note}</p>
      )}
      {children}
    </div>
  );
}

function EmptyChart({ label }: { label: string }) {
  return (
    <div
      className="py-12 text-center text-sm"
      style={{ color: "var(--text-muted)", height: CHART_HEIGHT }}
    >
      {label}
    </div>
  );
}

function StackedTooltip({ active, payload, label, unit }: {
  active?: boolean;
  payload?: { name: string; value: number; color: string }[];
  label?: string;
  unit: string;
}) {
  if (!active || !payload?.length) return null;
  const total = payload.reduce((sum, p) => sum + (p.value ?? 0), 0);
  return (
    <div
      className="border rounded-lg px-3 py-2 text-xs"
      style={{ borderColor: "var(--border-cyan)", backgroundColor: "var(--bg-card)" }}
    >
      <p className="font-mono mb-1" style={{ color: "var(--text-primary)" }}>{label}</p>
      {payload.map((p) => (
        <p key={p.name} style={{ color: p.color }}>
          {p.name} {p.value} {unit}
        </p>
      ))}
      <p style={{ color: "var(--text-secondary)" }}>合計 {total} {unit}</p>
    </div>
  );
}

/** 時間帯別の積み上げ棒（`changes` = 変更回数 / `impact` = インパクト合計） */
function HourlyStack({
  meta, kind, unit,
}: {
  meta: RrmMeta;
  kind: "changes" | "impact";
  unit: string;
}) {
  const hourly: RrmHourlyBucket[] = meta.hourly ?? [];
  if (hourly.length === 0) return <EmptyChart label="この期間に集計できるバケットがありません" />;

  const days = new Set(hourly.map((h) => h.bucket.slice(0, 10)));
  const data = hourly.map((h) => ({
    ...h,
    label: bucketLabel(h.bucket, days.size <= 1),
  }));

  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="label"
          tick={AXIS_TICK}
          interval="preserveStartEnd"
          axisLine={{ stroke: "var(--chart-grid)" }}
          tickLine={false}
        />
        <YAxis
          allowDecimals={false}
          tick={AXIS_TICK}
          axisLine={{ stroke: "var(--chart-grid)" }}
          tickLine={false}
        />
        <Tooltip content={<StackedTooltip unit={unit} />} cursor={{ fill: "var(--bg-hover)" }} />
        <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-secondary)" }} />
        {classifications(meta).map((name) => (
          <Bar
            key={name}
            dataKey={`${kind}_${name}`}
            name={CLASS_LABELS[name] ?? name}
            stackId="rrm"
            isAnimationActive={false}
            fill={rrmClassColor(name, meta)}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

/** 分類別のインパクト合計（変更前に接続していた端末数の合計） */
function ClassImpact({ meta }: { meta: RrmMeta }) {
  const data = (meta.by_classification ?? []).map((item) => ({
    classification: item.classification,
    label: CLASS_LABELS[item.classification] ?? item.classification,
    impact_total: item.impact_total ?? 0,
  }));
  if (data.length === 0) return <EmptyChart label="集計できる分類がありません" />;

  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="classification" tick={AXIS_TICK}
               axisLine={{ stroke: "var(--chart-grid)" }} tickLine={false} />
        <YAxis allowDecimals={false} tick={AXIS_TICK}
               axisLine={{ stroke: "var(--chart-grid)" }} tickLine={false} />
        <Tooltip content={<StackedTooltip unit="台" />} cursor={{ fill: "var(--bg-hover)" }} />
        <Bar dataKey="impact_total" name="インパクト合計" isAnimationActive={false}>
          {data.map((row) => (
            <Cell key={row.classification} fill={rrmClassColor(row.classification, meta)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export default function RrmCharts({ meta }: { meta: RrmMeta }) {
  const bucketMinutes = Math.round((meta.bucket_seconds ?? 3600) / 60);
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <ChartCard
        title={`時間帯別のチャネル変更回数（${bucketMinutes} 分バケット・分類別の積み上げ）`}
        note="no-op（評価のみ）は含みません。指定期間の実時系列です（時刻で丸めた平均ではありません）"
      >
        <HourlyStack meta={meta} kind="changes" unit="件" />
      </ChartCard>

      <ChartCard
        title={`時間帯別のインパクト（${bucketMinutes} 分バケット・分類別の積み上げ）`}
        note="インパクト = チャネル変更の直前に接続していた端末数（impact_clients）の合計"
      >
        <HourlyStack meta={meta} kind="impact" unit="台" />
      </ChartCard>

      <ChartCard title="分類別のインパクト合計">
        <ClassImpact meta={meta} />
      </ChartCard>

      <ChartCard
        title={`AP 別のチャネル変更回数（上位 ${meta.top_ap_count ?? 30}）`}
        note="どの AP が頻繁にチャネルを変えているかを見るための一覧"
      >
        <div className="overflow-x-auto" style={{ maxHeight: CHART_HEIGHT }}>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-left" style={{ borderColor: "var(--chart-grid)" }}>
                {["AP", "サイト", "変更", "RADAR", "POST_RADAR", "RRM", "インパクト"].map((h) => (
                  <th key={h} className="py-1.5 px-2 font-normal whitespace-nowrap"
                      style={{ color: "var(--text-muted)" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(meta.by_ap ?? []).map((row) => (
                <tr key={`${row.ap_mac}/${row.ap_name}`} className="border-b"
                    style={{ borderColor: "var(--chart-grid)" }}>
                  <td className="py-1.5 px-2 font-mono whitespace-nowrap"
                      style={{ color: "var(--text-primary)" }}>{row.ap_name}</td>
                  <td className="py-1.5 px-2" style={{ color: "var(--text-secondary)" }}>{row.site_name}</td>
                  <td className="py-1.5 px-2 font-mono" style={{ color: "var(--cyan)" }}>{row.changes}</td>
                  <td className="py-1.5 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{row.changes_RADAR}</td>
                  <td className="py-1.5 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{row.changes_POST_RADAR}</td>
                  <td className="py-1.5 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{row.changes_RRM}</td>
                  <td className="py-1.5 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{row.impact_total}</td>
                </tr>
              ))}
              {(meta.by_ap ?? []).length === 0 && (
                <tr>
                  <td colSpan={7} className="py-6 text-center" style={{ color: "var(--text-muted)" }}>
                    チャネル変更がありません
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </ChartCard>
    </div>
  );
}
