<template>
  <view class="app-container" style="padding-top: 64px;">
    <NavBar />

    <view v-if="homeBanner.enabled" class="hero-section">
      <image :src="heroImageUrl" mode="aspectFill" class="hero-media" />
      <view class="hero-atmosphere-overlay"></view>

      <view class="hero-content">
        <view class="hero-text-ritual">
          <text class="hero-tag">{{ t('index.hero_tag') }}</text>
          <view class="hero-title-block">
            <text v-for="(line, idx) in heroTitleLines" :key="idx" class="hero-title heading-serif">{{ line }}</text>
          </view>
          <text class="hero-descriptor">{{ heroSubtitleText }}</text>
        </view>

        <view class="hero-actions">
          <view class="hero-btn hero-btn-primary" @tap="goToCustom">
            <text class="btn-kicker">{{ t('index.hero_primary_kicker') }}</text>
            <text class="btn-label">{{ heroPrimaryLabel }}</text>
          </view>
          <view class="hero-btn hero-btn-ghost" @tap="scrollToGallery">
            <text class="btn-kicker">{{ t('index.hero_secondary_kicker') }}</text>
            <text class="btn-label">{{ heroSecondaryLabel }}</text>
          </view>
        </view>
      </view>
    </view>

    <view class="page-body">
      <view v-if="homeBanner.portal_enabled" class="direct-studio-access cursor-pointer" @tap="goToCustom">
        <view class="luxury-portal-card shadow-xl">
          <view class="portal-gradient-border"></view>
          <view class="portal-inner">
            <view class="portal-icon">
              <view class="icon-orb"></view>
              <text class="icon-glyph">*</text>
            </view>
            <view class="portal-info">
              <text class="p-label">{{ t('index.portal_label') }}</text>
              <text class="p-title heading-serif">{{ t('index.portal_title') }}</text>
              <text class="p-desc">{{ t('index.portal_desc') }}</text>
            </view>
            <view class="portal-action-btn">
              <text>{{ t('index.portal_action') }}</text>
              <text class="arr">-&gt;</text>
            </view>
          </view>
        </view>
      </view>

      <view
        v-if="homeBanner.legacy_enabled && vintageTemplates.length"
        class="marketing-entry shadow-xl"
        @tap="focusCategory('vintage')"
      >
        <view class="m-left">
          <text class="m-kicker">{{ t('index.legacy_kicker') }}</text>
          <text class="m-title heading-serif">{{ t('index.legacy_title') }}</text>
          <text class="m-desc">{{ t('index.legacy_desc') }}</text>
          <view class="m-features">
            <text class="m-feature">{{ t('index.legacy_feature_restore') }}</text>
            <text class="m-feature">{{ t('index.legacy_feature_texture') }}</text>
            <text class="m-feature">{{ t('index.legacy_feature_memory') }}</text>
          </view>
        </view>
        <view class="m-right">
          <image src="/static/legacy_promo_banner.jpg" mode="aspectFill" class="m-cover" />
        </view>
      </view>

      <view class="collection-header" id="gallery">
        <view class="header-main">
          <text class="h-num">01</text>
          <text class="h-title heading-serif">{{ t('index.collection_title') }}</text>
        </view>
        <view class="header-meta">
          <text class="m-text">{{ t('index.collection_meta') }}</text>
          <view class="m-line"></view>
        </view>
      </view>

      <view class="category-filter">
        <view class="cat-chip" :class="{ active: selectedCategory === 'all' }" @tap="selectedCategory = 'all'">
          {{ t('category.all') }}
        </view>
        <view class="cat-chip" :class="{ active: selectedCategory === 'single' }" @tap="selectedCategory = 'single'">
          {{ t('category.portrait') }}
        </view>
        <view class="cat-chip" :class="{ active: selectedCategory === 'vintage' }" @tap="selectedCategory = 'vintage'">
          {{ t('category.legacy') }}
        </view>
        <view class="cat-chip" :class="{ active: selectedCategory === 'custom' }" @tap="selectedCategory = 'custom'">
          {{ t('category.bespoke') }}
        </view>
      </view>

      <view v-if="filteredTemplates.length > 0" class="style-grid">
        <view
          v-for="template in filteredTemplates"
          :key="template.id"
          class="style-card-container"
          @tap="goToDetail(template)"
        >
          <view class="style-card hover-lift shadow-glass">
            <view class="card-media-wrap">
              <image
                :src="resolveTemplateCardImage(template)"
                mode="aspectFill"
                class="card-image"
                @error="onTemplateImageError(template)"
              />
              <view class="card-overlay-ritual"></view>
            </view>
            <view class="card-content">
              <text class="c-title heading-serif">{{ displayTemplateTitle(template) }}</text>
              <text v-if="displayTemplateMarketingSubtitle(template)" class="c-sub">{{ displayTemplateMarketingSubtitle(template) }}</text>
              <view class="c-action">{{ t('index.discover') }}</view>
            </view>
          </view>
        </view>
      </view>
      <view v-else class="style-grid-loading">
        <text class="loading-ritual">{{ t('index.loading_title') }}</text>
        <text class="loading-sub">{{ t('index.loading_sub') }}</text>
      </view>

      <view class="editorial-features">
        <view v-for="(item, idx) in features" :key="idx" class="feature-item">
          <view class="f-icon-orb">{{ item.icon }}</view>
          <text class="f-title">{{ item.label }}</text>
          <text class="f-desc">{{ t('index.feature_desc') }}</text>
        </view>
      </view>
    </view>

      <view class="studio-footer-ritual">
        <view class="footer-inner">
          <text class="f-logo heading-serif" @tap="onLogoTap">{{ t('index.footer_logo') }}</text>
          <text class="f-credo">{{ t('index.footer_credo') }}</text>
          <view class="footer-legal">
            <text class="footer-legal-intro">{{ t('index.footer_legal_intro') }}</text>
            <view class="footer-legal-links">
              <text class="footer-legal-link" @tap="goToPrivacy">{{ t('nav.privacy_policy') }}</text>
              <text class="footer-legal-divider">·</text>
              <text class="footer-legal-link" @tap="goToTerms">{{ t('nav.terms_service') }}</text>
              <text class="footer-legal-divider">·</text>
              <text class="footer-legal-link" @tap="goToRefund">{{ i18nStore.locale === 'zh' ? '退款与客服' : 'Refunds & Support' }}</text>
            </view>
          </view>
          <view class="f-dots">
            <view v-for="i in 3" :key="i" class="dot"></view>
          </view>
      </view>
      <view class="secret-admin" @tap="goToAdmin">...</view>
    </view>

    <view style="height: 40px;"></view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import NavBar from '../../components/NavBar.vue';
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
const t = i18nStore.t;
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const templates = ref<Template[]>([]);
const selectedCategory = ref<'all' | 'single' | 'vintage' | 'custom'>('all');
const logoTapCount = ref(0);

