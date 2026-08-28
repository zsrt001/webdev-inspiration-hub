<template>
  <view class="app-container product-landing" style="padding-top: 64px;">
    <a class="skip-link" href="#gallery">{{ tr('跳到主要内容', 'Skip to main content') }}</a>
    <NavBar ref="navBarRef" @show-payment="openPaymentModal" />

    <view v-if="homeBanner.enabled" class="hero-section">
      <img
        :src="heroImageUrl"
        class="hero-media"
        :alt="tr('AI 婚纱照风格预览', 'AI wedding portrait style preview')"
      />
      <view class="hero-overlay"></view>
      <view class="hero-content">
        <view class="hero-copy">
          <text class="section-label">{{ tr('AI 婚纱照生成', 'AI Wedding Studio') }}</text>
          <text class="hero-title heading-serif" role="heading" aria-level="1">
            {{ tr('上传照片，生成你的高定婚纱大片', 'Wedding portraits, made from your photos') }}
          </text>
          <text class="hero-subtitle">
            {{ tr('无需预约影棚。上传人像、选择风格或描述想法，快速生成适合请柬、头像、纪念日和社交分享的婚纱影像。', 'Upload a portrait, choose a look, and generate polished AI wedding images for invitations, profiles, anniversaries, and keepsakes.') }}
          </text>
          <view class="hero-actions">
            <view v-if="!opsStore.loaded" class="availability-notice loading-notice" aria-busy="true">{{ tr('正在确认创作能力…', 'Checking studio availability…') }}</view>
            <a v-else-if="creationAvailable" class="btn primary" href="/pages/create/index" @click.prevent="goToCustom">{{ tr('开始免费预览', 'Start a Free Preview') }}</a>
            <view v-else class="availability-notice">{{ tr('创作功能暂未开放', 'Studio temporarily unavailable') }}</view>
            <a class="btn secondary" href="#gallery" @click.prevent="scrollToGallery">{{ tr('浏览婚纱风格', 'Explore Styles') }}</a>
          </view>
          <view class="hero-proof-grid">
            <view v-for="item in heroProofs" :key="item.title" class="proof-item">
              <text class="proof-title">{{ item.title }}</text>
              <text class="proof-desc">{{ item.desc }}</text>
            </view>
          </view>
        </view>

        <a class="hero-preview" href="#gallery" @click.prevent="scrollToGallery">
          <view class="preview-frame">
            <img
              :src="heroPreviewUrl"
              class="preview-image"
              :alt="tr('精选婚纱风格预览', 'Curated wedding look preview')"
            />
          </view>
          <view class="preview-copy">
            <text class="preview-title">{{ tr('精选婚纱风格', 'Curated Wedding Looks') }}</text>
            <text class="preview-text">{{ tr('中式秀禾、韩系极简、古堡、海边、金婚纪念等风格都可以直接开始。', 'Chinese Xiuhe, Korean minimal, castle romance, beach sunset, and anniversary remakes are ready to try.') }}</text>
          </view>
        </a>
      </view>
    </view>

    <view class="landing-body" role="main">
      <section class="gallery-section section-block" id="gallery">
        <view class="section-heading split">
          <view>
            <text class="section-label">{{ tr('结果示例', 'Result Examples') }}</text>
            <text class="section-title heading-serif">{{ tr('先看真实风格方向，再开始生成', 'See the result direction before you create') }}</text>
          </view>
          <text class="section-note">{{ tr('支持单人婚纱照和双人同机合拍。进入详情后可继续上传照片。', 'Supports solo portraits and local couple creation. Open a style to upload photos.') }}</text>
        </view>

        <view class="category-filter">
          <view
            class="cat-chip"
            :class="{ active: selectedCategory === 'all' }"
            role="button"
            tabindex="0"
            :aria-pressed="selectedCategory === 'all'"
            @tap="selectCategory('all')"
            @keydown.enter.prevent="selectCategory('all')"
            @keydown.space.prevent="selectCategory('all')"
          >
            {{ tr('全部', 'All') }}
          </view>
          <view
            class="cat-chip"
            :class="{ active: selectedCategory === 'single' }"
            role="button"
            tabindex="0"
            :aria-pressed="selectedCategory === 'single'"
            @tap="selectCategory('single')"
            @keydown.enter.prevent="selectCategory('single')"
            @keydown.space.prevent="selectCategory('single')"
          >
            {{ tr('人像婚纱', 'Portrait') }}
          </view>
          <view
            class="cat-chip"
            :class="{ active: selectedCategory === 'vintage' }"
            role="button"
            tabindex="0"
            :aria-pressed="selectedCategory === 'vintage'"
            @tap="selectCategory('vintage')"
            @keydown.enter.prevent="selectCategory('vintage')"
            @keydown.space.prevent="selectCategory('vintage')"
          >
            {{ tr('金婚纪念', 'Legacy') }}
          </view>
          <view
            class="cat-chip"
            :class="{ active: selectedCategory === 'custom' }"
            role="button"
            tabindex="0"
            :aria-pressed="selectedCategory === 'custom'"
            @tap="selectCategory('custom')"
            @keydown.enter.prevent="selectCategory('custom')"
            @keydown.space.prevent="selectCategory('custom')"
          >
            {{ tr('自由定制', 'Bespoke') }}
          </view>
        </view>

        <view v-if="templateStore.loading && filteredTemplates.length === 0" class="style-grid" aria-busy="true" :aria-label="tr('正在加载结果示例', 'Loading result examples')">
          <view v-for="index in 4" :key="index" class="style-card style-skeleton">
            <view class="style-image-frame skeleton-block"></view>
            <view class="style-copy">
              <view class="skeleton-line wide"></view>
              <view class="skeleton-line"></view>
            </view>
          </view>
        </view>
        <view v-else-if="filteredTemplates.length > 0" class="style-grid">
          <a
            v-for="template in filteredTemplates"
            :key="template.id"
            class="style-card"
            :href="detailHref(template)"
            @click.prevent="goToDetail(template)"
          >
            <view class="style-image-frame">
              <img
                :src="resolveTemplateCardImage(template)"
                class="style-image"
                :alt="displayTemplateTitle(template)"
                loading="lazy"
                @error="onTemplateImageError(template)"
              />
            </view>
            <view class="style-copy">
              <text class="style-title heading-serif">{{ displayTemplateTitle(template) }}</text>
              <text v-if="displayTemplateMarketingSubtitle(template)" class="style-desc">{{ displayTemplateMarketingSubtitle(template) }}</text>
              <text class="style-action">{{ tr('查看详情', 'View Details') }}</text>
            </view>
          </a>
        </view>
        <view v-else class="empty-gallery">
          <text class="empty-title">{{ tr('暂时无法读取风格作品', 'Result examples are temporarily unavailable') }}</text>
          <text class="empty-desc">{{ tr('请稍后刷新；不会因此误报创作或付款能力已关闭。', 'Refresh shortly. This does not change studio or billing availability.') }}</text>
        </view>
      </section>

      <section class="benefits-section section-block" id="steps">
        <view class="section-heading">
          <text class="section-label">{{ tr('三步完成', 'Three Simple Steps') }}</text>
          <text class="section-title heading-serif">{{ tr('从人物照片到私密成片', 'From portraits to private delivery') }}</text>
        </view>
        <view class="benefit-grid">
          <view v-for="item in benefitItems" :key="item.title" class="benefit-card">
            <text class="card-mark">{{ item.mark }}</text>
            <text class="card-title">{{ item.title }}</text>
            <text class="card-desc">{{ item.desc }}</text>
          </view>
        </view>
      </section>

      <section class="testimonials-section section-block" id="testimonials">
        <view class="section-heading">
          <text class="section-label">{{ tr('适用场景', 'When to use it') }}</text>
          <text class="section-title heading-serif">{{ tr('适合这些真实需求', 'Useful before and after the wedding') }}</text>
        </view>
        <view class="testimonial-list">
          <view v-for="item in testimonials" :key="item.name" class="testimonial-card">
            <text class="quote-text">{{ item.quote }}</text>
            <text class="quote-name">{{ item.name }}</text>
          </view>
        </view>
      </section>

      <section class="pricing-section section-block" id="pricing">
        <view class="section-heading">
          <text class="section-label">{{ tr('定价', 'Pricing') }}</text>
          <text class="section-title heading-serif">{{ tr('先看清积分和续费规则，再决定购买', 'See credits and renewal terms before you buy') }}</text>
          <text class="pricing-intro">{{ generationCostSummary }}</text>
        </view>
        <view v-if="pricingLoading" class="pricing-inline-grid" aria-busy="true" :aria-label="tr('正在加载当前价格', 'Loading current pricing')">
          <view v-for="index in 2" :key="index" class="pricing-inline-card">
            <view class="skeleton-line wide"></view>
            <view class="skeleton-line"></view>
            <view class="skeleton-line"></view>
          </view>
        </view>
        <view v-else class="pricing-inline-grid">
          <view v-for="card in pricingCards" :key="card.mode" class="pricing-inline-card">
            <text class="pricing-kind">{{ card.kicker }}</text>
            <text class="pricing-name heading-serif">{{ card.title }}</text>
            <text class="pricing-price">{{ card.price }}</text>
            <text class="pricing-copy">{{ card.copy }}</text>
          </view>
        </view>
        <view class="section-action-row">
          <view
            v-if="billingAvailable"
            class="btn primary"
            role="button"
            tabindex="0"
            @tap="openPaymentModal"
            @keydown.enter.prevent="openPaymentModal"
            @keydown.space.prevent="openPaymentModal"
          >{{ tr('比较全部套餐', 'Compare All Plans') }}</view>
          <text v-else-if="opsStore.loaded" class="availability-notice">{{ tr('当前付款入口暂未开放', 'Billing is temporarily unavailable') }}</text>
        </view>
      </section>

      <section class="trust-section section-block" id="privacy">
        <view class="section-heading">
          <text class="section-label">{{ tr('隐私与安全', 'Privacy & Security') }}</text>
          <text class="section-title heading-serif">{{ tr('照片、账户和付款各自有清晰边界', 'Clear boundaries for photos, accounts, and payments') }}</text>
        </view>
        <view class="trust-grid">
          <view v-for="item in trustItems" :key="item.title" class="trust-card">
            <text class="card-title">{{ item.title }}</text>
            <text class="card-desc">{{ item.desc }}</text>
          </view>
        </view>
      </section>

      <section class="faq-section section-block" id="faq">
        <view class="section-heading">
          <text class="section-label">{{ tr('常见问题', 'FAQ') }}</text>
          <text class="section-title heading-serif">{{ tr('开始前需要知道的事', 'What to know before you start') }}</text>
        </view>
        <view class="faq-list">
          <details v-for="item in faqItems" :key="item.question" class="faq-item">
            <summary>{{ item.question }}</summary>
            <text class="faq-answer">{{ item.answer }}</text>
          </details>
        </view>
      </section>

      <section class="cta-section section-block" id="cta">
        <text class="section-label">{{ tr('开始体验', 'Start Now') }}</text>
        <text class="cta-title heading-serif">{{ tr('从真实照片开始，逐步完成你的婚纱创作', 'Start with your photos and a guided creation flow') }}</text>
        <text class="cta-desc">{{ tr('系统会在提交前显示当前部署可用的能力和所需额度；未启用的付费选项不会提前展示。', 'The app shows available capabilities and required credits before submission. Paid options remain hidden until billing is available on this deployment.') }}</text>
        <view class="hero-actions centered">
          <view v-if="!opsStore.loaded" class="availability-notice loading-notice" aria-busy="true">{{ tr('正在确认创作能力…', 'Checking studio availability…') }}</view>
          <a v-else-if="creationAvailable" class="btn primary" href="/pages/create/index" @click.prevent="goToCustom">{{ tr('立即开始', 'Start Now') }}</a>
          <view v-else class="availability-notice">{{ tr('当前部署仅开放浏览', 'This deployment is browse-only') }}</view>
          <view
            v-if="billingAvailable"
            class="btn secondary"
            role="button"
            tabindex="0"
            @tap="openPaymentModal"
            @keydown.enter.prevent="openPaymentModal"
            @keydown.space.prevent="openPaymentModal"
          >{{ tr('查看套餐', 'View Plans') }}</view>
        </view>
      </section>
    </view>

    <view class="site-footer" id="footer">
      <view class="footer-main">
        <text class="footer-brand heading-serif">VowPic</text>
        <text class="footer-copy">{{ tr('VowPic 提供 AI 婚纱照生成、本地双人创作、订单跟踪和私密交付。', 'VowPic offers AI wedding portraits, local couple creation, order tracking, and private delivery.') }}</text>
      </view>
      <view class="footer-links">
        <a href="#gallery" @click.prevent="scrollToGallery">{{ tr('功能', 'Features') }}</a>
        <view
          v-if="billingAvailable"
          role="button"
          tabindex="0"
          @tap="openPaymentModal"
          @keydown.enter.prevent="openPaymentModal"
          @keydown.space.prevent="openPaymentModal"
        >{{ tr('套餐', 'Plans') }}</view>
        <a href="/pages/legal/privacy" @click.prevent="goToPrivacy">{{ tr('隐私政策', 'Privacy') }}</a>
        <a href="/pages/legal/terms" @click.prevent="goToTerms">{{ tr('服务条款', 'Terms') }}</a>
        <a href="/pages/legal/refund" @click.prevent="goToRefund">{{ tr('退款与客服', 'Refunds') }}</a>
      </view>
      <LegalFooter />
    </view>

    <PaymentModal
      v-if="billingAvailable"
      :visible="showPaymentModal"
      @close="showPaymentModal = false"
      @purchase-complete="onPurchaseComplete"
    />
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import NavBar from '../../components/NavBar.vue';
import PaymentModal from '../../components/PaymentModal.vue';
import LegalFooter from '../../components/LegalFooter.vue';
import { useI18nStore } from '../../stores/i18n';
import { useOpsStore } from '../../stores/ops';
import {
  useTemplateStore,
  type Template,
  getLocalizedTemplateMarketingSubtitle,
  getLocalizedTemplateTitle,
} from '../../stores/template';
import { get, post, resolvePublicUrl } from '../../utils/api';
import { filterCreditPackages, readBillingIntent } from '../../utils/billingDisplay';

