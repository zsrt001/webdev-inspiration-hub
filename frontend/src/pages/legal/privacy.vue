<template>
  <view class="legal-page">
    <NavBar />
    <view class="legal-shell">
      <view class="legal-hero">
        <text class="eyebrow">{{ tr('法务与合规', 'Legal & Compliance') }}</text>
        <text class="title heading-serif">{{ tr('隐私政策', 'Privacy Policy') }}</text>
        <text class="subtitle">{{ tr('最后更新：2026 年 3 月 7 日', 'Last updated: March 7, 2026') }}</text>
      </view>

      <view class="legal-layout">
        <view class="legal-nav">
          <view v-for="section in sections" :key="section.anchor" class="nav-item" @tap="scrollTo(section.anchor)">
            <text class="nav-index">{{ section.index }}</text>
            <text class="nav-label">{{ tr(section.titleZh, section.titleEn) }}</text>
          </view>
        </view>

        <view class="legal-content">
          <view v-for="section in sections" :id="section.anchor" :key="section.anchor" class="section-card">
            <text class="section-title heading-serif">{{ tr(section.titleZh, section.titleEn) }}</text>
            <text v-for="line in activeLines(section)" :key="line" class="section-line">{{ line }}</text>
          </view>
        </view>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import NavBar from '../../components/NavBar.vue';
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
      '本政策适用于 AI 婚纱网页端与小程序端，以及与之直接相关的上传、生成、支付、客服和运营流程。',
      '你使用本服务，即表示你同意我们按本政策说明的方式收集、使用、存储与保护相关信息。',
    ],
    en: [
      'This policy applies to the AI Wedding web app, mini program, and related upload, generation, payment, support, and operations workflows.',
      'By using the service, you consent to the collection, use, storage, and protection practices described in this policy.',
    ],
  },
  {
    anchor: 'collection',
    index: '02',
    titleZh: '我们收集的信息',
    titleEn: 'Information We Collect',
    zh: [
      '账户与设备信息：登录标识、访客标识、基础设备信息与访问日志。',
      '内容数据：你上传的人像照片、参考图、文字描述、生成结果、订单记录。',
      '支付数据：当前支付提供方会处理支付状态、交易标识和账单结果；我们不直接保存完整银行卡信息。',
      '线索与联系信息：当你主动提交姓名、电话、城市、婚期等信息时，我们会用于业务跟进与客户服务。',
    ],
    en: [
      'Account and device data: login identifiers, visitor identifiers, device metadata, and access logs.',
      'Content data: uploaded portraits, reference images, text prompts, generated results, and order records.',
      'Payment data: the active payment provider processes payment status, transaction identifiers, and billing outcomes; we do not directly store full card details.',
      'Lead and contact data: when you submit your name, phone number, city, wedding date, or similar details, we use them for follow-up and customer support.',
    ],
  },
  {
    anchor: 'usage',
    index: '03',
    titleZh: '信息如何被使用',
    titleEn: 'How We Use Information',
    zh: [
      '用于身份识别、积分结算、订单跟踪、结果交付与售后支持。',
      '用于质量控制与安全风控，包括上传前后的人像检测、文本审核、内容过滤与 NSFW 拦截。',
      '用于产品改进与运营分析，例如模板点击、支付转化、失败原因统计与服务诊断。',
    ],
    en: [
      'We use information for identity resolution, credit settlement, order tracking, delivery, and support.',
      'We use it for quality and safety controls, including portrait checks, text moderation, content filtering, and NSFW blocking.',
      'We also use it for product improvement and operations analytics such as template clicks, payment conversion, failure diagnosis, and service quality monitoring.',
    ],
  },
  {
    anchor: 'processors',
    index: '04',
    titleZh: 'AI 与第三方处理方',
    titleEn: 'AI and Third-Party Processors',
    zh: [
      '生成与审核链路可能会使用 ComfyUI、自有模型服务、Jiekou.ai 模型接口、对象存储以及当前支付服务。',
      '第三方仅在完成相应功能所需的范围内处理数据。你不应上传证件、支付码、未成年人敏感内容或其他你无权提供的数据。',
    ],
    en: [
      'Generation and moderation may involve ComfyUI, internal model services, Jiekou.ai model APIs, object storage, and the active payment services.',
      'Third parties process data only to the extent necessary for the relevant function. You must not upload IDs, payment codes, sexualized minors, or other data you are not authorized to provide.',
    ],
  },
  {
    anchor: 'retention',
    index: '05',
    titleZh: '保存期限与删除',
    titleEn: 'Retention and Deletion',
    zh: [
      '原始上传与生成结果仅在业务必要期限内保存，用于交付、复核、退款与风控；超过必要期限后将删除或匿名化。',
      '如你要求删除账户或内容，我们会在合理期限内处理，但法律、审计或争议处理另有要求的除外。',
    ],
    en: [
      'Uploads and generated results are retained only as long as needed for delivery, review, refunds, and risk control, then deleted or anonymized.',
      'If you request deletion of your account or content, we will handle it within a reasonable time unless retention is required for legal, audit, or dispute reasons.',
    ],
  },
  {
    anchor: 'rights',
    index: '06',
    titleZh: '你的权利',
    titleEn: 'Your Rights',
    zh: [
      '你可以请求访问、更正、删除或导出你的个人信息，也可以撤回对营销联系的授权。',
      '你也可以联系我们处理账户、订单、退款、线索删除或隐私投诉。',
    ],
    en: [
      'You may request access, correction, deletion, or export of your personal data, and you may withdraw consent for marketing outreach.',
      'You may also contact us regarding accounts, orders, refunds, lead deletion, or privacy complaints.',
    ],
  },
  {
    anchor: 'contact',
    index: '07',
    titleZh: '联系与支持',
    titleEn: 'Contact',
    zh: [
      '如需隐私支持，请通过你部署环境中配置的客服邮箱、法务邮箱或官方服务渠道联系我们。',
    ],
    en: [
      'For privacy support, contact us through the support email, legal email, or official service channel configured for your deployment.',
    ],
  },
]);

