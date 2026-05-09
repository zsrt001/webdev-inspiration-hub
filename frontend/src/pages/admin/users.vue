<template>
  <AdminLayout
    active="users"
    title="用户管理"
    subtitle="搜索用户、查看账号状态与积分余额，仅允许编辑用户 status。"
  >
    <view class="admin-card admin-toolbar">
      <view class="filter-field">
        <text class="filter-label">搜索 name / email</text>
        <input v-model="filters.search" class="filter-input" placeholder="输入昵称、用户名、邮箱或用户 ID" confirm-type="search" @confirm="applyFilters" />
      </view>
      <view class="filter-field">
        <text class="filter-label">状态</text>
        <picker :range="statusFilterLabels" :value="statusFilterIndex" @change="onFilterStatusChange">
          <view class="filter-select">{{ statusFilterLabels[statusFilterIndex] }}</view>
        </picker>
      </view>
      <button class="primary-action" @tap="applyFilters">查询</button>
    </view>

    <view v-if="loading" class="admin-card admin-state">
      <text class="state-title">正在加载用户</text>
      <text class="state-copy">正在读取用户列表和积分余额。</text>
    </view>

    <view v-else-if="error" class="admin-card admin-state">
      <text class="state-title">用户列表加载失败</text>
      <text class="state-copy">{{ error }}</text>
      <button class="primary-action" @tap="fetchUsers">重试</button>
    </view>

    <view v-else class="admin-card admin-table">
      <view class="table-head user-table-grid">
        <text class="th">ID</text>
        <text class="th">Name</text>
        <text class="th">Email</text>
        <text class="th">Created</text>
        <text class="th">Status</text>
      </view>

      <view v-if="users.length === 0" class="admin-state compact-state">
        <text class="state-title">暂无匹配用户</text>
        <text class="state-copy">调整搜索词或状态筛选后再试。</text>
      </view>

      <view v-for="user in users" v-else :key="user.id" class="table-row user-table-grid">
        <view class="td mono">
          <text class="strong">{{ shortId(user.id) }}</text>
          <text class="subtle">余额 {{ user.balance }}</text>
        </view>
        <view class="td">
          <text class="strong">{{ user.name || '-' }}</text>
          <text class="subtle">{{ user.username || user.user_id }}</text>
        </view>
        <text class="td td-muted">{{ user.email || '-' }}</text>
        <text class="td td-muted">{{ formatDate(user.created_at) }}</text>
        <view class="td">
          <picker :range="userStatusOptions" :value="statusIndex(user.status)" @change="onUserStatusChange(user, $event)">
            <view class="status-pill" :class="user.status">{{ user.status || 'active' }}</view>
          </picker>
        </view>
      </view>

      <view class="pagination">
        <button class="ghost-action" :disabled="page <= 1" @tap="prevPage">上一页</button>
        <text class="page-copy">第 {{ page }} 页 / 共 {{ total }} 条</text>
        <button class="ghost-action" :disabled="page * pageSize >= total" @tap="nextPage">下一页</button>
      </view>
    </view>
  </AdminLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import AdminLayout from './AdminLayout.vue';
import { get, post } from '../../utils/api';

interface AdminUser {
  id: string;
  user_id: string;
  name: string;
  username?: string | null;
  email?: string | null;
  status: string;
  role: string;
  balance: number;
  created_at?: string | null;
  last_login_at?: string | null;
}

interface UsersResponse {
  users: AdminUser[];
  total: number;
  page: number;
  page_size: number;
}

const userStatusOptions = ['active', 'disabled', 'suspended', 'blocked'];
const statusFilterValues = ['', ...userStatusOptions];
const statusFilterLabels = ['全部状态', 'active', 'disabled', 'suspended', 'blocked'];

const loading = ref(true);
const error = ref('');
const users = ref<AdminUser[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(20);
const filters = ref({ search: '', status: '' });

const statusFilterIndex = computed(() => {
  const index = statusFilterValues.indexOf(filters.value.status);
  return index >= 0 ? index : 0;
});

function buildQuery(): string {
  const query = new URLSearchParams();
  query.set('page', String(page.value));
  query.set('page_size', String(pageSize.value));
  if (filters.value.search.trim()) query.set('search', filters.value.search.trim());
  if (filters.value.status) query.set('status', filters.value.status);
  return query.toString();
}

async function fetchUsers() {
  loading.value = true;
  error.value = '';
  try {
    const response = await get<UsersResponse>(`/admin/users?${buildQuery()}`, { showLoading: false, showError: false });
    users.value = response.users || [];
    total.value = response.total || 0;
    page.value = response.page || page.value;
    pageSize.value = response.page_size || pageSize.value;
  } catch (err: any) {
    error.value = err?.statusCode === 401
      ? '当前账号没有管理员权限。'
      : (err?.message || '无法加载用户列表。');
  } finally {
    loading.value = false;
  }
}

function applyFilters() {
  page.value = 1;
  fetchUsers();
}

function onFilterStatusChange(event: any) {
  const index = Number(event.detail?.value || 0);
  filters.value.status = statusFilterValues[index] || '';
  applyFilters();
}

function statusIndex(status: string): number {
  const index = userStatusOptions.indexOf(status || 'active');
  return index >= 0 ? index : 0;
}

async function onUserStatusChange(user: AdminUser, event: any) {
  const nextStatus = userStatusOptions[Number(event.detail?.value || 0)] || 'active';
  if (nextStatus === user.status) return;
  try {
    await post<AdminUser>(`/admin/users/${user.id}/status`, { status: nextStatus }, { showLoading: true, showError: false });
    uni.showToast({ title: '状态已更新', icon: 'success' });
    await fetchUsers();
  } catch (err: any) {
    uni.showToast({ title: err?.message || '状态更新失败', icon: 'none' });
  }
}

function prevPage() {
  if (page.value <= 1) return;
  page.value -= 1;
  fetchUsers();
}

function nextPage() {
  if (page.value * pageSize.value >= total.value) return;
  page.value += 1;
  fetchUsers();
}

function shortId(value: string): string {
  return value.length > 14 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value;
}

function formatDate(value?: string | null): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

onMounted(fetchUsers);
</script>

<style lang="scss" scoped>
@import './admin.scss';

.user-table-grid {
  grid-template-columns: 170px minmax(180px, 1.3fr) minmax(220px, 1.4fr) 150px 130px;
}

.compact-state {
  min-height: 180px;
}

@media (max-width: 1100px) {
  .user-table-grid {
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