interface PublicCreditPackage {
  id: string;
  product_kind?: string;
  credits: number;
  price_cents?: number;
  price?: number;
  currency?: string;
  display_price?: string | null;
}

interface PublicSubscriptionPlan {
  code: string;
  credits: number;
  pre_tax_minor_units: number;
  currency: string;
  display_price: string;
}

interface PublicLegalPolicies {
  pricing?: {
    single?: number;
    couple_local?: number;
    golden_anniversary?: number;
    summary?: string;
  };
  retention?: {
    source_images_days?: number;
    free_generated_days?: number;
    paid_generated_days?: number;
    subscription_generated_days?: number;
    studio_generated_days?: number;
    summary?: string;
  };
}

const templateStore = useTemplateStore();
const opsStore = useOpsStore();
const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const navBarRef = ref<InstanceType<typeof NavBar> | null>(null);
const templates = ref<Template[]>([]);
const selectedCategory = ref<'all' | 'single' | 'vintage' | 'custom'>('all');
const showPaymentModal = ref(false);
const templateImageAttempts = ref<Record<string, number>>({});
const creditPackages = ref<PublicCreditPackage[]>([]);
const subscriptionPlans = ref<PublicSubscriptionPlan[]>([]);
const legalPolicies = ref<PublicLegalPolicies | null>(null);
const pricingLoading = ref(true);

