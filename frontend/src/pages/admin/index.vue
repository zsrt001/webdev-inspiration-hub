<template>
  <AdminLayout
    active="overview"
    :title="tr('运营总览', 'Operations overview')"
    :subtitle="tr('检查后台权限、邮件投递、风控与商用质量信号。', 'Verify admin access, email delivery, risk controls, and commercial quality signals.')"
  >
    <view v-if="loading" class="admin-card admin-state">
      <text class="state-title">{{ tr('正在加载后台', 'Loading admin console') }}</text>
      <text class="state-copy">{{ tr('正在校验管理员权限并读取生产信号。', 'Checking admin permission and reading production signals.') }}</text>
    </view>

    <view v-else-if="error" class="admin-card admin-state">
      <text class="state-title">{{ tr('需要后台权限', 'Admin access required') }}</text>
      <text class="state-copy">{{ error }}</text>
      <button class="primary-action" @tap="goLogin">{{ tr('登录', 'Sign in') }}</button>
    </view>

    <template v-else>
      <view class="metrics-grid">
        <view class="metric-card admin-card">
          <text class="metric-label">{{ tr('用户', 'Users') }}</text>
          <text class="metric-value">{{ stats.total_users }}</text>
          <text class="metric-sub">{{ tr('7 天新增', 'New in 7 days') }} {{ stats.recent_users || 0 }}</text>
        </view>
        <view class="metric-card admin-card">
          <text class="metric-label">{{ tr('订单', 'Orders') }}</text>
          <text class="metric-value">{{ stats.total_orders }}</text>
          <text class="metric-sub">{{ tr('7 天新增', 'New in 7 days') }} {{ stats.recent_orders || 0 }}</text>
        </view>
        <view class="metric-card admin-card">
          <text class="metric-label">{{ tr('积分收入', 'Credit revenue') }}</text>
          <text class="metric-value">{{ stats.total_revenue_credits || 0 }}</text>
          <text class="metric-sub">{{ tr('流通积分', 'Credits in circulation') }} {{ stats.total_credits_in_circulation || 0 }}</text>
        </view>
        <view class="metric-card admin-card">
          <text class="metric-label">{{ tr('订阅 MRR', 'Subscription MRR') }}</text>
          <text class="metric-value">{{ formatMoney(stats.subscription_mrr_cents || 0) }}</text>
          <text class="metric-sub">{{ tr('活跃订阅', 'Active subscriptions') }} {{ stats.active_subscriptions || 0 }}</text>
        </view>
      </view>

      <view class="admin-card overview-section">
        <view class="section-head">
          <view>
            <text class="section-title">{{ tr('商业漏斗', 'Commercial funnel') }}</text>
            <text class="section-copy">{{ tr('7 天漏斗，包含上传质量、身份一致性、支付和交付信号。', '7-day funnel with upload quality, identity grade, payment, and delivery signals.') }}</text>
          </view>
          <button class="ghost-action" @tap="refreshOps">{{ tr('刷新', 'Refresh') }}</button>
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
              <text class="section-title small-title">{{ tr('模板转化', 'Template conversion') }}</text>
              <text class="section-copy">{{ tr('A/B 选择会和点击、订单、完成及下载一起进入模板排序。', 'A/B picks feed template ranking together with clicks, orders, completions, and downloads.') }}</text>
            </view>
          </view>
          <view v-if="templateRanking.length === 0" class="admin-state compact-state">
            <text class="state-title">{{ tr('暂无模板数据', 'No template data yet') }}</text>
            <text class="state-copy">{{ tr('用户浏览并生成后，模板活动会显示在这里。', 'Template activity will appear after users browse and generate.') }}</text>
          </view>
          <view v-else class="recent-list">
            <view v-for="item in templateRanking" :key="item.template_id" class="template-row">
              <view>
                <text class="strong">{{ item.template_title || item.template_id }}</text>
                <text class="subtle mono">{{ item.template_id }}</text>
              </view>
              <text class="td-muted">{{ tr('点击', 'Clicks') }} {{ item.clicks || 0 }}</text>
              <text class="td-muted">A/B {{ item.ab_picks || 0 }}</text>
              <text class="td-muted">{{ tr('订单', 'Orders') }} {{ item.orders || 0 }}</text>
              <text class="td-muted">{{ tr('完成', 'Done') }} {{ formatPercent(item.completion_rate) }}</text>
              <text class="td-muted">{{ tr('得分', 'Score') }} {{ formatNumber(item.ranking_score) }}</text>
            </view>
          </view>
        </view>
      </view>

      <view class="admin-card overview-section quality-dashboard">
        <view class="section-head">
          <view>
            <text class="section-title">{{ tr('订单质量看板', 'Order quality dashboard') }}</text>
            <text class="section-copy">{{ tr('按模板、失败原因和修复轮次查看 30 天质检结果。', '30-day gate results by template, failure reason, and repair-round recovery.') }}</text>
          </view>
          <button class="ghost-action" @tap="refreshOps">{{ tr('刷新', 'Refresh') }}</button>
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
                <text class="section-title small-title">{{ tr('模板失败地图', 'Template failure map') }}</text>
                <text class="section-copy">{{ tr('按 QA 失败和订单量排序的高风险模板。', 'Top templates ranked by QA failures and order volume.') }}</text>
              </view>
            </view>
            <view v-if="qualityTemplates.length === 0" class="admin-state compact-state">
              <text class="state-title">{{ tr('暂无质量数据', 'No quality data yet') }}</text>
              <text class="state-copy">{{ tr('订单完成或质检失败后，会出现模板级 QA 数据。', 'Template-level QA data will appear after orders complete or fail gates.') }}</text>
            </view>
            <view v-else class="quality-table">
              <view class="quality-row quality-head-row template-quality-row">
                <text class="th">{{ tr('模板', 'Template') }}</text>
                <text class="th">{{ tr('订单', 'Orders') }}</text>
                <text class="th">QA fail</text>
                <text class="th">{{ tr('身份', 'Identity') }}</text>
                <text class="th">{{ tr('布光', 'Lighting') }}</text>
                <text class="th">{{ tr('首要原因', 'Top reason') }}</text>
              </view>
              <view v-for="item in qualityTemplates" :key="item.template_id" class="quality-row template-quality-row">
                <view>
                  <text class="strong">{{ item.template_id }}</text>
                  <text class="subtle">{{ tr('完成', 'Done') }} {{ formatPercent(item.completion_rate) }} / {{ tr('修复', 'repair') }} {{ formatNumber(item.avg_repair_rounds) }}</text>
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
                <text class="section-title small-title">{{ tr('失败原因', 'Failure reasons') }}</text>
                <text class="section-copy">{{ tr('按身份、布光、构图和技术问题归类失败代码。', 'Reason codes grouped into identity, lighting, composition, and technical buckets.') }}</text>
              </view>
            </view>
            <view v-if="qualityReasons.length === 0" class="admin-state compact-state">
              <text class="state-title">{{ tr('暂无失败原因', 'No failure reasons') }}</text>
              <text class="state-copy">{{ tr('硬性拦截和修复轮次未通过会统计在这里。', 'Hard-gate rejects and repair-round misses will be counted here.') }}</text>
            </view>
            <view v-else class="quality-table">
              <view class="quality-row quality-head-row reason-quality-row">
                <text class="th">{{ tr('原因', 'Reason') }}</text>
                <text class="th">{{ tr('分组', 'Group') }}</text>
                <text class="th">{{ tr('数量', 'Count') }}</text>
                <text class="th">{{ tr('轮次', 'Rounds') }}</text>
                <text class="th">{{ tr('高发模板', 'Top template') }}</text>
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
                <text class="section-title small-title">{{ tr('修复轮次', 'Repair rounds') }}</text>
                <text class="section-copy">{{ tr('按生成/修复轮次查看通过率。', 'Success rate by generation / repair pass.') }}</text>
              </view>
            </view>
            <view class="repair-strip">
              <view v-for="item in qualityRepairRounds" :key="item.round" class="repair-cell">
                <text class="metric-label">{{ tr('轮次', 'Round') }} {{ item.round }}</text>
                <text class="metric-value small-value">{{ formatPercent(item.success_rate) }}</text>
                <text class="metric-sub">{{ item.successes || 0 }}/{{ item.attempts || 0 }} {{ tr('通过', 'passed') }} · {{ tr('得分', 'score') }} {{ formatNumber(item.avg_selected_score) }}</text>
                <text class="subtle">{{ topReasonLabel(item.top_reasons) }}</text>
              </view>
              <view v-if="qualityRepairRounds.length === 0" class="admin-state compact-state">
                <text class="state-title">{{ tr('暂无修复轮次', 'No repair rounds') }}</text>
                <text class="state-copy">{{ tr('记录 QA 尝试后会显示修复通过率。', 'Repair pass rates will appear after QA attempts are recorded.') }}</text>
              </view>
            </view>
          </view>

          <view>
            <view class="section-head compact-head quality-table-head">
              <view>
                <text class="section-title small-title">{{ tr('修复模式', 'Repair modes') }}</text>
                <text class="section-copy">{{ tr('查看重打光等修复策略是否能恢复可交付图片。', 'Shows whether relight-only and other repair policies are recovering usable images.') }}</text>
              </view>
            </view>
            <view class="quality-table compact-quality-table">
              <view v-for="item in qualityRepairModes" :key="item.repair_mode" class="mode-row">
                <view>
                  <text class="strong mono">{{ item.repair_mode }}</text>
                  <text class="subtle">{{ item.successes || 0 }}/{{ item.attempts || 0 }} {{ tr('已恢复', 'recovered') }}</text>
                </view>
                <text class="status-pill" :class="{ active: Number(item.success_rate || 0) >= 0.8 }">
                  {{ formatPercent(item.success_rate) }}
                </text>
              </view>
              <view v-if="qualityRepairModes.length === 0" class="admin-state compact-state">
                <text class="state-title">{{ tr('暂无修复模式', 'No repair modes') }}</text>
                <text class="state-copy">{{ tr('修复尝试后会出现模式级恢复数据。', 'Mode-level recovery stats will appear after repair attempts.') }}</text>
              </view>
            </view>
          </view>
        </view>
      </view>

      <view class="ops-grid">
        <view class="admin-card ops-card">
          <view class="section-head compact-head">
            <view>
              <text class="section-title">{{ tr('后台权限', 'Admin access') }}</text>
              <text class="section-copy">{{ tr('使用已登录且数据库角色为 owner、admin 或 operator 的账号访问 /admin。', 'Use /admin with a signed-in account whose database role is owner, admin, or operator.') }}</text>
            </view>
          </view>
          <view class="diagnostic-list">
            <view class="diag-row">
              <text>{{ tr('入口 URL', 'Entry URL') }}</text>
              <text class="mono">{{ adminMe?.entry_url || '/admin' }}</text>
            </view>
            <view class="diag-row">
              <text>{{ tr('操作者', 'Actor') }}</text>
              <text class="mono">{{ adminMe?.actor || '--' }}</text>
            </view>
            <view class="diag-row">
              <text>{{ tr('角色', 'Roles') }}</text>
              <text class="mono">{{ adminRoleLabel }}</text>
            </view>
            <view class="diag-row">
              <text>{{ tr('生成模式', 'Generation mode') }}</text>
              <text class="mono">{{ adminMe?.generation_execution_mode || '--' }}</text>
            </view>
          </view>
          <view class="admin-actions-row">
            <button class="ghost-action" @tap="goUsers">{{ tr('用户与积分', 'Users & credits') }}</button>
            <button class="ghost-action" @tap="goOrders">{{ tr('订单', 'Orders') }}</button>
          </view>
        </view>

        <view class="admin-card ops-card">
          <view class="section-head compact-head">
            <view>
              <text class="section-title">{{ tr('邮件投递', 'Email delivery') }}</text>
              <text class="section-copy">{{ tr('生产发件配置、DNS 信号和最近投递记录。', 'Production sender config, DNS signals, and latest delivery attempts.') }}</text>
            </view>
            <button class="ghost-action" @tap="refreshOps">{{ tr('刷新', 'Refresh') }}</button>
          </view>

          <view class="diagnostic-list">
            <view class="diag-row">
              <text>Resend API key</text>
              <text class="status-pill" :class="{ active: emailDiagnostics?.resend_api_key_configured }">
                {{ emailDiagnostics?.resend_api_key_configured ? tr('已配置', 'Configured') : tr('缺失', 'Missing') }}
              </text>
            </view>
            <view class="diag-row">
              <text>{{ tr('发件域名', 'From domain') }}</text>
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
              {{ sendingTestEmail ? tr('发送中...', 'Sending...') : tr('发送测试', 'Send test') }}
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
              <text class="section-title">{{ tr('注册风控', 'Signup risk') }}</text>
              <text class="section-copy">{{ tr('新手积分、验证、设备、IP 和邮箱域名滥用信号。', 'Starter-credit, verification, device, IP, and email-domain abuse signals.') }}</text>
            </view>
          </view>

          <view class="risk-summary">
            <view>
              <text class="metric-label">{{ tr('事件', 'Events') }}</text>
              <text class="metric-value small-value">{{ riskOverview?.total_events || 0 }}</text>
            </view>
            <view>
              <text class="metric-label">{{ tr('欢迎积分', 'Welcome credits') }}</text>
              <text class="metric-value small-value">{{ riskOverview?.welcome_bonus_count || 0 }}</text>
            </view>
            <view>
              <text class="metric-label">{{ tr('已拦截', 'Blocked') }}</text>
              <text class="metric-value small-value">{{ riskOverview?.blocked_events || 0 }}</text>
            </view>
            <view>
              <text class="metric-label">{{ tr('高风险', 'High risk') }}</text>
              <text class="metric-value small-value">{{ riskOverview?.high_risk_events || 0 }}</text>
            </view>
          </view>

          <view class="mini-log-list">
            <view v-for="event in recentRiskEvents" :key="event.id" class="mini-log-row">
              <view>
                <text class="strong">{{ event.event_type }} / {{ event.provider || '--' }}</text>
                <text class="subtle">{{ event.email_domain || 'no-domain' }} / {{ tr('分数', 'score') }} {{ event.risk_score }}</text>
              </view>
              <text class="td-muted">{{ formatDate(event.created_at) }}</text>
            </view>
          </view>
        </view>
      </view>

      <view class="admin-card overview-section">
        <view class="section-head">
          <view>
            <text class="section-title">{{ tr('最近订单', 'Recent orders') }}</text>
            <text class="section-copy">{{ tr('快速查看生成链路状态。', 'A quick view of the generation pipeline status.') }}</text>
          </view>
          <button class="ghost-action" @tap="goOrders">{{ tr('查看全部订单', 'View all orders') }}</button>
        </view>

        <view v-if="recentOrders.length === 0" class="admin-state compact-state">
          <text class="state-title">{{ tr('暂无订单', 'No orders yet') }}</text>
          <text class="state-copy">{{ tr('创建后的订单会显示在这里。', 'Created orders will appear here.') }}</text>
        </view>
        <view v-else class="recent-list">
          <view v-for="order in recentOrders" :key="order.id" class="recent-row">
            <view>
              <text class="strong mono">{{ shortId(order.id) }}</text>
              <text class="subtle">{{ order.template_title || order.template_id || tr('无模板', 'No template') }}</text>
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
import { get, post } from '../../utils/api';
import { useI18nStore } from '../../stores/i18n';

