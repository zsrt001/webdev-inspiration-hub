<template>
  <view class="admin-page">
    <view class="admin-header">
      <view class="header-content">
        <text class="header-title heading-serif">{{ tx('headerTitle') }}</text>
        <text class="header-subtitle">{{ tx('headerSubtitle') }}</text>
      </view>
      <view class="header-badge">
        <text class="badge-text">{{ tx('adminMode') }}</text>
      </view>
    </view>

    <view class="admin-token-bar">
      <input
        v-model="adminTokenInput"
        class="token-input"
        :placeholder="tx('tokenPlaceholder')"
        :password="true"
      />
      <button class="token-save" @tap="saveAdminToken">{{ tx('apply') }}</button>
    </view>

    <view v-if="loading" class="loading-state">
      <text class="loading-icon">...</text>
      <text class="loading-text">{{ tx('loading') }}</text>
    </view>

    <view v-else class="dashboard-content">
      <view class="kpi-row">
        <view class="kpi-card">
          <view class="kpi-icon">🧾</view>
          <text class="kpi-label">{{ tx('totalOrders') }}</text>
          <text class="kpi-value">{{ stats.total_orders }}</text>
        </view>

        <view class="kpi-card green">
          <view class="kpi-icon">💎</view>
          <text class="kpi-label">{{ tx('revenueCredits') }}</text>
          <text class="kpi-value">{{ stats.total_revenue_credits }}</text>
          <text class="kpi-sub">${{ stats.estimated_revenue_usd || 0 }}</text>
        </view>

        <view class="kpi-card purple">
          <view class="kpi-icon">👥</view>
          <text class="kpi-label">{{ tx('totalUsers') }}</text>
          <text class="kpi-value">{{ stats.total_users }}</text>
        </view>

        <view class="kpi-card blue">
          <view class="kpi-icon">*</view>
          <text class="kpi-label">{{ tx('active24h') }}</text>
          <text class="kpi-value">{{ stats.active_users_24h }}</text>
        </view>
      </view>

      <view class="section god-mode">
        <view class="section-header">
          <text class="section-title">{{ tx('godModeTitle') }}</text>
          <text class="section-desc">{{ tx('godModeDesc') }}</text>
        </view>

        <view class="control-panel">
          <view class="input-group">
            <text class="input-label">{{ tx('userId') }}</text>
            <input
              v-model="targetUserId"
              class="text-input"
              :placeholder="tx('userIdPlaceholder')"
            />
          </view>

          <view class="input-group">
            <text class="input-label">{{ tx('amount') }}</text>
            <input
              v-model="creditAmount"
              type="number"
              class="number-input"
              :placeholder="tx('amountPlaceholder')"
            />
          </view>

          <button class="boost-btn" @tap="grantCredits" :disabled="granting">
            {{ granting ? tx('granting') : tx('grantCredits') }}
          </button>
        </view>

        <view v-if="grantResult" class="grant-result success">
          {{ tx('grantedPrefix') }} {{ grantResult.credits_granted }} {{ tx('grantedMiddle') }} {{ grantResult.user_id }}
          <text class="new-balance">{{ tx('newBalance') }} {{ grantResult.new_balance }}</text>
        </view>
      </view>

      <view class="section">
        <view class="section-header">
          <view>
            <text class="section-title">{{ tx('opsConfigTitle') }}</text>
            <text class="section-desc">{{ tx('opsConfigDesc') }}</text>
          </view>
        </view>

        <textarea
          v-model="opsConfigText"
          class="ops-textarea"
          :placeholder="tx('opsConfigPlaceholder')"
          auto-height
        />

        <view class="ops-actions">
          <button class="token-save" @tap="fetchOpsConfig">{{ tx('reloadConfig') }}</button>
          <button class="boost-btn" @tap="saveOpsConfig" :disabled="opsSaving || !opsConfigText.trim()">
            {{ opsSaving ? tx('savingConfig') : tx('saveConfig') }}
          </button>
        </view>
      </view>



      <view class="section">
        <view class="section-header">
          <view>
            <text class="section-title">{{ tx('analyticsTitle') }}</text>
            <text class="section-desc">{{ tx('analyticsDesc') }}</text>
          </view>
          <button class="token-save" @tap="fetchAnalyticsOverview">
            {{ analyticsLoading ? tx('loadingReport') : tx('reloadAnalytics') }}
          </button>
        </view>
        <textarea
          :value="analyticsSummaryText"
          class="ops-textarea readonly-textarea"
          :placeholder="tx('analyticsPlaceholder')"
          auto-height
          disabled
        />
      </view>

      <view class="section">
        <view class="section-header">
          <view>
            <text class="section-title">{{ tx('opsOverviewTitle') }}</text>
            <text class="section-desc">{{ tx('opsOverviewDesc') }}</text>
          </view>
          <button class="token-save" @tap="fetchOpsOverview">
            {{ opsOverviewLoading ? tx('loadingReport') : tx('reloadOpsOverview') }}
          </button>
        </view>
        <textarea
          :value="opsOverviewText"
          class="ops-textarea readonly-textarea"
          :placeholder="tx('opsOverviewPlaceholder')"
          auto-height
          disabled
        />
      </view>

      <view class="section">
        <view class="section-header">
          <view>
            <text class="section-title">{{ tx('opsAlertsTitle') }}</text>
            <text class="section-desc">{{ tx('opsAlertsDesc') }}</text>
          </view>
          <button class="token-save" @tap="fetchOpsAlerts">
            {{ opsAlertsLoading ? tx('loadingReport') : tx('reloadOpsAlerts') }}
          </button>
        </view>
        <textarea
          :value="opsAlertsText"
          class="ops-textarea readonly-textarea"
          :placeholder="tx('opsAlertsPlaceholder')"
          auto-height
          disabled
        />
      </view>

      <view class="section">
        <view class="section-header">
          <view>
            <text class="section-title">{{ tx('crmTitle') }}</text>
            <text class="section-desc">{{ tx('crmDesc') }}</text>
          </view>
          <view class="leads-header-actions">
            <button class="token-save" @tap="fetchCrmPreview">
              {{ crmLoading ? tx('loadingReport') : tx('reloadCrmPreview') }}
            </button>
            <button class="boost-btn" @tap="pushCrmBatch" :disabled="crmPushing">
              {{ crmPushing ? tx('pushingCrm') : tx('pushCrm') }}
            </button>
          </view>
        </view>
        <textarea
          :value="crmPreviewText"
          class="ops-textarea readonly-textarea"
          :placeholder="tx('crmPlaceholder')"
          auto-height
          disabled
        />
        <textarea
          :value="crmHistoryText"
          class="ops-textarea readonly-textarea"
          :placeholder="tx('crmHistoryPlaceholder')"
          auto-height
          disabled
          style="margin-top: 14px;"
        />
      </view>

      <view class="section">
        <view class="section-header">
          <text class="section-title">{{ tx('templateOrders') }}</text>
        </view>

        <view class="breakdown-grid">
          <view
            v-for="(count, templateId) in stats.template_breakdown"
            :key="templateId"
            class="breakdown-item"
          >
            <text class="breakdown-label">{{ templateId }}</text>
            <text class="breakdown-value">{{ count }}</text>
          </view>
        </view>
      </view>

      <view class="section">
        <view class="section-header">
          <text class="section-title">{{ tx('recentGenerations') }}</text>
          <text class="section-count">{{ stats.recent_activity?.length || 0 }} {{ tx('items') }}</text>
        </view>

        <view v-if="stats.recent_activity?.length > 0" class="audit-grid">
          <view
            v-for="order in stats.recent_activity"
            :key="order.id"
            class="audit-item"
          >
            <image
              class="audit-image"
              :src="order.image_url"
              mode="aspectFill"
              @error="handleImageError"
            />
            <view class="audit-meta">
              <text class="audit-template">{{ order.template_title || order.template_id }}</text>
              <text class="audit-date">{{ formatDate(order.created_at) }}</text>
            </view>
          </view>
        </view>

        <view v-else class="empty-audit">
          <text class="empty-text">{{ tx('noGenerations') }}</text>
        </view>
      </view>

      <view class="section">
        <view class="section-header leads-header">
          <text class="section-title">{{ tx('leads') }}</text>
          <view class="leads-header-actions">
            <button class="token-save" @tap="fetchLeads">{{ tx('reloadLeads') }}</button>
            <button class="export-btn" @tap="exportLeads">{{ tx('exportCsv') }}</button>
          </view>
        </view>

        <view class="lead-filters">
          <input v-model="leadFilters.city" class="text-input compact" :placeholder="tx('filterCity')" />
          <input v-model="leadFilters.sourcePage" class="text-input compact" :placeholder="tx('filterSourcePage')" />
          <input v-model="leadFilters.templateId" class="text-input compact" :placeholder="tx('filterTemplateId')" />
        </view>

        <view v-if="leads.length > 0" class="leads-list">
          <view v-for="lead in leads" :key="lead.id" class="lead-item">
            <view class="lead-main">
              <text class="lead-name">{{ lead.name }}</text>
              <text class="lead-phone">{{ maskPhone(lead.phone) }}</text>
            </view>
            <view class="lead-sub">
              <text class="lead-city">{{ lead.city }}</text>
              <text class="lead-time">{{ formatDate(lead.created_at) }}</text>
            </view>
          </view>
        </view>
        <view v-else class="empty-audit">
          <text class="empty-text">{{ tx('noLeads') }}</text>
        </view>
      </view>

      <view class="section">
        <view class="section-header">
          <text class="section-title">{{ tx('users') }}</text>
        </view>

        <view class="users-list">
          <view
            v-for="user in users"
            :key="user.user_id"
            class="user-item"
          >
            <text class="user-id">{{ user.user_id }}</text>
            <text class="user-balance">💎 {{ user.balance }}</text>
          </view>
        </view>
      </view>
    </view>

    <view class="admin-footer">
      <text class="footer-text">{{ tx('footer') }}</text>
    </view>
  </view>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { get, post, put, resolveApiUrl } from '../../utils/api';