const homeBanner = computed(() => opsStore.publicConfig.placements.home_banner);
const creationAvailable = computed(() => opsStore.creationAvailable);
const billingAvailable = computed(() => opsStore.billingAvailable);
const heroBackgroundFallback = '/style-previews/royal_castle.jpg';
const staleHeroBackgrounds = new Set([
  '/hero_wedding_luxury_bg.jpg',
  '/static/hero_wedding_luxury_bg.jpg',
  '/legacy_promo_banner.jpg',
  '/static/legacy_promo_banner.jpg',
]);

const styleImageFallbacks: Record<string, string[]> = {
  chn_xiuhe: ['/style-previews/couple_chn_xiuhe.jpg', '/style-previews/chn_xiuhe.jpg'],
  korean_minimal: ['/style-previews/couple_korean_minimal.jpg', '/style-previews/solo_korean_minimal.jpg'],
  royal_castle: ['/style-previews/royal_castle.jpg', '/style-previews/solo_royal_castle.jpg'],
  old_money: ['/style-previews/couple_old_money.jpg', '/style-previews/solo_old_money.jpg'],
  gothic_romance: ['/style-previews/couple_gothic_romance.jpg', '/style-previews/solo_gothic_romance.jpg'],
  beach_sunset: ['/style-previews/couple_beach_sunset.jpg', '/style-previews/solo_beach_sunset.jpg'],
  hk_retro: ['/style-previews/couple_hk_retro.jpg', '/style-previews/hk_retro.jpg'],
  twilight_forest: ['/style-previews/couple_twilight_forest.jpg', '/style-previews/twilight_forest.jpg'],
  japanese_shiromuku: ['/style-previews/couple_japanese_shiromuku.jpg', '/style-previews/japanese_shiromuku.jpg'],
  cyberpunk_city: ['/style-previews/couple_cyberpunk_city.jpg', '/style-previews/cyberpunk_city.jpg'],
  school_days: ['/style-previews/couple_school_days.jpg', '/style-previews/school_days.jpg'],
  classic_bw: ['/style-previews/couple_classic_bw.jpg', '/style-previews/classic_bw.jpg'],
  golden_vintage_studio_8090: ['/style-previews/golden_vintage_studio_8090.jpg'],
  golden_chinese_courtyard: ['/style-previews/golden_chinese_courtyard.jpg'],
  golden_modern_remake: ['/style-previews/golden_modern_remake.jpg'],
  custom_mode: ['/style-previews/custom_mode.jpg'],
};

const heroImageUrl = computed(() => {
  const raw = String(homeBanner.value.image_url || '').trim();
  const normalized = raw.startsWith('/static/') ? raw.replace(/^\/static/, '') : raw;
  if (!raw || staleHeroBackgrounds.has(raw) || staleHeroBackgrounds.has(normalized)) {
    return resolvePublicUrl(heroBackgroundFallback);
  }
  return resolvePublicUrl(raw);
});

const heroPreviewUrl = computed(() => {
  const candidate = templates.value.find((item) => item.category === 'couple')?.image_url || '/style-previews/royal_castle.jpg';
  return resolvePublicUrl(candidate);
});

const heroProofs = computed(() => [
  {
    title: tr('先预览', 'Preview first'),
    desc: tr('先看真实照片效果，再决定是否继续。', 'See the look with your own photo before deciding what to do next.'),
  },
  {
    title: tr('支持双人', 'Couple-ready'),
    desc: tr('支持单人和双人同机上传创作。', 'Create solo or local couple portraits.'),
  },
  {
    title: tr('能力透明', 'Clear availability'),
    desc: tr('只展示当前部署真实可用的能力。', 'Only capabilities available on this deployment are shown.'),
  },
]);

const benefitItems = computed(() => [
  {
    mark: '01',
    title: tr('上传清晰人物照片', 'Upload clear portraits'),
    desc: tr('选择单人、双人或金婚模式，只上传本人拥有或已获授权的人物照片。', 'Choose solo, couple, or anniversary mode and upload only portraits you own or are authorized to use.'),
  },
  {
    mark: '02',
    title: tr('选择风格并写下方向', 'Choose a look and direction'),
    desc: tr('从现有风格开始，再用文字描述服装、场景、氛围、镜头和布光。', 'Start from a curated look, then describe outfit, scene, mood, lens, and lighting in text.'),
  },
  {
    mark: '03',
    title: tr('确认积分并私密下载', 'Confirm credits and download privately'),
    desc: tr('提交前确认实际积分；完成后在自己的账户中预览订单并下载私密成片。', 'Confirm the exact credit cost before submission, then review and download private results from your account.'),
  },
]);