interface AdminMe {
  actor: string;
  admin_roles: string[];
  entry_url: string;
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

const loading = ref(true);
const error = ref('');
const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);
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
const dnsSummary = computed(() => {
  const dns = emailDiagnostics.value?.dns || {};
  const ok = tr('正常', 'ok');
  const missing = tr('缺失', 'missing');
  return `SPF ${dns.spf_found ? ok : missing} | DMARC ${dns.dmarc_found ? ok : missing} | MX ${dns.mx_found ? ok : missing}`;
});

const funnelCards = computed(() => {
  const totals = funnelTotals.value;
  const grades = (totals.identity_grade_counts || {}) as Record<string, number>;
  const blockingIdentity = Number(grades.major_mismatch || 0) + Number(grades.role_swap || 0);
  return [
    { label: tr('已注册', 'Registered'), value: fmtCount(totals.registered), sub: `${tr('支付', 'Pay')} ${formatPercent(totals.payment_conversion_rate)}` },
    { label: tr('开始上传', 'Upload start'), value: fmtCount(totals.upload_started), sub: `${tr('成功', 'Success')} ${formatPercent(totals.upload_success_rate)}` },
    { label: tr('上传成功', 'Upload success'), value: fmtCount(totals.upload_completed), sub: `${tr('平均', 'Avg')} ${formatDuration(totals.avg_upload_duration_ms)}` },
    { label: tr('上传质量', 'Upload quality'), value: formatNumber(totals.avg_upload_quality_score), sub: `${tr('警告', 'Warn')} ${fmtCount(totals.upload_quality_warning)} / ${tr('较差', 'Poor')} ${fmtCount(totals.upload_quality_poor)}` },
    { label: tr('创建订单', 'Order created'), value: fmtCount(totals.order_created), sub: `${tr('完成', 'Done')} ${formatPercent(totals.generation_success_rate)}` },
    { label: tr('已完成', 'Completed'), value: fmtCount(totals.order_completed), sub: `QA ${tr('失败', 'fail')} ${formatPercent(totals.qa_failure_rate)}` },
    { label: tr('身份等级', 'Identity grade'), value: fmtCount(blockingIdentity), sub: `${tr('轻微漂移', 'Minor')} ${fmtCount(grades.minor_drift)} / ${tr('通过', 'Pass')} ${fmtCount(grades.identity_pass)}` },
    { label: tr('查看结果', 'Result viewed'), value: fmtCount(totals.result_viewed), sub: `${tr('修复', 'Repair')} ${formatNumber(totals.avg_repair_rounds)}` },
    { label: tr('已支付', 'Paid'), value: fmtCount(totals.payments_completed), sub: `${tr('收入', 'Revenue')} $${formatNumber(totals.payment_revenue_usd)}` },
    { label: tr('已下载', 'Downloaded'), value: fmtCount(totals.download_success), sub: `${tr('下载锁点击', 'Lock')} ${fmtCount(totals.download_locked_clicked)}` },
  ];
});