import { useI18nStore } from '../../stores/i18n';

interface DashboardStats {
  total_orders: number;
  total_revenue_credits: number;
  estimated_revenue_usd: number;
  total_users: number;
  active_users_24h: number;
  total_credits_in_circulation: number;
  template_breakdown: Record<string, number>;
  recent_activity: Array<{
    id: string;
    image_url: string;
    template_id: string;
    template_title?: string;
    created_at: string;
  }>;
}

interface User {
  user_id: string;
  balance: number;
}

interface GrantResult {
  success: boolean;
  user_id: string;
  credits_granted: number;
  new_balance: number;
}

interface LeadItem {
  id: string;
  name: string;
  phone: string;
  city: string;
  created_at: string;
  notes?: string | null;
  meta?: Record<string, string> | null;
}


interface OpsConfigResponse {
  template_overrides: Record<string, Record<string, any>>;
  pricing: Record<string, any>;
  placements: Record<string, any>;
  feature_flags: Record<string, any>;
  recommendations: Record<string, any>;
  crm: Record<string, any>;
}


interface AnalyticsOverviewResponse {
  funnel: Record<string, any>;
  template_ranking: Array<Record<string, any>>;
  city_ranking: Array<Record<string, any>>;
}

interface OpsOverviewResponse {
  runtime: Record<string, any>;
  orders: Record<string, number>;
  live_portrait: Record<string, number>;
  payments: Record<string, number>;
  costs: Array<Record<string, any>>;
  recent_failures: Array<Record<string, any>>;
}

