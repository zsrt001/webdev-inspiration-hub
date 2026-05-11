<template>
  <view class="admin-shell">
    <aside class="admin-sidebar">
      <view class="admin-brand" @tap="go('/admin')">
        <text class="brand-mark">AI</text>
        <view>
          <text class="brand-title">Wedding Admin</text>
          <text class="brand-subtitle">Operations Console</text>
        </view>
      </view>

      <nav class="admin-nav">
        <button
          v-for="item in navItems"
          :key="item.path"
          class="nav-item"
          :class="{ active: active === item.key }"
          @tap="go(item.path)"
        >
          <text class="nav-dot"></text>
          <text>{{ item.label }}</text>
        </button>
      </nav>

      <view class="admin-session">
        <text class="session-label">Current session</text>
        <text class="session-user">{{ sessionLabel }}</text>
        <text class="session-note">Requires owner, admin, operator, ADMIN_EMAILS, or ADMIN_USER_IDS.</text>
      </view>
    </aside>

    <main class="admin-main">
      <view class="admin-topbar">
        <view>
          <text class="page-kicker">Admin</text>
          <text class="page-title">{{ title }}</text>
          <text v-if="subtitle" class="page-subtitle">{{ subtitle }}</text>
        </view>
        <button class="secondary-action" @tap="goHome">Back to site</button>
      </view>

      <slot />
    </main>
  </view>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { getAuthProvider, getUsername } from '../../utils/auth';

const props = defineProps<{
  title: string;
  subtitle?: string;
  active: 'overview' | 'users' | 'orders';
}>();

const active = computed(() => props.active);

const navItems = [
  { key: 'overview', label: 'Overview', path: '/admin' },
  { key: 'users', label: 'Users & credits', path: '/admin/users' },
  { key: 'orders', label: 'Orders', path: '/admin/orders' },
] as const;

const sessionLabel = computed(() => {
  const username = getUsername();
  const provider = getAuthProvider();
  if (username) return username;
  return provider ? `Provider: ${provider}` : 'Unknown session';
});

function go(path: string) {
  uni.navigateTo({ url: path });
}

function goHome() {
  uni.reLaunch({ url: '/pages/index/index' });
}
</script>

<style lang="scss" scoped>
.admin-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  background: #f5f7fb;
  color: #111827;
}

.admin-sidebar {
  min-height: 100vh;
  padding: 24px 18px;
  background: #111827;
  color: #ffffff;
  display: flex;
  flex-direction: column;
  gap: 24px;
  position: sticky;
  top: 0;
}

.admin-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 6px 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  cursor: pointer;
}

.brand-mark {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #f2c078;
  color: #111827;
  font-weight: 900;
  font-size: 14px;
}

.brand-title,
.brand-subtitle,
.session-label,
.session-user,
.session-note,
.page-kicker,
.page-title,
.page-subtitle {
  display: block;
}

.brand-title {
  font-size: 16px;
  font-weight: 800;
}

.brand-subtitle {
  margin-top: 2px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.58);
}

.admin-nav {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.nav-item {
  min-height: 44px;
  padding: 0 12px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: rgba(255, 255, 255, 0.74);
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 10px;
  font-size: 14px;
  font-weight: 700;
  text-align: left;
}

.nav-item.active {
  background: rgba(242, 192, 120, 0.14);
  color: #ffffff;
}

.nav-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: currentColor;
  opacity: 0.8;
}

.admin-session {
  margin-top: auto;
  padding: 14px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.08);
}

.session-label {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.52);
}

.session-user {
  margin-top: 4px;
  font-size: 13px;
  font-weight: 700;
  word-break: break-all;
}

.session-note {
  margin-top: 8px;
  font-size: 11px;
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.52);
}

.admin-main {
  min-width: 0;
  padding: 28px;
}

.admin-topbar {
  min-height: 88px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 20px;
}

.page-kicker {
  font-size: 12px;
  font-weight: 900;
  color: #b37428;
  text-transform: uppercase;
}

.page-title {
  margin-top: 4px;
  font-size: 30px;
  line-height: 1.2;
  font-weight: 850;
  color: #111827;
}

.page-subtitle {
  margin-top: 8px;
  max-width: 780px;
  font-size: 14px;
  line-height: 1.6;
  color: #5b6472;
}

.secondary-action {
  min-height: 40px;
  padding: 0 16px;
  border-radius: 8px;
  border: 1px solid #d7dce5;
  background: #ffffff;
  color: #1f2937;
  font-size: 13px;
  font-weight: 800;
}

@media (max-width: 900px) {
  .admin-shell {
    grid-template-columns: 1fr;
  }

  .admin-sidebar {
    min-height: auto;
    position: static;
  }

  .admin-nav {
    flex-direction: row;
    overflow-x: auto;
  }

  .admin-main {
    padding: 20px 14px 40px;
  }

  .admin-topbar {
    flex-direction: column;
    min-height: auto;
  }
}
</style>