const homeBanner = computed(() => opsStore.publicConfig.placements.home_banner);
const templateImageAttempts = ref<Record<string, number>>({});

const styleImageFallbacks: Record<string, string[]> = {
  chn_xiuhe: ['/style-previews/couple_chn_xiuhe.jpg', '/style-previews/solo_chn_xiuhe.jpg'],
  korean_minimal: ['/style-previews/couple_korean_minimal.jpg', '/style-previews/solo_korean_minimal.jpg'],
  royal_castle: ['/style-previews/couple_royal_castle.jpg', '/style-previews/solo_royal_castle.jpg'],
  old_money: ['/style-previews/couple_old_money.jpg', '/style-previews/solo_old_money.jpg'],
  gothic_romance: ['/style-previews/couple_gothic_romance.jpg', '/style-previews/solo_gothic_romance_v2.png'],
  beach_sunset: ['/style-previews/couple_beach_sunset.jpg', '/style-previews/solo_beach_sunset.jpg'],
  hk_retro: ['/style-previews/couple_hk_retro_v2.png', '/style-previews/hk_retro.jpg'],
  twilight_forest: ['/style-previews/couple_twilight_forest.jpg', '/style-previews/twilight_forest.jpg'],
  japanese_shiromuku: ['/style-previews/couple_japanese_shiromuku.jpg', '/style-previews/japanese_shiromuku.jpg'],
  cyberpunk_city: ['/style-previews/couple_cyberpunk_city_v2.png', '/style-previews/cyberpunk_city.jpg'],
  school_days: ['/style-previews/couple_school_days.jpg', '/style-previews/school_days.jpg'],
  classic_bw: ['/style-previews/couple_classic_bw.jpg', '/style-previews/classic_bw.jpg'],
  golden_vintage_studio_8090: ['/style-previews/golden_vintage_studio_8090.jpg'],
  golden_chinese_courtyard: ['/style-previews/golden_chinese_courtyard.jpg'],
  golden_modern_remake: ['/style-previews/golden_modern_remake.jpg'],
  custom_mode: ['/style-previews/custom_mode.jpg'],
};

