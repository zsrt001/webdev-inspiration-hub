<template>
  <view class="auth-page">
    <view class="auth-shell">
      <view class="brand" @tap="goHome">
        <text class="brand-title heading-serif">AI Wedding</text>
        <text class="brand-subtitle">{{ tr('创建账号后，注册会自动登录，后续订单、积分和生成记录都归到同一个用户下。', 'Create an account and you will be signed in automatically. Orders, credits, and generation records stay under the same user.') }}</text>
      </view>

      <view class="auth-card">
        <text class="auth-kicker">{{ tr('创建账号', 'Create Account') }}</text>
        <text class="auth-title heading-serif">{{ tr('开始保存你的创作资产', 'Start saving your creative work') }}</text>
        <text class="auth-copy">{{ tr('密码会使用 bcrypt 加密后存储，不会明文保存。', 'Passwords are stored as bcrypt hashes, never plain text.') }}</text>

        <view class="form-stack">
          <view class="field">
            <text class="field-label">{{ tr('用户名', 'Username') }}</text>
            <input v-model="username" class="field-input" maxlength="64" :placeholder="tr('3-64 位，可用字母、数字、下划线', '3-64 characters, letters/numbers/underscore')" />
          </view>

          <view class="field">
            <text class="field-label">{{ tr('密码', 'Password') }}</text>
            <input v-model="password" class="field-input" password maxlength="128" :placeholder="tr('至少 6 位', 'At least 6 characters')" />
          </view>

          <view class="field">
            <text class="field-label">{{ tr('确认密码', 'Confirm Password') }}</text>
            <input v-model="confirmPassword" class="field-input" password maxlength="128" :placeholder="tr('再次输入密码', 'Enter password again')" />
          </view>

          <text v-if="error" class="error-text">{{ error }}</text>

          <button class="btn btn-primary auth-button" :disabled="submitting" @tap="submit">
            {{ submitting ? tr('注册中…', 'Creating...') : tr('注册并登录', 'Create Account') }}
          </button>
        </view>

        <view class="auth-footer">
          <text>{{ tr('已有账号？', 'Already have an account?') }}</text>
          <text class="link" @tap="goLogin">{{ tr('去登录', 'Sign in') }}</text>
        </view>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { useI18nStore } from '../../stores/i18n';
import { registerWithPassword } from '../../utils/auth';

const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const username = ref('');
const password = ref('');
const confirmPassword = ref('');
const submitting = ref(false);
const error = ref('');

function validate(): boolean {
  const cleanUsername = username.value.trim();
  if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]{2,63}$/.test(cleanUsername)) {
    error.value = tr('用户名需为 3-64 位，可使用字母、数字、下划线、点或短横线。', 'Username must be 3-64 characters and use letters, numbers, underscore, dot, or hyphen.');
    return false;
  }
  if (password.value.length < 6) {
    error.value = tr('密码至少 6 位。', 'Password must be at least 6 characters.');
    return false;
  }
  if (password.value !== confirmPassword.value) {
    error.value = tr('两次输入的密码不一致。', 'Passwords do not match.');
    return false;
  }
  error.value = '';
  return true;
}

async function submit() {
  if (!validate()) return;
  submitting.value = true;
  try {
    await registerWithPassword(username.value, password.value);
    uni.showToast({ title: tr('注册成功', 'Account created'), icon: 'success' });
    uni.reLaunch({ url: '/pages/index/index' });
  } catch (err: any) {
    error.value = err?.message || tr('注册失败，请换一个用户名。', 'Registration failed. Try another username.');
  } finally {
    submitting.value = false;
  }
}

function goLogin() {
  uni.navigateTo({ url: '/pages/auth/login' });
}

function goHome() {
  uni.reLaunch({ url: '/pages/index/index' });
}
</script>

<style lang="scss" scoped>
@import './auth.scss';
</style>
