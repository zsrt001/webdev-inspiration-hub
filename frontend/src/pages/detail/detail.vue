<template>
  <view class="app-container detail-page" style="padding-top: 64px;">
    <NavBar />

    <view v-if="template" class="detail-shell">
      <view class="detail-hero shadow-xl">
        <view class="hero-media">
          <image :src="heroImageUrl" class="hero-image" mode="aspectFill" />
        </view>

        <view class="hero-copy">
          <text class="hero-kicker">{{ heroKicker }}</text>
          <text class="hero-title heading-serif">{{ heroTitle }}</text>
          <text class="hero-subtitle">{{ heroSubtitle }}</text>

          <view class="hero-points">
            <view v-for="point in differentiatedPoints" :key="point" class="point-row">
              <view class="point-dot"></view>
              <text class="point-text">{{ point }}</text>
            </view>
          </view>

          <view class="hero-actions">
            <button class="btn btn-primary action-btn shadow-glow" @tap="goCreate('single')">
              {{ tr('单人开始', 'Start Solo') }}
            </button>
            <button class="btn btn-outline action-btn" @tap="goCreate('couple_local')">
              {{ tr('双人同机', 'Local Couple') }}
            </button>
            <button v-if="remoteJoinEnabled" class="btn btn-outline action-btn" @tap="goCreate('couple_remote')">
              {{ tr('异地合拍', 'Remote Couple') }}
            </button>
          </view>

          <view class="logic-card">
            <text class="logic-title">{{ tr('创作建议', 'Creation Tip') }}</text>
            <text class="logic-line">
              {{ tr('进入创作页后，可继续补充服装、场景或参考图，让结果更贴近你的想法。', 'After opening the studio, add outfit, scene, or reference guidance to make the result closer to your idea.') }}
            </text>
          </view>
        </view>
      </view>

      <view class="support-grid">
        <view class="support-card">
          <text class="support-title heading-serif">{{ tr('适合什么人用', 'Best for') }}</text>
          <text class="support-copy">{{ supportUsage }}</text>
        </view>

        <view class="support-card">
          <text class="support-title heading-serif">{{ tr('你可以控制什么', 'What you can control') }}</text>
          <view class="support-list">
            <view class="support-row">
              <view class="support-dot"></view>
              <text class="support-copy">{{ tr('整体风格、服装描述、场景描述', 'Global style, outfit direction, and scene direction') }}</text>
            </view>
            <view class="support-row">
              <view class="support-dot"></view>
              <text class="support-copy">{{ tr('场景参考图与服装参考图', 'Scene and outfit references') }}</text>
            </view>
            <view class="support-row">
              <view class="support-dot"></view>
              <text class="support-copy">{{ tr('单人、双人同机、双人异地合拍', 'Single, local couple, and remote couple workflows') }}</text>
            </view>
          </view>
        </view>

        <view class="support-card">
          <text class="support-title heading-serif">{{ tr('下一步怎么做', 'How to use it') }}</text>
          <view class="support-list">
            <view class="support-row">
              <text class="step-chip">01</text>
              <text class="support-copy">{{ tr('进入统一创作页，上传人物照片', 'Enter the unified create page and upload portraits') }}</text>
            </view>
            <view class="support-row">
              <text class="step-chip">02</text>
              <text class="support-copy">{{ tr('使用当前风格，或继续补充服装 / 场景描述', 'Use this style, or add outfit and scene direction') }}</text>
            </view>
            <view class="support-row">
              <text class="step-chip">03</text>
              <text class="support-copy">{{ tr('想要更接近参考效果时，再上传参考图生成', 'Upload references when you want the result to follow a specific look') }}</text>
            </view>
          </view>
        </view>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import NavBar from '../../components/NavBar.vue';
import { resolvePublicUrl } from '../../utils/api';
import {
  type Template,
  getLocalizedTemplateMarketingSubtitle,
  getLocalizedTemplateTitle,
  getTemplateFamilyKey,
  useTemplateStore,
} from '../../stores/template';
import { useI18nStore } from '../../stores/i18n';
import { useOpsStore } from '../../stores/ops';

