<template>
  <AdminLayout
    active="overview"
    title="Operations overview"
    subtitle="Verify admin access, email delivery, risk controls, remote join, and the real image generation path."
  >
    <view v-if="loading" class="admin-card admin-state">
      <text class="state-title">Loading admin console</text>
      <text class="state-copy">Checking admin permission and reading production signals.</text>
    </view>

    <view v-else-if="error" class="admin-card admin-state">
      <text class="state-title">Admin access required</text>
      <text class="state-copy">{{ error }}</text>
      <button class="primary-action" @tap="goLogin">Sign in</button>
    </view>

    <template v-else>
      <view class="metrics-grid">
        <view class="metric-card admin-card">
          <text class="metric-label">Users</text>
          <text class="metric-value">{{ stats.total_users }}</text>
          <text class="metric-sub">New in 7 days {{ stats.recent_users || 0 }}</text>
        </view>
        <view class="metric-card admin-card">
          <text class="metric-label">Orders</text>
          <text class="metric-value">{{ stats.total_orders }}</text>
          <text class="metric-sub">New in 7 days {{ stats.recent_orders || 0 }}</text>
        </view>
        <view class="metric-card admin-card">
          <text class="metric-label">Credit revenue</text>
          <text class="metric-value">{{ stats.total_revenue_credits || 0 }}</text>
          <text class="metric-sub">Credits in circulation {{ stats.total_credits_in_circulation || 0 }}</text>
        </view>
        <view class="metric-card admin-card">
          <text class="metric-label">Subscription MRR</text>
          <text class="metric-value">{{ formatMoney(stats.subscription_mrr_cents || 0) }}</text>
          <text class="metric-sub">Active subscriptions {{ stats.active_subscriptions || 0 }}</text>
        </view>
      </view>

      <view class="ops-grid">
        <view class="admin-card ops-card">
          <view class="section-head compact-head">
            <view>
              <text class="section-title">Admin access</text>
              <text class="section-copy">Entry: /admin. Visible only to owner, admin, operator, ADMIN_EMAILS, or ADMIN_USER_IDS.</text>
            </view>
          </view>
          <view class="diagnostic-list">
            <view class="diag-row">
              <text>Actor</text>
              <text class="mono">{{ adminMe?.actor || '--' }}</text>
            </view>
            <view class="diag-row">
              <text>Remote join</text>
              <text class="status-pill" :class="{ active: adminMe?.remote_join_enabled }">
                {{ adminMe?.remote_join_enabled ? 'Enabled' : 'Disabled' }}
              </text>
            </view>
            <view class="diag-row">
              <text>Session store</text>
              <text class="mono">{{ adminMe?.remote_join_session_store || '--' }}</text>
            </view>
            <view class="diag-row">
              <text>Generation mode</text>
              <text class="mono">{{ adminMe?.generation_execution_mode || '--' }}</text>
            </view>
          </view>
        </view>

        <view class="admin-card ops-card">
          <view class="section-head compact-head">
            <view>
              <text class="section-title">Email delivery</text>
              <text class="section-copy">Production sender config, DNS signals, and latest delivery attempts.</text>
            </view>
            <button class="ghost-action" @tap="refreshOps">Refresh</button>
          </view>

          <view class="diagnostic-list">
            <view class="diag-row">
              <text>Resend API key</text>
              <text class="status-pill" :class="{ active: emailDiagnostics?.resend_api_key_configured }">
                {{ emailDiagnostics?.resend_api_key_configured ? 'Configured' : 'Missing' }}
              </text>
            </view>
            <view class="diag-row">
              <text>From domain</text>
              <text class="mono">{{ emailDiagnostics?.from_domain || '--' }}</text>
            </view>
            <view class="diag-row">
              <text>SPF / DMARC / MX</text>
              <text class="mono">{{ dnsSummary }}</text>
            </view>
          </view>

          <view class="email-test-row">
            <input v-model="testEmailTo" class="filter-input" placeholder="recipient@example.com" />
            <button class="primary-action" :disabled="sendingTestEmail" @tap="sendAdminTestEmail">
              {{ sendingTestEmail ? 'Sending...' : 'Send test' }}
            </button>
          </view>
          <text v-if="testEmailResult" class="section-copy">{{ testEmailResult }}</text>

          <view class="mini-log-list">
            <view v-for="log in emailLogs" :key="log.id" class="mini-log-row">
              <view>
                <text class="strong">{{ log.purpose }} / {{ log.status }}</text>
                <text class="subtle">{{ log.to_email }} / {{ log.error_code || log.provider_message_id || 'ok' }}</text>
              </view>
              <text class="td-muted">{{ formatDate(log.created_at) }}</text>
            </view>
          </view>
        </view>
      </view>

      <view class="ops-grid">
        <view class="admin-card ops-card">
          <view class="section-head compact-head">
            <view>
              <text class="section-title">Real generation probe</text>
              <text class="section-copy">Creates a zero-cost admin probe order and runs the configured image provider.</text>
            </view>
          </view>

          <view class="probe-form">
            <input v-model="probeImageUrl" class="filter-input" placeholder="Primary public portrait image URL" />
            <input v-model="probeSecondImageUrl" class="filter-input" placeholder="Second portrait URL for couple or remote test" />
            <input v-model="probeTemplateId" class="filter-input" placeholder="Template ID, e.g. solo_royal_castle or royal_castle" />
            <view class="probe-options">
              <label class="check-row">
                <checkbox :checked="probeRemoteJoin" @tap="probeRemoteJoin = !probeRemoteJoin" />
                <text>Remote join couple probe</text>
              </label>
              <label class="check-row">
                <checkbox :checked="probeInline" @tap="probeInline = !probeInline" />
                <text>Run inline now</text>
              </label>
            </view>
            <button class="primary-action" :disabled="runningProbe" @tap="runGenerationProbe">
              {{ runningProbe ? 'Starting probe...' : 'Run real probe' }}
            </button>
          </view>

          <view v-if="probeResult" class="probe-result">
            <view class="diag-row">
              <text>Result</text>
              <text class="status-pill" :class="{ active: probeResult.ok }">{{ probeResultLabel }}</text>
            </view>
            <view class="diag-row">
              <text>Order</text>
              <text class="mono">{{ probeResult.order_id || '--' }}</text>
            </view>
            <text v-if="probeResult.error_message" class="error-copy">{{ probeResult.error_message }}</text>
            <image v-if="probePreviewUrl" class="probe-image" :src="probePreviewUrl" mode="aspectFill" />
          </view>
        </view>

        <view class="admin-card ops-card">
          <view class="section-head compact-head">
            <view>
              <text class="section-title">Signup risk</text>
              <text class="section-copy">Starter-credit, verification, device, IP, and email-domain abuse signals.</text>
            </view>
          </view>

          <view class="risk-summary">
            <view>
              <text class="metric-label">Events</text>
              <text class="metric-value small-value">{{ riskOverview?.total_events || 0 }}</text>
            </view>
            <view>
              <text class="metric-label">Welcome credits</text>
              <text class="metric-value small-value">{{ riskOverview?.welcome_bonus_count || 0 }}</text>
            </view>
            <view>
              <text class="metric-label">Blocked</text>
              <text class="metric-value small-value">{{ riskOverview?.blocked_events || 0 }}</text>
            </view>
            <view>
              <text class="metric-label">High risk</text>
              <text class="metric-value small-value">{{ riskOverview?.high_risk_events || 0 }}</text>
            </view>
          </view>

          <view class="mini-log-list">
            <view v-for="event in recentRiskEvents" :key="event.id" class="mini-log-row">
              <view>
                <text class="strong">{{ event.event_type }} / {{ event.provider || '--' }}</text>
                <text class="subtle">{{ event.email_domain || 'no-domain' }} / score {{ event.risk_score }}</text>
              </view>
              <text class="td-muted">{{ formatDate(event.created_at) }}</text>
            </view>
          </view>
        </view>
      </view>

      <view class="admin-card overview-section">
        <view class="section-head">
          <view>
            <text class="section-title">Recent orders</text>
            <text class="section-copy">A quick view of the generation pipeline status.</text>
          </view>
          <button class="ghost-action" @tap="goOrders">View all orders</button>
        </view>

        <view v-if="recentOrders.length === 0" class="admin-state compact-state">
          <text class="state-title">No orders yet</text>
          <text class="state-copy">Created orders will appear here.</text>
        </view>
        <view v-else class="recent-list">
          <view v-for="order in recentOrders" :key="order.id" class="recent-row">
            <view>
              <text class="strong mono">{{ shortId(order.id) }}</text>
              <text class="subtle">{{ order.template_title || order.template_id || 'No template' }}</text>
            </view>
            <text class="status-pill" :class="order.status">{{ order.status || 'UNKNOWN' }}</text>
            <text class="td-muted">{{ formatDate(order.created_at) }}</text>
          </view>
        </view>
      </view>
    </template>
  </AdminLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import AdminLayout from './AdminLayout.vue';
