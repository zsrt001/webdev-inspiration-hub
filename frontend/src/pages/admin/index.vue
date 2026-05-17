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

      <view class="admin-card overview-section">
        <view class="section-head">
          <view>
            <text class="section-title">Commercial funnel</text>
            <text class="section-copy">7-day funnel with upload quality, identity grade, payment, and delivery signals.</text>
          </view>
          <button class="ghost-action" @tap="refreshOps">Refresh</button>
        </view>

        <view class="funnel-grid">
          <view v-for="item in funnelCards" :key="item.label" class="funnel-cell">
            <text class="metric-label">{{ item.label }}</text>
            <text class="metric-value small-value">{{ item.value }}</text>
            <text v-if="item.sub" class="metric-sub">{{ item.sub }}</text>
          </view>
        </view>

        <view class="template-ranking">
          <view class="section-head compact-head">
            <view>
              <text class="section-title small-title">Template conversion</text>
              <text class="section-copy">A/B picks now feed template ranking together with clicks, orders, completions, downloads, and leads.</text>
            </view>
          </view>
          <view v-if="templateRanking.length === 0" class="admin-state compact-state">
            <text class="state-title">No template data yet</text>
            <text class="state-copy">Template activity will appear after users browse and generate.</text>
          </view>
          <view v-else class="recent-list">
            <view v-for="item in templateRanking" :key="item.template_id" class="template-row">
              <view>
                <text class="strong">{{ item.template_title || item.template_id }}</text>
                <text class="subtle mono">{{ item.template_id }}</text>
              </view>
              <text class="td-muted">Clicks {{ item.clicks || 0 }}</text>
              <text class="td-muted">A/B {{ item.ab_picks || 0 }}</text>
              <text class="td-muted">Orders {{ item.orders || 0 }}</text>
              <text class="td-muted">Done {{ formatPercent(item.completion_rate) }}</text>
              <text class="td-muted">Score {{ formatNumber(item.ranking_score) }}</text>
            </view>
          </view>
        </view>
      </view>

      <view class="admin-card overview-section quality-dashboard">
        <view class="section-head">
          <view>
            <text class="section-title">Order quality dashboard</text>
            <text class="section-copy">30-day gate results by template, failure reason, and repair-round recovery.</text>
          </view>
          <button class="ghost-action" @tap="refreshOps">Refresh</button>
        </view>

        <view class="quality-kpi-grid">
          <view v-for="item in qualityKpis" :key="item.label" class="quality-kpi">
            <text class="metric-label">{{ item.label }}</text>
            <text class="metric-value small-value">{{ item.value }}</text>
            <text class="metric-sub">{{ item.sub }}</text>
          </view>
        </view>

        <view class="quality-tables-grid">
          <view>
            <view class="section-head compact-head quality-table-head">
              <view>
                <text class="section-title small-title">Template failure map</text>
                <text class="section-copy">Top templates ranked by QA failures and order volume.</text>
              </view>
            </view>
            <view v-if="qualityTemplates.length === 0" class="admin-state compact-state">
              <text class="state-title">No quality data yet</text>
              <text class="state-copy">Template-level QA data will appear after orders complete or fail gates.</text>
            </view>
            <view v-else class="quality-table">
              <view class="quality-row quality-head-row template-quality-row">
                <text class="th">Template</text>
                <text class="th">Orders</text>
                <text class="th">QA fail</text>
                <text class="th">Identity</text>
                <text class="th">Lighting</text>
                <text class="th">Top reason</text>
              </view>
              <view v-for="item in qualityTemplates" :key="item.template_id" class="quality-row template-quality-row">
                <view>
                  <text class="strong">{{ item.template_id }}</text>
                  <text class="subtle">Done {{ formatPercent(item.completion_rate) }} · repair {{ formatNumber(item.avg_repair_rounds) }}</text>
                </view>
                <text class="td">{{ item.orders || 0 }}</text>
                <text class="td">{{ formatPercent(item.qa_failure_rate) }}</text>
                <text class="td">{{ formatPercent(item.identity_failure_rate) }}</text>
                <text class="td">{{ formatPercent(item.lighting_failure_rate) }}</text>
                <text class="td-muted">{{ topReasonLabel(item.top_reasons) }}</text>
              </view>
            </view>
          </view>

          <view>
            <view class="section-head compact-head quality-table-head">
              <view>
                <text class="section-title small-title">Failure reasons</text>
                <text class="section-copy">Reason codes grouped into identity, lighting, composition, and technical buckets.</text>
              </view>
            </view>
            <view v-if="qualityReasons.length === 0" class="admin-state compact-state">
              <text class="state-title">No failure reasons</text>
              <text class="state-copy">Hard-gate rejects and repair-round misses will be counted here.</text>
            </view>
            <view v-else class="quality-table">
              <view class="quality-row quality-head-row reason-quality-row">
                <text class="th">Reason</text>
                <text class="th">Group</text>
                <text class="th">Count</text>
                <text class="th">Rounds</text>
                <text class="th">Top template</text>
              </view>
              <view v-for="item in qualityReasons" :key="item.reason" class="quality-row reason-quality-row">
                <text class="td mono">{{ item.reason }}</text>
                <text class="status-pill">{{ item.group || 'ops' }}</text>
                <text class="td">{{ item.count || 0 }}</text>
                <text class="td-muted">{{ roundCountsLabel(item.round_counts) }}</text>
                <text class="td-muted">{{ topTemplateLabel(item.top_templates) }}</text>
              </view>
            </view>
          </view>
        </view>

        <view class="repair-grid">
          <view>
            <view class="section-head compact-head quality-table-head">
              <view>
                <text class="section-title small-title">Repair rounds</text>
                <text class="section-copy">Success rate by generation / repair pass.</text>
              </view>
            </view>
            <view class="repair-strip">
              <view v-for="item in qualityRepairRounds" :key="item.round" class="repair-cell">
                <text class="metric-label">Round {{ item.round }}</text>
                <text class="metric-value small-value">{{ formatPercent(item.success_rate) }}</text>
                <text class="metric-sub">{{ item.successes || 0 }}/{{ item.attempts || 0 }} passed · score {{ formatNumber(item.avg_selected_score) }}</text>
                <text class="subtle">{{ topReasonLabel(item.top_reasons) }}</text>
              </view>
              <view v-if="qualityRepairRounds.length === 0" class="admin-state compact-state">
                <text class="state-title">No repair rounds</text>
                <text class="state-copy">Repair pass rates will appear after QA attempts are recorded.</text>
              </view>
            </view>
          </view>

          <view>
            <view class="section-head compact-head quality-table-head">
              <view>
                <text class="section-title small-title">Repair modes</text>
                <text class="section-copy">Shows whether relight-only and other repair policies are recovering usable images.</text>
              </view>
            </view>
            <view class="quality-table compact-quality-table">
              <view v-for="item in qualityRepairModes" :key="item.repair_mode" class="mode-row">
                <view>
                  <text class="strong mono">{{ item.repair_mode }}</text>
                  <text class="subtle">{{ item.successes || 0 }}/{{ item.attempts || 0 }} recovered</text>
                </view>
                <text class="status-pill" :class="{ active: Number(item.success_rate || 0) >= 0.8 }">
                  {{ formatPercent(item.success_rate) }}
                </text>
              </view>
              <view v-if="qualityRepairModes.length === 0" class="admin-state compact-state">
                <text class="state-title">No repair modes</text>
                <text class="state-copy">Mode-level recovery stats will appear after repair attempts.</text>
              </view>
            </view>
          </view>
        </view>
      </view>

      <view class="ops-grid">
        <view class="admin-card ops-card">
          <view class="section-head compact-head">
            <view>
              <text class="section-title">Admin access</text>
              <text class="section-copy">Use /admin after signing in with an authorized owner, admin, operator, ADMIN_EMAILS, or ADMIN_USER_IDS account.</text>
            </view>
          </view>
          <view class="diagnostic-list">
            <view class="diag-row">
              <text>Entry URL</text>
              <text class="mono">{{ adminMe?.entry_url || '/admin' }}</text>
            </view>
            <view class="diag-row">
              <text>Actor</text>
              <text class="mono">{{ adminMe?.actor || '--' }}</text>
            </view>
            <view class="diag-row">
              <text>Roles</text>
              <text class="mono">{{ adminRoleLabel }}</text>
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
          <view class="admin-actions-row">
            <button class="ghost-action" @tap="goUsers">Users & credits</button>
            <button class="ghost-action" @tap="goOrders">Orders</button>
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
            <input v-model="probeTemplateId" class="filter-input" :placeholder="`Template ID, default ${defaultProbeTemplateId}`" />
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

          <view v-if="currentProbeInputImages.length" class="probe-gallery">
            <view v-for="item in currentProbeInputImages" :key="item.label" class="probe-gallery-item">
              <text class="probe-gallery-label">{{ item.label }}</text>
              <image class="probe-image small" :src="item.url" mode="aspectFill" />
            </view>
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
            <view v-if="lastProbeInputImages.length || probePreviewUrl" class="probe-gallery">
              <view v-for="item in lastProbeInputImages" :key="item.label" class="probe-gallery-item">
                <text class="probe-gallery-label">{{ item.label }}</text>
                <image class="probe-image small" :src="item.url" mode="aspectFill" />
              </view>
              <view v-if="probePreviewUrl" class="probe-gallery-item generated">
                <text class="probe-gallery-label">Generated wedding image</text>
                <image class="probe-image" :src="probePreviewUrl" mode="aspectFill" />
              </view>
            </view>
            <button v-if="probeResult.order_id" class="ghost-action probe-order-action" @tap="goOrders">Open orders</button>
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

