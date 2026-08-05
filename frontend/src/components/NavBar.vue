<template>
  <view
    class="navbar shadow-sm"
    role="navigation"
    :aria-label="i18nStore.locale === 'zh' ? '主导航' : 'Primary navigation'"
  >
    <view class="navbar-inner">
      <a class="logo-area" href="/" @click.prevent="goHome">
        <text class="logo-main heading-serif">{{ t('nav.brand') }}</text>
      </a>

      <view class="nav-links-permanent">
        <a
          class="nav-link"
          href="/"
          :class="{ active: currentPath === '/pages/index/index' }"
          :aria-current="currentPath === '/pages/index/index' ? 'page' : undefined"
          @click.prevent="navigate('/pages/index/index')"
        >
          {{ t('nav.home') }}
        </a>
        <a
          v-if="creationAvailable"
          class="nav-link"
          href="/pages/create/index"
          :class="{ active: currentPath === '/pages/create/index' || currentPath === '/pages/detail/detail' }"
          :aria-current="currentPath === '/pages/create/index' || currentPath === '/pages/detail/detail' ? 'page' : undefined"
          @click.prevent="navigate('/pages/create/index')"
        >
          {{ t('nav.studio') }}
        </a>
        <a
          class="nav-link"
          href="/pages/orders/orders"
          :class="{ active: currentPath === '/pages/orders/orders' }"
          :aria-current="currentPath === '/pages/orders/orders' ? 'page' : undefined"
          @click.prevent="navigate('/pages/orders/orders')"
        >
          {{ t('nav.orders') }}
        </a>
        <a
          class="nav-link"
          href="/pages/account/index"
          :class="{ active: currentPath === '/pages/account/index' }"
          :aria-current="currentPath === '/pages/account/index' ? 'page' : undefined"
          @click.prevent="navigate('/pages/account/index')"
        >
          {{ accountLabel }}
        </a>
        <a
          v-if="isAdmin"
          class="nav-link"
          href="/admin"
          :class="{ active: currentPath === '/admin' || currentPath.startsWith('/admin/') }"
          :aria-current="currentPath === '/admin' || currentPath.startsWith('/admin/') ? 'page' : undefined"
          @click.prevent="navigate('/admin')"
        >
          {{ adminLabel }}
        </a>
      </view>

      <view class="nav-actions">
        <view
          class="lang-toggle"
          role="button"
          tabindex="0"
          :aria-label="i18nStore.locale === 'zh' ? 'Switch to English' : '切换到中文'"
          @tap.stop="toggleLocale"
          @keydown.enter.stop.prevent="toggleLocale"
          @keydown.space.stop.prevent="toggleLocale"
        >
          <text class="lang-text">{{ localeButtonText }}</text>
        </view>

        <a
          v-if="accountAuthed || googleAuthAvailable"
          class="auth-chip"
          :href="authHref"
          @click.stop.prevent="handleAuthTap"
        >
          <text class="auth-text">{{ authLabel }}</text>
        </a>

        <view
          v-if="accountAuthed"
          class="balance-chip"
          :class="{ disabled: !billingAvailable }"
          role="button"
          :tabindex="billingAvailable ? 0 : -1"
          :aria-disabled="!billingAvailable"
          @tap="showTopUp"
          @keydown.enter.prevent="showTopUp"
          @keydown.space.prevent="showTopUp"
        >
          <text class="chip-icon">CR</text>
          <text class="chip-val">{{ creditBalance }}</text>
        </view>

        <view
          class="menu-dots-mobile"
          role="button"
          tabindex="0"
          aria-controls="mobile-navigation-menu"
          :aria-expanded="showMenu"
          :aria-label="i18nStore.locale === 'zh' ? '打开导航菜单' : 'Open navigation menu'"
          @tap="toggleMenu"
          @keydown.enter.prevent="toggleMenu"
          @keydown.space.prevent="toggleMenu"
        >
          <view class="dot"></view>
          <view class="dot"></view>
          <view class="dot"></view>
        </view>
      </view>

      <view v-if="showMenu" id="mobile-navigation-menu" class="dropdown-menu-mobile">
        <a class="menu-item" href="/" @click.prevent="navigate('/pages/index/index')">{{ t('nav.home') }}</a>
        <a v-if="creationAvailable" class="menu-item" href="/pages/create/index" @click.prevent="navigate('/pages/create/index')">{{ t('nav.studio') }}</a>
        <a class="menu-item" href="/pages/orders/orders" @click.prevent="navigate('/pages/orders/orders')">{{ t('nav.orders') }}</a>
        <a v-if="isAdmin" class="menu-item" href="/admin" @click.prevent="navigate('/admin')">{{ adminLabel }}</a>
        <a v-if="accountAuthed || googleAuthAvailable" class="menu-item" :href="authHref" @click.prevent="handleAuthTap">{{ authLabel }}</a>
        <a class="menu-item" href="/pages/legal/refund" @click.prevent="navigate('/pages/legal/refund')">{{ i18nStore.locale === 'zh' ? '退款与客服' : 'Refunds & Support' }}</a>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useI18nStore } from '../stores/i18n';
