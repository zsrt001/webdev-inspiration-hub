<template>
  <AdminLayout
    active="orders"
    :title="tr('订单运营', 'Order operations')"
    :subtitle="tr('查看生成轮次、QA 结论、失败原因、退款，并可不重复扣费地重启失败生成。', 'Inspect generation rounds, QA verdicts, failure reasons, refunds, and restart a failed generation without charging again.')"
  >
    <view class="admin-card admin-toolbar">
      <view class="filter-field">
        <text class="filter-label">{{ tr('搜索订单或用户', 'Search order or user') }}</text>
        <input
          v-model="filters.search"
          class="filter-input"
          :placeholder="tr('订单 ID、用户名、邮箱或用户 ID', 'Order ID, user name, email, or user ID')"
          confirm-type="search"
          @confirm="applyFilters"
        />
      </view>
      <view class="filter-field">
        <text class="filter-label">{{ tr('订单状态', 'Order status') }}</text>
        <picker :range="statusFilterLabels" :value="statusFilterIndex" @change="onFilterStatusChange">
          <view class="filter-select">{{ statusFilterLabels[statusFilterIndex] }}</view>
        </picker>
      </view>
      <button class="primary-action" @tap="applyFilters">{{ tr('搜索', 'Search') }}</button>
    </view>

    <view v-if="loading" class="admin-card admin-state">
      <text class="state-title">{{ tr('正在加载订单', 'Loading orders') }}</text>
      <text class="state-copy">{{ tr('正在读取订单、用户、QA 和生成状态。', 'Reading order, user, QA, and generation state.') }}</text>
    </view>

    <view v-else-if="error" class="admin-card admin-state">
      <text class="state-title">{{ tr('订单加载失败', 'Orders failed to load') }}</text>
      <text class="state-copy">{{ error }}</text>
      <button class="primary-action" @tap="fetchOrders">{{ tr('重试', 'Retry') }}</button>
    </view>

    <view v-else class="admin-card admin-table">
      <view class="table-head order-table-grid">
        <text class="th">{{ tr('订单', 'Order') }}</text>
        <text class="th">{{ tr('用户', 'User') }}</text>
        <text class="th">{{ tr('积分', 'Credits') }}</text>
        <text class="th">{{ tr('状态', 'Status') }}</text>
        <text class="th">{{ tr('QA / 失败', 'QA / Failure') }}</text>
        <text class="th">{{ tr('操作', 'Action') }}</text>
      </view>

      <view v-if="orders.length === 0" class="admin-state compact-state">
        <text class="state-title">{{ tr('没有匹配订单', 'No matching orders') }}</text>
        <text class="state-copy">{{ tr('调整搜索词或状态筛选后再试。', 'Adjust search terms or status filters and try again.') }}</text>
      </view>

      <view v-for="order in orders" v-else :key="order.id" class="table-row order-table-grid">
        <view class="td mono">
          <text class="strong">{{ shortId(order.order_no || order.id) }}</text>
          <text class="subtle">{{ order.template_id || 'no template' }}</text>
          <text class="subtle">{{ formatDate(order.created_at) }}</text>
        </view>
        <view class="td">
          <text class="strong">{{ order.user?.name || '-' }}</text>
          <text class="subtle">{{ order.user?.email || order.user?.username || order.user?.id || '-' }}</text>
        </view>
        <view class="td">
          <text class="strong">{{ creditSummary(order) }}</text>
          <text v-if="Number(order.refunded_credits || 0) > 0" class="subtle refund-copy">
            {{ tr('已退', 'Refunded') }} {{ order.refunded_credits }}
          </text>
        </view>
        <view class="td">
          <picker :range="orderStatusOptions" :value="orderStatusIndex(order.status)" @change="onOrderStatusChange(order, $event)">
            <view class="status-pill" :class="order.status">{{ order.status }}</view>
          </picker>
        </view>
        <view class="td">
          <text class="strong">{{ order.failure_code || qaReasonText(order.qa_last_reasons) || tr('通过', 'Clear') }}</text>
          <text class="subtle">{{ qaReasonText(order.qa_last_reasons) || tr('无阻断 QA 原因', 'No blocking QA reason') }}</text>
        </view>
        <view class="td action-cell">
          <button class="table-action" @tap="viewDetail(order.id)">{{ tr('详情', 'Details') }}</button>
        </view>
      </view>

      <view class="pagination">
        <button class="ghost-action" :disabled="page <= 1" @tap="prevPage">{{ tr('上一页', 'Previous') }}</button>
        <text class="page-copy">{{ tr('第', 'Page') }} {{ page }} / {{ total }} {{ tr('个订单', 'orders') }}</text>
        <button class="ghost-action" :disabled="page * pageSize >= total" @tap="nextPage">{{ tr('下一页', 'Next') }}</button>
      </view>
    </view>

    <view v-if="detail" class="admin-card detail-panel">
      <view class="detail-head">
        <view>
          <text class="section-title">{{ tr('订单详情', 'Order detail') }}</text>
          <text class="section-copy mono">{{ detail.id }}</text>
        </view>
        <view class="detail-actions">
          <button
            class="primary-action"
            :disabled="regenerating || !detail.can_regenerate"
            @tap="regenerateOrder"
          >
            {{ regenerating ? tr('启动中...', 'Starting...') : tr('重新生成', 'Regenerate') }}
          </button>
          <button class="ghost-action" @tap="viewDetail(detail.id)">{{ tr('刷新', 'Refresh') }}</button>
          <button class="ghost-action" @tap="detail = null">{{ tr('关闭', 'Close') }}</button>
        </view>
      </view>

      <view class="detail-grid">
        <view class="detail-item">
          <text class="detail-label">{{ tr('用户', 'User') }}</text>
          <text class="detail-value">{{ detail.user?.name || '-' }} / {{ detail.user?.email || detail.user_id }}</text>
        </view>
        <view class="detail-item">
          <text class="detail-label">{{ tr('状态', 'Status') }}</text>
          <text class="detail-value">{{ detail.status }}</text>
        </view>
        <view class="detail-item">
          <text class="detail-label">{{ tr('积分', 'Credits') }}</text>
          <text class="detail-value">{{ creditSummary(detail) }} / {{ tr('已退', 'refunded') }} {{ detail.refunded_credits || 0 }}</text>
        </view>
        <view class="detail-item">
          <text class="detail-label">{{ tr('任务', 'Task') }}</text>
          <text class="detail-value">{{ detail.task_id || '-' }}</text>
        </view>
        <view class="detail-item">
          <text class="detail-label">{{ tr('失败原因', 'Failure') }}</text>
          <text class="detail-value">{{ failureSummary }}</text>
        </view>
        <view class="detail-item">
          <text class="detail-label">{{ tr('QA 次数', 'QA attempts') }}</text>
          <text class="detail-value">{{ detail.qa_summary?.qa_attempt_count || detail.generation_rounds?.length || 0 }}</text>
        </view>
      </view>

      <view class="media-section">
        <view class="section-head">
          <view>
            <text class="section-title small-title">{{ tr('原图与交付图', 'Source and delivered images') }}</text>
            <text class="section-copy">{{ tr('用户原始上传、当前预览和最终交付图。', 'Original uploads, current preview, and final delivery.') }}</text>
          </view>
        </view>
        <view class="image-strip">
          <view v-for="item in sourceGallery" :key="item.label" class="image-tile">
            <text class="image-label">{{ item.label }}</text>
            <image class="admin-image" :src="item.url" mode="aspectFill" />
          </view>
          <view v-for="item in deliveredGallery" :key="item.label" class="image-tile delivered">
            <text class="image-label">{{ item.label }}</text>
            <image class="admin-image" :src="item.url" mode="aspectFill" />
          </view>
          <view v-if="sourceGallery.length === 0 && deliveredGallery.length === 0" class="empty-inline">
            <text>{{ tr('该订单没有记录图片。', 'No images recorded for this order.') }}</text>
          </view>
        </view>
      </view>

      <view class="media-section">
        <view class="section-head">
          <view>
            <text class="section-title small-title">{{ tr('生成轮次', 'Generation rounds') }}</text>
            <text class="section-copy">{{ tr('每轮图像生成/编辑、候选图、QA 结论和修复提示。', 'Every image-edit round, its candidate image, QA verdict, and repair hints.') }}</text>
          </view>
        </view>

        <view v-if="rounds.length === 0" class="empty-inline">
          <text>{{ tr('还没有记录轮次级图片。', 'No round-level images have been recorded yet.') }}</text>
        </view>
        <view v-else class="round-list">
          <view v-for="round in rounds" :key="roundKey(round)" class="round-item">
            <view class="round-media">
              <image v-if="roundPrimaryUrl(round)" class="round-image" :src="resolveImage(roundPrimaryUrl(round))" mode="aspectFill" />
              <view v-else class="round-image placeholder">
                <text>{{ tr('无图片', 'No image') }}</text>
              </view>
            </view>
            <view class="round-body">
              <view class="round-title-row">
                <view class="round-title-copy">
                  <text class="strong">{{ tr('轮次', 'Round') }} {{ round.round || '-' }} / {{ round.stage || tr('未知', 'unknown') }}</text>
                  <view class="round-meta-row">
                    <text v-if="round.repair_mode" class="meta-chip">{{ repairModeLabel(round.repair_mode) }}</text>
                    <text v-if="round.used_previous_result" class="meta-chip">{{ tr('使用上一轮画布', 'Used previous canvas') }}</text>
                    <text class="meta-chip" :class="{ success: !round.billable && Number(round.extra_credits_charged || 0) === 0 }">
                      {{ round.billable ? tr('计费', 'Billable') : tr('包含修复', 'Included repair') }}
                    </text>
                    <text v-if="round.billing_reason" class="meta-chip">{{ billingReasonLabel(round.billing_reason) }}</text>
                  </view>
                </view>
                <text class="status-pill" :class="{ active: round.qa_passed, failed: round.qa_passed === false }">
                  {{ round.qa_passed ? tr('QA 通过', 'QA passed') : tr('QA 失败', 'QA failed') }}
                </text>
              </view>
              <text class="subtle">
                {{ tr('尝试', 'Attempt') }} {{ round.generation_attempt || '-' }} / {{ tr('候选', 'candidates') }} {{ round.candidate_count || candidateUrls(round).length || 0 }} / {{ tr('选中', 'selected') }} #{{ selectedCandidateNumber(round) }} / {{ tr('计费', 'billable') }} {{ round.billable ? tr('是', 'yes') : tr('否', 'no') }} / {{ tr('额外积分', 'extra credits') }} {{ round.extra_credits_charged || 0 }}
              </text>
              <view v-if="candidateScoreRows(round).length" class="score-grid">
                <view
                  v-for="score in candidateScoreRows(round)"
                  :key="scoreKey(score)"
                  class="score-chip"
                  :class="{ selected: score.index === normalizedSelectedIndex(round), failed: score.qa_passed === false }"
                >
                  <text class="score-title">#{{ Number(score.index || 0) + 1 }} · {{ formatScore(score.score) }}</text>
                  <text class="score-copy">{{ score.qa_passed ? tr('通过', 'passed') : scoreText(score) }}</text>
                </view>
              </view>
              <view v-if="candidateUrls(round).length > 1" class="candidate-strip">
                <view
                  v-for="(url, index) in candidateUrls(round)"
                  :key="`${roundKey(round)}-${index}`"
                  class="candidate-thumb"
                  :class="{ selected: index === normalizedSelectedIndex(round) }"
                >
                  <image class="candidate-image" :src="resolveImage(url)" mode="aspectFill" />
                  <text class="candidate-label">#{{ index + 1 }}</text>
                </view>
              </view>
              <text class="round-copy">{{ tr('原因', 'Reasons') }}: {{ qaReasonText(round.qa_reasons) || tr('无', 'none') }}</text>
              <view v-if="round.qa_issues?.length" class="issue-list">
                <view v-for="issue in round.qa_issues" :key="issueKey(issue)" class="issue-pill">
                  <text>{{ issueLabel(issue) }}</text>
                </view>
              </view>
              <text class="subtle">{{ formatDate(round.completed_at) }}</text>
            </view>
          </view>
        </view>
      </view>

      <textarea class="json-box" disabled :value="detailJson" />
    </view>
  </AdminLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import AdminLayout from './AdminLayout.vue';
