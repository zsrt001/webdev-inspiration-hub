<template>
  <view class="auth-page" role="main">
    <view class="auth-shell">
      <a class="brand" href="/" @click.prevent="goHome">
        <text class="brand-title heading-serif">VowPic</text>
        <text class="brand-subtitle">
          {{ tr('使用 Google 登录后，积分、订单和生成记录都会绑定到同一个已验证账号。', 'Sign in with Google to keep credits, orders, and generation records tied to one verified account.') }}
        </text>
      </a>

      <view class="auth-card">
        <view class="auth-card-head">
          <text class="auth-kicker">{{ tr('安全登录', 'Secure sign in') }}</text>
          <button
            class="language-toggle"
            :aria-label="tr('切换到英文', 'Switch to Chinese')"
            @tap="i18nStore.toggleLocale()"
          >{{ localeButtonText }}</button>
        </view>
        <text class="auth-title heading-serif" role="heading" aria-level="1">{{ tr('使用 Google 继续', 'Continue with Google') }}</text>
        <text class="auth-copy">
          {{ tr('VowPic 使用 Google 作为网站的统一登录方式。', 'VowPic uses Google as the single sign-in method for the web app.') }}
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
            <text>{{ submitting ? tr('连接中...', 'Connecting...') : tr('使用 Google 登录', 'Sign in with Google') }}</text>
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
            <text class="auth-note-title">{{ tr('账户保护', 'Account protection') }}</text>
            <text class="auth-note-line">{{ tr('每个已验证 Google 账号只发放一次欢迎积分。', 'One welcome credit grant per verified Google account.') }}</text>
            <text class="auth-note-line">{{ tr('设备和网络限流会继续保护免费试用，防止刷积分。', 'Device and network limits still protect free trials from abuse.') }}</text>
          </view>
        </view>

        <view class="auth-footer">
          <text>{{ tr('第一次使用？Google 会自动创建你的账号。', 'New here? Google creates your account automatically.') }}</text>
        </view>
        <a class="home-link" href="/" @click.prevent="goHome">{{ tr('返回首页', 'Back to home') }}</a>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { onLoad } from '@dcloudio/uni-app';
import { useI18nStore } from '../../stores/i18n';
import { localizedAuthError, signInWithGoogle } from '../../utils/auth';
import { sanitizeLoginNextPath } from '../../utils/billingDisplay';
import { getSupabaseClient, refreshSupabaseConfig } from '../../utils/supabase';

const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);
const localeButtonText = computed(() => (i18nStore.locale === 'zh' ? 'EN' : '中文'));

const supabaseEnabled = ref(false);
const configLoading = ref(true);
const submitting = ref(false);
const rawError = ref<unknown>(null);
const error = computed(() => rawError.value ? localizedAuthError(rawError.value, i18nStore.locale) : '');
const nextPath = ref('/pages/account/index');

async function googleSignIn(selectAccount = false) {
  if (!supabaseEnabled.value) return;
  if (submitting.value) return;
  submitting.value = true;
  rawError.value = null;
  try {
    await signInWithGoogle(nextPath.value, { selectAccount });
  } catch (err: any) {
    rawError.value = err || new Error('Google sign-in was not completed.');
    submitting.value = false;
  }
}

onLoad((options) => {
  nextPath.value = sanitizeLoginNextPath(String(options?.next || ''));
});

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
