<template>
  <view class="navbar shadow-sm">
    <view class="navbar-inner">
      <view class="logo-area" @tap="goHome">
        <text class="logo-main heading-serif">{{ t('nav.brand') }}</text>
      </view>

      <view class="nav-links-permanent">
        <view class="nav-link" :class="{ active: currentPath === '/pages/index/index' }" @tap="navigate('/pages/index/index')">
          {{ t('nav.home') }}
        </view>
        <view class="nav-link" :class="{ active: currentPath === '/pages/create/index' || currentPath === '/pages/detail/detail' }" @tap="navigate('/pages/create/index')">
          {{ t('nav.studio') }}
        </view>
        <view class="nav-link" :class="{ active: currentPath === '/pages/orders/orders' }" @tap="navigate('/pages/orders/orders')">
          {{ t('nav.orders') }}
        </view>
        <view class="nav-link" :class="{ active: currentPath === '/pages/account/index' }" @tap="navigate('/pages/account/index')">
          {{ accountLabel }}
        </view>
      </view>

      <view class="nav-actions">
        <view class="lang-toggle" @tap.stop="toggleLocale">
          <text class="lang-text">{{ localeButtonText }}</text>
        </view>

        <view class="auth-chip" @tap.stop="handleAuthTap">
          <text class="auth-text">{{ authLabel }}</text>
        </view>

        <view v-if="accountAuthed" class="balance-chip" @tap="showTopUp">
          <text class="chip-icon">CR</text>
          <text class="chip-val">{{ creditBalance }}</text>
        </view>
        <view v-else class="balance-chip balance-chip-cta" @tap="handleAuthTap">
          <text class="chip-val">{{ i18nStore.locale === 'zh' ? '注册领积分' : 'Sign up for credits' }}</text>
        </view>

        <view class="menu-dots-mobile" @tap="toggleMenu">
          <view class="dot"></view>
          <view class="dot"></view>
          <view class="dot"></view>
        </view>
      </view>

      <view v-if="showMenu" class="dropdown-menu-mobile" @tap="showMenu = false">
        <view class="menu-item" @tap="navigate('/pages/index/index')">{{ t('nav.home') }}</view>
        <view class="menu-item" @tap="navigate('/pages/create/index')">{{ t('nav.studio') }}</view>
        <view class="menu-item" @tap="navigate('/pages/orders/orders')">{{ t('nav.orders') }}</view>
        <view class="menu-item" @tap="handleAuthTap">{{ authLabel }}</view>
        <view class="menu-item" @tap="navigate('/pages/legal/refund')">{{ i18nStore.locale === 'zh' ? '退款与客服' : 'Refunds & Support' }}</view>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useI18nStore } from '../stores/i18n';
import { get } from '../utils/api';
import { getUsername, isPasswordLoggedIn, isSupabaseLoggedIn } from '../utils/auth';

const creditBalance = ref(0);
const showMenu = ref(false);
const accountAuthed = ref(false);
const username = ref('');
const i18nStore = useI18nStore();
const t = i18nStore.t;
const localeButtonText = computed(() => (i18nStore.locale === 'zh' ? 'EN' : '中文'));
const accountLabel = computed(() => (i18nStore.locale === 'zh' ? '账户' : 'Account'));

const authLabel = computed(() => {
  if (accountAuthed.value) return username.value || accountLabel.value;
  return i18nStore.locale === 'zh' ? '登录' : 'Sign in';
});

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
  '/pages/join/landing',
  '/pages/admin/index',
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

const refreshAuthState = () => {
  accountAuthed.value = isPasswordLoggedIn() || isSupabaseLoggedIn();
  username.value = getUsername() || '';
};

const handleAuthTap = async () => {
  showMenu.value = false;
  if (accountAuthed.value) {
    navigate('/pages/account/index');
    return;
  }
  navigate('/pages/auth/login');
};

const showTopUp = () => {
  emit('show-payment');
};

const refreshBalance = () => fetchBalance();

onMounted(async () => {
  refreshAuthState();
  await fetchBalance();
  refreshAuthState();
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
}

.nav-links-permanent {
  display: flex;
  gap: 28px;

  @media (max-width: 960px) {
    display: none;
  }
}

.nav-link {
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
  padding: 14px 16px;
  font-size: 14px;
  color: $uni-text-color;
  border-bottom: 1px solid #edf0f4;

  &:last-child {
    border-bottom: none;
  }
}
</style>