const activeLines = (section: { zh: string[]; en: string[] }) => (i18nStore.locale === 'zh' ? section.zh : section.en);

const scrollTo = (anchor: string) => {
  const query = uni.createSelectorQuery();
  query.select(`#${anchor}`).boundingClientRect();
  query.selectViewport().scrollOffset();
  query.exec((res) => {
    const target = res?.[0];
    const viewport = res?.[1];
    if (!target || !viewport) return;
    uni.pageScrollTo({ scrollTop: target.top + viewport.scrollTop - 92, duration: 220 });
  });
};
</script>

<style lang="scss" scoped>
.legal-page {
  min-height: 100vh;
  background: #faf7f8;
}

.legal-shell {
  max-width: 1320px;
  margin: 0 auto;
  padding: 96px 28px 56px;
}

.legal-hero {
  max-width: 820px;
  padding: 12px 0 28px;
}

.eyebrow {
  display: block;
  color: $uni-color-accent;
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  margin-bottom: 10px;
}

.title {
  display: block;
  color: $uni-color-primary;
  font-size: 52px;
  line-height: 1;
  margin-bottom: 10px;
}

.subtitle {
  color: $uni-text-color-muted;
  font-size: 14px;
}

.legal-layout {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}

.legal-nav {
  position: sticky;
  top: 88px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.nav-item,
.section-card {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(131, 24, 67, 0.08);
  box-shadow: 0 18px 38px rgba(131, 24, 67, 0.06);
}

.nav-item {
  border-radius: 20px;
  padding: 14px 16px;
}

.nav-index {
  display: block;
  margin-bottom: 4px;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.12em;
  color: $uni-color-accent;
}

.nav-label {
  display: block;
  font-size: 14px;
  color: $uni-color-primary;
  line-height: 1.5;
}

.legal-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.section-card {
  border-radius: 24px;
  padding: 28px;
}

.section-title {
  display: block;
  font-size: 30px;
  color: $uni-color-primary;
  margin-bottom: 16px;
}

.section-line {
  display: block;
  color: $uni-text-color;
  line-height: 1.85;
  margin-bottom: 12px;
}

@media (max-width: 980px) {
  .legal-layout {
    grid-template-columns: 1fr;
  }

  .legal-nav {
    position: static;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .legal-shell {
    padding: 88px 18px 40px;
  }

  .title {
    font-size: 38px;
  }

  .section-card {
    padding: 22px 18px;
  }

  .section-title {
    font-size: 24px;
  }

  .legal-nav {
    grid-template-columns: 1fr;
  }
}
</style>