import { get, post, resolvePublicUrl } from '../../utils/api';

interface AdminMe {
  actor: string;
  admin_roles: string[];
  entry_url: string;
  remote_join_enabled: boolean;
  remote_join_session_store: string;
  generation_execution_mode: string;
}

interface DashboardStats {
  total_orders: number;
  recent_orders?: number;
  total_users: number;
  recent_users?: number;
  total_revenue_credits?: number;
  total_credits_in_circulation?: number;
  subscription_mrr_cents?: number;
  active_subscriptions?: number;
  recent_activity?: Array<{
    id: string;
    template_id?: string | null;
    template_title?: string | null;
    status?: string;
    created_at?: string;
  }>;
}

interface EmailDiagnostics {
  resend_api_key_configured: boolean;
  from_domain: string;
  dns?: {
    spf_found?: boolean;
    dmarc_found?: boolean;
    mx_found?: boolean;
  };
}

interface EmailLogItem {
  id: string;
  purpose: string;
  status: string;
  to_email: string;
  error_code?: string | null;
  provider_message_id?: string | null;
  created_at?: string;
}

interface RiskEvent {
  id: string;
  event_type: string;
  provider?: string | null;
  email_domain?: string | null;
  risk_score: number;
  created_at?: string;
}

interface RiskOverview {
  total_events: number;
  welcome_bonus_count: number;
  blocked_events: number;
  high_risk_events: number;
  recent_events: RiskEvent[];
}

