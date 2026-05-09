<template>
  <AdminLayout
    active="orders"
    title="订单管理"
    subtitle="查看订单、关联用户、金额、状态和详情。只允许编辑订单 status，不提供删除操作。"
  >
    <view class="admin-card admin-toolbar">
      <view class="filter-field">
        <text class="filter-label">搜索订单 / 用户</text>
        <input v-model="filters.search" class="filter-input" placeholder="订单 ID、用户姓名、邮箱或用户 ID" confirm-type="search" @confirm="applyFilters" />
      </view>
      <view class="filter-field">
        <text class="filter-label">订单状态</text>
        <picker :range="statusFilterLabels" :value="statusFilterIndex" @change="onFilterStatusChange">
          <view class="filter-select">{{ statusFilterLabels[statusFilterIndex] }}</view>
        </picker>
      </view>
      <button class="primary-action" @tap="applyFilters">查询</button>
    </view>

    <view v-if="loading" class="admin-card admin-state">
      <text class="state-title">正在加载订单</text>
      <text class="state-copy">正在读取订单与关联用户信息。</text>
    </view>

    <view v-else-if="error" class="admin-card admin-state">
      <text class="state-title">订单列表加载失败</text>
      <text class="state-copy">{{ error }}</text>
      <button class="primary-action" @tap="fetchOrders">重试</button>
    </view>

    <view v-else class="admin-card admin-table">
      <view class="table-head order-table-grid">
        <text class="th">Order</text>
        <text class="th">User</text>
        <text class="th">Amount</text>
        <text class="th">Status</text>
        <text class="th">Created</text>
        <text class="th">Action</text>
      </view>

      <view v-if="orders.length === 0" class="admin-state compact-state">
        <text class="state-title">暂无匹配订单</text>
        <text class="state-copy">调整搜索词或订单状态后再试。</text>
      </view>

      <view v-for="order in orders" v-else :key="order.id" class="table-row order-table-grid">
        <view class="td mono">
          <text class="strong">{{ shortId(order.order_no || order.id) }}</text>
          <text class="subtle">{{ order.template_id || 'no template' }}</text>
        </view>
        <view class="td">
          <text class="strong">{{ order.user?.name || '-' }}</text>
          <text class="subtle">{{ order.user?.email || order.user?.username || order.user?.id || '-' }}</text>
        </view>
        <text class="td td-muted">{{ formatMoney(order.amount_cents) }}</text>
        <view class="td">
          <picker :range="orderStatusOptions" :value="orderStatusIndex(order.status)" @change="onOrderStatusChange(order, $event)">
            <view class="status-pill" :class="order.status">{{ order.status }}</view>
          </picker>
        </view>
        <text class="td td-muted">{{ formatDate(order.created_at) }}</text>
        <view class="td action-cell">
          <button class="table-action" @tap="viewDetail(order.id)">查看详情</button>
        </view>
      </view>

      <view class="pagination">
        <button class="ghost-action" :disabled="page <= 1" @tap="prevPage">上一页</button>
        <text class="page-copy">第 {{ page }} 页 / 共 {{ total }} 条</text>
        <button class="ghost-action" :disabled="page * pageSize >= total" @tap="nextPage">下一页</button>
      </view>
    </view>

    <view v-if="detail" class="admin-card detail-panel">
      <view class="detail-head">
        <view>
          <text class="section-title">订单详情</text>
          <text class="section-copy mono">{{ detail.id }}</text>
        </view>
        <button class="ghost-action" @tap="detail = null">关闭</button>
      </view>

      <view class="detail-grid">
        <view class="detail-item">
          <text class="detail-label">关联用户</text>
          <text class="detail-value">{{ detail.user?.name || '-' }} / {{ detail.user?.email || detail.user_id }}</text>
        </view>
        <view class="detail-item">
          <text class="detail-label">状态</text>
          <text class="detail-value">{{ detail.status }}</text>
        </view>
        <view class="detail-item">
          <text class="detail-label">金额</text>
          <text class="detail-value">{{ formatMoney(detail.amount_cents) }}</text>
        </view>
        <view class="detail-item">
          <text class="detail-label">创建时间</text>
          <text class="detail-value">{{ formatDate(detail.created_at) }}</text>
        </view>
        <view class="detail-item">
          <text class="detail-label">支付 ID</text>
          <text class="detail-value">{{ detail.payment_id || '-' }}</text>
        </view>
        <view class="detail-item">
          <text class="detail-label">任务 ID</text>
          <text class="detail-value">{{ detail.task_id || '-' }}</text>
        </view>
        <view class="detail-item">
          <text class="detail-label">存储状态</text>
          <text class="detail-value">{{ detail.storage_cleanup_status || '-' }}</text>
        </view>
        <view class="detail-item">
          <text class="detail-label">错误信息</text>
          <text class="detail-value">{{ detail.error_message || '-' }}</text>
        </view>
      </view>

      <textarea class="json-box" disabled :value="detailJson" />
    </view>
  </AdminLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import AdminLayout from './AdminLayout.vue';