const qualityKpis = computed(() => {
  const totals = qualityTotals.value;
  return [
    {
      label: tr('采样订单', 'Sampled orders'),
      value: fmtCount(qualityDashboard.value?.sampled_orders || totals.orders),
      sub: `${qualityDashboard.value?.days || 30} ${tr('天', 'days')} · ${tr('完成', 'done')} ${formatPercent(totals.completion_rate)}`,
    },
    {
      label: 'QA ' + tr('失败', 'failure'),
      value: formatPercent(totals.qa_failure_rate),
      sub: `${fmtCount(totals.qa_failed_orders)} ${tr('交付前被拦截', 'blocked before delivery')}`,
    },
    {
      label: tr('身份失败', 'Identity failures'),
      value: formatPercent(totals.identity_failure_rate),
      sub: `${fmtCount(totals.identity_failed_orders)} ${tr('人脸一致性未达标', 'face-consistency misses')}`,
    },
    {
      label: tr('布光失败', 'Lighting failures'),
      value: formatPercent(totals.lighting_failure_rate),
      sub: `${fmtCount(totals.lighting_failed_orders)} ${tr('光照未达标', 'photometric misses')}`,
    },
    {
      label: tr('重打光恢复', 'Relight recovery'),
      value: formatPercent(totals.relight_success_rate),
      sub: `${fmtCount(totals.relight_successes)}/${fmtCount(totals.relight_attempts)} ${tr('仅重打光通过', 'relight-only passed')}`,
    },
    {
      label: tr('平均修复轮次', 'Avg repair rounds'),
      value: formatNumber(totals.avg_repair_rounds),
      sub: `${fmtCount(totals.completed_orders)} ${tr('完成', 'completed')} · ${fmtCount(totals.failed_orders)} ${tr('失败', 'failed')}`,
    },
  ];
});