const testimonials = computed(() => [
  {
    quote: tr('婚礼前想快速试不同风格，可以先用 AI 看一版效果，再和伴侣、化妆师或摄影师沟通方向。', 'Explore several bridal directions before the wedding, then discuss the look with your partner, stylist, or photographer.'),
    name: tr('婚礼灵感与试片', 'Style proofing before the shoot'),
  },
  {
    quote: tr('给父母准备结婚纪念日礼物时，可以用一组更正式的纪念婚纱照，补上过去没有好好拍过的遗憾。', 'Create a more formal anniversary portrait set for parents and family milestones.'),
    name: tr('金婚 / 结婚周年纪念', 'Anniversary and legacy gifts'),
  },
]);

const generationCostSummary = computed(() => {
  const pricing = legalPolicies.value?.pricing;
  if (pricing?.single && pricing?.couple_local && pricing?.golden_anniversary) {
    return tr(
      `单人生成 ${pricing.single} 积分；双人同机和金婚纪念各 ${pricing.couple_local} 积分。提交前再次确认实际扣除。`,
      `Solo generation costs ${pricing.single} credits; local couple and anniversary generation cost ${pricing.couple_local} credits. The exact charge is confirmed before submission.`,
    );
  }
  return tr(
    '实际积分会在生成提交前显示并确认。',
    'The exact credit charge is shown and confirmed before generation.',
  );
});

function publicPrice(
  displayPrice: string | null | undefined,
  minorUnits: number | undefined,
  currency = 'USD',
): string {
  if (displayPrice) return displayPrice;
  if (Number.isFinite(minorUnits) && Number(minorUnits) > 0) {
    const prefix = String(currency).toUpperCase() === 'USD' ? '$' : `${currency} `;
    return `${prefix}${(Number(minorUnits) / 100).toFixed(2)}`;
  }
  return tr('查看当前价格', 'See current price');
}

const pricingCards = computed(() => {
  const cards: Array<{
    mode: string;
    kicker: string;
    title: string;
    price: string;
    copy: string;
  }> = [];
  const pack = [...creditPackages.value].sort((a, b) => Number(a.price_cents || 0) - Number(b.price_cents || 0))[0];
  if (opsStore.publicConfig.capabilities.credit_pack_checkout) {
    cards.push({
      mode: 'credits',
      kicker: tr('一次性购买', 'One-time purchase'),
      title: tr('按需补充积分', 'Pay as you go'),
      price: pack
        ? tr(
          `${publicPrice(pack.display_price, pack.price_cents, pack.currency)} 起 / ${pack.credits} 积分`,
          `From ${publicPrice(pack.display_price, pack.price_cents, pack.currency)} / ${pack.credits} credits`,
        )
        : tr('在套餐中查看当前价格', 'See current price in plans'),
      copy: tr('一次性到账，不会自动续费。适合试风格、继续生成或按项目使用。', 'Credits are added once with no auto-renewal. Best for occasional or project-based use.'),
    });
  }
  const plan = [...subscriptionPlans.value].sort((a, b) => a.pre_tax_minor_units - b.pre_tax_minor_units)[0];
  if (opsStore.publicConfig.capabilities.subscription_billing) {
    cards.push({
      mode: 'subscription',
      kicker: tr('月度订阅', 'Monthly subscription'),
      title: tr('持续创作套餐', 'Ongoing creation'),
      price: plan
        ? tr(
          `${publicPrice(plan.display_price, plan.pre_tax_minor_units, plan.currency)} 起 / 月，${plan.credits} 积分`,
          `From ${publicPrice(plan.display_price, plan.pre_tax_minor_units, plan.currency)} / month, ${plan.credits} credits`,
        )
        : tr('在套餐中查看当前价格', 'See current price in plans'),
      copy: tr('每月自动加入积分并按月续费，取消后下一周期不再扣款。', 'Credits are added monthly and the plan renews until canceled.'),
    });
  }
  if (cards.length === 0) {
    cards.push({
      mode: 'unavailable',
      kicker: tr('当前状态', 'Current status'),
      title: tr('付款入口暂未开放', 'Billing is temporarily unavailable'),
      price: tr('不会创建订单', 'No checkout will be created'),
      copy: tr('创作和浏览状态以页面当前提示为准。', 'Use the current page status for studio and gallery availability.'),
    });
  }
  return cards;
});

const trustItems = computed(() => {
  const retention = legalPolicies.value?.retention;
  const sourceDays = retention?.source_images_days;
  const paidDays = retention?.paid_generated_days;
  return [
    {
      title: tr('账户级私密访问', 'Account-bound private access'),
      desc: tr('上传、订单和下载绑定到已验证 Google 账号；私密文件不作为公开作品墙展示。', 'Uploads, orders, and downloads stay bound to the verified Google account and are not published as a public gallery.'),
    },
    {
      title: tr('保留规则如实公开', 'Retention disclosed clearly'),
      desc: sourceDays && paidDays
        ? tr(
          `原图计划保留 ${sourceDays} 天，付费作品计划保留 ${paidDays} 天；自动删除目前仍暂停，等待可审计清理流程完成验证。`,
          `Source uploads are scheduled for ${sourceDays} days and paid results for ${paidDays} days. Automated deletion remains paused pending an audited cleanup flow.`,
        )
        : tr('具体保留期和当前删除状态以隐私政策为准。', 'Exact retention periods and deletion status are published in the Privacy Policy.'),
    },
    {
      title: tr('支付信息由 Creem 处理', 'Checkout handled by Creem'),
      desc: tr('VowPic 不保存银行卡号、CVV 或原始支付凭据；适用税费会在付款前显示。', 'VowPic does not store card numbers, CVV, or raw payment credentials. Applicable taxes are shown before payment.'),
    },
  ];
});