interface OpsAlertItem {
  level: string;
  code: string;
  title: string;
  detail: string;
  metric: Record<string, any>;
}

interface CrmPushResponse {
  pushed: boolean;
  reason: string;
  status_code?: number | null;
  payload: Record<string, any>;
  response_text?: string | null;
}

type TextKey =
  | 'headerTitle'
  | 'headerSubtitle'
  | 'adminMode'
  | 'tokenPlaceholder'
  | 'apply'
  | 'loading'
  | 'totalOrders'
  | 'revenueCredits'
  | 'totalUsers'
  | 'active24h'
  | 'godModeTitle'
  | 'godModeDesc'
  | 'userId'
  | 'userIdPlaceholder'
  | 'amount'
  | 'amountPlaceholder'
  | 'granting'
  | 'grantCredits'
  | 'grantedPrefix'
  | 'grantedMiddle'
  | 'newBalance'
  | 'templateOrders'
  | 'recentGenerations'
  | 'items'
  | 'noGenerations'
  | 'leads'
  | 'exportCsv'
  | 'reloadLeads'
  | 'filterCity'
  | 'filterSourcePage'
  | 'filterTemplateId'
  | 'noLeads'
  | 'users'
  | 'opsConfigTitle'
  | 'opsConfigDesc'
  | 'opsConfigPlaceholder'
  | 'analyticsTitle'
  | 'analyticsDesc'
  | 'analyticsPlaceholder'
  | 'reloadAnalytics'
  | 'loadingReport'
  | 'opsOverviewTitle'
  | 'opsOverviewDesc'
  | 'opsOverviewPlaceholder'
  | 'reloadOpsOverview'
  | 'opsAlertsTitle'
  | 'opsAlertsDesc'
  | 'opsAlertsPlaceholder'
  | 'reloadOpsAlerts'
  | 'crmTitle'
  | 'crmDesc'
  | 'crmPlaceholder'
  | 'reloadCrmPreview'
  | 'pushCrm'
  | 'pushingCrm'
  | 'crmPushSuccess'
  | 'crmPushFailed'
  | 'crmHistoryPlaceholder'
  | 'reloadConfig'
  | 'saveConfig'
  | 'savingConfig'
  | 'configSaved'
  | 'configLoadFailed'
  | 'configSaveFailed'
  | 'configJsonInvalid'
  | 'footer'
  | 'tokenApplied'
  | 'tokenCleared'
  | 'tokenSaveFailed'
  | 'dashboardLoadFailed'
  | 'exportFailed'
  | 'enterUserId'
  | 'enterAmount'
  | 'creditsGranted'
  | 'grantFailed';

const i18nStore = useI18nStore();