interface ProbeResponse {
  ok: boolean;
  started: boolean;
  completed: boolean;
  execution_mode: string;
  order_id?: string | null;
  status?: string | null;
  task_id?: string | null;
  template_id?: string | null;
  error_message?: string | null;
  preview_image_urls?: Record<string, string> | null;
  final_image_urls?: Record<string, string> | null;
}

const loading = ref(true);
const error = ref('');
const adminMe = ref<AdminMe | null>(null);
const stats = ref<DashboardStats>({
  total_orders: 0,
  total_users: 0,
  recent_activity: [],
});
const emailDiagnostics = ref<EmailDiagnostics | null>(null);
const emailLogs = ref<EmailLogItem[]>([]);
const riskOverview = ref<RiskOverview | null>(null);
const testEmailTo = ref('');
const testEmailResult = ref('');
const sendingTestEmail = ref(false);
const probeImageUrl = ref('');
const probeSecondImageUrl = ref('');
const probeTemplateId = ref('solo_royal_castle');
const probeRemoteJoin = ref(false);
const probeInline = ref(true);
const runningProbe = ref(false);
const probeResult = ref<ProbeResponse | null>(null);

const recentOrders = computed(() => (stats.value.recent_activity || []).slice(0, 8));
const recentRiskEvents = computed(() => (riskOverview.value?.recent_events || []).slice(0, 6));
const dnsSummary = computed(() => {
  const dns = emailDiagnostics.value?.dns || {};
  return `SPF ${dns.spf_found ? 'ok' : 'missing'} | DMARC ${dns.dmarc_found ? 'ok' : 'missing'} | MX ${dns.mx_found ? 'ok' : 'missing'}`;
});

const probeResultLabel = computed(() => {
  if (!probeResult.value) return '--';
  if (probeResult.value.completed) return 'Completed';
  if (probeResult.value.started && probeResult.value.ok) return `Started (${probeResult.value.execution_mode})`;
  return 'Failed';
});

const probePreviewUrl = computed(() => {
  const result = probeResult.value;
  if (!result) return '';
  const finalValues = result.final_image_urls ? Object.values(result.final_image_urls) : [];
  if (finalValues.length && finalValues[0]) return resolvePublicUrl(finalValues[0]);
  const previewValues = result.preview_image_urls ? Object.values(result.preview_image_urls) : [];
  if (previewValues.length && previewValues[0]) return resolvePublicUrl(previewValues[0]);
  return '';
});

function shortId(value: string): string {
  return value.length > 14 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value;
}

function formatDate(value?: string): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function formatMoney(cents: number): string {
  return `$${(Number(cents || 0) / 100).toFixed(2)}`;
}

async function loadDashboard() {
  loading.value = true;
  error.value = '';
  try {
    await Promise.all([loadAdminMe(), loadCoreDashboard(), refreshOps()]);
  } catch (err: any) {
    error.value = err?.statusCode === 401
      ? 'This account is not authorized for admin access. Use an owner/admin/operator account or configure ADMIN_EMAILS / ADMIN_USER_IDS.'
      : (err?.message || 'Admin data failed to load. Please retry.');
  } finally {
    loading.value = false;
  }
}

async function loadAdminMe() {
  adminMe.value = await get<AdminMe>('/admin/me', { showLoading: false, showError: false });
}

async function loadCoreDashboard() {
  stats.value = await get<DashboardStats>('/admin/dashboard', { showLoading: false, showError: false });
}

