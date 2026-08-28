<template>
  <view class="legal-page">
    <NavBar />
    <view class="legal-shell" role="main" tabindex="0" :aria-label="tr('退款与客服说明', 'Refunds and Support')">
      <view class="legal-hero">
        <text class="eyebrow">{{ tr('法务与支持', 'Legal & Support') }}</text>
        <text class="title heading-serif" role="heading" aria-level="1">{{ tr('退款与客服说明', 'Refunds & Support') }}</text>
        <text class="subtitle">{{ tr('最后更新：2026 年 7 月 20 日', 'Last updated: July 20, 2026') }}</text>
      </view>

      <view class="legal-content">
        <view v-for="section in sections" :key="section.anchor" class="section-card">
          <text class="section-index">{{ section.index }}</text>
          <text class="section-title heading-serif">{{ tr(section.titleZh, section.titleEn) }}</text>
          <text v-for="line in activeLines(section)" :key="line" class="section-line">{{ line }}</text>
          <a
            v-if="section.anchor === 'contact' && supportAvailable"
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
    ? tr('打开已验证客服入口', 'Open verified support channel')
    : opsStore.publicConfig.support.email,
);

const sections = computed(() => [
  {
    anchor: 'scope',
    index: '01',
    titleZh: '适用范围',
    titleEn: 'Scope',
    zh: [
      '本说明适用于积分包购买、订阅扣款、AI 生成失败、重复支付、账户异常和客服处理。',
      '积分是平台内消费权益，不是现金或储值卡；所有余额、退款和补偿以后台账本与支付提供商记录为准。',
    ],
    en: [
      'This policy covers credit-pack purchases, subscription billing, AI generation failures, duplicate payments, account issues, and support handling.',
      'Credits are in-platform usage rights, not cash or stored value. Balances, refunds, and compensation are based on the backend ledger and payment provider records.',
    ],
  },
  {
    anchor: 'eligible',
    index: '02',
    titleZh: '可申请处理的情况',
    titleEn: 'Eligible Cases',
    zh: [
      '重复扣款、支付成功但积分未到账、订阅扣款异常、任务无法开始、生成或交付失败等情况，可申请退款、补发积分或人工调整。',
      '如果任务已经成功生成并交付，通常不因主观审美、提示词选择、用户上传素材质量或第三方支付通道延迟直接退款。',
    ],
    en: [
      'Duplicate charges, paid credits not delivered, subscription billing issues, tasks that cannot start, and confirmed generation or delivery failures can be reviewed for refund, credit reissue, or manual adjustment.',
      'If a job was generated and delivered successfully, refunds are generally not issued solely for subjective preference, prompt choices, upload quality, or payment-provider delays.',
    ],
  },
  {
    anchor: 'process',
    index: '03',
    titleZh: '处理流程',
    titleEn: 'Process',
    zh: [
      '联系客服时请提供登录邮箱、订单号、支付时间、支付提供商订单号或截图。不要发送银行卡号、CVV、完整证件号或支付密码。',
      '平台会核对支付事件、积分流水、订单状态和生成日志。确认异常后，通过原路退款、补发积分或账本调整处理。',
    ],
    en: [
      'When contacting support, provide sign-in email, order ID, payment time, provider order ID, or screenshots. Do not send card numbers, CVV, full ID numbers, or payment passwords.',
      'The platform checks payment events, credit ledger entries, order status, and generation logs. Confirmed issues are handled by provider refund, credit reissue, or ledger adjustment.',
    ],
  },
  {
    anchor: 'contact',
    index: '04',
    titleZh: '客服入口',
    titleEn: 'Support Contact',
    zh: supportAvailable.value
      ? [
          '如需退款、补发积分或查询订单，请使用下方经运行时确认的受监控客服入口。',
          '为了保护你的账户安全，请不要在客服沟通中发送银行卡号、CVV、完整证件号或支付密码。',
        ]
      : [
          '目前没有经过验证且受监控的客服入口；本页不会显示未验证邮箱或工单链接。',
          '请先在账户中心保存订单号、支付提供商收据和错误时间，并保留支付提供商的交易记录；客服渠道恢复后再提交处理。',
        ],
    en: supportAvailable.value
      ? [
          'For refunds, credit reissues, or order checks, use the monitored support channel confirmed by the runtime below.',
          'For account safety, do not send card numbers, CVV, full ID numbers, or payment passwords in support conversations.',
        ]
      : [
          'No verified, monitored support channel is currently available. This page does not publish unverified email addresses or ticket links.',
          'Keep the order ID, provider receipt, failure timestamp, and provider transaction record in your account; submit them after a verified channel is restored.',
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