const textMap: Record<TextKey, { zh: string; en: string }> = {
  headerTitle: { zh: '\u8fd0\u8425\u770b\u677f', en: 'Boss Dashboard' },
  headerSubtitle: { zh: '\u7ba1\u7406\u63a7\u5236\u4e2d\u5fc3', en: 'Admin Command Center' },
  adminMode: { zh: '\u7ba1\u7406\u6a21\u5f0f', en: 'ADMIN MODE' },
  tokenPlaceholder: { zh: '\u7ba1\u7406\u5458\u4ee4\u724c\uff08\u53ef\u9009\uff09', en: 'Admin token (optional)' },
  apply: { zh: '\u5e94\u7528', en: 'Apply' },
  loading: { zh: '\u52a0\u8f7d\u770b\u677f\u4e2d...', en: 'Loading dashboard...' },
  totalOrders: { zh: '\u8ba2\u5355\u603b\u6570', en: 'Total Orders' },
  revenueCredits: { zh: '\u8425\u6536\uff08\u79ef\u5206\uff09', en: 'Revenue (Credits)' },
  totalUsers: { zh: '\u7528\u6237\u603b\u6570', en: 'Total Users' },
  active24h: { zh: '\u6d3b\u8dc3\uff0824h\uff09', en: 'Active (24h)' },
  godModeTitle: { zh: '\u26a1 \u8d85\u7ea7\u7ba1\u7406', en: '\u26a1 God Mode' },
  godModeDesc: { zh: '\u4e3a\u7528\u6237\u53d1\u653e\u79ef\u5206', en: 'Grant credits to users' },
  userId: { zh: '\u7528\u6237 ID', en: 'User ID' },
  userIdPlaceholder: { zh: '\u8f93\u5165 user_openid \u6216 uuid', en: 'user_openid_or_uuid' },
  amount: { zh: '\u6570\u91cf', en: 'Amount' },
  amountPlaceholder: { zh: '\u4f8b\u5982\uff1a100', en: 'e.g. 100' },
  granting: { zh: '\u53d1\u653e\u4e2d...', en: 'Granting...' },
  grantCredits: { zh: '\ud83d\ude80 \u53d1\u653e\u79ef\u5206', en: '\ud83d\ude80 Grant Credits' },
  grantedPrefix: { zh: '\u2705 \u5df2\u5411\u7528\u6237\u53d1\u653e', en: '\u2705 Granted' },
  grantedMiddle: { zh: '\u79ef\u5206\uff0c\u7528\u6237\uff1a', en: 'credits to' },
  newBalance: { zh: '\u65b0\u4f59\u989d\uff1a\ud83d\udc8e', en: 'New balance: \ud83d\udc8e' },
  templateOrders: { zh: '\ud83d\udcca \u6a21\u677f\u8ba2\u5355\u5206\u5e03', en: '\ud83d\udcca Orders by Template' },
  recentGenerations: { zh: '\ud83d\udcf8 \u6700\u8fd1\u751f\u6210\uff08\u5ba1\u8ba1\uff09', en: '\ud83d\udcf8 Recent Generations (Audit)' },
  items: { zh: '\u6761', en: 'items' },
  noGenerations: { zh: '\u6682\u65e0\u751f\u6210\u8bb0\u5f55', en: 'No generations yet' },
  leads: { zh: '\ud83e\uddfe \u7ebf\u7d22', en: '\ud83e\uddfe Leads' },
  exportCsv: { zh: '\u5bfc\u51fa CSV', en: 'Export CSV' },
  reloadLeads: { zh: '\u5237\u65b0\u7ebf\u7d22', en: 'Reload Leads' },
  filterCity: { zh: '\u7b5b\u9009\u57ce\u5e02', en: 'Filter by city' },
  filterSourcePage: { zh: '\u7b5b\u9009\u6765\u6e90\u9875', en: 'Filter by source page' },
  filterTemplateId: { zh: '\u7b5b\u9009\u6a21\u677f ID', en: 'Filter by template id' },
  noLeads: { zh: '\u6682\u65e0\u7ebf\u7d22', en: 'No leads yet' },
  users: { zh: '\ud83d\udc64 \u7528\u6237\u5217\u8868', en: '\ud83d\udc64 Users' },
  opsConfigTitle: { zh: '\u2699\ufe0f \u8fd0\u8425\u914d\u7f6e', en: '\u2699\ufe0f Ops Config' },
  opsConfigDesc: { zh: '\u6a21\u677f\u3001\u4ef7\u683c\u548c\u6d3b\u52a8\u53ef\u5728\u6b64\u76f4\u63a5\u7f16\u8f91', en: 'Edit template, pricing, and placement config without redeploy.' },
  opsConfigPlaceholder: { zh: '\u5728\u6b64\u7f16\u8f91 JSON \u914d\u7f6e', en: 'Edit ops JSON config here' },
  analyticsTitle: { zh: '\ud83d\udcca \u7ecf\u8425\u5206\u6790', en: '\ud83d\udcca Analytics Overview' },
  analyticsDesc: { zh: '\u6f0f\u6597\u3001\u6a21\u677f\u6392\u540d\u548c\u57ce\u5e02\u6392\u540d', en: 'Funnel, template ranking, and city ranking.' },
  analyticsPlaceholder: { zh: '\u6682\u65e0\u5206\u6790\u6570\u636e', en: 'No analytics data yet' },
  reloadAnalytics: { zh: '\u5237\u65b0\u5206\u6790', en: 'Reload Analytics' },
  loadingReport: { zh: '\u52a0\u8f7d\u4e2d...', en: 'Loading...' },
  opsOverviewTitle: { zh: '\ud83d\udee0\ufe0f \u8fd0\u7ef4\u6982\u89c8', en: '\ud83d\udee0\ufe0f Ops Overview' },
  opsOverviewDesc: { zh: '\u8fd0\u884c\u65f6\u5065\u5eb7\u3001\u9519\u8bef\u6d41\u6c34\u4e0e\u6210\u672c\u6982\u51b5', en: 'Runtime health, failure feed, and cost summary.' },
  opsOverviewPlaceholder: { zh: '\u6682\u65e0\u8fd0\u7ef4\u6570\u636e', en: 'No ops data yet' },
  reloadOpsOverview: { zh: '\u5237\u65b0\u8fd0\u7ef4', en: 'Reload Ops' },
  opsAlertsTitle: { zh: '\u26a0\ufe0f \u8fd0\u7ef4\u544a\u8b66', en: '\u26a0\ufe0f Ops Alerts' },
  opsAlertsDesc: { zh: '\u6309\u961f\u5217\u79ef\u538b\u3001\u5931\u8d25\u7387\u4e0e\u8fd0\u884c\u65f6\u5065\u5eb7\u6d3e\u751f\u544a\u8b66', en: 'Derived alerts for backlog, failures, and runtime health.' },
  opsAlertsPlaceholder: { zh: '\u6682\u65e0\u544a\u8b66', en: 'No active alerts' },
  reloadOpsAlerts: { zh: '\u5237\u65b0\u544a\u8b66', en: 'Reload Alerts' },
  crmTitle: { zh: '\ud83d\udd17 CRM \u5bf9\u63a5', en: '\ud83d\udd17 CRM Bridge' },
  crmDesc: { zh: '\u9884\u89c8\u6216\u624b\u52a8\u63a8\u9001\u5f53\u524d\u7ebf\u7d22\u6279\u6b21\u5230 CRM webhook', en: 'Preview or manually push the filtered lead batch to the CRM webhook.' },
  crmPlaceholder: { zh: '\u6682\u65e0 CRM \u8f7d\u8377', en: 'No CRM payload yet' },
  reloadCrmPreview: { zh: '\u5237\u65b0\u8f7d\u8377', en: 'Reload Payload' },
  pushCrm: { zh: '\u63a8\u9001 CRM', en: 'Push to CRM' },
  pushingCrm: { zh: '\u63a8\u9001\u4e2d...', en: 'Pushing...' },
  crmPushSuccess: { zh: 'CRM \u63a8\u9001\u6210\u529f', en: 'CRM push succeeded' },
  crmPushFailed: { zh: 'CRM \u63a8\u9001\u5931\u8d25', en: 'CRM push failed' },
  crmHistoryPlaceholder: { zh: '暂无 CRM 推送历史', en: 'No CRM push history yet' },
  reloadConfig: { zh: '\u91cd\u65b0\u52a0\u8f7d', en: 'Reload' },
  saveConfig: { zh: '\u4fdd\u5b58\u914d\u7f6e', en: 'Save Config' },
  savingConfig: { zh: '\u4fdd\u5b58\u4e2d...', en: 'Saving...' },
  configSaved: { zh: '\u914d\u7f6e\u5df2\u4fdd\u5b58', en: 'Config saved' },
  configLoadFailed: { zh: '\u52a0\u8f7d\u914d\u7f6e\u5931\u8d25', en: 'Failed to load config' },
  configSaveFailed: { zh: '\u4fdd\u5b58\u914d\u7f6e\u5931\u8d25', en: 'Failed to save config' },
  configJsonInvalid: { zh: 'JSON \u683c\u5f0f\u65e0\u6548', en: 'Invalid JSON payload' },
  footer: { zh: 'AI \u5a5a\u7eb1\u5de5\u4f5c\u5ba4\u7ba1\u7406\u7aef \u2022 v1.0.0', en: 'AI Wedding Studio Admin \u2022 v1.0.0' },
  tokenApplied: { zh: '\u4ee4\u724c\u5df2\u5e94\u7528', en: 'Token applied' },
  tokenCleared: { zh: '\u4ee4\u724c\u5df2\u6e05\u9664', en: 'Token cleared' },
  tokenSaveFailed: { zh: '\u4fdd\u5b58\u4ee4\u724c\u5931\u8d25', en: 'Failed to save token' },
  dashboardLoadFailed: { zh: '\u52a0\u8f7d\u770b\u677f\u5931\u8d25', en: 'Failed to load dashboard' },
  exportFailed: { zh: '\u5bfc\u51fa\u5931\u8d25', en: 'Export failed' },
  enterUserId: { zh: '\u8bf7\u8f93\u5165\u7528\u6237 ID', en: 'Enter user id' },
  enterAmount: { zh: '\u8bf7\u8f93\u5165\u6709\u6548\u6570\u91cf', en: 'Enter valid amount' },
  creditsGranted: { zh: '\u79ef\u5206\u53d1\u653e\u6210\u529f', en: 'Credits granted!' },
  grantFailed: { zh: '\u53d1\u653e\u5931\u8d25', en: 'Grant failed' },
};