function shortId(value: string): string {
  return value.length > 14 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value;
}

function formatDate(value?: string): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleString(i18nStore.locale === 'zh' ? 'zh-CN' : 'en-US', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
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
    await loadAdminMe();
    await Promise.all([loadCoreDashboard(), refreshOps()]);
  } catch (err: any) {
    error.value = err?.statusCode === 401 || err?.statusCode === 403
      ? tr('当前账号没有后台权限。请确认已登录，并由数据库管理员授予 owner、admin 或 operator 角色。', 'This account is not authorized for admin access. Sign in and ask a database administrator to grant the owner, admin, or operator role.')
      : (err?.message || tr('后台数据加载失败，请重试。', 'Admin data failed to load. Please retry.'));
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
    testEmailResult.value = tr('请输入有效的收件邮箱。', 'Enter a valid recipient email.');
    return;
  }
  sendingTestEmail.value = true;
  testEmailResult.value = '';
  try {
    const result: any = await post('/admin/email_test', { to }, { showLoading: false, showError: false });
    testEmailResult.value = result?.sent
      ? tr('测试邮件已发送。', 'Test email sent.')
      : tr(`发送失败：${result?.reason || result?.status || result?.error || 'unknown'}`, `Send failed: ${result?.reason || result?.status || result?.error || 'unknown'}`);
    await refreshOps();
  } catch (err: any) {
    testEmailResult.value = err?.message || tr('测试邮件失败。', 'Test email failed.');
  } finally {
    sendingTestEmail.value = false;
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
@use './admin.scss';

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
.mini-log-list {
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

}
</style>
