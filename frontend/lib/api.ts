const API_URL = "/api";

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("token");
}

// The server anchors "day"/"week"/"month" periods on its own UTC date, which
// disagrees with the browser's (and bot client's) local date for part of each
// day. Pass the browser's local date so period filters land on the right day.
function localDateParam(): string {
  const d = new Date();
  const tzOffsetMs = d.getTimezoneOffset() * 60000;
  return new Date(d.getTime() - tzOffsetMs).toISOString().slice(0, 10);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_URL}${path}`, { ...init, headers });

  if (res.status === 401) {
    localStorage.removeItem("token");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.detail ?? `Request failed: ${res.status}`);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

// Auth
export async function login(email: string, password: string) {
  const form = new URLSearchParams({ username: email, password });
  const res = await fetch(`${API_URL}/auth/jwt/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  if (!res.ok) throw new Error("Invalid credentials.");
  const data = await res.json();
  localStorage.setItem("token", data.access_token);
  return data;
}

export async function register(email: string, password: string, displayName?: string, inviteCode?: string) {
  return request("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, display_name: displayName, invite_code: inviteCode }),
  });
}

export async function logout() {
  await request("/auth/jwt/logout", { method: "POST" }).catch(() => {});
  localStorage.removeItem("token");
}

export async function getMe() {
  return request<User>("/users/me");
}

// Accounts
export async function getAccounts() {
  return request<Account[]>("/accounts");
}

export async function createAccount(data: Partial<Account>) {
  return request<Account>("/accounts", { method: "POST", body: JSON.stringify(data) });
}

export async function updateAccount(id: string, data: Partial<Account>) {
  return request<Account>(`/accounts/${id}`, { method: "PATCH", body: JSON.stringify(data) });
}

export async function deleteAccount(id: string) {
  return request(`/accounts/${id}`, { method: "DELETE" });
}

export async function getAccountSettings(id: string) {
  return request<AccountSettings>(`/accounts/${id}/settings`);
}