const tx = (key: TextKey): string => (i18nStore.locale === 'zh' ? textMap[key].zh : textMap[key].en);

const loading = ref(true);
const stats = ref<DashboardStats>({
  total_orders: 0,
  total_revenue_credits: 0,
  estimated_revenue_usd: 0,
  total_users: 0,
  active_users_24h: 0,
  total_credits_in_circulation: 0,
  template_breakdown: {},
  recent_activity: [],
});
const users = ref<User[]>([]);
const leads = ref<LeadItem[]>([]);
const leadFilters = ref({
  city: '',
  sourcePage: '',
  templateId: '',
});

const adminTokenInput = ref('');
const targetUserId = ref('');
const creditAmount = ref(100);
const granting = ref(false);
const grantResult = ref<GrantResult | null>(null);
const opsConfigText = ref('');
const opsSaving = ref(false);
const analyticsSummaryText = ref('');
const analyticsLoading = ref(false);
const opsOverviewText = ref('');
const opsOverviewLoading = ref(false);
const opsAlertsText = ref('');
const opsAlertsLoading = ref(false);
const crmPreviewText = ref('');
const crmHistoryText = ref('');
const crmLoading = ref(false);
const crmPushing = ref(false);

const loadAdminToken = () => {
  try {
    const raw = uni.getStorageSync('ADMIN_TOKEN');
    adminTokenInput.value = typeof raw === 'string' ? raw : String(raw || '');
  } catch (e) {
    adminTokenInput.value = '';
  }
};