import { get, post, resolvePublicUrl } from '../../utils/api';
import { useI18nStore } from '../../stores/i18n';

interface AdminOrderUser {
  id: string;
  name: string;
  email?: string | null;
  username?: string | null;
}

interface QaIssue {
  code?: string;
  category?: string;
  target?: string;
  severity?: string;
  repair_action?: string;
  repair_hint?: string;
}

interface CandidateScore {
  index?: number;
  url?: string;
  score?: number;
  qa_passed?: boolean;
  passed?: boolean;
  hard_gate_reasons?: string[];
  reasons?: string[];
  issue_count?: number;
  policy?: string;
}

interface GenerationRound {
  round?: number | null;
  generation_attempt?: number | null;
  stage?: string | null;
  candidate_url?: string | null;
  candidate_urls?: string[];
  selected_candidate_url?: string | null;
  selected_candidate_index?: number | null;
  candidate_count?: number;
  provider_url_count?: number;
  candidate_scores?: CandidateScore[];
  selection_policy?: string | null;
  qa_passed?: boolean | null;
  qa_reasons?: string[];
  qa_issues?: QaIssue[];
  used_previous_result?: boolean;
  billable?: boolean;
  repair_mode?: string | null;
  billing_reason?: string | null;
  extra_credits_charged?: number;
  completed_at?: string | null;
}

