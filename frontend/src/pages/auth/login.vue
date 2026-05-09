<template>
  <view class="auth-page">
    <view class="auth-shell">
      <view class="brand" @tap="goHome">
        <text class="brand-title heading-serif">AI Wedding</text>
        <text class="brand-subtitle">{{ tr('登录后，创作记录、积分和订单会绑定到你的账号。', 'Sign in to keep credits, orders, and generation records tied to your account.') }}</text>
      </view>

      <view class="auth-card">
        <text class="auth-kicker">{{ tr('账号登录', 'Account Sign In') }}</text>
        <text class="auth-title heading-serif">{{ tr('欢迎回来', 'Welcome back') }}</text>
        <text class="auth-copy">{{ tr('使用用户名和密码登录，继续管理你的婚纱照创作。', 'Use your username and password to continue your wedding portrait workspace.') }}</text>

        <view class="form-stack">
          <view class="field">
            <text class="field-label">{{ tr('用户名', 'Username') }}</text>
            <input v-model="username" class="field-input" maxlength="64" :placeholder="tr('请输入用户名', 'Enter username')" />
          </view>

          <view class="field">
            <text class="field-label">{{ tr('密码', 'Password') }}</text>
            <input v-model="password" class="field-input" password maxlength="128" :placeholder="tr('请输入密码', 'Enter password')" />
          </view>

          <text v-if="error" class="error-text">{{ error }}</text>

          <button class="btn btn-primary auth-button" :disabled="submitting" @tap="submit">
            {{ submitting ? tr('登录中…', 'Signing in...') : tr('登录', 'Sign In') }}
          </button>

          <button v-if="supabaseEnabled" class="btn btn-outline auth-button secondary" @tap="googleSignIn">
            {{ tr('使用 Google 登录', 'Sign in with Google') }}
          </button>
        </view>

        <view class="auth-footer">
          <text>{{ tr('还没有账号？', 'No account yet?') }}</text>
          <text class="link" @tap="goRegister">{{ tr('立即注册', 'Create account') }}</text>
        </view>
        <text class="guest-link" @tap="goHome">{{ tr('先以游客身份体验', 'Continue as guest') }}</text>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useI18nStore } from '../../stores/i18n';
import { loginWithPassword, signInWithGoogle } from '../../utils/auth';
import { refreshSupabaseConfig } from '../../utils/supabase';

const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);
const supabaseEnabled = ref(false);

const username = ref('');
const password = ref('');
const submitting = ref(false);
const error = ref('');

function validate(): boolean {
  const cleanUsername = username.value.trim();
  if (cleanUsername.length < 3) {
    error.value = tr('用户名至少 3 位。', 'Username must be at least 3 characters.');
    return false;
  }
  if (password.value.length < 6) {
    error.value = tr('密码至少 6 位。', 'Password must be at least 6 characters.');
    return false;
  }
  error.value = '';
  return true;
}

async function submit() {
  if (!validate()) return;
  submitting.value = true;
  try {
    await loginWithPassword(username.value, password.value);
    uni.showToast({ title: tr('登录成功', 'Signed in'), icon: 'success' });
    uni.reLaunch({ url: '/pages/index/index' });
  } catch (err: any) {
    error.value = err?.message || tr('用户名或密码不正确。', 'Invalid username or password.');
  } finally {
    submitting.value = false;
  }
}

async function googleSignIn() {
  try {
    await signInWithGoogle();
  } catch (err: any) {
    error.value = err?.message || tr('Google 登录失败。', 'Google sign-in failed.');
  }
}

function goRegister() {
  uni.navigateTo({ url: '/pages/auth/register' });
}

function goHome() {
  uni.reLaunch({ url: '/pages/index/index' });
}

onMounted(async () => {
  supabaseEnabled.value = await refreshSupabaseConfig();
});
</script>

<style lang="scss" scoped>
@import './auth.scss';
</style>