const saveAdminToken = () => {
  const value = (adminTokenInput.value || '').trim();
  try {
    if (value) {
      uni.setStorageSync('ADMIN_TOKEN', value);
      uni.showToast({ title: tx('tokenApplied'), icon: 'success' });
    } else {
      uni.removeStorageSync('ADMIN_TOKEN');
      uni.showToast({ title: tx('tokenCleared'), icon: 'none' });
    }
  } catch (e) {
    uni.showToast({ title: tx('tokenSaveFailed'), icon: 'none' });
  }
  fetchDashboard();
  fetchLeads();
  fetchOpsConfig();
  fetchAnalyticsOverview();
  fetchOpsOverview();
  fetchOpsAlerts();
  fetchCrmPreview();
};

const fetchDashboard = async () => {
  loading.value = true;
  try {
    const [dashboardRes, usersRes] = await Promise.all([
      get<DashboardStats>('/admin/dashboard'),
      get<{ users: User[] }>('/admin/users'),
    ]);
    stats.value = dashboardRes;
    users.value = usersRes.users;
  } catch (error) {
    console.error('Failed to fetch dashboard:', error);
    uni.showToast({ title: tx('dashboardLoadFailed'), icon: 'none' });
  } finally {
    loading.value = false;
  }
};

const fetchLeads = async () => {
  try {
    const query = new URLSearchParams();
    if (leadFilters.value.city.trim()) query.set('city', leadFilters.value.city.trim());
    if (leadFilters.value.sourcePage.trim()) query.set('source_page', leadFilters.value.sourcePage.trim());
    if (leadFilters.value.templateId.trim()) query.set('template_id', leadFilters.value.templateId.trim());
    const suffix = query.toString() ? `?${query.toString()}` : '';
    const res = await get<{ leads: LeadItem[] }>(`/leads/list${suffix}`, { showLoading: false, showError: false } as any);
    leads.value = res.leads || [];
  } catch (e) {
    // silent for MVP
  }
};


const fetchOpsConfig = async () => {
  try {
    const res = await get<OpsConfigResponse>('/admin/ops_config', {
      showLoading: false,
      showError: false,
    } as any);
    opsConfigText.value = JSON.stringify(res, null, 2);
  } catch (e) {
    uni.showToast({ title: tx('configLoadFailed'), icon: 'none' });
  }
};

const saveOpsConfig = async () => {
  let payload: OpsConfigResponse;
  try {
    payload = JSON.parse(opsConfigText.value || '{}');
  } catch (e) {
    uni.showToast({ title: tx('configJsonInvalid'), icon: 'none' });
    return;
  }

  opsSaving.value = true;
  try {
    const res = await put<OpsConfigResponse>('/admin/ops_config', payload as any);
    opsConfigText.value = JSON.stringify(res, null, 2);
    uni.showToast({ title: tx('configSaved'), icon: 'success' });
  } catch (e) {
    uni.showToast({ title: tx('configSaveFailed'), icon: 'none' });
  } finally {
    opsSaving.value = false;
  }
};


const fetchAnalyticsOverview = async () => {
  analyticsLoading.value = true;
  try {
    const res = await get<AnalyticsOverviewResponse>('/admin/analytics_overview', {
      showLoading: false,
      showError: false,
    } as any);
    analyticsSummaryText.value = JSON.stringify(res, null, 2);
  } catch (e) {
    analyticsSummaryText.value = '';
  } finally {
    analyticsLoading.value = false;
  }
};

const fetchOpsOverview = async () => {
  opsOverviewLoading.value = true;
  try {
    const res = await get<OpsOverviewResponse>('/admin/ops_overview', {
      showLoading: false,
      showError: false,
    } as any);
    opsOverviewText.value = JSON.stringify(res, null, 2);
  } catch (e) {
    opsOverviewText.value = '';
  } finally {
    opsOverviewLoading.value = false;
  }
};

const fetchOpsAlerts = async () => {
  opsAlertsLoading.value = true;
  try {
    const res = await get<OpsAlertItem[]>('/admin/ops_alerts', {
      showLoading: false,
      showError: false,
    } as any);
    opsAlertsText.value = JSON.stringify(res, null, 2);
  } catch (e) {
    opsAlertsText.value = '';
  } finally {
    opsAlertsLoading.value = false;
  }
};

const buildLeadFilterQuery = (): string => {
  const query = new URLSearchParams();
  if (leadFilters.value.city.trim()) query.set('city', leadFilters.value.city.trim());
  if (leadFilters.value.sourcePage.trim()) query.set('source_page', leadFilters.value.sourcePage.trim());
  if (leadFilters.value.templateId.trim()) query.set('template_id', leadFilters.value.templateId.trim());
  return query.toString() ? `?${query.toString()}` : '';
};

const fetchCrmPreview = async () => {
  crmLoading.value = true;
  try {
    const suffix = buildLeadFilterQuery();
    const [preview, history] = await Promise.all([
      get<Record<string, any>>(`/admin/crm_preview${suffix}`, {
        showLoading: false,
        showError: false,
      } as any),
      get<Record<string, any>[]>(`/admin/crm_push_history?limit=12`, {
        showLoading: false,
        showError: false,
      } as any),
    ]);
    crmPreviewText.value = JSON.stringify(preview, null, 2);
    crmHistoryText.value = JSON.stringify(history, null, 2);
  } catch (e) {
    crmPreviewText.value = '';
    crmHistoryText.value = '';
  } finally {
    crmLoading.value = false;
  }
};