interface QaSummary {
  qa_last_reasons?: string[];
  qa_last_issues?: QaIssue[];
  qa_attempt_count?: number | null;
  failure_code?: string | null;
  failure_provider?: string | null;
  error_message?: string | null;
  credit_refund?: Record<string, any> | null;
}

interface AdminOrder {
  id: string;
  order_no: string;
  user?: AdminOrderUser | null;
  user_id?: string;
  amount_cents?: number | null;
  amount_usd?: number | null;
  credits_cost?: number | null;
  refunded_credits?: number | null;
  status: string;
  template_id?: string | null;
  failure_code?: string | null;
  qa_last_reasons?: string[];
  created_at?: string | null;
  paid_at?: string | null;
}

interface AdminOrderDetail extends AdminOrder {
  style_template?: string | null;
  generation_params?: Record<string, any> | null;
  source_image_urls?: Record<string, any> | null;
  preview_image_urls?: Record<string, any> | null;
  final_image_urls?: Record<string, any> | null;
  generation_rounds?: GenerationRound[];
  qa_summary?: QaSummary | null;
  can_regenerate?: boolean;
  payment_id?: string | null;
  task_id?: string | null;
  error_message?: string | null;
  storage_cleanup_status?: string | null;
  source_images_expires_at?: string | null;
  expires_at?: string | null;
  deleted_at?: string | null;
  updated_at?: string | null;
}