const heroImageUrl = computed(() => {
  const configured = resolvePublicUrl(homeBanner.value.image_url);
  if (configured) return configured;
  const backup = templates.value.find((item) => !!item.image_url)?.image_url;
  return resolvePublicUrl(backup || '/style-previews/couple_royal_castle.jpg');
});

const heroTitleLines = computed(() => {
  const raw = (homeBanner.value.title || '').trim();
  if (!raw || raw === 'AI Wedding Studio') {
    return [t('index.hero_title_line1'), t('index.hero_title_line2')].filter(Boolean);
  }
  const parts = raw
    .split('|')
    .map((item) => item.trim())
    .filter(Boolean);
  return parts.length ? parts : [raw];
});

const heroSubtitleText = computed(() => {
  const configured = String(homeBanner.value.subtitle || '').trim();
  if (!configured) return t('index.hero_descriptor');
  if (i18nStore.locale === 'zh' && configured === 'Premium wedding portraits in minutes') return t('index.hero_descriptor');
  return configured;
});

const heroPrimaryLabel = computed(() => {
  const configured = String(homeBanner.value.cta_label || '').trim();
  if (!configured) return t('index.hero_primary_label');
  if (i18nStore.locale === 'zh' && configured === 'Start Now') return t('index.hero_primary_label');
  return configured;
});

const heroSecondaryLabel = computed(() => {
  const configured = String(homeBanner.value.secondary_cta_label || '').trim();
  if (!configured) return t('index.hero_secondary_label');
  if (i18nStore.locale === 'zh' && configured === 'Browse Collection') return t('index.hero_secondary_label');
  return configured;
});

const features = computed(() => [
  { icon: '*', label: t('index.feature_flux') },
  { icon: '+', label: t('index.feature_couture') },
  { icon: 'o', label: t('index.feature_masterpiece') },
]);

const goToAdmin = () => {
  uni.navigateTo({ url: '/pages/admin/index' });
};

const onLogoTap = () => {
  logoTapCount.value += 1;
  if (logoTapCount.value >= 5) {
    goToAdmin();
    logoTapCount.value = 0;
  }
};

const getCategoryLabel = (category: string): string => {
  const labels: Record<string, string> = {
    single: t('category.portrait'),
    solo: t('category.portrait'),
    couple: t('category.portrait'),
    vintage: t('category.legacy'),
    custom: t('category.bespoke'),
  };
  return labels[category] || t('category.collection');
};

const displayTemplateTitle = (template: Template): string => getLocalizedTemplateTitle(template, i18nStore.locale);

const displayTemplateMarketingSubtitle = (template: Template): string =>
  getLocalizedTemplateMarketingSubtitle(template, i18nStore.locale);

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
    '/style-previews/couple_royal_castle.jpg',
  ]
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .filter((item, index, list) => list.indexOf(item) === index);

  const attempt = templateImageAttempts.value[template.id] || 0;
  const target = candidates[Math.min(attempt, candidates.length - 1)] || '/style-previews/couple_royal_castle.jpg';
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
      const portraitLike = templates.value.filter(
        (item) => item.category === 'single' || item.category === 'solo' || item.category === 'couple'
      );
      return dedupeTemplateCards(portraitLike);
    }
    return templates.value.filter((item) => item.category === selectedCategory.value);
  }
  return dedupeTemplateCards(templates.value);
});

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

onMounted(async () => {
  await opsStore.fetchPublicConfig();
  await templateStore.fetchTemplates();
  templates.value = templateStore.templates;
});
</script>