const faqItems = computed(() => [
  {
    question: tr('一次生成需要多少积分？', 'How many credits does one generation use?'),
    answer: generationCostSummary.value,
  },
  {
    question: tr('可以上传服装或场景参考图吗？', 'Can I upload outfit or scene reference images?'),
    answer: tr('当前私有上传合同只接收人物照片。服装、场景与氛围请使用文字描述，页面不会承诺尚未开放的参考图上传。', 'The current private upload contract accepts portraits only. Describe outfit, scene, and mood in text; the site does not promise reference-image uploads that are not available.'),
  },
  {
    question: tr('积分包和订阅有什么区别？', 'What is the difference between credit packs and subscriptions?'),
    answer: tr('积分包是一次性购买且不续费；订阅每月发放积分并自动续费，直到你取消。付款前会显示最终金额。', 'Credit packs are one-time purchases with no renewal. Subscriptions add credits monthly and renew until canceled. The final amount is shown before payment.'),
  },
  {
    question: tr('照片和生成结果是否公开？', 'Are uploads or generated results public?'),
    answer: tr('不会自动公开。上传、订单、预览和私密下载绑定到你的已验证账户；保留期和自动删除状态请查看隐私政策。', 'They are not published automatically. Uploads, orders, previews, and private downloads stay bound to your verified account; see the Privacy Policy for retention and deletion status.'),
  },
]);

const displayTemplateTitle = (template: Template): string => getLocalizedTemplateTitle(template, i18nStore.locale);

const displayTemplateMarketingSubtitle = (template: Template): string =>
  getLocalizedTemplateMarketingSubtitle(template, i18nStore.locale);

const detailHref = (template: Template): string => `/pages/detail/detail?id=${encodeURIComponent(template.id)}`;

const goToPrivacy = () => {
  uni.navigateTo({ url: '/pages/legal/privacy' });
};

const goToTerms = () => {
  uni.navigateTo({ url: '/pages/legal/terms' });
};

const goToRefund = () => {
  uni.navigateTo({ url: '/pages/legal/refund' });
};

const goToCustom = () => {
  if (!creationAvailable.value) return;
  uni.navigateTo({ url: '/pages/create/index' });
};

const goToDetail = (template: Template) => {
  post(
    '/analytics/click',
    {
      event_type: 'template_click',
      source_page: 'index',
      template_id: template.id,
    },
    { showLoading: false, showError: false } as any
  ).catch(() => {});
  templateStore.selectTemplate(template);
  uni.navigateTo({ url: `/pages/detail/detail?id=${template.id}` });
};

const vintageTemplates = computed(() => templates.value.filter((item) => item.category === 'vintage'));

const categoryPriority = (category: string): number => {
  const rank: Record<string, number> = {
    single: 40,
    solo: 40,
    vintage: 30,
    couple: 20,
    custom: 10,
  };
  return rank[category] || 0;
};

const normalizeTemplateTitle = (template: Template): string => {
  const title = (template.marketing_title || template.title || '').toLowerCase();
  return title
    .replace(/\((solo|couple)\)/g, '')
    .replace(/\bsolo\b|\bcouple\b/g, '')
    .replace(/\s+/g, ' ')
    .trim();
};

const styleFamilyKey = (template: Template): string => {
  const explicitFamily = (template.style_family || '').trim().toLowerCase();
  if (explicitFamily) return explicitFamily;

  const normalizedImageName = (template.image_url || '')
    .split('/')
    .pop()
    ?.toLowerCase()
    .replace(/\.(jpg|jpeg|png|webp)$/i, '')
    .replace(/^(solo_|couple_)/, '')
    .trim();
  if (normalizedImageName) return normalizedImageName;

  return normalizeTemplateTitle(template) || template.id;
};

const resolveTemplateCardImage = (template: Template): string => {
  const familyKey = styleFamilyKey(template);
  const candidates = [
    template.image_url,
    ...(styleImageFallbacks[familyKey] || []),
    '/style-previews/royal_castle.jpg',
  ]
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .filter((item, index, list) => list.indexOf(item) === index);

  const attempt = templateImageAttempts.value[template.id] || 0;
  const target = candidates[Math.min(attempt, candidates.length - 1)] || '/style-previews/royal_castle.jpg';
  return resolvePublicUrl(target);
};

const onTemplateImageError = (template: Template) => {
  templateImageAttempts.value = {
    ...templateImageAttempts.value,
    [template.id]: (templateImageAttempts.value[template.id] || 0) + 1,
  };
};

const dedupeTemplateCards = (source: Template[]): Template[] => {
  const grouped = new Map<string, Template[]>();
  for (const template of source) {
    const key = styleFamilyKey(template);
    const bucket = grouped.get(key) || [];
    bucket.push(template);
    grouped.set(key, bucket);
  }

  return Array.from(grouped.values()).map((group, index) => {
    const sorted = [...group].sort((a, b) => categoryPriority(b.category) - categoryPriority(a.category));
    const singleVariant = sorted.find((item) => item.category === 'single' || item.category === 'solo');
    const coupleVariant = sorted.find((item) => item.category === 'couple');

    if (singleVariant && coupleVariant) {
      return index % 2 === 0 ? singleVariant : coupleVariant;
    }

    return singleVariant || coupleVariant || sorted[0];
  });
};

const filteredTemplates = computed(() => {
  if (selectedCategory.value !== 'all') {
    if (selectedCategory.value === 'single') {
      return templates.value.filter(
        (item) => item.category === 'single' || item.category === 'solo' || item.category === 'couple'
      );
    }
    return templates.value.filter((item) => item.category === selectedCategory.value);
  }
  return templates.value;
});

const selectCategory = (category: 'all' | 'single' | 'vintage' | 'custom') => {
  selectedCategory.value = category;
};

const scrollToGallery = () => {
  uni.pageScrollTo({
    selector: '#gallery',
    duration: 500,
  });
};

const openPaymentModal = () => {
  if (!billingAvailable.value) return;
  showPaymentModal.value = true;
};

function onPurchaseComplete() {
  showPaymentModal.value = false;
  navBarRef.value?.refreshBalance();
}

async function fetchCommercialFacts() {
  pricingLoading.value = true;
  const tasks: Promise<unknown>[] = [
    get<PublicLegalPolicies>('/legal/policies', { showLoading: false, showError: false } as any)
      .then((result) => {
        legalPolicies.value = result;
      })
      .catch(() => {
        legalPolicies.value = null;
      }),
  ];
  if (opsStore.publicConfig.capabilities.credit_pack_checkout) {
    tasks.push(
      get<{ packages: PublicCreditPackage[] }>(
        `/credits/packages?locale=${encodeURIComponent(i18nStore.locale)}`,
        { showLoading: false, showError: false } as any,
      )
        .then((result) => {
          creditPackages.value = filterCreditPackages(
            Array.isArray(result.packages) ? result.packages : [],
          );
        })
        .catch(() => {
          creditPackages.value = [];
        }),
    );
  }
  if (opsStore.publicConfig.capabilities.subscription_billing) {
    tasks.push(
      get<PublicSubscriptionPlan[]>(
        '/subscriptions/plans',
        { showLoading: false, showError: false } as any,
      )
        .then((result) => {
          subscriptionPlans.value = Array.isArray(result) ? result : [];
        })
        .catch(() => {
          subscriptionPlans.value = [];
        }),
    );
  }
  await Promise.allSettled(tasks);
  pricingLoading.value = false;
}