interface OrdersResponse {
  orders: AdminOrder[];
  total: number;
  page: number;
  page_size: number;
}

interface RegenerateResponse {
  ok: boolean;
  started: boolean;
  execution_mode: string;
  task_id?: string | null;
  order: AdminOrderDetail;
}

const orderStatusOptions = ['CREATED', 'CHECKING', 'GENERATING', 'COMPLETED', 'FAILED'];
const statusFilterValues = ['', ...orderStatusOptions];
const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const loading = ref(true);
const error = ref('');
const orders = ref<AdminOrder[]>([]);
const detail = ref<AdminOrderDetail | null>(null);
const total = ref(0);
const page = ref(1);
const pageSize = ref(20);
const regenerating = ref(false);
const filters = ref({ search: '', status: '' });
const statusFilterLabels = computed(() => [tr('全部状态', 'All statuses'), ...orderStatusOptions]);

const statusFilterIndex = computed(() => {
  const index = statusFilterValues.indexOf(filters.value.status);
  return index >= 0 ? index : 0;
});

const rounds = computed(() => detail.value?.generation_rounds || []);

const failureSummary = computed(() => {
  const current = detail.value;
  if (!current) return '-';
  const qa = current.qa_summary;
  const reasons = qaReasonText(qa?.qa_last_reasons || current.qa_last_reasons || []);
  const failure = qa?.failure_code || current.failure_code || current.error_message || qa?.error_message;
  return [failure, reasons].filter(Boolean).join(' / ') || '-';
});

const sourceGallery = computed(() => {
  const current = detail.value;
  if (!current) return [];
  const images = Array.isArray(current.source_image_urls?.images) ? current.source_image_urls?.images : [];
  return images
    .filter((url: any) => String(url || '').trim())
    .map((url: string, index: number) => ({ label: `Source ${index + 1}`, url: resolveImage(url) }));
});