export async function saveAccountSettings(id: string, data: Partial<AccountSettings>) {
  return request<AccountSettings>(`/accounts/${id}/settings`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

// Bot
export async function getEntitlement() {
  return request<Entitlement>("/bot/entitlement");
}

// Subscription
export async function getSubscriptionInfo() {
  return request<SubscriptionInfo>("/subscription/me");
}

export async function createCheckoutSession(planTier: string) {
  return request<{ url: string }>("/subscription/checkout", {
    method: "POST",
    body: JSON.stringify({ plan_tier: planTier }),
  });
}

export async function createPortalSession() {
  return request<{ url: string }>("/subscription/portal", { method: "POST" });
}

// Config
export async function getUserConfig() {
  return request<UserConfig>("/config");
}

export async function updateUserConfig(data: Partial<UserConfig>) {
  return request<UserConfig>("/config", { method: "PUT", body: JSON.stringify(data) });
}

export async function getIgnoreHandles() {
  return request<{ handles: string[] }>("/config/ignore-handles");
}

export async function updateIgnoreHandles(handles: string[]) {
  return request<{ handles: string[] }>("/config/ignore-handles", { method: "PUT", body: JSON.stringify({ handles }) });
}

// Admin
export async function adminListUsers() {
  return request<AdminUser[]>("/admin/users");
}

export async function adminSyncSubscription(userId: string) {
  return request(`/admin/users/${userId}/sync-subscription`, { method: "POST" });
}

export async function adminActivateSubscription(userId: string) {
  return request<{ status: string; plan_tier: string }>(`/admin/users/${userId}/activate`, { method: "POST" });
}

export async function adminDeactivateSubscription(userId: string) {
  return request<{ status: string; plan_tier: string }>(`/admin/users/${userId}/deactivate`, { method: "POST" });
}

export async function adminGetNotificationCredentials() {
  return request<NotificationCredentials>("/admin/notification-credentials");
}

export async function adminUpdateNotificationCredentials(data: {
  smtp_server?: string;
  smtp_port?: number;
  smtp_user?: string;
  smtp_password?: string;
  textbelt_key?: string;
  resend_api_key?: string;
  resend_from_address?: string;
  resend_reply_to?: string;
}) {
  return request<NotificationCredentials>("/admin/notification-credentials", {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function getAccountDatabase(id: string, page: number, pageSize = 100, sort = "followed", sortDir = "desc") {
  return request<{ total: number; page: number; page_size: number; items: FollowTarget[] }>(
    `/accounts/${id}/database?page=${page}&page_size=${pageSize}&sort=${sort}&sort_dir=${sortDir}`
  );
}

export async function downloadAccountDatabaseCsv(id: string, accountName: string) {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_URL}/accounts/${id}/database/export`, { headers });
  if (!res.ok) throw new Error("Export failed");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const safeName = accountName.replace(/[^a-zA-Z0-9_-]/g, "_") || "account";
  a.download = `${safeName}_database.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export async function getAccountLog(id: string, page: number, pageSize = 100, sort = "date", sortDir = "desc") {
  return request<{ total: number; page: number; page_size: number; items: SessionLogEntry[] }>(
    `/accounts/${id}/log?page=${page}&page_size=${pageSize}&sort=${sort}&sort_dir=${sortDir}`
  );
}

export interface RecentSessionLogEntry extends SessionLogEntry {
  account_id: string;
  account_name: string;
}

export async function getRecentSessionLog(limit = 15) {
  return request<{ items: RecentSessionLogEntry[] }>(`/accounts/recent-log?limit=${limit}`);
}

export interface LogSummaryEntry {
  sessions: number;
  likes: number;
  follows: number;
  unfollows: number;
}

export async function getLogSummary(period: string = "day") {
  return request<Record<string, LogSummaryEntry>>(
    `/accounts/log-summary?period=${period}&client_date=${localDateParam()}`
  );
}

export interface FollowbackSummaryEntry {
  followed: number;
  complete: number;
  followed_back: number;
  rate: number | null;
  days: number;
}

export async function getFollowbackSummary(period: string = "day") {
  return request<Record<string, FollowbackSummaryEntry>>(
    `/accounts/followback-summary?period=${period}&client_date=${localDateParam()}`
  );
}

export interface SessionLogEntry {
  id: string;
  run_date: string | null;
  run_sequence: number;
  start_time: string | null;
  end_time: string | null;
  action_1_type: string | null;
  action_1_count: number;
  action_2_type: string | null;
  action_2_count: number;
  action_3_type: string | null;
  action_3_count: number;
  action_4_type: string | null;
  action_4_count: number;
  error_message: string | null;
  warning_message: string | null;
}

export interface AccountStats {
  following: number;
  unfollow_ready: number;
  complete: number;
  ignored: number;
  total: number;
  success: number;
  last_25: number | null;
  all_time: number | null;
}

export async function getAccountStats(id: string) {
  return request<AccountStats>(`/accounts/${id}/stats`);
}

export interface SourceStat {
  source: string | null;
  total: number;
  complete: number;
  followed_back: number;
  not_followed_back: number;
  rate: number | null;
}

export interface FollowSeed {
  id: string;
  handle: string;
  origin: string;
  active: boolean;
  added_at: string;
  retired_at: string | null;
  retire_reason: string | null;
  last_used_at: string | null;
  last_saturation: number | null;
  total: number;
  complete: number;
  followed_back: number;
  rate: number | null;
}

export interface FollowSeedList {
  items: FollowSeed[];
  active_count: number;
  account_rate: number | null;
}

export async function getFollowSeeds(id: string) {
  return request<FollowSeedList>(`/accounts/${id}/seeds`);
}

export async function addFollowSeed(id: string, handle: string) {
  return request<FollowSeed>(`/accounts/${id}/seeds`, { method: "POST", body: JSON.stringify({ handle }) });
}

export async function updateFollowSeed(id: string, seedId: string, data: { active?: boolean }) {
  return request<FollowSeed>(`/accounts/${id}/seeds/${seedId}`, { method: "PATCH", body: JSON.stringify(data) });
}

export async function deleteFollowSeed(id: string, seedId: string) {
  return request<void>(`/accounts/${id}/seeds/${seedId}`, { method: "DELETE" });
}

export async function getAccountSourceStats(id: string, period: string = "week") {
  return request<{ days: number; items: SourceStat[] }>(
    `/accounts/${id}/source-stats?period=${period}&client_date=${localDateParam()}`
  );
}

export interface ClientStatus {
  client_id: number;
  client_name: string;
  system_type: string;
  status: string;
  current_account: string | null;
  last_session_account: string | null;
  last_heartbeat: string;
  connected: boolean;
}

export async function getClientStatus() {
  return request<ClientStatus[]>("/accounts/client-status");
}

export async function adminListAccounts() {
  return request<AdminAccount[]>("/admin/accounts");
}

export async function adminSetTier(userId: string, planTier: string) {
  return request<{ plan_tier: string; status: string }>(`/admin/users/${userId}/set-tier`, {
    method: "POST",
    body: JSON.stringify({ plan_tier: planTier }),
  });
}

export async function adminDeleteAccount(accountId: string) {
  return request<void>(`/admin/accounts/${accountId}`, { method: "DELETE" });
}

export async function adminGetFollowTargets(accountId: string, page: number, pageSize = 100) {
  return request<{ total: number; page: number; page_size: number; items: FollowTarget[] }>(
    `/admin/accounts/${accountId}/follow-targets?page=${page}&page_size=${pageSize}`
  );
}

// Invites
export interface InviteCode {
  id: string;
  code: string;
  email: string | null;
  free_trial_days: number | null;
  plan_tier: string;
  used_by_user_id: string | null;
  used_at: string | null;
  created_at: string;
  expires_at: string | null;
}

export async function adminListInvites() {
  return request<InviteCode[]>("/admin/invites");
}

export async function adminCreateInvite(data: {
  email?: string;
  free_trial_days?: number;
  plan_tier?: string;
  send_email?: boolean;
}) {
  return request<InviteCode & { email_error?: string }>("/admin/invites", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function adminDeleteInvite(inviteId: string) {
  return request<void>(`/admin/invites/${inviteId}`, { method: "DELETE" });
}

// Types
export interface User {
  id: string;
  email: string;
  display_name: string | null;
  plan_tier: string;
  is_active: boolean;
  is_superuser: boolean;
}

export interface Account {
  id: string;
  user_id: string;
  name: string;
  enabled: boolean;
  system_disabled: boolean;
  group_number: number | null;
  has_password: boolean;
  proxy_enabled: boolean;
  proxy_type: string | null;
  created_at: string;
  updated_at: string;
}

export const PLAN_LIMITS: Record<string, number> = {
  crawl: 3,
  walk: 10,
  run: 25,
};

export interface ActionBlock {
  enabled: boolean;
  type: string;
  target: string;
  fixed_count: number;
  variable_count: number;
}

export interface AccountSettings {
  id: string;
  account_id: string;
  schedule_days: string | null;
  schedule_start: string | null;
  schedule_end: string | null;
  delay_base_minutes: number;
  delay_random_minutes: number;
  max_runs_per_day: number;
  max_runs_random_per_day: number;
  actions: ActionBlock[] | null;
  actions_random_order: boolean;
  unfollow_days: number;
  max_followers: number;
  min_follow_ratio_pct: number;
  min_posts: number;
  list_tab: string | null;
  account_group: string | null;
  account_group_mode: string;
  account_list_tab: string | null;
  topics: string | null;
  updated_at: string;
}

export interface Entitlement {
  active: boolean;
  plan_tier: string;
  current_period_end: string | null;
}

export interface TierInfo {
  name: string;
  price: number;
  max_accounts: number;
  max_clients: number;
}

export interface SubscriptionInfo {
  plan_tier: string;
  status: string;
  max_accounts: number;
  current_accounts: number;
  max_clients: number;
  current_clients: number;
  current_period_end: string | null;
  tiers: TierInfo[];
}

export interface UserConfig {
  id: string;
  user_id: string;
  like_suggested: boolean;
  like_sponsored: boolean;
  skip_login_check: boolean;
  login_tries: number;
  skip_private: boolean;
  notices_type: string;
  notices_session: boolean;
  notices_login: boolean;
  login_notices_type: string;
  login_notify_email: string | null;
  login_notify_phone: string | null;
  notify_email: string | null;
  notify_phone: string | null;
  bot_debug: boolean;
  updated_at: string;
}

export interface NotificationCredentials {
  smtp_server: string;
  smtp_port: number;
  smtp_user: string | null;
  smtp_password_set: boolean;
  textbelt_key_set: boolean;
  resend_api_key_set: boolean;
  resend_from_address: string | null;
  resend_reply_to: string | null;
  updated_at: string;
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string | null;
  plan_tier: string;
  is_active: boolean;
  subscription_status: string;
  created_at: string;
}

export interface AdminAccount {
  id: string;
  user_id: string;
  user_email: string;
  name: string;
  enabled: boolean;
  system_disabled: boolean;
  group_number: number | null;
  created_at: string;
}

export interface FollowTarget {
  id: string;
  target_handle: string;
  source: string | null;
  status: string;
  follow_date: string | null;
  unfollow_date: string | null;
  follow_back: boolean | null;
}

// Desktop builds
export interface DesktopBuildConfig {
  client_name: string;
  system_type: "windows" | "linux";
  novnc_url?: string;
}

export interface DesktopBuild {
  id: string;
  client_id: number;
  status: "pending_activation" | "activated" | "revoked";
  build_options: DesktopBuildConfig;
  failure_reason: string | null;
  bot_version: string | null;
  activated_at: string | null;
  consumed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface DesktopBuildWithToken extends DesktopBuild {
  activation_token: string;
}

export interface DownloadInfo {
  // Windows
  url?: string;
  filename?: string;
  // Linux
  image_ref?: string;
  run_cmd?: string;
}

export async function createDesktopBuild(config: DesktopBuildConfig, slotNumber?: number): Promise<DesktopBuildWithToken> {
  return request<DesktopBuildWithToken>("/desktop-builds", {
    method: "POST",
    body: JSON.stringify({ config, slot_number: slotNumber ?? null }),
  });
}

export async function listDesktopBuilds(): Promise<DesktopBuild[]> {
  return request<DesktopBuild[]>("/desktop-builds");
}

export async function getDesktopBuildsMeta(): Promise<{ current_bot_version: string; current_bot_release_date: string }> {
  return request<{ current_bot_version: string; current_bot_release_date: string }>("/desktop-builds/meta");
}

export async function getDesktopBuild(id: string): Promise<DesktopBuild> {
  return request<DesktopBuild>(`/desktop-builds/${id}`);
}

export async function getDownloadInfo(id: string): Promise<DownloadInfo> {
  return request<DownloadInfo>(`/desktop-builds/${id}/download-url`);
}

export async function revokeDesktopBuild(id: string): Promise<void> {
  return request<void>(`/desktop-builds/${id}`, { method: "DELETE" });
}

export async function rebuildDesktopBuild(id: string): Promise<DesktopBuildWithToken> {
  return request<DesktopBuildWithToken>(`/desktop-builds/${id}/rebuild`, { method: "POST" });
}