interface DetailCopyEntry {
  kickerZh: string;
  kickerEn: string;
  subtitleZh: string;
  subtitleEn: string;
  usageZh: string;
  usageEn: string;
  pointsZh: string[];
  pointsEn: string[];
}

const DETAIL_COPY: Record<string, DetailCopyEntry> = {
  chn_xiuhe: {
    kickerZh: '中式仪式感',
    kickerEn: 'Chinese Ceremony',
    subtitleZh: '礼服纹样、庭院空间与东方婚礼氛围更突出，适合庄重正式的纪念表达。',
    subtitleEn: 'Chinese attire, courtyard texture, and ceremonial atmosphere create a formal keepsake look.',
    usageZh: '适合强调礼服细节、传统庭院氛围与正面人物展示。',
    usageEn: 'Best for ornate bridal attire, traditional courtyards, and ceremonial portrait framing.',
    pointsZh: ['适合正面站姿与服装展示。', '可继续用文字强化喜庆、典礼、传统感。', '适合单人、双人和长辈纪念改造。'],
    pointsEn: ['Works well with frontal portrait framing.', 'Text can further push festive or ceremonial cues.', 'Useful for solo, couple, and legacy-style outputs.'],
  },
  korean_minimal: {
    kickerZh: '极简高定',
    kickerEn: 'Refined Minimalism',
    subtitleZh: '干净空间、柔和光线和克制构图会成为默认走向，适合高级感、画廊感、轻 editorial 风格。',
    subtitleEn: 'Clean interiors, soft lighting, and restrained composition create a refined bridal look.',
    usageZh: '适合室内白空间、极简礼服、轻法式或韩系高级婚纱表达。',
    usageEn: 'Best for white interiors, minimal gowns, and quiet editorial bridal styling.',
    pointsZh: ['适合轻柔、克制、干净的婚纱风格。', '可以用文字继续指定法式、胶片、画廊感。', '非常适合网页端首发和社交分享图。'],
    pointsEn: ['Works for clean and soft bridal styling.', 'Text can steer it toward French, filmic, or editorial moods.', 'Good for premium social-ready outputs.'],
  },
  royal_castle: {
    kickerZh: '古堡叙事',
    kickerEn: 'Castle Narrative',
    subtitleZh: '建筑尺度感、拖尾礼服与电影化场景会被优先强调，适合做史诗感和高定感更强的婚纱风格。',
    subtitleEn: 'Architectural scale, long-train gowns, and cinematic staging are emphasized first.',
    usageZh: '适合城堡、教堂、阳台、大场景婚纱与叙事性强的情侣画面。',
    usageEn: 'Best for castles, cathedrals, balconies, and cinematic couple portraits.',
    pointsZh: ['更适合大场景和纵深感强的构图。', '文本可继续强化皇室、史诗、电影感。', '双人同机和异地合拍都适配。'],
    pointsEn: ['Works best in larger-scale cinematic scenes.', 'Text can push it toward regal or epic direction.', 'Fits both local and remote couple flows.'],
  },
  old_money: {
    kickerZh: '静奢庄园',
    kickerEn: 'Quiet Luxury',
    subtitleZh: '低饱和、园林空间与克制高级感会作为默认调性，适合做轻英伦、庄园式、干净优雅的婚纱表达。',
    subtitleEn: 'Low-saturation elegance, estates, and quiet luxury define the default mood.',
    usageZh: '适合园林、石径、庄园、轻英伦与自然优雅的人物气质。',
    usageEn: 'Best for gardens, estates, stone paths, and understated elegance.',
    pointsZh: ['适合轻奢、老钱风、纪实优雅路线。', '可以继续用文本补充胶片、英伦、花园气质。', '适配单人和双人温柔关系表达。'],
    pointsEn: ['Best for quiet luxury and understated styling.', 'Text can add British, garden, or filmic cues.', 'Good for soft solo or couple portraits.'],
  },
  gothic_romance: {
    kickerZh: '暗黑浪漫',
    kickerEn: 'Dark Romance',
    subtitleZh: '深色礼服、教堂空间与戏剧性光影更突出，适合更强烈、更风格化的婚纱表达。',
    subtitleEn: 'Dark gowns, cathedral architecture, and dramatic light create a more stylized bridal mood.',
    usageZh: '适合哥特、教堂、烛光、暗调婚纱与风格强烈的人像表现。',
    usageEn: 'Best for gothic bridal styling, cathedral scenes, candlelight, and dramatic portraits.',
    pointsZh: ['适合强风格化而非日常写实路线。', '可继续补充烛光、暗红、宗教建筑等文本。', '人物姿态与情绪会比场景更重要。'],
    pointsEn: ['Made for stylized rather than casual realism.', 'Text can add candlelight, crimson, or sacred architecture cues.', 'Posture and attitude matter as much as the setting.'],
  },
  hk_retro: {
    kickerZh: '港风霓虹',
    kickerEn: 'Neon Retro',
    subtitleZh: '夜街、霓虹灯牌与都市电影感更突出，适合复古港片感和情侣街头叙事。',
    subtitleEn: 'Night streets, neon signs, and retro city energy create a cinematic couple story.',
    usageZh: '适合街头情侣、雨夜霓虹、电影海报感与都市叙事。',
    usageEn: 'Best for neon streets, rainy nights, and couple poster-style storytelling.',
    pointsZh: ['更适合双人关系感表达。', '可补充雨夜、出租车、胶片霓虹等方向。', '对服装风格和站位关系很敏感。'],
    pointsEn: ['Especially strong for couple storytelling.', 'Text can add rainy night, taxis, or filmic neon cues.', 'Outfit and couple positioning matter a lot here.'],
  },
  cyberpunk_city: {
    kickerZh: '赛博未来',
    kickerEn: 'Future City',
    subtitleZh: '未来灯光、城市霓虹和先锋感表达会成为默认走向，适合非传统婚纱与时尚实验性创作。',
    subtitleEn: 'Futuristic lights, city neon, and avant-garde styling become the default direction.',
    usageZh: '适合未来感、赛博都市、时装化婚纱与强表达型情侣作品。',
    usageEn: 'Best for cyber city mood, future fashion, and bold bridal experimentation.',
    pointsZh: ['适合潮流化、视觉对比强的生成。', '可继续用文字强化 hologram、future couture 等词。', '更适合追求差异化和传播感的风格。'],
    pointsEn: ['Built for bold and contrast-heavy outputs.', 'Text can push hologram or future-couture direction.', 'Good for high-difference, social-forward styles.'],
  },
};