const pushCrmBatch = async () => {
  crmPushing.value = true;
  try {
    const suffix = buildLeadFilterQuery();
    const res = await post<CrmPushResponse>(`/admin/crm_push${suffix}`, {}, {
      showLoading: false,
      showError: false,
    } as any);
    crmPreviewText.value = JSON.stringify(res.payload || {}, null, 2);
    await fetchCrmPreview();
    uni.showToast({
      title: res.pushed ? tx('crmPushSuccess') : tx('crmPushFailed'),
      icon: res.pushed ? 'success' : 'none',
    });
  } catch (e) {
    uni.showToast({ title: tx('crmPushFailed'), icon: 'none' });
  } finally {
    crmPushing.value = false;
  }
};

const exportLeads = async () => {
  const suffix = buildLeadFilterQuery();
  const baseUrl = resolveApiUrl(`/leads/export.csv${suffix}`);
  const token = (() => {
    try {
      const raw = uni.getStorageSync('ADMIN_TOKEN');
      return typeof raw === 'string' ? raw.trim() : '';
    } catch (e) {
      return '';
    }
  })();
  const url = token
    ? `${baseUrl}${baseUrl.includes('?') ? '&' : '?'}admin_token=${encodeURIComponent(token)}`
    : baseUrl;
  // #ifdef H5
  try {
    window.open(url, '_blank');
    return;
  } catch (e) {
    // fallthrough
  }
  // #endif
  try {
    uni.downloadFile({ url });
  } catch (e) {
    uni.showToast({ title: tx('exportFailed'), icon: 'none' });
  }
};

const grantCredits = async () => {
  if (!targetUserId.value.trim()) {
    uni.showToast({ title: tx('enterUserId'), icon: 'none' });
    return;
  }

  if (!creditAmount.value || creditAmount.value <= 0) {
    uni.showToast({ title: tx('enterAmount'), icon: 'none' });
    return;
  }

  granting.value = true;
  grantResult.value = null;

  try {
    const result = await post<GrantResult>('/admin/grant_credits', {
      user_id: targetUserId.value,
      amount: Number(creditAmount.value),
    });
    grantResult.value = result;

    uni.showToast({ title: tx('creditsGranted'), icon: 'success' });

    const usersRes = await get<{ users: User[] }>('/admin/users');
    users.value = usersRes.users;
  } catch (error: any) {
    uni.showToast({
      title: error.message || tx('grantFailed'),
      icon: 'none',
    });
  } finally {
    granting.value = false;
  }
};

const formatDate = (dateStr: string): string => {
  if (!dateStr) return '';
  try {
    const date = new Date(dateStr);
    return `${date.toLocaleDateString()} ${date.toLocaleTimeString().slice(0, 5)}`;
  } catch {
    return dateStr;
  }
};

const handleImageError = (e: Event) => {
  const target = e.target as HTMLImageElement;
  if (target) {
    target.style.background = '#f0f0f0';
  }
};

const maskPhone = (phone: string): string => {
  const s = (phone || '').trim();
  if (s.length <= 7) return s;
  return `${s.slice(0, 3)}****${s.slice(-4)}`;
};

onMounted(() => {
  loadAdminToken();
  fetchDashboard();
  fetchLeads();
  fetchOpsConfig();
  fetchAnalyticsOverview();
  fetchOpsOverview();
  fetchOpsAlerts();
  fetchCrmPreview();
});
</script>

<style lang="scss" scoped>
.admin-page {
  min-height: 100vh;
  background: linear-gradient(180deg, #1a1a2e 0%, #16213e 100%);
  padding-bottom: 60px;
}

/* Header */
.admin-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 40px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.admin-token-bar {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 18px 40px 0;
}

.token-input {
  flex: 1;
  height: 40px;
  padding: 0 14px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.14);
  color: #fff;
  font-size: 12px;
}

.token-save {
  height: 40px;
  padding: 0 14px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.18);
  color: rgba(255, 255, 255, 0.9);
  font-size: 12px;
  font-weight: 800;
}

.header-title {
  display: block;
  font-size: 32px;
  color: #fff;
}

.header-subtitle {
  display: block;
  font-size: 14px;
  color: rgba(255, 255, 255, 0.5);
  margin-top: 4px;
}

.header-badge {
  background: linear-gradient(135deg, #e74c3c, #c0392b);
  padding: 8px 16px;
  border-radius: 20px;
}

.badge-text {
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 0.1em;
}

/* Loading */
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 120px 40px;
}

.loading-icon {
  font-size: 48px;
  margin-bottom: 16px;
  animation: pulse 1.5s ease-in-out infinite;
}

.loading-text {
  color: rgba(255, 255, 255, 0.6);
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* Dashboard Content */
.dashboard-content {
  padding: 40px;
  max-width: 1400px;
  margin: 0 auto;
}

/* KPI Cards */
.kpi-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 20px;
  margin-bottom: 40px;
}

.kpi-card {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 16px;
  padding: 24px;
  text-align: center;
  transition: transform 0.2s, background 0.2s;

  &:hover {
    transform: translateY(-4px);
    background: rgba(255, 255, 255, 0.08);
  }

  &.green { border-color: rgba(46, 204, 113, 0.3); }
  &.purple { border-color: rgba(155, 89, 182, 0.3); }
  &.blue { border-color: rgba(52, 152, 219, 0.3); }
}