onMounted(async () => {
  await Promise.all([
    opsStore.fetchPublicConfig(),
    templateStore.fetchTemplates(),
  ]);
  templates.value = templateStore.templates;
  if (!vintageTemplates.value.length && selectedCategory.value === 'vintage') {
    selectedCategory.value = 'all';
  }
  await fetchCommercialFacts();
  if (
    billingAvailable.value
    && typeof window !== 'undefined'
    && readBillingIntent(window.location.href)
  ) {
    showPaymentModal.value = true;
  }
});
</script>

<style lang="scss" scoped>
.product-landing {
  min-height: 100vh;
  background: #f5f6f4;
  color: #17191f;
  overflow-x: hidden;
}

.skip-link {
  position: fixed;
  top: 8px;
  left: 8px;
  z-index: 1200;
  transform: translateY(-160%);
  padding: 10px 14px;
  border-radius: 6px;
  background: #17191f;
  color: #ffffff;
  font-weight: 800;
  text-decoration: none;
}

.skip-link:focus {
  transform: translateY(0);
}

.hero-section {
  position: relative;
  min-height: clamp(620px, calc(100dvh - 82px), 720px);
  display: flex;
  align-items: stretch;
  overflow: hidden;
  background:
    linear-gradient(90deg, #f7f7f3 0%, #f7f7f3 54%, #edf1ee 54%, #eef1ee 100%);
}

.hero-media {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 48%;
  height: 100%;
  z-index: 1;
  filter: saturate(0.98) contrast(1.06) brightness(0.98);
  object-fit: cover;
  object-position: center center;
}

.hero-overlay {
  position: absolute;
  inset: 0;
  z-index: 2;
  background:
    linear-gradient(90deg, #f7f7f3 0%, #f7f7f3 50%, rgba(247, 247, 243, 0.92) 58%, rgba(247, 247, 243, 0.28) 82%, rgba(247, 247, 243, 0.08) 100%),
    linear-gradient(0deg, #f5f6f4 0%, rgba(245, 246, 244, 0.5) 18%, rgba(245, 246, 244, 0.02) 46%);
}

.hero-content {
  position: relative;
  z-index: 3;
  width: min(1280px, calc(100% - 48px));
  margin: 0 auto;
  padding: 58px 0 72px;
  display: grid;
  grid-template-columns: minmax(0, 0.96fr) minmax(340px, 390px);
  gap: clamp(48px, 6vw, 96px);
  align-items: center;
}

.hero-copy {
  max-width: 660px;
}

.section-label {
  display: block;
  margin-bottom: 14px;
  color: #9a5b16;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
}

.hero-title {
  display: block;
  max-width: 720px;
  font-size: clamp(48px, 5.4vw, 74px);
  line-height: 1.02;
  color: #17191f;
  text-wrap: balance;
  overflow-wrap: break-word;
}

.hero-subtitle {
  display: block;
  max-width: 570px;
  margin-top: 22px;
  color: #394151;
  font-size: 17px;
  line-height: 1.65;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 34px;
}

.hero-actions.centered {
  justify-content: center;
}

.availability-notice {
  display: inline-flex;
  align-items: center;
  min-height: 46px;
  padding: 0 18px;
  border: 1px solid rgba(17, 106, 96, 0.24);
  border-radius: 8px;
  background: #eef7f5;
  color: #0b5e55;
  font-size: 14px;
  font-weight: 800;
}

.btn {
  min-height: 52px;
  padding: 0 28px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 900;
  cursor: pointer;
  transition: transform 0.2s ease, background 0.2s ease, border-color 0.2s ease;
  text-decoration: none;
}

.btn:active {
  transform: translateY(1px);
}

.btn.primary {
  background: #17191f;
  color: #ffffff;
  border: 1px solid #17191f;
  box-shadow: 0 14px 28px rgba(23, 25, 31, 0.18);
}

.btn.secondary {
  background: rgba(255, 255, 255, 0.78);
  color: #17191f;
  border: 1px solid #b8bec8;
}

.hero-proof-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  max-width: 680px;
  margin-top: 24px;
}

.proof-item {
  min-height: 88px;
  padding: 15px;
  border: 1px solid rgba(32, 43, 62, 0.1);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.84);
  box-shadow: 0 12px 34px rgba(23, 25, 31, 0.05);
  box-sizing: border-box;
}

.proof-title {
  display: block;
  margin-bottom: 6px;
  color: #17191f;
  font-size: 13px;
  font-weight: 900;
  line-height: 1.25;
}

.proof-desc {
  display: block;
  color: #4c5360;
  font-size: 12px;
  line-height: 1.45;
}

.hero-preview {
  align-self: center;
  width: 100%;
  padding: 14px;
  border: 1px solid rgba(32, 43, 62, 0.1);
  background: rgba(255, 255, 255, 0.96);
  border-radius: 8px;
  box-shadow: 0 32px 80px rgba(23, 25, 31, 0.16);
  cursor: pointer;
  color: inherit;
  text-decoration: none;
  box-sizing: border-box;
}

.preview-frame {
  overflow: hidden;
  border-radius: 6px;
  aspect-ratio: 4 / 5.2;
  background: #d9dde3;
}

.preview-image {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  object-position: center top;
}

.preview-copy {
  padding: 16px 4px 10px;
}

.preview-title {
  display: block;
  margin-bottom: 8px;
  font-size: 16px;
  font-weight: 900;
  color: #17191f;
}

.preview-text,
.section-note,
.card-desc,
.feature-desc,
.quote-text,
.cta-desc,
.footer-copy {
  display: block;
  color: #4c5360;
  font-size: 14px;
  line-height: 1.75;
}

.landing-body {
  width: calc(100% - 48px);
  max-width: 1280px;
  margin: 0 auto;
}

.section-block {
  padding: 76px 0;
}

.benefits-section {
  padding-top: 58px;
}

.section-heading {
  max-width: 780px;
  margin: 0 auto 42px;
  text-align: center;
}

.section-heading.split {
  max-width: none;
  display: flex;
  justify-content: space-between;
  gap: 32px;
  align-items: flex-end;
  text-align: left;
}

.section-title {
  display: block;
  color: #17191f;
  font-size: clamp(30px, 3.6vw, 46px);
  line-height: 1.15;
  overflow-wrap: break-word;
}

.section-note {
  max-width: 360px;
}

.benefit-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}

.benefit-card,
.testimonial-card {
  border: 1px solid rgba(32, 43, 62, 0.1);
  background: #ffffff;
  border-radius: 8px;
  padding: 26px;
  box-shadow: 0 12px 34px rgba(23, 25, 31, 0.04);
}

