<template>
  <view class="auth-page" role="main">
    <view class="auth-shell">
      <a class="brand" href="/" @click.prevent="goHome">
        <text class="brand-title heading-serif">VowPic</text>
        <text class="brand-subtitle">
          {{ tr('使用 Google 创建账号，积分、订单和后续购买都会绑定到同一个已验证身份。', 'Create your account with Google so credits, orders, and future purchases stay under one verified identity.') }}
        </text>
      </a>

      <view class="auth-card">
        <view class="auth-card-head">
          <text class="auth-kicker">{{ tr('创建账号', 'Create account') }}</text>
          <button
            class="language-toggle"
            :aria-label="tr('切换到英文', 'Switch to Chinese')"
            @tap="i18nStore.toggleLocale()"
          >{{ localeButtonText }}</button>
        </view>
        <text class="auth-title heading-serif" role="heading" aria-level="1">{{ tr('从 Google 开始', 'Start with Google') }}</text>
        <text class="auth-copy">
          {{ tr('VowPic 使用 Google 作为网站的统一注册和登录方式。', 'VowPic uses Google as the single registration and sign-in method for the web app.') }}
        </text>

        <view class="form-stack">
          <text v-if="error" class="error-text">{{ error }}</text>

          <button v-if="configLoading" class="btn btn-primary auth-button" disabled>
            {{ tr('正在准备登录...', 'Preparing sign-in...') }}
          </button>

          <button
            v-else-if="supabaseEnabled"
            class="btn btn-primary auth-button google-button"
            :disabled="submitting"
            role="button"
            :tabindex="submitting ? -1 : 0"
            @tap="googleSignIn(false)"
            @keydown.enter.prevent="googleSignIn(false)"
            @keydown.space.prevent="googleSignIn(false)"
          >
            <text class="google-mark">G</text>
            <text>{{ submitting ? tr('连接中...', 'Connecting...') : tr('使用 Google 创建', 'Create with Google') }}</text>
          </button>

          <button v-else class="btn btn-primary auth-button" disabled>
            {{ tr('Google 登录暂不可用', 'Google sign-in unavailable') }}
          </button>

          <button
            v-if="supabaseEnabled && !submitting"
            class="switch-account-button"
            @tap="googleSignIn(true)"
          >{{ tr('使用其他 Google 账号', 'Use another Google account') }}</button>

          <view class="auth-note">
            <text class="auth-note-title">{{ tr('试用规则', 'Trial rules') }}</text>
            <text class="auth-note-line">{{ tr('欢迎积分只发放给首次验证的 Google 账号。', 'Starter credits are granted once per verified Google account.') }}</text>
            <text class="auth-note-line">{{ tr('付费积分用于解锁高清和无水印交付。', 'Paid credits unlock HD and watermark-free delivery.') }}</text>
          </view>
        </view>

        <view class="auth-footer">
          <text>{{ tr('已经登录？', 'Already signed in?') }}</text>
          <a class="link" href="/pages/account/index" @click.prevent="goAccount">{{ tr('打开账户', 'Open account') }}</a>
        </view>
        <a class="home-link" href="/" @click.prevent="goHome">{{ tr('返回首页', 'Back to home') }}</a>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useI18nStore } from '../../stores/i18n';
import { localizedAuthError, signInWithGoogle } from '../../utils/auth';
import { getSupabaseClient, refreshSupabaseConfig } from '../../utils/supabase';

const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);
const localeButtonText = computed(() => (i18nStore.locale === 'zh' ? 'EN' : '中文'));

const supabaseEnabled = ref(false);
const configLoading = ref(true);
const submitting = ref(false);
const rawError = ref<unknown>(null);
const error = computed(() => rawError.value ? localizedAuthError(rawError.value, i18nStore.locale) : '');

async function googleSignIn(selectAccount = false) {
  if (!supabaseEnabled.value) return;
  if (submitting.value) return;
  submitting.value = true;
  rawError.value = null;
  try {
    await signInWithGoogle('/pages/index/index', { selectAccount });
  } catch (err: any) {
    rawError.value = err || new Error('Google sign-in was not completed.');
    submitting.value = false;
  }
}

function goAccount() {
  uni.navigateTo({ url: '/pages/account/index' });
}

function goHome() {
  uni.reLaunch({ url: '/pages/index/index' });
}

onMounted(async () => {
  try {
    supabaseEnabled.value = await refreshSupabaseConfig();
    if (supabaseEnabled.value) await getSupabaseClient();
    else rawError.value = new Error('Google sign-in is not configured on this deployment.');
  } catch (err) {
    supabaseEnabled.value = false;
    rawError.value = err || new Error('Google sign-in is not configured on this deployment.');
  } finally {
    configLoading.value = false;
  }
});
</script>

<style lang="scss" scoped>
@use './auth.scss';
</style>