<style lang="scss" scoped>
.hero-section {
  position: relative;
  height: clamp(300px, 40vh, 420px);
  display: flex;
  align-items: center;
  overflow: hidden;
  background: #111;

  @media (max-width: 768px) {
    height: 360px;
  }
}

.hero-media {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 1;
  opacity: 0.98;
  filter: contrast(1.05) saturate(1.08);
}

.hero-atmosphere-overlay {
  position: absolute;
  inset: 0;
  z-index: 2;
  background:
    linear-gradient(to right, rgba(12, 10, 14, 0.38) 0%, rgba(12, 10, 14, 0.16) 52%, rgba(12, 10, 14, 0.04) 100%),
    linear-gradient(to top, rgba(253, 242, 248, 0.82) 0%, rgba(253, 242, 248, 0.18) 38%, transparent 66%);
}

.hero-content {
  position: relative;
  z-index: 10;
  width: 100%;
  padding: 0 clamp(24px, 6vw, 96px);
  color: white;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;

  @media (max-width: 1024px) {
    flex-direction: column;
    align-items: flex-start;
    gap: 30px;
  }
}

.hero-text-ritual {
  .hero-tag {
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.5em;
    color: $uni-color-secondary;
    display: block;
    margin-bottom: 20px;
  }

  .hero-title-block {
    margin-bottom: 18px;
  }

  .hero-title {
    display: block;
    font-size: 84px;
    line-height: 0.9;
    font-style: italic;
    text-shadow: 0 10px 40px rgba(0, 0, 0, 0.35);

    @media (max-width: 768px) {
      font-size: 54px;
    }
  }

  .hero-descriptor {
    font-size: 16px;
    max-width: 420px;
    line-height: 1.6;
    opacity: 0.86;
    font-family: $uni-font-family-sans;
  }
}

.hero-actions {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: flex-end;
  margin-bottom: 4px;

  @media (max-width: 1024px) {
    align-items: flex-start;
  }
}

.hero-btn {
  min-width: 220px;
  padding: 14px 18px;
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.25);
  background: rgba(0, 0, 0, 0.2);
  backdrop-filter: blur(10px);
  transition: transform 0.2s ease, border-color 0.2s ease;

  &:active {
    transform: translateY(1px);
  }

  .btn-kicker {
    display: block;
    font-size: 10px;
    font-weight: 900;
    letter-spacing: 0.35em;
    opacity: 0.76;
  }

  .btn-label {
    display: block;
    margin-top: 4px;
    font-size: 13px;
    font-weight: 900;
    letter-spacing: 0.18em;
    text-transform: uppercase;
  }
}

.hero-btn-primary {
  background: rgba(255, 255, 255, 0.92);
  color: #111;
  border-color: rgba(255, 255, 255, 0.65);
}

.hero-btn-ghost {
  color: white;
}

.page-body {
  max-width: 1320px;
  width: 100%;
  margin: 0 auto;
  padding: 0 32px;

  @media (max-width: 768px) {
    padding: 0 20px;
  }
}

.direct-studio-access {
  margin: -32px 0 42px;
  position: relative;
  z-index: 30;
}

.luxury-portal-card {
  background: white;
  border-radius: $uni-border-radius-lg;
  position: relative;
  overflow: hidden;
  padding: 2px;
}

.portal-gradient-border {
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, $uni-color-accent, $uni-color-primary, $uni-color-secondary);
  opacity: 0.15;
}

.portal-inner {
  background: white;
  border-radius: calc($uni-border-radius-lg - 2px);
  padding: 30px 40px;
  display: flex;
  align-items: center;
  gap: 28px;
  position: relative;
  z-index: 2;

  @media (max-width: 768px) {
    padding: 24px;
    gap: 16px;
  }
}

.portal-icon {
  width: 80px;
  height: 80px;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;

  .icon-orb {
    position: absolute;
    width: 100%;
    height: 100%;
    background: radial-gradient(circle, $uni-color-secondary 0%, transparent 70%);
    opacity: 0.2;
    animation: pulse 4s infinite ease-in-out;
  }

  .icon-glyph {
    position: relative;
    z-index: 2;
    font-size: 40px;
    color: $uni-color-primary;
    font-style: normal;
    line-height: 1;
  }
}