interface AnalyticsOverview {
  funnel?: {
    totals?: Record<string, number>;
    daily?: Array<Record<string, any>>;
  };
  template_ranking?: Array<Record<string, any>>;
  city_ranking?: Array<Record<string, any>>;
  quality_dashboard?: QualityDashboard;
}

interface QualityDashboard {
  days?: number;
  sampled_orders?: number;
  totals?: Record<string, any>;
  templates?: Array<Record<string, any>>;
  failure_reasons?: Array<Record<string, any>>;
  repair_rounds?: Array<Record<string, any>>;
  repair_modes?: Array<Record<string, any>>;
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
const analyticsOverview = ref<AnalyticsOverview | null>(null);
const qualityDashboard = ref<QualityDashboard | null>(null);
const testEmailTo = ref('');
const testEmailResult = ref('');
const sendingTestEmail = ref(false);
const probeImageUrl = ref('');
const probeSecondImageUrl = ref('');
const probeTemplateId = ref('');
const probeRemoteJoin = ref(false);
const probeInline = ref(true);
const runningProbe = ref(false);
const probeResult = ref<ProbeResponse | null>(null);
const lastProbeInputs = ref<{ primary: string; second: string }>({ primary: '', second: '' });

const recentOrders = computed(() => (stats.value.recent_activity || []).slice(0, 8));
const recentRiskEvents = computed(() => (riskOverview.value?.recent_events || []).slice(0, 6));
const funnelTotals = computed(() => analyticsOverview.value?.funnel?.totals || {});
const templateRanking = computed(() => (analyticsOverview.value?.template_ranking || []).slice(0, 8));
const qualityTotals = computed(() => qualityDashboard.value?.totals || {});
const qualityTemplates = computed(() => (qualityDashboard.value?.templates || []).slice(0, 6));
const qualityReasons = computed(() => (qualityDashboard.value?.failure_reasons || []).slice(0, 8));
const qualityRepairRounds = computed(() => qualityDashboard.value?.repair_rounds || []);
const qualityRepairModes = computed(() => qualityDashboard.value?.repair_modes || []);
const adminRoleLabel = computed(() => (adminMe.value?.admin_roles || []).join(', ') || '--');
const defaultProbeTemplateId = computed(() => (probeRemoteJoin.value ? 'royal_castle' : 'solo_royal_castle'));
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

const funnelCards = computed(() => {
  const totals = funnelTotals.value;
  const grades = (totals.identity_grade_counts || {}) as Record<string, number>;
  const blockingIdentity = Number(grades.major_mismatch || 0) + Number(grades.role_swap || 0);
  return [
    { label: 'Registered', value: fmtCount(totals.registered), sub: `Pay ${formatPercent(totals.payment_conversion_rate)}` },
    { label: 'Upload start', value: fmtCount(totals.upload_started), sub: `Success ${formatPercent(totals.upload_success_rate)}` },
    { label: 'Upload success', value: fmtCount(totals.upload_completed), sub: `Avg ${formatDuration(totals.avg_upload_duration_ms)}` },
    { label: 'Upload quality', value: formatNumber(totals.avg_upload_quality_score), sub: `Warn ${fmtCount(totals.upload_quality_warning)} / Poor ${fmtCount(totals.upload_quality_poor)}` },
    { label: 'Order created', value: fmtCount(totals.order_created), sub: `Done ${formatPercent(totals.generation_success_rate)}` },
    { label: 'Completed', value: fmtCount(totals.order_completed), sub: `QA fail ${formatPercent(totals.qa_failure_rate)}` },
    { label: 'Identity grade', value: fmtCount(blockingIdentity), sub: `Minor ${fmtCount(grades.minor_drift)} / Pass ${fmtCount(grades.identity_pass)}` },
    { label: 'Result viewed', value: fmtCount(totals.result_viewed), sub: `Repair ${formatNumber(totals.avg_repair_rounds)}` },
    { label: 'Paid', value: fmtCount(totals.payments_completed), sub: `Revenue $${formatNumber(totals.payment_revenue_usd)}` },
    { label: 'Downloaded', value: fmtCount(totals.download_success), sub: `Lock ${fmtCount(totals.download_locked_clicked)}` },
  ];
});

const qualityKpis = computed(() => {
  const totals = qualityTotals.value;
  return [
    {
      label: 'Sampled orders',
      value: fmtCount(qualityDashboard.value?.sampled_orders || totals.orders),
      sub: `${qualityDashboard.value?.days || 30} days · done ${formatPercent(totals.completion_rate)}`,
    },
    {
      label: 'QA failure',
      value: formatPercent(totals.qa_failure_rate),
      sub: `${fmtCount(totals.qa_failed_orders)} blocked before delivery`,
    },
    {
      label: 'Identity failures',
      value: formatPercent(totals.identity_failure_rate),
      sub: `${fmtCount(totals.identity_failed_orders)} face-consistency misses`,
    },
    {
      label: 'Lighting failures',
      value: formatPercent(totals.lighting_failure_rate),
      sub: `${fmtCount(totals.lighting_failed_orders)} photometric misses`,
    },
    {
      label: 'Relight recovery',
      value: formatPercent(totals.relight_success_rate),
      sub: `${fmtCount(totals.relight_successes)}/${fmtCount(totals.relight_attempts)} relight-only passed`,
    },
    {
      label: 'Avg repair rounds',
      value: formatNumber(totals.avg_repair_rounds),
      sub: `${fmtCount(totals.completed_orders)} completed · ${fmtCount(totals.failed_orders)} failed`,
    },
  ];
});

const currentProbeInputImages = computed(() => buildProbeInputImages(probeImageUrl.value, probeSecondImageUrl.value));
const lastProbeInputImages = computed(() => buildProbeInputImages(lastProbeInputs.value.primary, lastProbeInputs.value.second));

function isHttpImageUrl(value: string): boolean {
  return /^https?:\/\//i.test(String(value || '').trim());
}

function buildProbeInputImages(primary: string, second: string) {
  const items: Array<{ label: string; url: string }> = [];
  const first = String(primary || '').trim();
  const secondValue = String(second || '').trim();
  if (isHttpImageUrl(first)) items.push({ label: 'Uploaded source image 1', url: first });
  if (isHttpImageUrl(secondValue)) items.push({ label: 'Uploaded source image 2', url: secondValue });
  return items;
}

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

function fmtCount(value: unknown): string {
  return String(Number(value || 0));
}

function formatNumber(value: unknown): string {
  const numeric = Number(value || 0);
  return Number.isFinite(numeric) ? numeric.toFixed(2).replace(/\.00$/, '') : '0';
}

function formatPercent(value: unknown): string {
  const numeric = Number(value || 0);
  return `${(numeric * 100).toFixed(1)}%`;
}

function formatDuration(value: unknown): string {
  const ms = Number(value || 0);
  if (!Number.isFinite(ms) || ms <= 0) return '--';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function topReasonLabel(items: unknown): string {
  const rows = Array.isArray(items) ? items : [];
  const first = rows[0] as Record<string, any> | undefined;
  if (!first) return '--';
  const reason = String(first.reason || first.template_id || '--');
  return `${reason} (${Number(first.count || 0)})`;
}

function topTemplateLabel(items: unknown): string {
  const rows = Array.isArray(items) ? items : [];
  const first = rows[0] as Record<string, any> | undefined;
  if (!first) return '--';
  return `${first.template_id || 'unknown'} (${Number(first.count || 0)})`;
}

function roundCountsLabel(value: unknown): string {
  if (!value || typeof value !== 'object') return '--';
  const entries = Object.entries(value as Record<string, number>)
    .filter(([, count]) => Number(count || 0) > 0)
    .sort(([left], [right]) => Number(left) - Number(right))
    .slice(0, 3);
  if (!entries.length) return '--';
  return entries.map(([round, count]) => `R${round}:${count}`).join(' / ');
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
  const [emailDiag, logs, risk, analytics, quality] = await Promise.allSettled([
    get<EmailDiagnostics>('/admin/email_diagnostics', { showLoading: false, showError: false }),
    get<EmailLogItem[]>('/admin/email_logs?limit=8', { showLoading: false, showError: false }),
    get<RiskOverview>('/admin/risk_overview?days=7&limit=8', { showLoading: false, showError: false }),
    get<AnalyticsOverview>('/admin/analytics_overview?days=7&ranking_days=30&limit=8', { showLoading: false, showError: false }),
    get<QualityDashboard>('/admin/quality_dashboard?days=30&limit=10', { showLoading: false, showError: false }),
  ]);
  emailDiagnostics.value = emailDiag.status === 'fulfilled' ? emailDiag.value : null;
  emailLogs.value = logs.status === 'fulfilled' ? logs.value : [];
  riskOverview.value = risk.status === 'fulfilled' ? risk.value : null;
  analyticsOverview.value = analytics.status === 'fulfilled' ? analytics.value : null;
  qualityDashboard.value = quality.status === 'fulfilled'
    ? quality.value
    : (analyticsOverview.value?.quality_dashboard || null);
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
  const secondImageUrl = probeSecondImageUrl.value.trim();
  if (!isHttpImageUrl(imageUrl)) {
    probeResult.value = {
      ok: false,
      started: false,
      completed: false,
      execution_mode: probeInline.value ? 'inline' : 'arq',
      error_message: 'Enter a public http(s) portrait image URL.',
    };
    return;
  }
  if (probeRemoteJoin.value && !isHttpImageUrl(secondImageUrl)) {
    probeResult.value = {
      ok: false,
      started: false,
      completed: false,
      execution_mode: probeInline.value ? 'inline' : 'arq',
      error_message: 'Remote join probes require a second public http(s) portrait image URL.',
    };
    return;
  }
  runningProbe.value = true;
  probeResult.value = null;
  lastProbeInputs.value = { primary: imageUrl, second: secondImageUrl };
  try {
    probeResult.value = await post<ProbeResponse>(
      '/admin/generation_probe',
      {
        image_url: imageUrl,
        second_image_url: secondImageUrl || undefined,
        template_id: probeTemplateId.value.trim() || defaultProbeTemplateId.value,
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

function goUsers() {
  uni.navigateTo({ url: '/admin/users' });
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
  margin-bottom: 18px;
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

.admin-actions-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
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

.probe-gallery {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 14px;
}

.probe-gallery-item {
  min-width: 132px;
  max-width: 220px;
}

.probe-gallery-item.generated {
  max-width: 280px;
}

.probe-gallery-label {
  display: block;
  color: #687180;
  font-size: 12px;
  font-weight: 800;
}

.probe-image {
  margin-top: 12px;
  width: 180px;
  height: 180px;
  border-radius: 8px;
  background: #edf1f6;
}

.probe-image.small {
  width: 132px;
  height: 132px;
}

.probe-order-action {
  margin-top: 12px;
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

.small-title {
  font-size: 15px;
}

.funnel-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
  gap: 10px;
}

.funnel-cell {
  min-height: 116px;
  padding: 14px;
  border: 1px solid #edf1f6;
  border-radius: 8px;
  background: #fbfcfd;
}

.template-ranking {
  margin-top: 18px;
}

.quality-dashboard {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.quality-kpi-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
}

.quality-kpi,
.repair-cell {
  min-height: 112px;
  padding: 14px;
  border: 1px solid #edf1f6;
  border-radius: 8px;
  background: #fbfcfd;
  box-sizing: border-box;
}

.quality-tables-grid,
.repair-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.18fr) minmax(0, 1fr);
  gap: 16px;
}

.quality-table-head {
  min-height: 52px;
  margin-bottom: 10px;
}

.quality-table {
  border: 1px solid #edf1f6;
  border-radius: 8px;
  overflow: hidden;
}

.quality-row {
  display: grid;
  align-items: center;
  gap: 12px;
  min-height: 56px;
  padding: 10px 14px;
  border-bottom: 1px solid #edf1f6;
  box-sizing: border-box;
}

.quality-row:last-child {
  border-bottom: 0;
}

.quality-head-row {
  min-height: 42px;
  background: #f8fafc;
}

.template-quality-row {
  grid-template-columns: minmax(0, 1.4fr) 64px 78px 78px 78px minmax(0, 1fr);
}

.reason-quality-row {
  grid-template-columns: minmax(0, 1.2fr) 88px 58px 92px minmax(0, 1fr);
}

.repair-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
}

.compact-quality-table {
  min-height: 112px;
}

.mode-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 58px;
  padding: 10px 14px;
  border-bottom: 1px solid #edf1f6;
  box-sizing: border-box;
}

.mode-row:last-child {
  border-bottom: 0;
}

.template-row {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) repeat(5, minmax(72px, 0.5fr));
  align-items: center;
  gap: 12px;
  min-height: 58px;
  padding: 0 14px;
  border-bottom: 1px solid #edf1f6;
}

.template-row:last-child {
  border-bottom: 0;
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

  .quality-kpi-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .quality-tables-grid,
  .repair-grid {
    grid-template-columns: 1fr;
  }

  .funnel-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .metrics-grid,
  .ops-grid,
  .funnel-grid,
  .quality-kpi-grid,
  .template-quality-row,
  .reason-quality-row,
  .template-row,
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

  .probe-gallery-item,
  .probe-gallery-item.generated {
    max-width: none;
    width: 100%;
  }

  .probe-image,
  .probe-image.small {
    width: 100%;
    height: 220px;
  }
}
</style>