const templateStore = useTemplateStore();
const opsStore = useOpsStore();
const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const template = ref<Template | null>(null);

const familyKey = computed(() => getTemplateFamilyKey(template.value));
const copy = computed<DetailCopyEntry | null>(() => DETAIL_COPY[familyKey.value] || null);

const heroImageUrl = computed(() => resolvePublicUrl(template.value?.image_url || '/style-previews/royal_castle.jpg'));
const remoteJoinEnabled = computed(() => opsStore.publicConfig.feature_flags.remote_join !== false);
const heroTitle = computed(() => getLocalizedTemplateTitle(template.value, i18nStore.locale));
const heroKicker = computed(() => {
  if (!copy.value) return tr('精选风格', 'Curated Style');
  return i18nStore.locale === 'zh' ? copy.value.kickerZh : copy.value.kickerEn;
});
const heroSubtitle = computed(() => {
  if (copy.value) return i18nStore.locale === 'zh' ? copy.value.subtitleZh : copy.value.subtitleEn;
  const subtitle = getLocalizedTemplateMarketingSubtitle(template.value, i18nStore.locale);
  return subtitle || tr(
    '进入创作页后，你可以继续补充服装、场景和参考图，让画面更贴近你的想法。',
    'After opening the studio, you can add outfit, scene, and reference guidance to shape the result.',
  );
});
const differentiatedPoints = computed(() => {
  if (copy.value) return i18nStore.locale === 'zh' ? copy.value.pointsZh : copy.value.pointsEn;
  return i18nStore.locale === 'zh'
    ? ['适合作为婚纱照起始风格。', '可继续补充服装与场景描述。', '上传清晰照片，预览效果会更稳定。']
    : ['Works as a starting style for wedding portraits.', 'You can still add outfit and scene direction.', 'Clear source photos usually produce steadier previews.'];
});
const supportUsage = computed(() => {
  if (copy.value) return i18nStore.locale === 'zh' ? copy.value.usageZh : copy.value.usageEn;
  return tr('适合作为起始风格，再用文字和参考图继续细化。', 'Best used as a starting style and refined with text or references.');
});