.card-mark {
  display: inline-flex;
  width: 46px;
  height: 46px;
  align-items: center;
  justify-content: center;
  margin-bottom: 22px;
  border-radius: 8px;
  background: #eef7f5;
  color: #116a60;
  font-size: 13px;
  font-weight: 900;
}

.card-title,
.quote-name {
  display: block;
  margin-bottom: 10px;
  color: #17191f;
  font-size: 18px;
  font-weight: 900;
}

.features-section {
  width: 100vw;
  margin-left: calc((100% - 100vw) / 2);
  padding-left: max(24px, calc((100vw - 1280px) / 2));
  padding-right: max(24px, calc((100vw - 1280px) / 2));
  background: #eef1ee;
  border-top: 1px solid rgba(32, 43, 62, 0.08);
  border-bottom: 1px solid rgba(32, 43, 62, 0.08);
}

.feature-layout {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 22px;
}

.feature-panel {
  overflow: hidden;
  display: grid;
  grid-template-columns: minmax(210px, 0.5fr) minmax(0, 1fr);
  min-height: 340px;
  border: 1px solid rgba(32, 43, 62, 0.1);
  border-radius: 8px;
  background: #ffffff;
  cursor: pointer;
  box-shadow: 0 18px 44px rgba(23, 25, 31, 0.06);
  color: inherit;
  text-decoration: none;
}

.feature-panel.disabled {
  cursor: default;
}

.feature-image {
  width: 100%;
  height: 100%;
  min-height: 340px;
  display: block;
  background: #d9dde3;
  object-fit: cover;
  object-position: center top;
}

.feature-copy {
  padding: 28px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.feature-kicker,
.style-action {
  display: block;
  margin-bottom: 8px;
  color: #116a60;
  font-size: 12px;
  font-weight: 900;
}

.feature-title {
  display: block;
  margin-bottom: 12px;
  color: #17191f;
  font-size: 28px;
  line-height: 1.2;
}

.feature-action {
  margin-top: 18px;
  min-height: 42px;
  display: inline-flex;
  align-items: center;
  padding: 0 18px;
  border-radius: 8px;
  background: #17191f;
  color: #ffffff;
  font-size: 13px;
  font-weight: 900;
}

.feature-status {
  margin-top: 18px;
  display: inline-flex;
  color: #0b5e55;
  font-size: 13px;
  font-weight: 900;
}

.category-filter {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 24px;
}

.cat-chip {
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  padding: 0 16px;
  border-radius: 8px;
  border: 1px solid #c8ced8;
  background: #ffffff;
  color: #4c5360;
  font-size: 13px;
  font-weight: 900;
  cursor: pointer;
}

.cat-chip.active {
  color: #ffffff;
  background: #17191f;
  border-color: #17191f;
}

.style-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 20px;
}

.style-card {
  overflow: hidden;
  border: 1px solid rgba(32, 43, 62, 0.1);
  border-radius: 8px;
  background: #ffffff;
  cursor: pointer;
  box-shadow: 0 14px 36px rgba(23, 25, 31, 0.05);
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
  color: inherit;
  text-decoration: none;
}

.style-card:active {
  transform: translateY(1px);
}

.style-image-frame {
  aspect-ratio: 4 / 5;
  overflow: hidden;
  background: #d9dde3;
}

.style-image {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  object-position: center center;
}

.style-copy {
  padding: 17px 16px 18px;
}

.style-title {
  display: block;
  margin-bottom: 6px;
  color: #17191f;
  font-size: 20px;
  line-height: 1.25;
}

.style-desc {
  display: block;
  min-height: 42px;
  color: #4c5360;
  font-size: 12px;
  line-height: 1.55;
}

.style-action {
  margin-top: 12px;
  margin-bottom: 0;
}

.empty-gallery {
  padding: 48px 24px;
  border: 1px dashed #aeb6c2;
  border-radius: 8px;
  text-align: center;
}

.empty-title {
  display: block;
  margin-bottom: 8px;
  color: #17191f;
  font-size: 18px;
  font-weight: 900;
}

.empty-desc {
  display: block;
  color: #4c5360;
  font-size: 14px;
}

.style-skeleton {
  cursor: default;
}

.skeleton-block,
.skeleton-line {
  background: linear-gradient(90deg, #e4e8e5 25%, #f1f3f1 50%, #e4e8e5 75%);
  background-size: 200% 100%;
  animation: skeletonPulse 1.4s ease-in-out infinite;
}

.skeleton-line {
  width: 68%;
  height: 12px;
  margin-top: 10px;
  border-radius: 6px;
}

.skeleton-line.wide {
  width: 88%;
  height: 18px;
  margin-top: 0;
}

@keyframes skeletonPulse {
  from {
    background-position: 100% 0;
  }
  to {
    background-position: -100% 0;
  }
}

.testimonials-section {
  border-top: 1px solid #dde1e8;
}

.testimonial-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.quote-text {
  min-height: 96px;
  color: #17191f;
  font-size: 17px;
}

.quote-name {
  margin-top: 20px;
  margin-bottom: 0;
  color: #116a60;
}

.pricing-section {
  width: 100vw;
  margin-left: calc((100% - 100vw) / 2);
  padding-left: max(24px, calc((100vw - 1280px) / 2));
  padding-right: max(24px, calc((100vw - 1280px) / 2));
  background: #eef1ee;
  border-top: 1px solid rgba(32, 43, 62, 0.08);
  border-bottom: 1px solid rgba(32, 43, 62, 0.08);
}

.pricing-intro {
  display: block;
  max-width: 720px;
  margin: 16px auto 0;
  color: #4c5360;
  font-size: 15px;
  line-height: 1.7;
}

.pricing-inline-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
}

.pricing-inline-card,
.trust-card {
  min-width: 0;
  padding: 28px;
  border: 1px solid rgba(32, 43, 62, 0.1);
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 14px 36px rgba(23, 25, 31, 0.05);
}

.pricing-kind {
  display: block;
  margin-bottom: 10px;
  color: #116a60;
  font-size: 12px;
  font-weight: 900;
}

.pricing-name {
  display: block;
  color: #17191f;
  font-size: 28px;
  line-height: 1.2;
}

.pricing-price {
  display: block;
  margin-top: 16px;
  color: #17191f;
  font-size: 21px;
  font-weight: 900;
  font-variant-numeric: tabular-nums;
}

.pricing-copy {
  display: block;
  margin-top: 12px;
  color: #4c5360;
  font-size: 14px;
  line-height: 1.7;
}

.section-action-row {
  display: flex;
  justify-content: center;
  margin-top: 28px;
}

.trust-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}

.faq-section {
  border-top: 1px solid #dde1e8;
}

