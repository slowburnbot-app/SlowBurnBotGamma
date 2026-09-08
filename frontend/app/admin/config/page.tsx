"use client";

import { useEffect, useState } from "react";
import {
  adminGetNotificationCredentials,
  adminUpdateNotificationCredentials,
  NotificationCredentials,
} from "@/lib/api";
import { Bracket } from "@/lib/bracket";

const sectionCls = "border border-base03 bg-base01";

export default function AdminConfigPage() {
  const [creds, setCreds] = useState<NotificationCredentials | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  // Form state
  const [smtpServer, setSmtpServer] = useState("");
  const [smtpPort, setSmtpPort] = useState("587");
  const [smtpUser, setSmtpUser] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [textbeltKey, setTextbeltKey] = useState("");
  const [resendFrom, setResendFrom] = useState("");
  const [resendReplyTo, setResendReplyTo] = useState("");
  const [resendApiKey, setResendApiKey] = useState("");
  const [notifyEmail, setNotifyEmail] = useState("");
  const [editingSmtpPassword, setEditingSmtpPassword] = useState(false);
  const [editingTextbeltKey, setEditingTextbeltKey] = useState(false);
  const [editingResendApiKey, setEditingResendApiKey] = useState(false);

  useEffect(() => {
    adminGetNotificationCredentials()
      .then((c) => {
        setCreds(c);
        setSmtpServer(c.smtp_server || "");
        setSmtpPort(String(c.smtp_port));
        setSmtpUser(c.smtp_user || "");
        setResendFrom(c.resend_from_address || "");
        setResendReplyTo(c.resend_reply_to || "");
        setNotifyEmail(c.admin_notify_email || "");
      })
      .catch(() => {});
  }, []);

  async function handleSave() {
    setSaving(true);
    setMsg("");
    try {
      const data: Record<string, string | number> = {
        smtp_server: smtpServer,
        smtp_port: parseInt(smtpPort, 10) || 587,
        smtp_user: smtpUser,
      };
      if (smtpPassword) data.smtp_password = smtpPassword;
      if (textbeltKey) data.textbelt_key = textbeltKey;
      data.resend_from_address = resendFrom;
      data.resend_reply_to = resendReplyTo;
      data.admin_notify_email = notifyEmail;
      if (resendApiKey) data.resend_api_key = resendApiKey;

      const updated = await adminUpdateNotificationCredentials(data);
      setCreds(updated);
      setSmtpPassword("");
      setTextbeltKey("");
      setResendApiKey("");
      setResendFrom(updated.resend_from_address || "");
      setResendReplyTo(updated.resend_reply_to || "");
      setNotifyEmail(updated.admin_notify_email || "");
      setEditingSmtpPassword(false);
      setEditingTextbeltKey(false);
      setEditingResendApiKey(false);
      setMsg("saved.");
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "save failed.");
    } finally {
      setSaving(false);
    }
  }

  if (!creds) {
    return <div className="font-mono text-base04">loading…</div>;
  }

  return (
    <div className="space-y-6 font-mono">
      <h1 className="font-semibold text-base05">admin — Config</h1>

      <div className={sectionCls}>
        <div className="px-4 py-2 border-b border-base03 text-base04 bg-base02">Notification Settings</div>

        {/* SMTP row */}
        <div className="px-4 py-3 flex items-center gap-x-5 gap-y-2 flex-wrap text-sm border-b border-base03">
          <span className="text-base04" style={{ width: "8ch" }}>smtp:</span>

          <span className="inline-flex items-center gap-0">
            <span className="text-base04">{"server: "}</span>
            <span className="text-base05">{"["}</span>
            <input
              type="text"
              value={smtpServer}
              onChange={(e) => setSmtpServer(e.target.value)}
              placeholder="----"
              style={{ width: "18ch" }}
              className="bg-transparent text-base05 placeholder-base04 outline-none font-mono min-w-0 px-0"
            />
            <span className="text-base05">{"]"}</span>
          </span>

          <span className="inline-flex items-center gap-0">
            <span className="text-base04">{"port: "}</span>
            <span className="text-base05">{"["}</span>
            <input
              type="text"
              inputMode="numeric"
              value={smtpPort}
              onChange={(e) => setSmtpPort(e.target.value.replace(/\D/g, ""))}
              placeholder="----"
              style={{ width: "4ch" }}
              className="bg-transparent text-base05 placeholder-base04 outline-none font-mono min-w-0 px-0 text-center"
            />
            <span className="text-base05">{"]"}</span>
          </span>

          <span className="inline-flex items-center gap-0">
            <span className="text-base04">{"user: "}</span>
            <span className="text-base05">{"["}</span>
            <input
              type="text"
              value={smtpUser}
              onChange={(e) => setSmtpUser(e.target.value)}
              placeholder="----"
              style={{ width: "20ch" }}
              className="bg-transparent text-base05 placeholder-base04 outline-none font-mono min-w-0 px-0"
            />
            <span className="text-base05">{"]"}</span>
          </span>

          <span className="inline-flex items-center gap-0">
            <span className="text-base04">{"password: "}</span>
            <span className="text-base05">{"["}</span>
            {editingSmtpPassword ? (
              <input
                type="text"
                value={smtpPassword}
                onChange={(e) => setSmtpPassword(e.target.value)}
                placeholder="----"
                autoFocus
                onBlur={() => { if (!smtpPassword) setEditingSmtpPassword(false); }}
                style={{ width: "14ch" }}
                className="bg-transparent text-base05 placeholder-base04 outline-none font-mono min-w-0 pl-1"
              />
            ) : (
              <button
                type="button"
                onClick={() => setEditingSmtpPassword(true)}
                style={{ width: "14ch" }}
                className="bg-transparent text-left font-mono min-w-0 pl-1 cursor-pointer inline-flex items-center translate-y-px"
              >
                <span className={creds.smtp_password_set ? "text-base05" : "text-base04"}>
                  {creds.smtp_password_set ? " " + "*".repeat(13) : "----"}
                </span>
              </button>
            )}
            <span className="text-base05">{"]"}</span>
          </span>
        </div>

        {/* Resend row */}
        <div className="px-4 py-3 flex items-center gap-x-5 gap-y-2 flex-wrap text-sm border-b border-base03">
          <span className="text-base04" style={{ width: "8ch" }}>resend:</span>

          <span className="inline-flex items-center gap-0">
            <span className="text-base04">{"from: "}</span>
            <span className="text-base05">{"["}</span>
            <input
              type="text"
              value={resendFrom}
              onChange={(e) => setResendFrom(e.target.value)}
              placeholder="----"
              style={{ width: "24ch" }}
              className="bg-transparent text-base05 placeholder-base04 outline-none font-mono min-w-0 px-0"
            />
            <span className="text-base05">{"]"}</span>
          </span>

          <span className="inline-flex items-center gap-0">
            <span className="text-base04">{"reply-to: "}</span>
            <span className="text-base05">{"["}</span>
            <input
              type="text"
              value={resendReplyTo}
              onChange={(e) => setResendReplyTo(e.target.value)}
              placeholder="----"
              style={{ width: "24ch" }}
              className="bg-transparent text-base05 placeholder-base04 outline-none font-mono min-w-0 px-0"
            />
            <span className="text-base05">{"]"}</span>
          </span>

          <span className="inline-flex items-center gap-0">
            <span className="text-base04">{"api key: "}</span>
            <span className="text-base05">{"["}</span>
            {editingResendApiKey ? (
              <input
                type="text"
                value={resendApiKey}
                onChange={(e) => setResendApiKey(e.target.value)}
                placeholder="----"
                autoFocus
                onBlur={() => { if (!resendApiKey) setEditingResendApiKey(false); }}
                style={{ width: "20ch" }}
                className="bg-transparent text-base05 placeholder-base04 outline-none font-mono min-w-0 pl-1"
              />
            ) : (
              <button
                type="button"
                onClick={() => setEditingResendApiKey(true)}
                style={{ width: "20ch" }}
                className="bg-transparent text-left font-mono min-w-0 pl-1 cursor-pointer inline-flex items-center translate-y-px"
              >
                <span className={creds.resend_api_key_set ? "text-base05" : "text-base04"}>
                  {creds.resend_api_key_set ? " " + "*".repeat(19) : "----"}
                </span>
              </button>
            )}
            <span className="text-base05">{"]"}</span>
          </span>
        </div>

        {/* Account-request notifications row */}
        <div className="px-4 py-3 flex items-center gap-x-5 gap-y-2 flex-wrap text-sm border-b border-base03">
          <span className="text-base04" style={{ width: "8ch" }}>requests:</span>

          <span className="inline-flex items-center gap-0">
            <span className="text-base04">{"notify email: "}</span>
            <span className="text-base05">{"["}</span>
            <input
              type="text"
              value={notifyEmail}
              onChange={(e) => setNotifyEmail(e.target.value)}
              placeholder="----"
              style={{ width: "24ch" }}
              className="bg-transparent text-base05 placeholder-base04 outline-none font-mono min-w-0 px-0"
            />
            <span className="text-base05">{"]"}</span>
          </span>
          <span className="text-base03">falls back to resend reply-to</span>
        </div>

        {/* TextBelt row */}
        <div className="px-4 py-3 flex items-center gap-x-5 gap-y-2 flex-wrap text-sm">
          <span className="text-base04" style={{ width: "8ch" }}>textbelt:</span>

          <span className="inline-flex items-center gap-0">
            <span className="text-base04">{"api key: "}</span>
            <span className="text-base05">{"["}</span>
            {editingTextbeltKey ? (
              <input
                type="text"
                value={textbeltKey}
                onChange={(e) => setTextbeltKey(e.target.value)}
                placeholder="----"
                autoFocus
                onBlur={() => { if (!textbeltKey) setEditingTextbeltKey(false); }}
                style={{ width: "20ch" }}
                className="bg-transparent text-base05 placeholder-base04 outline-none font-mono min-w-0 pl-1"
              />
            ) : (
              <button
                type="button"
                onClick={() => setEditingTextbeltKey(true)}
                style={{ width: "20ch" }}
                className="bg-transparent text-left font-mono min-w-0 pl-1 cursor-pointer inline-flex items-center translate-y-px"
              >
                <span className={creds.textbelt_key_set ? "text-base05" : "text-base04"}>
                  {creds.textbelt_key_set ? " " + "*".repeat(19) : "----"}
                </span>
              </button>
            )}
            <span className="text-base05">{"]"}</span>
          </span>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <button
          onClick={handleSave}
          disabled={saving}
          className="group disabled:opacity-50 transition-colors bg-base11 border border-base03 px-2 py-0.5"
        >
          <Bracket className="text-base0e group-hover:text-base05">
            {saving ? "saving…" : "save"}
          </Bracket>
        </button>
        {msg && <span className="text-status-ok">{msg}</span>}
      </div>
    </div>
  );
}