function currentQuery(): Record<string, string> {
  const pages = getCurrentPages();
  return ((pages[pages.length - 1] as any)?.options || {}) as Record<string, string>;
}

function goCreate(mode: 'single' | 'couple_local' | 'couple_remote') {
  if (mode === 'couple_remote' && !remoteJoinEnabled.value) return;
  if (!template.value) return;
  uni.navigateTo({
    url: `/pages/create/index?id=${encodeURIComponent(template.value.id)}&mode=${mode}`,
  });
}

onMounted(async () => {
  await opsStore.fetchPublicConfig();
  if (!templateStore.templates.length) {
    await templateStore.fetchTemplates();
  }

  const query = currentQuery();
  const requestedId = String(query.id || '').trim();
  template.value =
    templateStore.templates.find((item) => item.id === requestedId) ||
    templateStore.selectedTemplate ||
    templateStore.templates[0] ||
    null;

  if (template.value) {
    templateStore.selectTemplate(template.value);
  }
});
</script>

<style lang="scss" scoped>
.detail-page {
  min-height: 100vh;
  background: #f7f8fa;
}

.detail-shell {
  max-width: 1440px;
  margin: 0 auto;
  padding: 32px 28px 80px;
}

.detail-hero,
.support-card {
  background: #ffffff;
  border: 1px solid #dde1e8;
  border-radius: 8px;
  box-shadow: 0 14px 38px rgba(23, 25, 31, 0.06);
}

.detail-hero {
  display: grid;
  grid-template-columns: minmax(420px, 560px) minmax(0, 1fr);
  gap: 28px;
  padding: 28px;
  margin-bottom: 22px;

  @media (max-width: 1120px) {
    grid-template-columns: 1fr;
  }
}

.hero-media {
  aspect-ratio: 4 / 5;
  min-height: auto;
  border-radius: 8px;
  overflow: hidden;
  background: #d9dde3;

  @media (max-width: 1120px) {
    max-height: 620px;
  }
}

.hero-image {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  object-position: center top;
}

.hero-copy {
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.hero-kicker,
.logic-title {
  display: block;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
  color: #116a60;
}

.hero-kicker {
  margin-bottom: 14px;
}

.hero-title {
  display: block;
  font-size: 54px;
  line-height: 1.02;
  color: #17191f;
  margin-bottom: 16px;
}

.hero-subtitle,
.point-text,
.logic-line,
.support-copy {
  display: block;
  font-size: 14px;
  line-height: 1.8;
  color: #4c5360;
}

.hero-subtitle {
  margin-bottom: 18px;
}

.hero-points,
.support-list {
  display: grid;
  gap: 12px;
}

.hero-points {
  margin-bottom: 20px;
}

.point-row,
.support-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.point-dot,
.support-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  margin-top: 9px;
  background: #116a60;
  flex-shrink: 0;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 18px;
}

.action-btn {
  min-width: 180px;
}

.logic-card {
  padding: 16px 18px;
  border-radius: 8px;
  background: #f3faf8;
  border: 1px solid rgba(17, 106, 96, 0.16);
}

.logic-title {
  margin-bottom: 8px;
}

.support-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;

  @media (max-width: 1120px) {
    grid-template-columns: 1fr;
  }
}

.support-card {
  padding: 22px 22px 24px;
}

.support-title {
  display: block;
  font-size: 30px;
  color: #17191f;
  margin-bottom: 12px;
}

.step-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: #eef7f5;
  color: #116a60;
  font-size: 12px;
  font-weight: 800;
  flex-shrink: 0;
}
</style>