.faq-list {
  max-width: 900px;
  margin: 0 auto;
  border-top: 1px solid #cfd5de;
}

.faq-item {
  border-bottom: 1px solid #cfd5de;
}

.faq-item summary {
  min-height: 64px;
  padding: 18px 44px 18px 0;
  display: flex;
  align-items: center;
  color: #17191f;
  font-size: 17px;
  font-weight: 900;
  line-height: 1.5;
  cursor: pointer;
}

.faq-answer {
  display: block;
  padding: 0 44px 22px 0;
  color: #4c5360;
  font-size: 15px;
  line-height: 1.75;
}

.cta-section {
  width: 100vw;
  margin-left: calc((100% - 100vw) / 2);
  padding-left: 24px;
  padding-right: 24px;
  text-align: center;
  background: #e9f2ef;
  border-top: 1px solid rgba(17, 106, 96, 0.16);
  border-bottom: 1px solid rgba(17, 106, 96, 0.16);
}

.cta-title {
  display: block;
  max-width: 760px;
  margin: 0 auto 16px;
  color: #17191f;
  font-size: clamp(34px, 5vw, 58px);
  line-height: 1.1;
}

.cta-desc {
  max-width: 650px;
  margin: 0 auto;
}

.site-footer {
  padding: 64px 24px 20px;
  background: #eef1f4;
  border-top: 1px solid #dde1e8;
}

.footer-main {
  width: min(1280px, 100%);
  margin: 0 auto 24px;
  display: flex;
  justify-content: space-between;
  gap: 28px;
  align-items: flex-start;
}

.footer-brand {
  display: block;
  color: #17191f;
  font-size: 28px;
}

.footer-copy {
  max-width: 560px;
  text-align: right;
}

.footer-links {
  width: min(1280px, 100%);
  margin: 0 auto;
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  justify-content: center;
  color: #4c5360;
  font-size: 13px;
  font-weight: 900;
}

.footer-links a,
.footer-links [role="button"] {
  cursor: pointer;
}

.footer-links a {
  color: inherit;
  text-decoration: none;
}

.btn:focus-visible,
.hero-preview:focus-visible,
.feature-panel:focus-visible,
.cat-chip:focus-visible,
.style-card:focus-visible,
.faq-item summary:focus-visible,
.footer-links a:focus-visible,
.footer-links [role="button"]:focus-visible {
  outline: 3px solid #116a60;
  outline-offset: 3px;
}

@media (min-width: 961px) {
  .style-card:hover,
  .feature-panel:hover,
  .benefit-card:hover {
    box-shadow: 0 24px 60px rgba(23, 25, 31, 0.08);
    transform: translateY(-2px);
  }
}

@media (max-width: 1180px) {
  .hero-media {
    width: 56%;
    opacity: 0.72;
  }

  .hero-overlay {
    background:
      linear-gradient(90deg, #f7f7f3 0%, rgba(247, 247, 243, 0.96) 56%, rgba(247, 247, 243, 0.58) 100%),
      linear-gradient(0deg, #f5f6f4 0%, rgba(245, 246, 244, 0.52) 22%, rgba(245, 246, 244, 0.06) 52%);
  }

  .hero-content {
    grid-template-columns: 1fr;
    align-items: start;
  }

  .hero-preview {
    width: min(380px, 100%);
    justify-self: start;
  }

  .style-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .hero-section {
    min-height: auto;
  }

  .hero-media {
    width: 100%;
    opacity: 0.18;
    object-position: center top;
  }

  .hero-overlay {
    background:
      linear-gradient(90deg, rgba(247, 247, 243, 0.96) 0%, rgba(247, 247, 243, 0.88) 100%),
      linear-gradient(0deg, #f5f6f4 0%, rgba(245, 246, 244, 0.62) 32%, rgba(245, 246, 244, 0.18) 100%);
  }

  .hero-content {
    padding: 46px 0 52px;
  }

  .section-heading.split,
  .footer-main {
    display: block;
  }

  .section-note {
    max-width: none;
    margin-top: 14px;
  }

  .benefit-grid,
  .testimonial-list,
  .pricing-inline-grid,
  .trust-grid {
    grid-template-columns: 1fr;
  }

  .feature-layout {
    grid-template-columns: 1fr;
  }

  .style-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .footer-copy {
    margin-top: 14px;
    text-align: left;
  }
}

@media (max-width: 560px) {
  .product-landing,
  .hero-section {
    max-width: 100vw;
    overflow-x: hidden;
  }

  .landing-body,
  .hero-content {
    width: auto;
    max-width: none;
    margin-left: 24px;
    margin-right: 24px;
    box-sizing: border-box;
  }

  .landing-body {
    padding-left: 0;
    padding-right: 0;
  }

  .section-block {
    padding: 64px 0;
  }

  .benefits-section {
    padding-top: 56px;
  }

  .hero-content {
    padding: 36px 0 42px;
  }

  .hero-title {
    width: 100%;
    max-width: 78vw;
    margin-right: 24px;
    font-size: 38px;
    line-height: 1.06;
    overflow-wrap: anywhere;
    white-space: normal;
  }

  .hero-subtitle {
    width: 100%;
    max-width: 78vw;
    margin-right: 24px;
    font-size: 16px;
    overflow-wrap: anywhere;
    white-space: normal;
  }

  .hero-proof-grid {
    width: auto;
    max-width: 78vw;
    margin-right: 24px;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
  }

  .proof-item {
    min-width: 0;
    min-height: auto;
    padding: 10px 8px;
    text-align: center;
  }

  .proof-title {
    margin-bottom: 0;
    font-size: 12px;
  }

  .proof-desc {
    display: none;
  }

  .hero-preview {
    width: min(300px, 100%);
    justify-self: start;
    padding: 10px;
    box-sizing: border-box;
  }

  .feature-panel {
    grid-template-columns: 1fr;
  }

  .feature-image {
    min-height: auto;
    height: auto;
    aspect-ratio: 4 / 5;
  }

  .hero-actions,
  .hero-actions.centered {
    width: auto;
    max-width: 78vw;
    margin-right: 24px;
    flex-direction: column;
    align-items: stretch;
  }

  .btn {
    width: 312px !important;
    max-width: 78vw !important;
    min-width: 0;
    padding-left: 16px;
    padding-right: 16px;
    box-sizing: border-box;
  }

  .style-grid {
    grid-template-columns: 1fr;
  }

  .feature-title {
    font-size: 26px;
  }

  .benefit-card,
  .testimonial-card,
  .pricing-inline-card,
  .trust-card {
    padding: 22px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton-block,
  .skeleton-line {
    animation: none;
  }

  .btn,
  .style-card {
    transition: none;
  }
}
</style>
