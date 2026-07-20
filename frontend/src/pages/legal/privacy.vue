<template>
  <view class="legal-page">
    <NavBar />
    <view class="legal-shell" role="main" tabindex="0" aria-label="Privacy Policy">
      <view class="legal-hero">
        <text class="eyebrow">{{ tr('法务与合规', 'Legal & Compliance') }}</text>
        <text class="title heading-serif" role="heading" aria-level="1">{{ tr('隐私政策', 'Privacy Policy') }}</text>
        <text class="subtitle">{{ tr('最后更新：2026 年 7 月 20 日', 'Last updated: July 20, 2026') }}</text>
      </view>

      <view class="legal-content">
        <view v-for="section in sections" :key="section.anchor" class="section-card">
          <text class="section-index">{{ section.index }}</text>
          <text class="section-title heading-serif">{{ tr(section.titleZh, section.titleEn) }}</text>
          <text v-for="line in activeLines(section)" :key="line" class="section-line">{{ line }}</text>
          <a
            v-if="section.anchor === 'rights' && supportAvailable"
            class="support-link"
            :href="supportContactHref"
            :target="supportOpensNewTab ? '_blank' : undefined"
            :rel="supportOpensNewTab ? 'noopener noreferrer' : undefined"
          >
            {{ supportContactLabel }}
          </a>
        </view>
      </view>
      <LegalFooter />
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue';
import NavBar from '../../components/NavBar.vue';
import LegalFooter from '../../components/LegalFooter.vue';
import { useI18nStore } from '../../stores/i18n';
import { useOpsStore } from '../../stores/ops';

const i18nStore = useI18nStore();
const opsStore = useOpsStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);
const supportAvailable = computed(() => opsStore.supportAvailable);
const supportContactHref = computed(() => opsStore.supportContactHref);
const supportOpensNewTab = computed(() => supportContactHref.value.startsWith('https://'));
const supportContactLabel = computed(() =>
  opsStore.publicConfig.support.url
    ? tr('打开已验证隐私客服入口', 'Open verified privacy-support channel')
    : opsStore.publicConfig.support.email,
);

const sections = computed(() => [
  {
    anchor: 'scope',
    index: '01',
    titleZh: '适用范围',
    titleEn: 'Scope',
    zh: [
      '本政策适用于 VowPic 的照片上传、AI 生成、支付、订阅、账户中心和客服支持流程。',
      '你使用本服务即表示同意我们按本政策处理必要信息。',
    ],
    en: [
      'This policy applies to VowPic photo uploads, AI generation, payments, subscriptions, account center, and support workflows.',
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
    titleZh: '图片保留与删除状态',
    titleEn: 'Image Retention and Deletion Status',
    zh: [
      '计划保留期为：上传原图 7 天；免费生成结果 30 天；积分包付费结果 90 天；订阅结果 180 天；Studio 订阅结果 365 天。',
      '自动删除和账户中心删除目前均已暂停，直到可审计、可重试的删除流程完成验证；当前页面不会声称文件已经自动移除。',
      '安全基线阶段不开放新的上传和生成。需要立即删除的数据不应通过本阶段网站提交；删除入口恢复后，本政策会同步更新。',
    ],
    en: [
      'Scheduled retention is 7 days for source uploads, 30 days for free results, 90 days for paid credit-pack results, 180 days for subscription results, and 365 days for Studio subscription results.',
      'Automated and in-account deletion are temporarily paused until the auditable, retryable cleanup flow is verified. This page does not claim that files have already been automatically removed.',
      'New uploads and generation remain closed during the safe-baseline stage. Data requiring immediate deletion should not be submitted through this stage of the site; this policy will be updated when deletion is restored.',
    ],
  },
  {
    anchor: 'rights',
    index: '04',
    titleZh: '你的权利',
    titleEn: 'Your Rights',
    zh: supportAvailable.value
      ? [
          '你可以查看账户、订单、生成历史和积分流水。站内图片删除和账户关闭请求目前暂停，恢复时间以本页状态为准。',
          '如需提交隐私请求，请使用下方经运行时确认的受监控入口；不要发送完整证件号、银行卡信息或支付密码。',
        ]
      : [
          '你可以查看账户、订单、生成历史和积分流水。站内图片删除和账户关闭请求目前暂停，恢复时间以本页状态为准。',
          '目前尚未发布经过验证且受监控的隐私客服渠道；请勿向未经验证的地址发送身份证件、支付凭证或其他敏感信息。',
        ],
    en: supportAvailable.value
      ? [
          'You can view account data, orders, generated history, and credit ledger entries. In-product image deletion and account-closure requests are currently paused; this page is the current status notice.',
          'Use the monitored runtime-confirmed channel below for privacy requests. Do not send full identity numbers, card details, or payment passwords.',
        ]
      : [
          'You can view account data, orders, generated history, and credit ledger entries. In-product image deletion and account-closure requests are currently paused; this page is the current status notice.',
          'A verified, monitored privacy-support channel has not yet been published. Do not send identity documents, payment evidence, or other sensitive information to an unverified address.',
        ],
  },
]);

onMounted(() => {
  void opsStore.fetchPublicConfig();
});

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

.support-link {
  display: inline-flex;
  margin-top: 16px;
  min-height: 44px;
  align-items: center;
  border-radius: 6px;
  background: #116a60;
  color: #ffffff;
  font-size: 14px;
  font-weight: 800;
  padding: 0 18px;
  text-decoration: none;
}

.support-link:focus-visible {
  outline: 3px solid rgba(17, 106, 96, 0.35);
  outline-offset: 3px;
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