const deliveredGallery = computed(() => {
  const current = detail.value;
  if (!current) return [];
  const items: Array<{ label: string; url: string }> = [];
  imageMapValues(current.preview_image_urls).forEach((url, index) => items.push({ label: `Preview ${index + 1}`, url: resolveImage(url) }));
  imageMapValues(current.final_image_urls).forEach((url, index) => items.push({ label: `Final ${index + 1}`, url: resolveImage(url) }));
  return items;
});

const detailJson = computed(() => {
  if (!detail.value) return '';
  return JSON.stringify({
    qa_summary: detail.value.qa_summary,
    generation_rounds: detail.value.generation_rounds,
    generation_params: detail.value.generation_params,
    source_image_urls: detail.value.source_image_urls,
    preview_image_urls: detail.value.preview_image_urls,
    final_image_urls: detail.value.final_image_urls,
    expires_at: detail.value.expires_at,
    deleted_at: detail.value.deleted_at,
    updated_at: detail.value.updated_at,
  }, null, 2);
});

function buildQuery(): string {
  const query = new URLSearchParams();
  query.set('page', String(page.value));
  query.set('page_size', String(pageSize.value));
  if (filters.value.search.trim()) query.set('search', filters.value.search.trim());
  if (filters.value.status) query.set('status', filters.value.status);
  return query.toString();
}

async function fetchOrders() {
  loading.value = true;
  error.value = '';
  try {
    const response = await get<OrdersResponse>(`/admin/orders?${buildQuery()}`, { showLoading: false, showError: false });
    orders.value = response.orders || [];
    total.value = response.total || 0;
    page.value = response.page || page.value;
    pageSize.value = response.page_size || pageSize.value;
  } catch (err: any) {
    error.value = err?.statusCode === 401
      ? tr('当前账号没有后台权限。', 'This account is not authorized for admin access.')
      : (err?.message || tr('订单加载失败。', 'Unable to load orders.'));
  } finally {
    loading.value = false;
  }
}

function applyFilters() {
  page.value = 1;
  detail.value = null;
  fetchOrders();
}

function onFilterStatusChange(event: any) {
  const index = Number(event.detail?.value || 0);
  filters.value.status = statusFilterValues[index] || '';
  applyFilters();
}

function orderStatusIndex(status: string): number {
  const index = orderStatusOptions.indexOf(status || 'CREATED');
  return index >= 0 ? index : 0;
}

async function onOrderStatusChange(order: AdminOrder, event: any) {
  const nextStatus = orderStatusOptions[Number(event.detail?.value || 0)] || 'CREATED';
  if (nextStatus === order.status) return;
  try {
    await post<AdminOrderDetail>(`/admin/orders/${order.id}/status`, { status: nextStatus }, { showLoading: true, showError: false });
    uni.showToast({ title: tr('订单状态已更新', 'Order status updated'), icon: 'success' });
    await fetchOrders();
    if (detail.value?.id === order.id) await viewDetail(order.id);
  } catch (err: any) {
    uni.showToast({ title: err?.message || tr('状态更新失败', 'Status update failed'), icon: 'none' });
  }
}

async function viewDetail(orderId: string) {
  try {
    detail.value = await get<AdminOrderDetail>(`/admin/orders/${orderId}`, { showLoading: true, showError: false });
  } catch (err: any) {
    uni.showToast({ title: err?.message || tr('详情加载失败', 'Detail load failed'), icon: 'none' });
  }
}

async function regenerateOrder() {
  const current = detail.value;
  if (!current || regenerating.value || !current.can_regenerate) return;
  regenerating.value = true;
  try {
    const response = await post<RegenerateResponse>(
      `/admin/orders/${current.id}/regenerate`,
      { reason: 'manual_admin_regenerate' },
      { showLoading: false, showError: false },
    );
    detail.value = response.order;
    uni.showToast({ title: tr('已开始重新生成', 'Regeneration started'), icon: 'success' });
    await fetchOrders();
  } catch (err: any) {
    uni.showToast({ title: err?.message || tr('重新生成失败', 'Regenerate failed'), icon: 'none' });
  } finally {
    regenerating.value = false;
  }
}