async function refreshOps() {
  const [emailDiag, logs, risk] = await Promise.allSettled([
    get<EmailDiagnostics>('/admin/email_diagnostics', { showLoading: false, showError: false }),
    get<EmailLogItem[]>('/admin/email_logs?limit=8', { showLoading: false, showError: false }),
    get<RiskOverview>('/admin/risk_overview?days=7&limit=8', { showLoading: false, showError: false }),
  ]);
  emailDiagnostics.value = emailDiag.status === 'fulfilled' ? emailDiag.value : null;
  emailLogs.value = logs.status === 'fulfilled' ? logs.value : [];
  riskOverview.value = risk.status === 'fulfilled' ? risk.value : null;
}

async function sendAdminTestEmail() {
  const to = testEmailTo.value.trim();
  if (!to || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(to)) {
    testEmailResult.value = 'Enter a valid recipient email.';
    return;
  }
  sendingTestEmail.value = true;
  testEmailResult.value = '';
  try {
    const result: any = await post('/admin/email_test', { to }, { showLoading: false, showError: false });
    testEmailResult.value = result?.sent ? 'Test email sent.' : `Send failed: ${result?.reason || result?.status || result?.error || 'unknown'}`;
    await refreshOps();
  } catch (err: any) {
    testEmailResult.value = err?.message || 'Test email failed.';
  } finally {
    sendingTestEmail.value = false;
  }
}

async function runGenerationProbe() {
  const imageUrl = probeImageUrl.value.trim();
  if (!/^https?:\/\//i.test(imageUrl)) {
    probeResult.value = {
      ok: false,
      started: false,
      completed: false,
      execution_mode: probeInline.value ? 'inline' : 'arq',
      error_message: 'Enter a public http(s) portrait image URL.',
    };
    return;
  }
  runningProbe.value = true;
  probeResult.value = null;
  try {
    probeResult.value = await post<ProbeResponse>(
      '/admin/generation_probe',
      {
        image_url: imageUrl,
        second_image_url: probeSecondImageUrl.value.trim() || undefined,
        template_id: probeTemplateId.value.trim() || undefined,
        remote_join: probeRemoteJoin.value,
        execute_inline: probeInline.value,
      },
      { showLoading: false, showError: false },
    );
    await loadCoreDashboard();
  } catch (err: any) {
    probeResult.value = {
      ok: false,
      started: false,
      completed: false,
      execution_mode: probeInline.value ? 'inline' : 'arq',
      error_message: err?.message || 'Generation probe failed.',
    };
  } finally {
    runningProbe.value = false;
  }
}

function goLogin() {
  uni.navigateTo({ url: '/pages/auth/login' });
}

function goOrders() {
  uni.navigateTo({ url: '/admin/orders' });
}

onMounted(loadDashboard);
</script>

<style lang="scss" scoped>
@import './admin.scss';

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 18px;
}

.metric-card {
  padding: 20px;
}

.metric-label,
.metric-sub,
.section-title,
.section-copy {
  display: block;
}

.metric-label {
  font-size: 12px;
  font-weight: 900;
  color: #687180;
}

.metric-value {
  display: block;
  margin-top: 8px;
  font-size: 30px;
  line-height: 1.1;
  font-weight: 900;
  color: #111827;
}

.metric-sub {
  margin-top: 8px;
  font-size: 13px;
  color: #8a5a00;
}

.overview-section {
  padding: 20px;
}

.ops-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 18px;
}

.ops-card {
  padding: 20px;
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 14px;
}

.compact-head {
  align-items: center;
}

.section-title {
  font-size: 18px;
  font-weight: 900;
  color: #111827;
}

.section-copy {
  margin-top: 4px;
  color: #687180;
  font-size: 13px;
  line-height: 1.5;
}

.compact-state {
  min-height: 150px;
}

.recent-list {
  border: 1px solid #edf1f6;
  border-radius: 8px;
  overflow: hidden;
}

.diagnostic-list,
.mini-log-list,
.probe-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.diag-row,
.mini-log-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-top: 1px solid #edf1f6;
}

.email-test-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  margin: 14px 0;
}

.probe-options {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.check-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #374151;
}

.probe-result {
  margin-top: 14px;
  padding-top: 4px;
}

.probe-image {
  margin-top: 12px;
  width: 180px;
  height: 180px;
  border-radius: 8px;
  background: #edf1f6;
}

.error-copy {
  display: block;
  margin-top: 10px;
  color: #be123c;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
}

.risk-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.small-value {
  font-size: 22px;
}

.recent-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 150px 150px;
  align-items: center;
  gap: 14px;
  min-height: 58px;
  padding: 0 14px;
  border-bottom: 1px solid #edf1f6;
}

.recent-row:last-child {
  border-bottom: 0;
}

@media (max-width: 1100px) {
  .metrics-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .metrics-grid,
  .ops-grid,
  .recent-row {
    grid-template-columns: 1fr;
  }

  .email-test-row,
  .risk-summary {
    grid-template-columns: 1fr;
  }

  .recent-row {
    padding: 12px;
    align-items: flex-start;
  }
}
</style>
