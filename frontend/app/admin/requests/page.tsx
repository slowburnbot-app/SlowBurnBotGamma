"use client";

import { Fragment, useEffect, useState } from "react";
import {
  adminListAccountRequests,
  adminSetAccountRequestStatus,
  adminDeleteAccountRequest,
  AccountRequest,
  AccountRequestStatus,
} from "@/lib/api";
import { Bracket } from "@/lib/bracket";
import { Dropdown } from "@/lib/dropdown";

const STATUS_OPTIONS: { value: AccountRequestStatus; label: string }[] = [
  { value: "new", label: "new" },
  { value: "contacted", label: "contacted" },
  { value: "invited", label: "invited" },
  { value: "declined", label: "declined" },
];

export default function AdminRequestsPage() {
  const [requests, setRequests] = useState<AccountRequest[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [msg, setMsg] = useState("");

  async function load() {
    adminListAccountRequests().then(setRequests).catch(() => {});
  }

  useEffect(() => {
    load();
  }, []);

  async function handleStatus(req: AccountRequest, status: string) {
    setMsg("");
    try {
      const updated = await adminSetAccountRequestStatus(req.id, status as AccountRequestStatus);
      setRequests((rs) => rs.map((r) => (r.id === updated.id ? updated : r)));
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "update failed.");
    }
  }

  async function handleDelete(req: AccountRequest) {
    if (!confirm(`delete request from ${req.email}?`)) return;
    setMsg("");
    try {
      await adminDeleteAccountRequest(req.id);
      setRequests((rs) => rs.filter((r) => r.id !== req.id));
      if (expanded === req.id) setExpanded(null);
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "delete failed.");
    }
  }

  const newCount = requests.filter((r) => r.status === "new").length;

  return (
    <div className="space-y-4 font-mono">
      <h1 className="font-semibold text-base05">admin — requests</h1>
      {msg && <p className="text-status-bad">{msg}</p>}

      <div className="border border-base02 bg-base01">
        <div className="border-b border-base02 px-4 py-2 bg-base02">
          <span className="text-base05">account requests</span>
          <span className="text-base04 ml-2">[{requests.length}]</span>
          {newCount > 0 && <span className="text-status-warning ml-2">{newCount} new</span>}
        </div>
        {requests.length === 0 ? (
          <p className="px-4 py-6 text-base04">no requests yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-base04 border-b border-base02 bg-base02">
                  <th className="px-4 py-2 font-normal">date</th>
                  <th className="px-4 py-2 font-normal">name</th>
                  <th className="px-4 py-2 font-normal">email</th>
                  <th className="px-4 py-2 font-normal">company</th>
                  <th className="px-4 py-2 font-normal">industry</th>
                  <th className="px-4 py-2 font-normal text-right">accounts</th>
                  <th className="px-4 py-2 font-normal">status</th>
                  <th className="px-4 py-2 font-normal"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-base02">
                {requests.map((req) => {
                  const isOpen = expanded === req.id;
                  const hasDetail = !!(req.instagram_handles || req.notes);
                  return (
                    <Fragment key={req.id}>
                      <tr
                        className={`hover:bg-base02/60 transition-colors ${hasDetail ? "cursor-pointer" : ""}`}
                        onClick={() => hasDetail && setExpanded(isOpen ? null : req.id)}
                      >
                        <td className="px-4 py-2 text-base04 whitespace-nowrap">
                          {new Date(req.created_at).toLocaleDateString()}
                        </td>
                        <td className="px-4 py-2 text-base05">
                          {hasDetail && (
                            <span className="text-base04 mr-1">{isOpen ? "-" : "+"}</span>
                          )}
                          {req.name}
                        </td>
                        <td className="px-4 py-2 text-base05">
                          <a
                            href={`mailto:${req.email}`}
                            onClick={(e) => e.stopPropagation()}
                            className="hover:text-base0d transition-colors"
                          >
                            {req.email}
                          </a>
                        </td>
                        <td className="px-4 py-2 text-base04">{req.company || "----"}</td>
                        <td className="px-4 py-2 text-base04">{req.industry || "----"}</td>
                        <td className="px-4 py-2 text-base0a text-right">
                          {req.account_count ?? "----"}
                        </td>
                        <td className="px-4 py-2" onClick={(e) => e.stopPropagation()}>
                          <span className="text-base05">{"["}</span>
                          <Dropdown
                            value={req.status}
                            onChange={(v) => handleStatus(req, v)}
                            options={STATUS_OPTIONS}
                          />
                          <span className="text-base05">{"]"}</span>
                        </td>
                        <td className="px-4 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                          <button onClick={() => handleDelete(req)} className="group transition-colors">
                            <Bracket className="text-status-bad group-hover:text-base05">delete</Bracket>
                          </button>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="bg-base02">
                          <td colSpan={8} className="px-4 py-3">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                              <div className="space-y-1">
                                <div className="text-base04">instagram handles</div>
                                <pre className="text-base05 whitespace-pre-wrap font-mono">
                                  {req.instagram_handles || "----"}
                                </pre>
                              </div>
                              <div className="space-y-1">
                                <div className="text-base04">notes</div>
                                <pre className="text-base05 whitespace-pre-wrap font-mono">
                                  {req.notes || "----"}
                                </pre>
                              </div>
                            </div>
                            {req.handled_at && (
                              <div className="text-base04 mt-3">
                                handled {new Date(req.handled_at).toLocaleString()}
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