import { useOpsStore } from '../stores/ops';
import { get } from '../utils/api';
import { ensureSession } from '../utils/auth';

const creditBalance = ref(0);
const showMenu = ref(false);
const accountAuthed = ref(false);
const username = ref('');
const isAdmin = ref(false);
const i18nStore = useI18nStore();
const opsStore = useOpsStore();
const t = i18nStore.t;
const localeButtonText = computed(() => (i18nStore.locale === 'zh' ? 'EN' : '中文'));
const accountLabel = computed(() => (i18nStore.locale === 'zh' ? '账户' : 'Account'));
const adminLabel = computed(() => (i18nStore.locale === 'zh' ? '后台' : 'Admin'));
const creationAvailable = computed(() => opsStore.creationAvailable);
const googleAuthAvailable = computed(() => opsStore.googleAuthAvailable);
const billingAvailable = computed(() => opsStore.billingAvailable);

const authLabel = computed(() => {
  if (accountAuthed.value) return username.value || accountLabel.value;
  return i18nStore.locale === 'zh' ? '登录' : 'Sign in';
});
const authHref = computed(() => (accountAuthed.value ? '/pages/account/index' : '/pages/auth/login'));

const currentPath = computed(() => {
  const pages = getCurrentPages();
  if (pages.length === 0) return '';
  return `/${pages[pages.length - 1].route}`;
});

const emit = defineEmits<{
  (e: 'show-payment'): void;
}>();

const pushPages = new Set([
  '/pages/create/index',
  '/pages/detail/detail',
  '/pages/legal/privacy',
  '/pages/legal/terms',
  '/pages/legal/refund',
  '/admin',
  '/admin/users',
  '/admin/orders',
  '/pages/account/index',
  '/pages/auth/login',
  '/pages/auth/register',
]);

const goHome = () => {
  uni.reLaunch({ url: '/pages/index/index' });
};

const toggleLocale = () => {
  i18nStore.toggleLocale();
};

const toggleMenu = () => {
  showMenu.value = !showMenu.value;
};

const navigate = (path: string) => {
  if (path === '/pages/create/index' && !creationAvailable.value) return;
  if (path === '/pages/auth/login' && !googleAuthAvailable.value) return;
  if (currentPath.value === path) return;
  if (pushPages.has(path)) {
    uni.navigateTo({ url: path });
    return;
  }
  uni.reLaunch({ url: path });
};

const fetchBalance = async () => {
  if (!accountAuthed.value) {
    creditBalance.value = 0;
    return;
  }
  try {
    const res = await get<{ balance: number }>('/credits/balance', { showLoading: false, showError: false });
    creditBalance.value = res.balance;
  } catch {
    creditBalance.value = 0;
  }
};

const fetchProfileRole = async () => {
  if (!accountAuthed.value) {
    isAdmin.value = false;
    return;
  }
  try {
    await get('/admin/me', { showLoading: false, showError: false });
    isAdmin.value = true;
  } catch {
    isAdmin.value = false;
  }
};

const refreshAuthState = async () => {
  const user = await ensureSession();
  accountAuthed.value = Boolean(user);
  username.value = user?.nickname || user?.email || '';
};

const handleAuthTap = async () => {
  showMenu.value = false;
  if (accountAuthed.value) {
    navigate('/pages/account/index');
    return;
  }
  if (!googleAuthAvailable.value) return;
  navigate('/pages/auth/login');
};