function prevPage() {
  if (page.value <= 1) return;
  page.value -= 1;
  fetchOrders();
}

function nextPage() {
  if (page.value * pageSize.value >= total.value) return;
  page.value += 1;
  fetchOrders();
}

function shortId(value: string): string {
  return value.length > 14 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value;
}

function creditSummary(order: AdminOrder): string {
  if (order.credits_cost === null || order.credits_cost === undefined) return '-';
  return `${Number(order.credits_cost || 0)} ${tr('积分', 'credits')}`;
}

function qaReasonText(reasons?: string[] | null): string {
  return (reasons || []).filter(Boolean).join(', ');
}

function imageMapValues(map?: Record<string, any> | null): string[] {
  if (!map || typeof map !== 'object') return [];
  return Object.values(map).filter((url) => String(url || '').trim()).map((url) => String(url));
}

function resolveImage(url?: string | null): string {
  return resolvePublicUrl(url || '');
}

function roundKey(round: GenerationRound): string {
  return `${round.generation_attempt || 'a'}-${round.round || 'r'}-${round.stage || 'stage'}-${roundPrimaryUrl(round) || ''}`;
}

function candidateUrls(round: GenerationRound): string[] {
  const urls = Array.isArray(round.candidate_urls) ? round.candidate_urls : [];
  if (urls.length) return urls.filter((url) => String(url || '').trim());
  return round.candidate_url ? [round.candidate_url] : [];
}

function normalizedSelectedIndex(round: GenerationRound): number {
  const raw = Number(round.selected_candidate_index ?? 0);
  return Number.isFinite(raw) && raw >= 0 ? raw : 0;
}

function selectedCandidateNumber(round: GenerationRound): number {
  return normalizedSelectedIndex(round) + 1;
}

function roundPrimaryUrl(round: GenerationRound): string {
  return round.selected_candidate_url || round.candidate_url || candidateUrls(round)[0] || '';
}

function candidateScoreRows(round: GenerationRound): CandidateScore[] {
  return Array.isArray(round.candidate_scores) ? round.candidate_scores : [];
}

function scoreKey(score: CandidateScore): string {
  return `${score.index ?? 'candidate'}-${score.score ?? 'score'}-${(score.reasons || []).join('|')}`;
}

