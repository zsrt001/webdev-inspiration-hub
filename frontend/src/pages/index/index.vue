<template>
  <view class="app-container product-landing" style="padding-top: 64px;">
    <a class="skip-link" href="#benefits">Skip to main content</a>
    <NavBar ref="navBarRef" @show-payment="openPaymentModal" />

    <view v-if="homeBanner.enabled" class="hero-section">
      <img
        :src="heroImageUrl"
        class="hero-media"
        alt="AI wedding portrait style preview"
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
            <a v-if="creationAvailable" class="btn primary" href="/pages/create/index" @click.prevent="goToCustom">{{ tr('开始免费预览', 'Start a Free Preview') }}</a>
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
              alt="Curated wedding look preview"
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
      <section class="benefits-section section-block" id="benefits">
        <view class="section-heading">
          <text class="section-label">{{ tr('为什么选择 VowPic', 'Why VowPic') }}</text>
          <text class="section-title heading-serif">{{ tr('先看到成片灵感，再决定是否继续', 'Preview the look before you continue') }}</text>
        </view>
        <view class="benefit-grid">
          <view v-for="item in benefitItems" :key="item.title" class="benefit-card">
            <text class="card-mark">{{ item.mark }}</text>
            <text class="card-title">{{ item.title }}</text>
            <text class="card-desc">{{ item.desc }}</text>
          </view>
        </view>
      </section>

      <section class="features-section section-block" id="features">
        <view class="section-heading">
          <text class="section-label">{{ tr('核心功能', 'Core Features') }}</text>
          <text class="section-title heading-serif">{{ tr('从一张照片到婚纱大片，只需要几步', 'From upload to keepsake in a few guided steps') }}</text>
        </view>

        <view class="feature-layout">
          <a
            class="feature-panel"
            :class="{ disabled: !creationAvailable }"
            :href="creationAvailable ? '/pages/create/index' : undefined"
            :aria-disabled="!creationAvailable"
            :tabindex="creationAvailable ? 0 : -1"
            @click.prevent="goToCustom"
          >
            <img
              src="/static/style-previews/couple_old_money.jpg"
              class="feature-image"
              alt="Classic couple wedding portrait example"
            />
            <view class="feature-copy">
              <text class="feature-kicker">{{ tr('自由定制', 'Custom Creation') }}</text>
              <text class="feature-title heading-serif">{{ tr('描述你想要的婚纱、场景和氛围', 'Describe the dress, scene, and mood you want') }}</text>
              <text class="feature-desc">{{ tr('上传人物照片后，可以选择模板，也可以补充服装、场景和参考图，让画面更接近你的审美。', 'Upload portraits, choose a style, then add outfit, scene, or reference guidance to bring the result closer to your taste.') }}</text>
              <view v-if="creationAvailable" class="feature-action">{{ tr('开始定制', 'Start Customizing') }}</view>
              <view v-else class="feature-status">{{ tr('暂未开放', 'Temporarily unavailable') }}</view>
            </view>
          </a>

          <a
            class="feature-panel alt"
            :class="{ disabled: !creationAvailable }"
            :href="creationAvailable ? '/pages/create/index?mode=golden_anniversary&id=golden_vintage_studio_8090' : undefined"
            :aria-disabled="!creationAvailable"
            :tabindex="creationAvailable ? 0 : -1"
            @click.prevent="goToGoldenCreate"
          >
            <img
              src="/static/style-previews/golden_chinese_courtyard.jpg"
              class="feature-image"
              alt="Golden anniversary wedding portrait example"
            />
            <view class="feature-copy">
              <text class="feature-kicker">{{ tr('金婚纪念', 'Legacy Series') }}</text>
              <text class="feature-title heading-serif">{{ tr('为父母和长辈重做一组纪念婚纱照', 'Create anniversary portraits for parents and elders') }}</text>
              <text class="feature-desc">{{ tr('适合结婚纪念日、金婚礼物和家庭相册，用更体面的方式留下珍贵关系与家庭记忆。', 'Perfect for anniversaries, legacy gifts, and family albums, with a more polished way to preserve important memories.') }}</text>
              <view v-if="creationAvailable" class="feature-action">{{ tr('开始纪念创作', 'Start Legacy Creation') }}</view>
              <view v-else class="feature-status">{{ tr('暂未开放', 'Temporarily unavailable') }}</view>
            </view>
          </a>
        </view>
      </section>

      <section class="gallery-section section-block" id="gallery">
        <view class="section-heading split">
          <view>
            <text class="section-label">{{ tr('风格灵感', 'Style Inspiration') }}</text>
            <text class="section-title heading-serif">{{ tr('选择一个喜欢的风格，马上开始生成', 'Choose a direction, then make it yours') }}</text>
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

        <view v-if="filteredTemplates.length > 0" class="style-grid">
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
          <text class="empty-title">{{ tr('正在加载风格作品', 'Loading styles') }}</text>
          <text class="empty-desc">{{ tr('请稍候，模板库会自动使用本地保底数据。', 'Please wait. The gallery falls back safely if the API is unavailable.') }}</text>
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

      <section class="cta-section section-block" id="cta">
        <text class="section-label">{{ tr('开始体验', 'Start Now') }}</text>
        <text class="cta-title heading-serif">{{ tr('从真实照片开始，逐步完成你的婚纱创作', 'Start with your photos and a guided creation flow') }}</text>
        <text class="cta-desc">{{ tr('系统会在提交前显示当前部署可用的能力和所需额度；未启用的付费选项不会提前展示。', 'The app shows available capabilities and required credits before submission. Paid options remain hidden until billing is available on this deployment.') }}</text>
        <view class="hero-actions centered">
          <a v-if="creationAvailable" class="btn primary" href="/pages/create/index" @click.prevent="goToCustom">{{ tr('立即开始', 'Start Now') }}</a>
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
import { post, resolvePublicUrl } from '../../utils/api';