.portal-info {
  flex: 1;

  .p-label {
    display: block;
    margin-bottom: 8px;
    font-size: 11px;
    font-weight: 900;
    letter-spacing: 0.3em;
    color: $uni-color-accent;
  }

  .p-title {
    display: block;
    margin-bottom: 6px;
    font-size: 36px;
    color: $uni-text-color;
  }

  .p-desc {
    font-size: 15px;
    color: $uni-text-color-muted;
    line-height: 1.5;
  }

  @media (max-width: 768px) {
    .p-title {
      font-size: 24px;
    }

    .p-desc {
      display: none;
    }
  }
}

.portal-action-btn {
  display: flex;
  align-items: center;
  gap: 12px;
  background: $uni-text-color;
  color: white;
  padding: 14px 30px;
  border-radius: 100px;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.15em;
  transition: all 0.3s;

  .arr {
    font-size: 18px;
    line-height: 1;
  }

  &:hover {
    transform: translateY(-2px);
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
  }
}

.marketing-entry {
  margin: -4px 0 40px;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.96), rgba(253, 242, 248, 0.92));
  border: 1px solid rgba($uni-color-primary, 0.12);
  border-radius: $uni-border-radius-lg;
  overflow: hidden;
  padding: 28px 30px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) clamp(320px, 32vw, 420px);
  align-items: center;
  gap: 28px;

  .m-left {
    flex: 1;
    min-width: 0;
  }

  .m-kicker {
    display: block;
    margin-bottom: 10px;
    font-size: 10px;
    font-weight: 900;
    letter-spacing: 0.2em;
    color: $uni-color-accent;
  }

  .m-title {
    display: block;
    margin-bottom: 8px;
    font-size: 28px;
    color: $uni-text-color;
    font-style: italic;
  }

  .m-desc {
    display: block;
    max-width: 620px;
    font-size: 14px;
    line-height: 1.7;
    color: $uni-text-color-muted;
    margin-bottom: 16px;
  }

  .m-features {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }

  .m-feature {
    padding: 8px 12px;
    border-radius: 999px;
    background: rgba($uni-color-primary, 0.06);
    border: 1px solid rgba($uni-color-primary, 0.1);
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.04em;
    color: $uni-color-primary;
  }

  .m-right {
    width: 100%;
    aspect-ratio: 16 / 10;
    border-radius: 28px;
    overflow: hidden;
    position: relative;
    border: 1px solid rgba($uni-color-primary, 0.12);
    background: linear-gradient(135deg, rgba(252, 244, 247, 1), rgba(248, 236, 242, 0.95));
    box-shadow: 0 18px 36px rgba(131, 24, 67, 0.12);
  }

  .m-cover {
    width: 100%;
    height: 100%;
    display: block;
    transform: scale(1.01);
    transform-origin: center center;
    object-position: center 36%;
  }

  @media (max-width: 768px) {
    padding: 22px 20px;
    grid-template-columns: 1fr;
    gap: 18px;

    .m-right {
      width: 100%;
      aspect-ratio: 14 / 9;
      border-radius: 20px;
      order: -1;
    }

    .m-title {
      font-size: 24px;
    }

    .m-desc {
      font-size: 12px;
      line-height: 1.6;
      margin-bottom: 12px;
    }

    .m-feature {
      font-size: 10px;
      padding: 6px 10px;
    }
  }
}

.collection-header {
  margin-bottom: 28px;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;

  .h-num {
    display: block;
    margin-bottom: 12px;
    font-size: 14px;
    font-weight: 800;
    color: $uni-color-accent;
  }

  .h-title {
    font-size: 42px;
    color: $uni-text-color;
    font-style: italic;

    @media (max-width: 768px) {
      font-size: 32px;
    }
  }

  .header-meta {
    text-align: right;
  }

  .m-text {
    font-size: 12px;
    font-weight: 700;
    color: $uni-text-color-muted;
    letter-spacing: 0.1em;
    opacity: 0.6;
  }

  .m-line {
    width: 60px;
    height: 2px;
    margin-top: 10px;
    margin-left: auto;
    background: $uni-color-accent;
  }
}

.category-filter {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin: -4px 0 20px;
}

.cat-chip {
  padding: 10px 14px;
  border-radius: 999px;
  background: #fff;
  border: 1px solid $uni-color-border;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.08em;
  color: $uni-text-color-muted;
}

