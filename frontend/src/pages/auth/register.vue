<template>
  <view class="auth-page">
    <view class="auth-shell">
      <view class="brand" @tap="goHome">
        <text class="brand-title heading-serif">AI Wedding</text>
        <text class="brand-subtitle">{{ tr('创建账号后，注册会自动登录，后续订单、积分和生成记录都归到同一个用户下。', 'Create an account and you will be signed in automatically. Orders, credits, and generation records stay under the same user.') }}</text>
      </view>

      <view class="auth-card">
        <text class="auth-kicker">{{ tr('创建账号', 'Create Account') }}</text>
        <text class="auth-title heading-serif">{{ tr('注册即送 2 体验积分', 'Sign up and get 2 starter credits') }}</text>
        <text class="auth-copy">{{ tr('体验积分仅用于一次基础单人生成。双人、异地、金婚重塑和导演模式需要充值或管理员授权积分。', 'Starter credits cover one base single portrait. Couple, remote, vintage, and director mode require top-up or admin-granted credits.') }}</text>

        <view class="form-stack">
          <view class="field">
            <text class="field-label">{{ tr('用户名', 'Username') }}</text>
            <input v-model="username" class="field-input" maxlength="64" :placeholder="tr('3-64 位，可用字母、数字、下划线', '3-64 characters, letters/numbers/underscore')" />
          </view>

          <view class="field">
            <text class="field-label">{{ tr('邮箱', 'Email') }}</text>
            <view class="field-row">
              <input v-model="email" class="field-input field-input-flex" type="text" maxlength="255" :placeholder="tr('用于验证和找回密码', 'For verification and recovery')" />
              <button class="btn btn-small btn-outline send-code-btn" :disabled="codeCooldown > 0 || sendingCode" @tap="handleSendCode">
                {{ codeCooldown > 0 ? `${codeCooldown}s` : (sendingCode ? '...' : tr('发送验证码', 'Send Code')) }}
              </button>
            </view>
          </view>

          <view class="field">
            <text class="field-label">{{ tr('验证码', 'Verification Code') }}</text>
            <input v-model="verificationCode" class="field-input" type="number" maxlength="6" :placeholder="tr('输入 6 位验证码', 'Enter 6-digit code')" />
          </view>

          <view class="field">
            <text class="field-label">{{ tr('密码', 'Password') }}</text>
            <input v-model="password" class="field-input" password maxlength="128" :placeholder="tr('至少 8 位', 'At least 8 characters')" />
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
import { ref, onUnmounted } from 'vue';
import { useI18nStore } from '../../stores/i18n';
import { registerWithPassword, sendVerificationCode } from '../../utils/auth';

const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const username = ref('');
const email = ref('');
const verificationCode = ref('');
const password = ref('');
const confirmPassword = ref('');
const submitting = ref(false);
const sendingCode = ref(false);
const codeCooldown = ref(0);
const error = ref('');

let cooldownTimer: ReturnType<typeof setInterval> | null = null;

function startCooldown() {
  codeCooldown.value = 60;
  cooldownTimer = setInterval(() => {
    codeCooldown.value--;
    if (codeCooldown.value <= 0 && cooldownTimer) {
      clearInterval(cooldownTimer);
      cooldownTimer = null;
    }
  }, 1000);
}

onUnmounted(() => {
  if (cooldownTimer) clearInterval(cooldownTimer);
});

async function handleSendCode() {
  const cleanEmail = email.value.trim();
  if (!cleanEmail || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(cleanEmail)) {
    error.value = tr('请输入有效的邮箱地址。', 'Please enter a valid email address.');
    return;
  }
  error.value = '';
  sendingCode.value = true;
  try {
    await sendVerificationCode(cleanEmail);
    uni.showToast({ title: tr('验证码已发送', 'Code sent'), icon: 'success' });
    startCooldown();
  } catch (err: any) {
    error.value = err?.message || tr('发送验证码失败，请稍后重试。', 'Failed to send code. Please try again.');
  } finally {
    sendingCode.value = false;
  }
}

function validate(): boolean {
  const cleanUsername = username.value.trim();
  if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]{2,63}$/.test(cleanUsername)) {
    error.value = tr('用户名需为 3-64 位，可使用字母、数字、下划线、点或短横线。', 'Username must be 3-64 characters and use letters, numbers, underscore, dot, or hyphen.');
    return false;
  }
  const cleanEmail = email.value.trim();
  if (!cleanEmail || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(cleanEmail)) {
    error.value = tr('请输入有效的邮箱地址。', 'Please enter a valid email address.');
    return false;
  }
  if (verificationCode.value.trim().length !== 6) {
    error.value = tr('请输入 6 位验证码。', 'Please enter the 6-digit verification code.');
    return false;
  }
  if (password.value.length < 8) {
    error.value = tr('密码至少 8 位。', 'Password must be at least 8 characters.');
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
    await registerWithPassword(
      username.value,
      password.value,
      email.value.trim(),
      verificationCode.value.trim()
    );
    uni.showToast({ title: tr('注册成功', 'Account created'), icon: 'success' });
    uni.reLaunch({ url: '/pages/index/index' });
  } catch (err: any) {
    error.value = err?.message || tr('注册失败，请检查信息后重试。', 'Registration failed. Please check your info and try again.');
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

.field-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.field-input-flex {
  flex: 1;
}

.send-code-btn {
  white-space: nowrap;
  min-width: 100px;
  font-size: 13px;
  padding: 8px 12px;
  border-radius: 6px;
}
</style>
