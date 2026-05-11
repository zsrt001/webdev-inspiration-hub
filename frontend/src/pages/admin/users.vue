<template>
  <AdminLayout
    active="users"
    title="Users & credits"
    subtitle="Search accounts, update operational status, and grant credits with an audit trail."
  >
    <view class="admin-card admin-toolbar">
      <view class="filter-field">
        <text class="filter-label">Search name, email, username, or user ID</text>
        <input
          v-model="filters.search"
          class="filter-input"
          placeholder="Type a name, email, username, openid, or UUID"
          confirm-type="search"
          @confirm="applyFilters"
        />
      </view>
      <view class="filter-field">
        <text class="filter-label">Status</text>
        <picker :range="statusFilterLabels" :value="statusFilterIndex" @change="onFilterStatusChange">
          <view class="filter-select">{{ statusFilterLabels[statusFilterIndex] }}</view>
        </picker>
      </view>
      <button class="primary-action" @tap="applyFilters">Search</button>
    </view>

    <view v-if="loading" class="admin-card admin-state">
      <text class="state-title">Loading users</text>
      <text class="state-copy">Reading account status and credit balances.</text>
    </view>

    <view v-else-if="error" class="admin-card admin-state">
      <text class="state-title">Unable to load users</text>
      <text class="state-copy">{{ error }}</text>
      <button class="primary-action" @tap="fetchUsers">Retry</button>
    </view>

    <view v-else class="admin-card admin-table">
      <view class="table-head user-table-grid">
        <text class="th">ID</text>
        <text class="th">Account</text>
        <text class="th">Email</text>
        <text class="th">Created</text>
        <text class="th">Status</text>
        <text class="th">Credits</text>
      </view>

      <view v-if="users.length === 0" class="admin-state compact-state">
        <text class="state-title">No matching users</text>
        <text class="state-copy">Adjust the search term or status filter and try again.</text>
      </view>

      <view v-for="user in users" v-else :key="user.id" class="table-row user-table-grid">
        <view class="td mono">
          <text class="strong">{{ shortId(user.id) }}</text>
          <text class="subtle">{{ shortId(user.user_id) }}</text>
        </view>
        <view class="td">
          <text class="strong">{{ user.name || '-' }}</text>
          <text class="subtle">{{ user.username || user.role || 'user' }}</text>
        </view>
        <text class="td td-muted">{{ user.email || '-' }}</text>
        <text class="td td-muted">{{ formatDate(user.created_at) }}</text>
        <view class="td">
          <picker :range="userStatusOptions" :value="statusIndex(user.status)" @change="onUserStatusChange(user, $event)">
            <view class="status-pill" :class="user.status">{{ user.status || 'active' }}</view>
          </picker>
        </view>
        <view class="td credit-cell">
          <text class="strong">{{ user.balance }}</text>
          <button class="table-action" @tap="openGrant(user)">Grant</button>
        </view>
      </view>

      <view class="pagination">
        <button class="ghost-action" :disabled="page <= 1" @tap="prevPage">Previous</button>
        <text class="page-copy">Page {{ page }} / {{ total }} users</text>
        <button class="ghost-action" :disabled="page * pageSize >= total" @tap="nextPage">Next</button>
      </view>
    </view>

    <view v-if="grantTarget" class="grant-overlay" @tap.self="closeGrant">
      <view class="admin-card grant-panel">
        <view class="section-head">
          <view>
            <text class="section-title">Grant credits</text>
            <text class="section-copy">{{ grantTarget.email || grantTarget.username || grantTarget.user_id }}</text>
          </view>
          <button class="ghost-action" @tap="closeGrant">Close</button>
        </view>

        <view class="grant-grid">
          <button v-for="amount in quickGrantAmounts" :key="amount" class="table-action" @tap="grantAmount = String(amount)">
            +{{ amount }}
          </button>
        </view>

        <view class="filter-field">
          <text class="filter-label">Amount</text>
          <input v-model="grantAmount" class="filter-input" type="number" placeholder="Credits to add" />
        </view>

        <button class="primary-action grant-submit" :disabled="granting" @tap="submitGrant">
          {{ granting ? 'Granting...' : 'Confirm grant' }}
        </button>
        <text v-if="grantResult" class="section-copy">{{ grantResult }}</text>
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

interface GrantResponse {
  success: boolean;
  user_id: string;
  credits_granted: number;
  new_balance: number;
}

const userStatusOptions = ['active', 'disabled', 'suspended', 'blocked'];
const statusFilterValues = ['', ...userStatusOptions];
const statusFilterLabels = ['All statuses', 'active', 'disabled', 'suspended', 'blocked'];
const quickGrantAmounts = [2, 10, 50, 120];

const loading = ref(true);
const error = ref('');
const users = ref<AdminUser[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(20);
const filters = ref({ search: '', status: '' });
const grantTarget = ref<AdminUser | null>(null);
const grantAmount = ref('10');
const granting = ref(false);
const grantResult = ref('');

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
      ? 'This account is not authorized for admin access. Use an owner/admin/operator account or configure ADMIN_EMAILS / ADMIN_USER_IDS.'
      : (err?.message || 'Unable to load users.');
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
    uni.showToast({ title: 'Status updated', icon: 'success' });
    await fetchUsers();
  } catch (err: any) {
    uni.showToast({ title: err?.message || 'Status update failed', icon: 'none' });
  }
}

function openGrant(user: AdminUser) {
  grantTarget.value = user;
  grantAmount.value = '10';
  grantResult.value = '';
}

function closeGrant() {
  grantTarget.value = null;
  grantResult.value = '';
  granting.value = false;
}

async function submitGrant() {
  if (!grantTarget.value) return;
  const amount = Number(grantAmount.value);
  if (!Number.isFinite(amount) || amount <= 0 || amount > 10000) {
    grantResult.value = 'Enter an amount from 1 to 10000.';
    return;
  }
  granting.value = true;
  grantResult.value = '';
  try {
    const result = await post<GrantResponse>(
      '/admin/grant_credits',
      { user_id: grantTarget.value.id, amount: Math.floor(amount) },
      { showLoading: false, showError: false },
    );
    grantResult.value = `Granted ${result.credits_granted}. New balance: ${result.new_balance}.`;
    await fetchUsers();
  } catch (err: any) {
    grantResult.value = err?.message || 'Credit grant failed.';
  } finally {
    granting.value = false;
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
  grid-template-columns: 170px minmax(180px, 1.2fr) minmax(220px, 1.3fr) 150px 130px 150px;
}

.compact-state {
  min-height: 180px;
}

.credit-cell {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.grant-overlay {
  position: fixed;
  inset: 0;
  z-index: 10000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(15, 23, 42, 0.44);
}

.grant-panel {
  width: min(520px, 100%);
  padding: 20px;
}

.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
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

.grant-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}

.grant-submit {
  width: 100%;
  margin-top: 16px;
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