.cat-chip.active {
  border-color: rgba($uni-color-primary, 0.35);
  color: $uni-text-color;
  background: rgba($uni-color-primary, 0.06);
}

.style-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 24px;

  @media (max-width: 1200px) {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 22px;
  }

  @media (max-width: 768px) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 20px;
  }

  @media (max-width: 480px) {
    grid-template-columns: 1fr;
    gap: 12px;
  }
}

.style-card {
  background: #fff;
  border-radius: $uni-border-radius-base;
  overflow: hidden;
}

.card-media-wrap {
  aspect-ratio: 3 / 4;
  position: relative;
  overflow: hidden;
  background: #f6f6f8;

  .card-image {
    width: 100%;
    height: 100%;
  }
}

.card-overlay-ritual {
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.2) 0%, transparent 50%);
}

.card-content {
  padding: 14px 10px;
  text-align: center;

  .c-title {
    display: block;
    margin-bottom: 6px;
    font-size: 18px;
    color: $uni-text-color;
  }

  .c-sub {
    display: block;
    margin-bottom: 10px;
    font-size: 11px;
    line-height: 1.4;
    color: $uni-text-color-muted;
    opacity: 0.85;
  }

  .c-action {
    font-size: 11px;
    font-weight: 800;
    color: $uni-color-primary;
  }
}

.style-grid-loading {
  padding: 40px 0 12px;
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: 12px;

  .loading-ritual {
    font-size: 18px;
    color: $uni-color-primary;
    letter-spacing: 0.2em;
  }

  .loading-sub {
    font-size: 12px;
    color: $uni-text-color-muted;
    opacity: 0.7;
  }
}

.editorial-features {
  margin: clamp(24px, 4vh, 36px) 0 clamp(34px, 5vh, 52px);
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;

  @media (max-width: 900px) {
    grid-template-columns: 1fr;
    gap: 16px;
    text-align: center;
  }
}

.feature-item {
  background: #fff;
  border: 1px solid rgba($uni-color-primary, 0.08);
  border-radius: $uni-border-radius-base;
  padding: 24px 20px;

  .f-icon-orb {
    width: 50px;
    height: 50px;
    border: 1px solid $uni-color-border;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 24px;
    font-size: 20px;
    color: $uni-color-primary;
  }

  .f-title {
    display: block;
    margin-bottom: 12px;
    font-size: 14px;
    font-weight: 900;
    letter-spacing: 0.2em;
    color: $uni-text-color;
  }

  .f-desc {
    font-size: 14px;
    line-height: 1.6;
    color: $uni-text-color-muted;
  }
}

.studio-footer-ritual {
  padding: 72px 48px;
  background: $uni-color-background;
  border-top: 1px solid $uni-color-border;
  text-align: center;
}

.f-logo {
  display: block;
  margin-bottom: 24px;
  font-size: 24px;
  color: $uni-text-color;
  letter-spacing: 0.35em;
}

.f-credo {
  max-width: 500px;
  margin: 0 auto;
  font-size: 14px;
  line-height: 1.8;
  color: $uni-text-color-muted;
  opacity: 0.75;
}

.footer-legal {
  margin-top: 20px;
}

.footer-legal-intro {
  display: block;
  font-size: 12px;
  color: $uni-text-color-muted;
  opacity: 0.72;
  margin-bottom: 10px;
}

.footer-legal-links {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 10px;
}

.footer-legal-link {
  font-size: 12px;
  color: $uni-color-primary;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.footer-legal-divider {
  font-size: 12px;
  color: rgba($uni-text-color-muted, 0.45);
}

.f-dots {
  display: flex;
  justify-content: center;
  gap: 12px;
  margin-top: 36px;

  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: $uni-color-accent;
    opacity: 0.3;
  }
}

.secret-admin {
  margin-top: 60px;
  color: $uni-color-border;
  font-size: 20px;
  opacity: 0.25;
}

@keyframes pulse {
  0% {
    transform: scale(1);
    opacity: 0.1;
  }
  50% {
    transform: scale(1.15);
    opacity: 0.3;
  }
  100% {
    transform: scale(1);
    opacity: 0.1;
  }
}
</style>

