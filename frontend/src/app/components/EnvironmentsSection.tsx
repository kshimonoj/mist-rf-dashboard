"use client";

import { CheckCircle2, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import {
  activateCredential,
  createCredential,
  deleteCredential,
  fetchCredentials,
  updateCredential,
  type CredentialItem,
} from "@/lib/api";

const MIST_REGIONS = [
  { label: "Global 01", url: "https://api.mist.com/api/v1" },
  { label: "Global 02 (GC1)", url: "https://api.gc1.mist.com/api/v1" },
  { label: "Global 03 / APAC (AC2)", url: "https://api.ac2.mist.com/api/v1" },
  { label: "Global 04 (GC2)", url: "https://api.gc2.mist.com/api/v1" },
  { label: "Global 05 (GC4)", url: "https://api.gc4.mist.com/api/v1" },
  { label: "EMEA 01 (EU)", url: "https://api.eu.mist.com/api/v1" },
  { label: "EMEA 02 (GC3)", url: "https://api.gc3.mist.com/api/v1" },
  { label: "EMEA 03 (AC6)", url: "https://api.ac6.mist.com/api/v1" },
  { label: "EMEA 04 (GC6)", url: "https://api.gc6.mist.com/api/v1" },
  { label: "APAC 01 (AC5)", url: "https://api.ac5.mist.com/api/v1" },
  { label: "APAC 02 (GC5)", url: "https://api.gc5.mist.com/api/v1" },
  { label: "APAC 03 (GC7)", url: "https://api.gc7.mist.com/api/v1" },
] as const;

function urlToRegion(url: string): string {
  const match = MIST_REGIONS.find((r) => r.url === url);
  return match ? match.url : "custom";
}

function regionLabel(url: string): string {
  const match = MIST_REGIONS.find((r) => r.url === url);
  return match ? match.label : url;
}

interface FormState {
  id: number | null; // null = 新規追加
  name: string;
  token: string;
  orgId: string;
  region: string;
  customUrl: string;
}

const EMPTY_FORM: FormState = {
  id: null,
  name: "",
  token: "",
  orgId: "",
  region: "https://api.mist.com/api/v1",
  customUrl: "",
};

export default function EnvironmentsSection() {
  const { data, mutate } = useSWR("credentials", fetchCredentials);
  const items = data?.items ?? [];
  const active = items.find((c) => c.is_active);

  const [form, setForm] = useState<FormState | null>(null);
  const [settingsKey, setSettingsKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  // アクティベート確認モーダル
  const [activateTarget, setActivateTarget] = useState<CredentialItem | null>(null);
  const [clearLogs, setClearLogs] = useState(false);
  const [clearSnapshots, setClearSnapshots] = useState(false);
  const [activating, setActivating] = useState(false);

  const showToast = (msg: string, ok: boolean) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), ok ? 4000 : 6000);
  };

  const openNew = () => setForm({ ...EMPTY_FORM });

  const openEdit = (c: CredentialItem) => {
    const region = urlToRegion(c.mist_base_url);
    setForm({
      id: c.id,
      name: c.name,
      token: "",
      orgId: c.mist_org_id,
      region,
      customUrl: region === "custom" ? c.mist_base_url : "",
    });
  };

  const handleSave = async () => {
    if (!form) return;
    const baseUrl = form.region === "custom" ? form.customUrl.trim() : form.region;
    if (!form.name.trim() || !form.orgId.trim() || !baseUrl) {
      showToast("Name / Org ID / Base URL は必須です", false);
      return;
    }
    setSaving(true);
    try {
      if (form.id === null) {
        if (!form.token.trim()) {
          showToast("API Token は必須です", false);
          return;
        }
        await createCredential(
          {
            name: form.name.trim(),
            mist_api_token: form.token.trim(),
            mist_org_id: form.orgId.trim(),
            mist_base_url: baseUrl,
          },
          settingsKey || undefined
        );
        showToast("環境を追加しました", true);
      } else {
        await updateCredential(
          form.id,
          {
            name: form.name.trim(),
            ...(form.token.trim() ? { mist_api_token: form.token.trim() } : {}),
            mist_org_id: form.orgId.trim(),
            mist_base_url: baseUrl,
          },
          settingsKey || undefined
        );
        showToast("環境を更新しました", true);
      }
      await mutate();
      setForm(null);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "保存に失敗しました", false);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (c: CredentialItem) => {
    if (!window.confirm(`環境「${c.name}」を削除します。よろしいですか？`)) return;
    try {
      await deleteCredential(c.id, settingsKey || undefined);
      await mutate();
      showToast(`環境「${c.name}」を削除しました`, true);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "削除に失敗しました", false);
    }
  };

  const openActivate = (c: CredentialItem) => {
    setClearLogs(false);
    setClearSnapshots(false);
    setActivateTarget(c);
  };

  const handleActivate = async () => {
    if (!activateTarget) return;
    setActivating(true);
    try {
      const result = await activateCredential(
        activateTarget.id,
        { clear_logs: clearLogs, clear_snapshots: clearSnapshots },
        settingsKey || undefined
      );
      showToast(`環境を${result.activated}に切り替えました`, true);
      setActivateTarget(null);
      await mutate();
      // 新環境でポーリングが再開されるためホーム画面へ（フルリロードでキャッシュも破棄）
      setTimeout(() => {
        window.location.href = "/";
      }, 1200);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "切り替えに失敗しました", false);
      setActivating(false);
    }
  };

  const inputStyle = {
    borderColor: "var(--border-cyan)",
    color: "var(--text-primary)",
  } as const;

  return (
    <div
      className="rounded-lg p-4 space-y-4"
      style={{ backgroundColor: "var(--bg-hover)", border: "1px solid var(--border-cyan)" }}
    >
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-mono font-semibold tracking-widest" style={{ color: "var(--cyan)" }}>
          ENVIRONMENTS
        </h3>
        {active && (
          <span
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-mono border"
            style={{ borderColor: "var(--green)", color: "var(--green)", backgroundColor: "rgba(0,255,128,0.08)" }}
          >
            <CheckCircle2 className="w-3.5 h-3.5" />
            Active: {active.name}
          </span>
        )}
      </div>

      {/* 環境一覧テーブル */}
      <div className="overflow-x-auto border rounded-lg" style={{ borderColor: "var(--border-cyan)" }}>
        <table className="w-full text-xs font-mono">
          <thead>
            <tr style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border-cyan)" }}>
              <th className="text-left px-3 py-2">Name</th>
              <th className="text-left px-3 py-2">Base URL</th>
              <th className="text-left px-3 py-2">Org ID</th>
              <th className="text-left px-3 py-2">Token</th>
              <th className="text-center px-3 py-2">Active</th>
              <th className="text-right px-3 py-2">Actions</th>
            </tr>
          </thead>
          <tbody>
            {!data ? (
              <tr>
                <td colSpan={6} className="px-3 py-3 text-center" style={{ color: "var(--text-muted)" }}>
                  Loading...
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-3 py-3 text-center" style={{ color: "var(--text-muted)" }}>
                  環境が登録されていません
                </td>
              </tr>
            ) : (
              items.map((c) => (
                <tr key={c.id} style={{ borderBottom: "1px solid var(--chart-grid)", color: "var(--text-primary)" }}>
                  <td className="px-3 py-2 whitespace-nowrap">{c.name}</td>
                  <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                    {regionLabel(c.mist_base_url)}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                    {c.mist_org_id ? `${c.mist_org_id.slice(0, 8)}…` : "—"}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                    {c.mist_api_token || "—"}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {c.is_active && <CheckCircle2 className="w-4 h-4 inline" style={{ color: "var(--green)" }} />}
                  </td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    <button
                      onClick={() => openActivate(c)}
                      disabled={c.is_active}
                      className="px-2 py-1 rounded border mr-1.5 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                      style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
                    >
                      アクティベート
                    </button>
                    <button
                      onClick={() => openEdit(c)}
                      className="p-1 rounded border mr-1.5 transition-all align-middle"
                      style={{ borderColor: "var(--chart-grid)", color: "var(--text-secondary)" }}
                      title="編集"
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => handleDelete(c)}
                      disabled={c.is_active}
                      className="p-1 rounded border transition-all align-middle disabled:opacity-30 disabled:cursor-not-allowed"
                      style={{ borderColor: "var(--chart-grid)", color: "var(--red)" }}
                      title="削除"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Admin key (SETTINGS_SECRET) */}
      {data?.secret_required && (
        <div>
          <label className="block text-sm font-mono mb-1" style={{ color: "var(--text-secondary)" }}>
            Admin Key <span style={{ color: "var(--text-muted)" }}>(SETTINGS_SECRET)</span>
          </label>
          <input
            type="password"
            value={settingsKey}
            onChange={(e) => setSettingsKey(e.target.value)}
            placeholder="サーバー設定の SETTINGS_SECRET 値"
            className="w-full px-3 py-2 rounded border bg-transparent text-sm font-mono"
            style={inputStyle}
          />
        </div>
      )}

      {/* 新規追加ボタン / 追加・編集フォーム */}
      {!form ? (
        <button
          onClick={openNew}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-mono transition-all"
          style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
        >
          <Plus className="w-3.5 h-3.5" />
          新規追加
        </button>
      ) : (
        <div className="rounded-lg p-3 space-y-3 border" style={{ borderColor: "var(--border-cyan)" }}>
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono" style={{ color: "var(--cyan)" }}>
              {form.id === null ? "新規環境の追加" : `環境の編集: ${form.name}`}
            </span>
            <button onClick={() => setForm(null)} style={{ color: "var(--text-muted)" }}>
              <X className="w-4 h-4" />
            </button>
          </div>

          <div>
            <label className="block text-sm font-mono mb-1" style={{ color: "var(--text-secondary)" }}>
              Name
            </label>
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. Kyobashi"
              className="w-full px-3 py-2 rounded border bg-transparent text-sm font-mono"
              style={inputStyle}
            />
          </div>

          <div>
            <label className="block text-sm font-mono mb-1" style={{ color: "var(--text-secondary)" }}>
              API Token
            </label>
            <input
              type="password"
              value={form.token}
              onChange={(e) => setForm({ ...form, token: e.target.value })}
              placeholder={form.id === null ? "Mist API Token" : "変更しない場合は空欄"}
              className="w-full px-3 py-2 rounded border bg-transparent text-sm font-mono"
              style={inputStyle}
            />
          </div>

          <div>
            <label className="block text-sm font-mono mb-1" style={{ color: "var(--text-secondary)" }}>
              Org ID
            </label>
            <input
              type="text"
              value={form.orgId}
              onChange={(e) => setForm({ ...form, orgId: e.target.value })}
              placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              className="w-full px-3 py-2 rounded border bg-transparent text-sm font-mono"
              style={inputStyle}
            />
          </div>

          <div>
            <label className="block text-sm font-mono mb-1" style={{ color: "var(--text-secondary)" }}>
              Region
            </label>
            <select
              value={form.region}
              onChange={(e) => setForm({ ...form, region: e.target.value })}
              className="w-full px-3 py-2 rounded border bg-transparent text-sm font-mono"
              style={{ ...inputStyle, backgroundColor: "var(--bg-card)" }}
            >
              {MIST_REGIONS.map((r) => (
                <option key={r.url} value={r.url} style={{ backgroundColor: "var(--bg-card)" }}>
                  {r.label}
                </option>
              ))}
              <option value="custom" style={{ backgroundColor: "var(--bg-card)" }}>
                カスタム
              </option>
            </select>
            {form.region === "custom" ? (
              <input
                type="text"
                value={form.customUrl}
                onChange={(e) => setForm({ ...form, customUrl: e.target.value })}
                placeholder="https://api.example.mist.com/api/v1"
                className="w-full mt-2 px-3 py-2 rounded border bg-transparent text-sm font-mono"
                style={inputStyle}
              />
            ) : (
              <p className="text-xs mt-1 font-mono" style={{ color: "var(--text-muted)" }}>
                {form.region}
              </p>
            )}
          </div>

          <div className="flex justify-end gap-2">
            <button
              onClick={() => setForm(null)}
              className="px-3 py-1.5 rounded-lg border text-xs font-mono"
              style={{ borderColor: "var(--chart-grid)", color: "var(--text-muted)" }}
            >
              キャンセル
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-1.5 rounded-lg border text-xs font-mono transition-all disabled:opacity-40"
              style={{
                backgroundColor: "rgba(0,212,255,0.15)",
                borderColor: "var(--border-cyan)",
                color: "var(--cyan)",
              }}
            >
              {saving ? "Saving..." : form.id === null ? "追加" : "更新"}
            </button>
          </div>
        </div>
      )}

      {toast && (
        <div
          className="px-3 py-2 rounded border text-xs font-mono"
          style={{
            borderColor: toast.ok ? "var(--green)" : "var(--red)",
            color: toast.ok ? "var(--green)" : "var(--red)",
            backgroundColor: toast.ok ? "rgba(0,255,128,0.05)" : "rgba(255,68,68,0.05)",
          }}
        >
          {toast.msg}
        </div>
      )}

      {/* アクティベート確認モーダル */}
      {activateTarget && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center p-4"
          style={{ backgroundColor: "rgba(0,0,0,0.7)" }}
          onClick={(e) => e.target === e.currentTarget && !activating && setActivateTarget(null)}
        >
          <div
            className="w-full max-w-md rounded-xl shadow-2xl border p-5 space-y-4"
            style={{ backgroundColor: "var(--bg-card)", borderColor: "var(--border-cyan)" }}
          >
            <h3 className="text-sm font-mono font-semibold" style={{ color: "var(--cyan)" }}>
              環境を「{activateTarget.name}」に切り替えます。
            </h3>

            <div className="text-xs font-mono space-y-1" style={{ color: "var(--text-secondary)" }}>
              <p>以下のデータは削除されます:</p>
              <p className="pl-3">• チャネル利用率・クライアント・Radio設定・Insights などの蓄積データ</p>
            </div>

            <div className="space-y-2">
              <label className="flex items-center gap-2 text-xs font-mono cursor-pointer" style={{ color: "var(--text-primary)" }}>
                <input
                  type="checkbox"
                  checked={clearLogs}
                  onChange={(e) => setClearLogs(e.target.checked)}
                  disabled={activating}
                  className="w-3.5 h-3.5 accent-cyan-400"
                />
                CSVログも削除する
                <span style={{ color: "var(--text-muted)" }}>(data/logs/ 内の全ファイル)</span>
              </label>
              <label className="flex items-center gap-2 text-xs font-mono cursor-pointer" style={{ color: "var(--text-primary)" }}>
                <input
                  type="checkbox"
                  checked={clearSnapshots}
                  onChange={(e) => setClearSnapshots(e.target.checked)}
                  disabled={activating}
                  className="w-3.5 h-3.5 accent-cyan-400"
                />
                スナップショットも削除する
                <span style={{ color: "var(--text-muted)" }}>(data/snapshots/ 内の全ファイル)</span>
              </label>
            </div>

            <p className="text-xs font-mono" style={{ color: "var(--green)" }}>
              タグ情報は引き継ぎます。
            </p>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => setActivateTarget(null)}
                disabled={activating}
                className="px-3 py-1.5 rounded-lg border text-xs font-mono disabled:opacity-40"
                style={{ borderColor: "var(--chart-grid)", color: "var(--text-muted)" }}
              >
                キャンセル
              </button>
              <button
                onClick={handleActivate}
                disabled={activating}
                className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg border text-xs font-mono transition-all disabled:opacity-60"
                style={{
                  backgroundColor: "rgba(0,212,255,0.15)",
                  borderColor: "var(--border-cyan)",
                  color: "var(--cyan)",
                }}
              >
                {activating && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                {activating ? "切り替え中..." : "切り替える"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
