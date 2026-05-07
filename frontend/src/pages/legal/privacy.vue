<template>
  <view class="legal-page">
    <NavBar />
    <view class="legal-shell">
      <view class="legal-hero">
        <text class="eyebrow">{{ tr('法务与合规', 'Legal & Compliance') }}</text>
        <text class="title heading-serif">{{ tr('隐私政策', 'Privacy Policy') }}</text>
        <text class="subtitle">{{ tr('最后更新：2026 年 4 月 26 日', 'Last updated: April 26, 2026') }}</text>
      </view>

      <view class="legal-content">
        <view v-for="section in sections" :key="section.anchor" class="section-card">
          <text class="section-index">{{ section.index }}</text>
          <text class="section-title heading-serif">{{ tr(section.titleZh, section.titleEn) }}</text>
          <text v-for="line in activeLines(section)" :key="line" class="section-line">{{ line }}</text>
        </view>
      </view>
      <LegalFooter />
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import NavBar from '../../components/NavBar.vue';
import LegalFooter from '../../components/LegalFooter.vue';
import { useI18nStore } from '../../stores/i18n';

const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const sections = computed(() => [
  {
    anchor: 'scope',
    index: '01',
    titleZh: '适用范围',
    titleEn: 'Scope',
    zh: [
      '本政策适用于 AI Wedding 的照片上传、AI 生成、支付、订阅、账户中心和客服支持流程。',
      '你使用本服务即表示同意我们按本政策处理必要信息。',
    ],
    en: [
      'This policy applies to AI Wedding photo uploads, AI generation, payments, subscriptions, account center, and support workflows.',
      'By using the service, you agree that we process necessary information under this policy.',
    ],
  },
  {
    anchor: 'data',
    index: '02',
    titleZh: '我们处理的信息',
    titleEn: 'Information We Process',
    zh: [
      '账户信息：邮箱、昵称、头像、Google OAuth 标识、Supabase 用户标识、登录时间和账户状态。',
      '业务信息：上传原图、参考图、文本提示词、生成结果、订单、积分余额、积分流水、订阅状态、支付提供商返回的客户 ID/订阅 ID/事件 ID。',
      '我们不存储银行卡号、CVV、银行账户、原始支付凭据或任何信用卡敏感信息。',
    ],
    en: [
      'Account data: email, name, avatar, Google OAuth ID, Supabase user ID, login time, and account status.',
      'Business data: source uploads, references, prompts, generated results, orders, credit balance, ledger entries, subscription status, and provider customer/subscription/event IDs.',
      'We do not store card numbers, CVV, bank accounts, raw payment credentials, or sensitive credit-card information.',
    ],
  },
  {
    anchor: 'retention',
    index: '03',
    titleZh: '图片保留与自动删除',
    titleEn: 'Image Retention and Deletion',
    zh: [
      '上传原图属于临时处理材料，默认 7 天后自动删除。',
      '生成结果按账户权益保留：免费用户 30 天，积分包付费用户 90 天，订阅用户 180 天，Studio 订阅用户 365 天。',
      '用户可在账户中心主动删除作品。删除后图片文件会被移除，订单元数据仅用于审计、对账和客服支持。',
    ],
    en: [
      'Uploaded source photos are temporary processing inputs and are deleted after 7 days by default.',
      'Generated images are retained by entitlement: free users 30 days, paid credit-pack users 90 days, subscription users 180 days, and Studio subscribers 365 days.',
      'Users can delete images from the account center. Image files are removed, while order metadata may remain for audit, reconciliation, and support.',
    ],
  },
  {
    anchor: 'rights',
    index: '04',
    titleZh: '你的权利',
    titleEn: 'Your Rights',
    zh: [
      '你可以查看账户、订单、生成历史和积分流水，也可以请求删除图片、停止使用账号或联系客服处理隐私问题。',
      '如需处理隐私、退款或侵权投诉，请通过网站页脚、支付页或账户中心提供的官方客服入口联系我们。',
    ],
    en: [
      'You can view account data, orders, generated history, and credit ledger entries, and request image deletion, account closure, or privacy support.',
      'For privacy, refund, or infringement requests, contact us through the official support channel shown in the footer, checkout page, or account center.',
    ],
  },
]);

function activeLines(section: any): string[] {
  return i18nStore.locale === 'zh' ? section.zh : section.en;
}
</script>

<style lang="scss" scoped>
.legal-page {
  min-height: 100vh;
  background: #f7f8fa;
  color: #17191f;
}

.legal-shell {
  max-width: 980px;
  margin: 0 auto;
  padding: 110px 24px 80px;
}

.legal-hero {
  margin-bottom: 26px;
}

.eyebrow {
  display: block;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
  color: #116a60;
}

.title {
  display: block;
  margin-top: 10px;
  font-size: 48px;
  line-height: 1.05;
}

.subtitle {
  display: block;
  margin-top: 10px;
  color: #4c5360;
}

.legal-content {
  display: grid;
  gap: 16px;
}

.section-card {
  padding: 24px;
  border-radius: 8px;
  background: #ffffff;
  border: 1px solid #dde1e8;
  box-shadow: 0 14px 38px rgba(23, 25, 31, 0.06);
}

.section-index {
  display: block;
  color: #116a60;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
}

.section-title {
  display: block;
  margin: 6px 0 12px;
  font-size: 26px;
}

.section-line {
  display: block;
  margin-top: 10px;
  color: #4c5360;
  line-height: 1.8;
  font-size: 14px;
}
</style>