.kpi-icon {
  font-size: 32px;
  margin-bottom: 12px;
}

.kpi-label {
  display: block;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.5);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 8px;
}

.kpi-value {
  display: block;
  font-size: 36px;
  font-weight: 700;
  color: #fff;
}

.kpi-sub {
  display: block;
  font-size: 14px;
  color: #2ecc71;
  margin-top: 4px;
}

/* Sections */
.section {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 16px;
  padding: 24px;
  margin-bottom: 24px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.section-title {
  font-size: 18px;
  font-weight: 600;
  color: #fff;
}

.section-desc {
  font-size: 14px;
  color: rgba(255, 255, 255, 0.5);
}

.section-count {
  font-size: 14px;
  color: rgba(255, 255, 255, 0.4);
}

/* God Mode */
.god-mode {
  border-color: rgba(231, 76, 60, 0.3);
  background: rgba(231, 76, 60, 0.05);
}

.control-panel {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  align-items: flex-end;
}

.input-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.input-label {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.5);
  text-transform: uppercase;
}

.text-input,
.number-input {
  padding: 12px 16px;
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 8px;
  color: #fff;
  font-size: 14px;
  min-width: 150px;
}

.boost-btn {
  padding: 12px 24px;
  background: linear-gradient(135deg, #e74c3c, #c0392b);
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;

  &:hover {
    transform: scale(1.02);
    box-shadow: 0 4px 16px rgba(231, 76, 60, 0.4);
  }

  &[disabled] {
    opacity: 0.6;
    cursor: not-allowed;
  }
}

.grant-result {
  margin-top: 16px;
  padding: 16px;
  background: rgba(46, 204, 113, 0.1);
  border: 1px solid rgba(46, 204, 113, 0.3);
  border-radius: 8px;
  color: #2ecc71;
  font-size: 14px;
}

.ops-textarea {
  width: 100%;
  min-height: 260px;
  padding: 16px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #fff;
  font-size: 12px;
  line-height: 1.6;
  box-sizing: border-box;
}

.readonly-textarea {
  opacity: 0.92;
}

.ops-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 16px;
}

.new-balance {
  display: block;
  margin-top: 8px;
  font-weight: 600;
}

/* Breakdown */
.breakdown-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.breakdown-item {
  background: rgba(255, 255, 255, 0.1);
  padding: 12px 20px;
  border-radius: 8px;
  display: flex;
  gap: 12px;
  align-items: center;
}

.breakdown-label {
  font-size: 14px;
  color: rgba(255, 255, 255, 0.7);
}

.breakdown-value {
  font-size: 18px;
  font-weight: 700;
  color: #fff;
}

/* Audit Grid */
.audit-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 12px;
}

.audit-item {
  background: rgba(255, 255, 255, 0.05);
  border-radius: 8px;
  overflow: hidden;
}

.audit-image {
  width: 100%;
  height: 100px;
  object-fit: cover;
  background: rgba(255, 255, 255, 0.1);
}

.audit-meta {
  padding: 8px;
}

.audit-template {
  display: block;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.7);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.audit-date {
  display: block;
  font-size: 10px;
  color: rgba(255, 255, 255, 0.4);
  margin-top: 4px;
}

.empty-audit {
  padding: 40px;
  text-align: center;
}

.empty-text {
  color: rgba(255, 255, 255, 0.4);
}

/* Leads */
.leads-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.leads-header-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}

.lead-filters {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.compact {
  min-width: 0;
}

.export-btn {
  height: 32px;
  padding: 0 14px;
  border-radius: 10px;
  background: rgba(243, 156, 18, 0.18);
  border: 1px solid rgba(243, 156, 18, 0.35);
  color: #f39c12;
  font-size: 12px;
  font-weight: 800;
}

.leads-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.lead-item {
  padding: 12px 16px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.06);
}

.lead-main {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 6px;
}

.lead-name {
  font-size: 14px;
  font-weight: 800;
  color: rgba(255, 255, 255, 0.9);
}

.lead-phone {
  font-size: 13px;
  font-weight: 800;
  color: #f39c12;
}

.lead-sub {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: rgba(255, 255, 255, 0.55);
  font-size: 11px;
}

/* Users List */
.users-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.user-item {
  display: flex;
  justify-content: space-between;
  padding: 12px 16px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 8px;
}

.user-id {
  font-size: 14px;
  color: rgba(255, 255, 255, 0.8);
}

.user-balance {
  font-size: 14px;
  font-weight: 600;
  color: #f39c12;
}

/* Footer */
.admin-footer {
  text-align: center;
  padding: 40px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}

.footer-text {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.3);
}

/* Responsive */
@media (max-width: 1024px) {
  .kpi-row {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .admin-header {
    flex-direction: column;
    gap: 16px;
    padding: 24px;
  }

  .dashboard-content {
    padding: 20px;
  }

  .kpi-row {
    grid-template-columns: repeat(2, 1fr);
    gap: 12px;
  }

  .kpi-card {
    padding: 16px;
  }

  .kpi-value {
    font-size: 28px;
  }

  .control-panel {
    flex-direction: column;
    align-items: stretch;
  }

  .text-input,
  .number-input {
    min-width: auto;
  }

  .leads-header {
    align-items: flex-start;
  }

  .leads-header-actions {
    width: 100%;
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .lead-filters {
    grid-template-columns: 1fr;
  }

  .audit-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}
</style>
