<template>
  <AdminLayout
    active="overview"
    title="运营概览"
    subtitle="查看用户、订单和成交额的核心运营指标。所有数据来自现有用户与订单表。"
  >
    <view v-if="loading" class="admin-card admin-state">
      <text class="state-title">正在加载后台数据</text>
      <text class="state-copy">正在校验管理员身份并读取运营指标。</text>
    </view>

    <view v-else-if="error" class="admin-card admin-state">
      <text class="state-title">无法访问管理后台</text>
      <text class="state-copy">{{ error }}</text>
      <button class="primary-action" @tap="goLogin">去登录</button>
    </view>

    <template v-else>
      <view class="metrics-grid">
        <view class="metric-card admin-card">
          <text class="metric-label">用户总数</text>
          <text class="metric-value">{{ stats.total_users }}</text>
          <text class="metric-sub">近 7 天新增 {{ stats.recent_users || 0 }}</text>
        </view>
        <view class="metric-card admin-card">
          <text class="metric-label">订单总数</text>
          <text class="metric-value">{{ stats.total_orders }}</text>
          <text class="metric-sub">近 7 天新增 {{ stats.recent_orders || 0 }}</text>
        </view>
        <view class="metric-card admin-card">
          <text class="metric-label">订单成交额</text>
          <text class="metric-value">{{ formatMoney(stats.total_revenue_cents || 0) }}</text>
          <text class="metric-sub">近 7 天 {{ formatMoney(stats.recent_revenue_cents || 0) }}</text>
        </view>
        <view class="metric-card admin-card">
          <text class="metric-label">订阅 MRR</text>
          <text class="metric-value">{{ formatMoney(stats.subscription_mrr_cents || 0) }}</text>
          <text class="metric-sub">活跃订阅 {{ stats.active_subscriptions || 0 }}</text>
        </view>
      </view>

      <view class="admin-card overview-section">
        <view class="section-head">
          <view>
            <text class="section-title">最近订单</text>
            <text class="section-copy">展示最近创建的订单状态，便于快速判断生成链路是否正常。</text>
          </view>
          <button class="ghost-action" @tap="goOrders">查看全部订单</button>
        </view>

        <view v-if="recentOrders.length === 0" class="admin-state compact-state">
          <text class="state-title">暂无订单</text>
          <text class="state-copy">有用户创建订单后会显示在这里。</text>
        </view>
        <view v-else class="recent-list">
          <view v-for="order in recentOrders" :key="order.id" class="recent-row">
            <view>
              <text class="strong mono">{{ shortId(order.id) }}</text>
              <text class="subtle">{{ order.template_title || order.template_id || '未选择模板' }}</text>
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
import { get } from '../../utils/api';

interface DashboardStats {
  total_orders: number;
  recent_orders?: number;
  total_users: number;
  recent_users?: number;
  total_revenue_cents?: number;
  recent_revenue_cents?: number;
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

const loading = ref(true);
const error = ref('');
const stats = ref<DashboardStats>({
  total_orders: 0,
  total_users: 0,
  recent_activity: [],
});

const recentOrders = computed(() => (stats.value.recent_activity || []).slice(0, 8));

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
    stats.value = await get<DashboardStats>('/admin/dashboard', { showLoading: false, showError: false });
  } catch (err: any) {
    error.value = err?.statusCode === 401
      ? '当前账号没有管理员权限。请使用已配置为 ADMIN_EMAILS / ADMIN_USER_IDS 或 role=admin 的账号登录。'
      : (err?.message || '后台数据加载失败，请稍后重试。');
  } finally {
    loading.value = false;
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

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 14px;
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

.compact-state {
  min-height: 150px;
}

.recent-list {
  border: 1px solid #edf1f6;
  border-radius: 8px;
  overflow: hidden;
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
  .recent-row {
    grid-template-columns: 1fr;
  }

  .recent-row {
    padding: 12px;
    align-items: flex-start;
  }
}
</style>