import { get, post } from '../../utils/api';

interface AdminOrderUser {
  id: string;
  name: string;
  email?: string | null;
  username?: string | null;
}

interface AdminOrder {
  id: string;
  order_no: string;
  user?: AdminOrderUser | null;
  user_id?: string;
  amount_cents?: number | null;
  amount_usd?: number | null;
  status: string;
  template_id?: string | null;
  created_at?: string | null;
  paid_at?: string | null;
}

interface AdminOrderDetail extends AdminOrder {
  style_template?: string | null;
  generation_params?: Record<string, any> | null;
  source_image_urls?: Record<string, any> | null;
  preview_image_urls?: Record<string, any> | null;
  final_image_urls?: Record<string, any> | null;
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

const orderStatusOptions = ['CREATED', 'CHECKING', 'GENERATING', 'PREVIEW_READY', 'PAID', 'UPSCALING', 'COMPLETED'];
const statusFilterValues = ['', ...orderStatusOptions];
const statusFilterLabels = ['全部状态', ...orderStatusOptions];

const loading = ref(true);
const error = ref('');
const orders = ref<AdminOrder[]>([]);
const detail = ref<AdminOrderDetail | null>(null);
const total = ref(0);
const page = ref(1);
const pageSize = ref(20);
const filters = ref({ search: '', status: '' });

const statusFilterIndex = computed(() => {
  const index = statusFilterValues.indexOf(filters.value.status);
  return index >= 0 ? index : 0;
});

const detailJson = computed(() => {
  if (!detail.value) return '';
  return JSON.stringify({
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
      ? '当前账号没有管理员权限。'
      : (err?.message || '无法加载订单列表。');
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
    uni.showToast({ title: '订单状态已更新', icon: 'success' });
    await fetchOrders();
    if (detail.value?.id === order.id) await viewDetail(order.id);
  } catch (err: any) {
    uni.showToast({ title: err?.message || '状态更新失败', icon: 'none' });
  }
}

async function viewDetail(orderId: string) {
  try {
    detail.value = await get<AdminOrderDetail>(`/admin/orders/${orderId}`, { showLoading: true, showError: false });
  } catch (err: any) {
    uni.showToast({ title: err?.message || '详情加载失败', icon: 'none' });
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

function formatMoney(cents?: number | null): string {
  if (cents === null || cents === undefined) return '-';
  return `$${(Number(cents || 0) / 100).toFixed(2)}`;
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
  grid-template-columns: 170px minmax(180px, 1.2fr) 110px 150px 150px 110px;
}

.compact-state {
  min-height: 180px;
}

.action-cell {
  display: flex;
  align-items: center;
}

.detail-head {
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

.section-copy {
  margin-top: 4px;
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
}
</style>