const showTopUp = () => {
  if (!billingAvailable.value) return;
  emit('show-payment');
};

const refreshBalance = () => fetchBalance();

onMounted(async () => {
  await opsStore.fetchPublicConfig();
  await refreshAuthState();
  await Promise.all([fetchBalance(), fetchProfileRole()]);
});

defineExpose({ refreshBalance });
</script>

<style lang="scss" scoped>
.navbar {
  width: 100%;
  height: 64px;
  background: rgba(247, 248, 250, 0.92);
  backdrop-filter: blur(16px);
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 9999;
  border-bottom: 1px solid rgba(221, 225, 232, 0.9);
}

.navbar-inner {
  max-width: 1440px;
  margin: 0 auto;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 40px;

  @media (max-width: 768px) {
    padding: 0 20px;
  }
}

.logo-main {
  font-size: 22px;
  color: #17191f;
  font-weight: 700;
  white-space: nowrap;
}

.logo-area,
.nav-link,
.auth-chip,
.menu-item {
  color: inherit;
  text-decoration: none;
}

.logo-area {
  display: flex;
  align-items: center;
}

.nav-links-permanent {
  display: flex;
  gap: 28px;

  @media (max-width: 960px) {
    display: none;
  }
}

.nav-link {
  display: flex;
  align-items: center;
  font-size: 14px;
  font-weight: 800;
  color: #4c5360;
  letter-spacing: 0;
  padding: 9px 0;
  position: relative;
  opacity: 1;
  transition: all 0.25s;
  cursor: pointer;

  &:hover,
  &.active {
    color: #17191f;

    &::after {
      width: 100%;
    }
  }

  &::after {
    content: '';
    position: absolute;
    bottom: 0;
    left: 0;
    width: 0;
    height: 2px;
    background: $uni-color-primary;
    transition: width 0.25s ease;
  }
}

.nav-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.lang-toggle,
.auth-chip,
.balance-chip {
  min-width: 44px;
  height: 36px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid #d8dde5;
  background: rgba(255, 255, 255, 0.96);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 1px 2px rgba(23, 25, 31, 0.04);
}

.balance-chip {
  gap: 6px;
  border-color: rgba(17, 106, 96, 0.22);
  background: #f3faf8;
}

.lang-text,
.auth-text,
.chip-icon,
.chip-val {
  font-size: 12px;
  font-weight: 800;
  color: $uni-color-primary;
}

.chip-icon {
  letter-spacing: 0;
}

.menu-dots-mobile {
  display: none;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  border: 1px solid #d8dde5;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 3px;

  @media (max-width: 960px) {
    display: flex;
  }
}

.dot {
  width: 4px;
  height: 4px;
  background: $uni-color-primary;
  border-radius: 999px;
}

.dropdown-menu-mobile {
  position: absolute;
  top: 60px;
  right: 20px;
  min-width: 140px;
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid #dde1e8;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 20px 50px rgba(23, 25, 31, 0.12);

  @media (min-width: 961px) {
    display: none;
  }
}

.menu-item {
  display: block;
  padding: 14px 16px;
  font-size: 14px;
  color: $uni-text-color;
  border-bottom: 1px solid #edf0f4;

  &:last-child {
    border-bottom: none;
  }
}

.logo-area:focus-visible,
.nav-link:focus-visible,
.lang-toggle:focus-visible,
.auth-chip:focus-visible,
.balance-chip:focus-visible,
.menu-dots-mobile:focus-visible,
.menu-item:focus-visible {
  outline: 3px solid #116a60;
  outline-offset: 3px;
}

@media (max-width: 560px) {
  .navbar {
    overflow: hidden;
  }

  .navbar-inner {
    width: 100%;
    max-width: 100%;
    box-sizing: border-box;
    padding: 0 16px;
    gap: 12px;
  }

  .logo-area {
    min-width: 0;
    flex: 1 1 auto;
  }

  .logo-main {
    font-size: 20px;
  }

  .nav-actions {
    flex: 0 0 auto;
    gap: 8px;
    margin-right: 8px;
  }

  .auth-chip {
    display: none;
  }

  .lang-toggle,
  .balance-chip {
    min-width: 40px;
    height: 36px;
    padding: 0 10px;
  }

  .menu-dots-mobile {
    width: 34px;
    height: 34px;
  }
}
</style>