function formatScore(value?: number): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(1)}` : '--';
}

function scoreText(score: CandidateScore): string {
  const hard = Array.isArray(score.hard_gate_reasons) ? score.hard_gate_reasons.filter(Boolean) : [];
  if (hard.length) return hard.join(', ');
  const reasons = Array.isArray(score.reasons) ? score.reasons.filter(Boolean) : [];
  return reasons.length ? reasons.join(', ') : 'failed';
}

function repairModeLabel(value?: string | null): string {
  const normalized = String(value || '').trim();
  if (normalized === 'relight_edit_only') return tr('仅重打光/编辑', 'Relight/edit only');
  if (normalized === 'targeted_repair') return tr('定向修复', 'Targeted repair');
  if (normalized === 'primary_generation') return tr('主生成', 'Primary generation');
  if (normalized === 'final_polish') return tr('最终精修', 'Final polish');
  return normalized || tr('未知模式', 'Unknown mode');
}

function billingReasonLabel(value?: string | null): string {
  const normalized = String(value || '').trim();
  if (normalized === 'automatic_repair_included') return tr('不额外扣积分', 'No extra credits');
  return normalized || '';
}

function issueKey(issue: QaIssue): string {
  return `${issue.code || 'issue'}-${issue.target || ''}-${issue.repair_action || ''}`;
}

function issueLabel(issue: QaIssue): string {
  const code = issue.code || 'issue';
  const target = issue.target || issue.category || 'target';
  const repair = issue.repair_action || issue.repair_hint || 'review';
  return `${code} / ${target} / ${repair}`;
}

function formatDate(value?: string | null): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

onMounted(fetchOrders);
</script>

<style lang="scss" scoped>
@import './admin.scss';

.order-table-grid {
  grid-template-columns: 170px minmax(180px, 1.1fr) 110px 140px minmax(180px, 1fr) 110px;
}

.compact-state {
  min-height: 180px;
}

.action-cell,
.detail-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.refund-copy {
  color: #137248;
}

.detail-head,
.section-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
}

.section-title,
.section-copy {
  display: block;
}

.section-title {
  font-size: 18px;
  font-weight: 900;
  color: #111827;
}

.small-title {
  font-size: 15px;
}

.section-copy {
  margin-top: 4px;
  color: #687180;
  font-size: 13px;
  line-height: 1.5;
}

.media-section {
  margin-top: 18px;
  padding-top: 18px;
  border-top: 1px solid #edf1f6;
}

.image-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.image-tile {
  width: 160px;
}

.image-tile.delivered {
  width: 190px;
}

.image-label {
  display: block;
  color: #687180;
  font-size: 12px;
  font-weight: 850;
}

.admin-image,
.round-image {
  margin-top: 8px;
  width: 100%;
  aspect-ratio: 3 / 4;
  border-radius: 8px;
  background: #edf1f6;
  border: 1px solid #e2e7ef;
}

.round-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.round-item {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr);
  gap: 14px;
  padding: 12px;
  border: 1px solid #edf1f6;
  border-radius: 8px;
  background: #fbfcfe;
}

.round-media {
  min-width: 0;
}

.round-image.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #687180;
  font-size: 12px;
}

.round-body {
  min-width: 0;
}

.round-title-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.round-title-copy {
  min-width: 0;
}

.round-meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.meta-chip {
  min-height: 24px;
  display: inline-flex;
  align-items: center;
  padding: 0 8px;
  border-radius: 8px;
  background: #eef2f7;
  color: #374151;
  font-size: 11px;
  font-weight: 850;
  line-height: 1.2;
}

.meta-chip.success {
  background: #e8f7ef;
  color: #137248;
}

.round-copy {
  display: block;
  margin-top: 8px;
  color: #374151;
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
}

.score-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
  gap: 8px;
  margin-top: 10px;
}

.score-chip {
  min-height: 54px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid #e2e7ef;
  background: #fff;
}

.score-chip.selected {
  border-color: #111827;
  background: #f4f7fb;
}

.score-chip.failed {
  background: #fff5f5;
  border-color: #fed7d7;
}

.score-title,
.score-copy {
  display: block;
  line-height: 1.25;
}

.score-title {
  color: #111827;
  font-size: 12px;
  font-weight: 900;
}

.score-copy {
  color: #687180;
  font-size: 11px;
  word-break: break-word;
}

.candidate-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.candidate-thumb {
  position: relative;
  width: 72px;
  aspect-ratio: 3 / 4;
  border-radius: 8px;
  border: 2px solid transparent;
  overflow: hidden;
  background: #edf1f6;
}

.candidate-thumb.selected {
  border-color: #111827;
}

.candidate-image {
  width: 100%;
  height: 100%;
}

.candidate-label {
  position: absolute;
  left: 4px;
  bottom: 4px;
  min-width: 24px;
  height: 20px;
  padding: 0 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: rgba(17, 24, 39, 0.82);
  color: #fff;
  font-size: 11px;
  font-weight: 900;
}

.issue-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.issue-pill {
  max-width: 100%;
  min-height: 26px;
  display: inline-flex;
  align-items: center;
  padding: 0 8px;
  border-radius: 8px;
  background: #fff5dc;
  color: #8a5a00;
  font-size: 12px;
  font-weight: 800;
}

.status-pill.failed {
  background: #ffe4e6;
  color: #be123c;
}

.empty-inline {
  width: 100%;
  padding: 14px;
  border: 1px dashed #d7dce5;
  border-radius: 8px;
  color: #687180;
  font-size: 13px;
}

@media (max-width: 1180px) {
  .order-table-grid {
    grid-template-columns: 1fr;
    row-gap: 8px;
    align-items: flex-start;
    padding-top: 14px;
    padding-bottom: 14px;
  }

  .table-head {
    display: none;
  }

  .detail-head,
  .section-head,
  .round-title-row {
    flex-direction: column;
  }
}

@media (max-width: 760px) {
  .detail-actions,
  .action-cell {
    flex-wrap: wrap;
  }

  .image-tile,
  .image-tile.delivered {
    width: calc(50% - 6px);
  }

  .round-item {
    grid-template-columns: 1fr;
  }
}
</style>