const templateStore = useTemplateStore();
const opsStore = useOpsStore();
const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const navBarRef = ref<InstanceType<typeof NavBar> | null>(null);
const templates = ref<Template[]>([]);
const selectedCategory = ref<'all' | 'single' | 'vintage' | 'custom'>('all');
const showPaymentModal = ref(false);
const templateImageAttempts = ref<Record<string, number>>({});

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
    title: tr('先看效果再决定', 'Preview with your real photos'),
    desc: tr('先用自己的照片试不同婚纱风格，看清楚氛围、构图和人物感觉，再决定是否继续。', 'Test the look with your own portrait before deciding whether to continue.'),
  },
  {
    mark: '02',
    title: tr('风格选择更自由', 'Solo and local couple creation'),
    desc: tr('可选择现成风格，也能用文字补充服装、场景和氛围，适合婚礼灵感、情侣写真和纪念礼物。', 'Choose a ready-made style or add outfit, scene, and mood guidance for solo, local couple, and anniversary portraits.'),
  },
  {
    mark: '03',
    title: tr('额度与能力透明', 'Clear credits and availability'),
    desc: tr('提交前会显示生成所需额度；未启用的购买或订阅能力不会展示。', 'Generation cost is shown before submission, while unavailable purchase or subscription options stay hidden.'),
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

const goToGoldenCreate = () => {
  if (!creationAvailable.value) return;
  const goldenTemplate =
    templates.value.find((item) => item.id === 'golden_vintage_studio_8090') ||
    templateStore.templates.find((item) => item.id === 'golden_vintage_studio_8090');
  if (goldenTemplate) templateStore.selectTemplate(goldenTemplate);
  post(
    '/analytics/click',
    {
      event_type: 'golden_anniversary_start',
      source_page: 'index',
      template_id: 'golden_vintage_studio_8090',
    },
    { showLoading: false, showError: false } as any
  ).catch(() => {});
  uni.navigateTo({ url: '/pages/create/index?mode=golden_anniversary&id=golden_vintage_studio_8090' });
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

const focusCategory = (category: 'single' | 'vintage' | 'custom') => {
  post(
    '/analytics/click',
    {
      event_type: 'collection_focus',
      source_page: 'index',
      template_id: category,
    },
    { showLoading: false, showError: false } as any
  ).catch(() => {});
  selectedCategory.value = category;
  scrollToGallery();
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

onMounted(async () => {
  await opsStore.fetchPublicConfig();
  await templateStore.fetchTemplates();
  templates.value = templateStore.templates;
  if (!vintageTemplates.value.length && selectedCategory.value === 'vintage') {
    selectedCategory.value = 'all';
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
  .testimonial-list {
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
  .testimonial-card {
    padding: 22px;
  }
}
</style>
