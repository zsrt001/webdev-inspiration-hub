<template>
  <view class="app-container create-page" style="padding-top: 64px;">
    <NavBar ref="navBarRef" @show-payment="openPaymentModal" />
    <view class="create-shell" role="main">
      <view v-if="!creationAvailable" class="capability-unavailable">
        <text class="unavailable-kicker">{{ tr('安全基线', 'Safe baseline') }}</text>
        <text class="unavailable-title heading-serif" role="heading" aria-level="1">
          {{ tr('创作功能暂未开放', 'Studio temporarily unavailable') }}
        </text>
        <text class="unavailable-copy">
          {{ tr('当前部署仅开放只读浏览。照片上传和 AI 生成会在私有存储、身份与生成链路完成真实验收后再开放。', 'This deployment is browse-only. Photo upload and AI generation will open only after identity, private storage, and generation pass real acceptance.') }}
        </text>
        <button class="btn btn-outline unavailable-action" role="button" tabindex="0" @tap="goHome" @keydown.enter.prevent="goHome" @keydown.space.prevent="goHome">
          {{ tr('返回风格浏览', 'Browse styles') }}
        </button>
      </view>
      <template v-else>
      <view class="hero-card">
        <view class="hero-main">
          <text class="hero-kicker">{{ tr('AI 婚纱创作台', 'AI Wedding Studio') }}</text>
          <text class="hero-title heading-serif">{{ tr('先把照片上传好，再决定要不要加风格', 'Upload portraits first, then add style if needed') }}</text>
          <text class="hero-subtitle">{{ tr('主线只有两步：选择生成对象、上传人物照片。身份和人数会被锁定；参考图优先控制场景/服装，没参考图时文字就是主创作指令。', 'The core path has two steps: choose the subject count and upload portraits. Identity and count stay locked; references control scene/outfit first, and text becomes the main creative brief when no reference is uploaded.') }}</text>
          <view class="flow-strip">
            <view class="flow-step primary">{{ tr('1 选人数', '1 Choose count') }}</view>
            <view class="flow-step primary">{{ tr('2 上传照片', '2 Upload portraits') }}</view>
            <view class="flow-step subtle">{{ tr('3 风格增强', '3 Style enhancers') }}</view>
            <view class="flow-step subtle">{{ tr('4 开始生成', '4 Generate') }}</view>
          </view>
        </view>
        <view class="hero-aside">
          <text class="aside-title">{{ tr('当前主线', 'Core Flow') }}</text>
          <text class="aside-mode">{{ outputModeLabel }}</text>
          <text class="aside-copy">{{ tr('照片齐了就能生成；参考图是强参考，文字在无参考图时主控，有参考图时负责氛围、镜头、布光和质感。', 'Once portraits are ready, generation can start. References are strong controls; text leads when no reference exists and otherwise refines mood, lens, lighting, and texture.') }}</text>
        </view>
      </view>

      <view class="layout">
        <view class="main-col">
          <view class="panel primary-panel">
            <view class="panel-head">
              <text class="badge required">{{ tr('必填 01', 'Required 01') }}</text>
              <text class="panel-title heading-serif">{{ tr('选择输出人数', 'Choose Subject Count') }}</text>
            </view>
            <text class="panel-desc">{{ tr('先确定是单人还是双人。这个选择会决定需要几张人物照片，也会自动筛选后面的参考风格。', 'First decide solo or couple. This sets the required portraits and filters the optional style references later.') }}</text>

            <view class="mode-grid">
              <view v-for="mode in modeOptions" :key="mode.value" class="mode-card" :class="{ active: generationMode === mode.value }" @tap="setMode(mode.value)">
                <text class="mode-title">{{ mode.title }}</text>
                <text class="mode-desc">{{ mode.desc }}</text>
              </view>
            </view>
          </view>

          <view class="panel primary-panel">
            <view class="panel-head">
              <text class="badge required">{{ tr('必填 02', 'Required 02') }}</text>
              <text class="panel-title heading-serif">{{ tr('上传人物照片', 'Upload Portraits') }}</text>
            </view>
            <text class="panel-desc">{{ portraitHint }}</text>
            <view class="primary-guidance">
              <text class="guidance-title">{{ tr('先完成这里', 'Do this first') }}</text>
              <text class="guidance-copy">{{ primaryPhotoInstruction }}</text>
            </view>

            <view class="portrait-grid" :class="{ single: generationMode === 'single' }">
              <view v-for="index in portraitIndexes" :key="index" class="upload-card main-upload-card">
                <text class="field-label">{{ portraitLabel(index) }}</text>
                <view v-if="portraitSlots[index].localPath" class="preview-box">
                  <image :src="portraitSlots[index].localPath" class="preview-image" mode="aspectFill" />
                  <view class="preview-actions">
                    <button class="mini-btn" @tap.stop="pickPortrait(index)">{{ tr('更换', 'Replace') }}</button>
                    <button class="mini-btn ghost" @tap.stop="clearPortrait(index)">{{ tr('移除', 'Remove') }}</button>
                  </view>
                </view>
                <view v-else class="empty-box primary-empty" @tap="pickPortrait(index)">
                  <text class="empty-plus">+</text>
                  <text class="empty-title">{{ portraitCta(index) }}</text>
                  <text class="empty-hint">{{ tr('清晰正脸、无遮挡，生成效果更稳定', 'Clear unobstructed faces produce steadier results') }}</text>
                </view>
              </view>

            </view>
          </view>

          <view class="panel enhancer-panel">
            <view class="panel-head">
              <text class="badge optional">{{ tr('增强 03', 'Enhance 03') }}</text>
              <text class="panel-title heading-serif">{{ directionPanelTitle }}</text>
            </view>
            <text class="panel-desc">{{ directionPanelDesc }}</text>
            <view class="priority-guide">
              <view class="priority-guide-head">
                <text>{{ tr('创作控制顺序', 'Creative Control Order') }}</text>
                <text>{{ tr('越靠前越不会被后面的选择改掉', 'Earlier choices cannot be changed by later ones') }}</text>
              </view>
              <view class="priority-steps">
                <view v-for="item in priorityGuideItems" :key="item.key" class="priority-step" :class="{ active: item.active }">
                  <text class="priority-index">{{ item.index }}</text>
                  <view>
                    <text class="priority-title">{{ item.title }}</text>
                    <text class="priority-copy">{{ item.copy }}</text>
                  </view>
                </view>
              </view>
              <view class="priority-current">
                <text>{{ tr('当前控制', 'Current control') }}</text>
                <text>{{ tr('场景', 'Scene') }}：{{ sceneControlLabel }}</text>
                <text>{{ tr('服装', 'Outfit') }}：{{ outfitControlLabel }}</text>
              </view>
            </view>
            <view class="ratio-guide">
              <text class="ratio-title">{{ tr('成片比例：3:4 竖版主图', 'Output: 3:4 portrait master') }}</text>
              <text class="ratio-copy">{{ tr('系统会按 3:4 重新规划人物、礼服和背景；下载裁切版不会被当作最终成片展示。', 'The system composes the subject, wardrobe, and scene for a 3:4 master; download crops are not shown as the final result.') }}</text>
            </view>
            <view class="direction-card">
              <view class="direction-head">
                <view>
                  <text class="direction-kicker">{{ tr('推荐先写文字', 'Text first') }}</text>
                  <text class="direction-title">{{ tr('像和摄影师沟通一样描述即可', 'Describe it like a photographer brief') }}</text>
                </view>
                <view class="direction-status" :class="{ active: hasDirectionText }">
                  {{ hasDirectionText ? tr('已填写', 'Added') : tr('可跳过', 'Optional') }}
                </view>
              </view>
              <textarea v-model="globalStyleText" class="text-area large" :placeholder="tr('整体方向：如极简高级、法式电影感、复古胶片、低饱和纪实', 'Overall direction: minimal luxury, French cinematic, vintage film, low-saturation documentary...')" maxlength="400" />
              <view class="dual-inputs">
                <textarea v-model="outfitText" class="text-area" :placeholder="tr('服装方向：如缎面白纱、黑色鱼尾裙、中式秀禾、复古西装', 'Outfit direction: satin white dress, black mermaid gown, Chinese xiuhe, vintage suit...')" maxlength="300" />
                <textarea v-model="sceneText" class="text-area" :placeholder="tr('场景方向：如白色画廊、古堡阳台、海边日落、中式庭院', 'Scene direction: white gallery, castle balcony, sunset beach, Chinese courtyard...')" maxlength="300" />
              </view>
            </view>

            <view class="style-section-head">
              <text>{{ stylePanelTitle }}</text>
              <text>{{ stylePanelNote }}</text>
            </view>
            <view class="style-grid">
              <view v-for="card in styleCards" :key="card.familyKey" class="style-card" :class="{ active: selectedStyleFamily === card.familyKey }" @tap="selectedStyleFamily = card.familyKey">
                <view class="style-image-frame">
                  <image :src="card.imageUrl" class="style-image" mode="aspectFit" />
                </view>
                <view class="style-copy">
                  <text class="style-title heading-serif">{{ card.title }}</text>
                  <text class="style-subtitle">{{ card.subtitle }}</text>
                </view>
              </view>
            </view>

            <view class="style-section-head reference-head">
              <text>{{ tr('参考图', 'Reference Images') }}</text>
              <text>{{ tr('当前私有资产合同仅接收人物照片；场景和服装请先使用文字描述。', 'The current private-asset contract accepts portraits only; use text for scene and outfit direction.') }}</text>
            </view>
          </view>
        </view>

        <view class="side-col">
          <view class="summary-card">
            <image :src="summaryImageUrl" class="summary-image" mode="aspectFill" />
            <view class="summary-body">
              <text class="summary-kicker">{{ tr('提交检查', 'Submit Checklist') }}</text>
              <text class="summary-title heading-serif">{{ readinessTitle }}</text>
              <text class="summary-subtitle">{{ readinessSubtitle }}</text>
              <view class="tag-row">
                <view class="tag">{{ outputModeLabel }}</view>
                <view class="tag subtle">{{ tr('3:4 竖版主成片', '3:4 portrait master') }}</view>
                <view class="tag subtle">{{ templateStateLabel }}</view>
              </view>
              <view class="summary-block">
                <text class="summary-block-title">{{ tr('增强项说明', 'Enhancer Note') }}</text>
                <text class="summary-block-text">{{ summaryTip }}</text>
              </view>
              <view class="summary-checklist">
                <view class="check-row done">
                  <view class="check-dot"></view>
                  <text>{{ outputModeLabel }}</text>
                </view>
                <view class="check-row" :class="{ done: portraitRequirementMet }">
                  <view class="check-dot"></view>
                  <text>{{ portraitRequirementText }}</text>
                </view>
                <view class="check-row" :class="{ done: selectedStyleFamily || hasDirectionText }">
                  <view class="check-dot"></view>
                  <text>{{ enhancerStateText }}</text>
                </view>
              </view>
              <view class="summary-block">
                <text class="summary-block-title">{{ tr('所需积分', 'Credits Required') }}</text>
                <text class="credit-value">{{ generationCost }}</text>
              </view>
              <button class="btn btn-primary create-btn shadow-glow" :disabled="!canSubmit || submitting" @tap="submitCreate">
                {{ submitting ? tr('正在提交…', 'Submitting...') : tr('照片齐了，开始生成', 'Generate When Ready') }}
              </button>
              <LegalConsentInline v-model="legalAccepted" mode="generate" />
            </view>
          </view>
        </view>
      </view>
      </template>
    </view>

    <LegalFooter />
    <PaymentModal v-if="billingAvailable" :visible="showPaymentModal" @close="showPaymentModal = false" @purchase-complete="onPurchaseComplete" />
  </view>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { onLoad } from '@dcloudio/uni-app';
import NavBar from '../../components/NavBar.vue';
import PaymentModal from '../../components/PaymentModal.vue';
import LegalConsentInline from '../../components/LegalConsentInline.vue';
import LegalFooter from '../../components/LegalFooter.vue';
import {
  buildBaseCreatePayload,
  isTemplateCategoryAllowedForMode,
  type GenerationMode,
} from '../../contracts/create';
import type { UploadBatchResponse } from '../../contracts/order';
import { useI18nStore } from '../../stores/i18n';
import { useOrderStore } from '../../stores/order';
import { useOpsStore } from '../../stores/ops';
import { type Template, getLocalizedTemplateMarketingSubtitle, getLocalizedTemplateTitle, getTemplateFamilyKey, useTemplateStore } from '../../stores/template';
import { resolvePublicUrl, uploadFile, type ApiError } from '../../utils/api';
import { ensureSession } from '../../utils/auth';
import { runLocalSmartInputCheck, type SmartInputVerdict } from '../../utils/local_smart_input';
import { trackEvent } from '../../utils/analytics';

type UploadQuality = {
  quality_score: number;
  quality_level: 'good' | 'warning' | 'poor';
  reasons: string[];
  risk_flags: string[];
  metrics: Record<string, number>;
};
type PortraitSlot = { localPath: string; uploadedAssetId: string; uploadQuality?: UploadQuality | null };

const STYLE_ORDER = ['chn_xiuhe', 'korean_minimal', 'royal_castle', 'old_money', 'gothic_romance', 'beach_sunset', 'hk_retro', 'twilight_forest', 'japanese_shiromuku', 'cyberpunk_city', 'school_days', 'classic_bw', 'golden_vintage_studio_8090', 'golden_chinese_courtyard', 'golden_modern_remake'];
const GOLDEN_STYLE_FAMILIES = ['golden_vintage_studio_8090', 'golden_chinese_courtyard', 'golden_modern_remake'];
const STYLE_SUBTITLE: Record<string, { zh: string; en: string }> = {
  chn_xiuhe: { zh: '传统礼服与中式庭院的庄重仪式感', en: 'Ceremonial Chinese bridal styling with courtyard texture' },
  korean_minimal: { zh: '干净构图、柔和光线与极简高级感', en: 'Clean composition and soft editorial minimalism' },
  royal_castle: { zh: '高定礼服与古堡叙事的电影感婚纱', en: 'Cinematic castle bridal styling with couture drama' },
  old_money: { zh: '克制、优雅、园林感的静奢婚纱风格', en: 'Quiet luxury with estate mood and understated elegance' },
  gothic_romance: { zh: '暗色礼服、戏剧光影与神秘氛围', en: 'Dark bridal drama with candlelight and gothic atmosphere' },
  beach_sunset: { zh: '海风、日落与自然亲密感', en: 'Relaxed intimacy by the sea at golden hour' },
  hk_retro: { zh: '霓虹街景与复古电影海报感', en: 'Neon street narrative with retro Hong Kong energy' },
  twilight_forest: { zh: '森林雾气与自然叙事', en: 'Dreamy woodland storytelling with mist and depth' },
  japanese_shiromuku: { zh: '和风庭院与静谧礼仪感', en: 'Serene Japanese ceremony styling' },
  cyberpunk_city: { zh: '未来灯光、都市霓虹与先锋表达', en: 'Futuristic city lights and avant-garde bridal styling' },
  school_days: { zh: '青春教室感与轻叙事氛围', en: 'Youthful classroom nostalgia' },
  classic_bw: { zh: '经典影楼感与纪实优雅', en: 'Timeless monochrome portraiture' },
  golden_vintage_studio_8090: { zh: '80/90 年代影楼质感的纪念重塑', en: 'Anniversary remake with 80s/90s studio nostalgia' },
  golden_chinese_courtyard: { zh: '中式庭院氛围中的长辈纪念合照', en: 'Warm courtyard keepsake for parents and elders' },
  golden_modern_remake: { zh: '简洁柔光的现代纪念翻拍', en: 'Modern anniversary remake with soft premium lighting' },
};

const templateStore = useTemplateStore();
const orderStore = useOrderStore();
const i18nStore = useI18nStore();
const opsStore = useOpsStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);
const creationAvailable = computed(() => opsStore.creationAvailable);
const billingAvailable = computed(() => opsStore.billingAvailable);

const navBarRef = ref<InstanceType<typeof NavBar> | null>(null);
const showPaymentModal = ref(false);
const legalAccepted = ref(false);
const submitting = ref(false);
const routeQuery = ref<Record<string, string>>({});

const generationMode = ref<GenerationMode>('single');
const selectedStyleFamily = ref('');
const portraitSlots = ref<PortraitSlot[]>([
  { localPath: '', uploadedAssetId: '' },
  { localPath: '', uploadedAssetId: '' },
]);
const globalStyleText = ref('');
const outfitText = ref('');
const sceneText = ref('');
const isGoldenAnniversaryMode = computed(() => generationMode.value === 'golden_anniversary');
const portraitIndexes = computed(() => (generationMode.value === 'single' ? [0] : [0, 1]));
const portraitHint = computed(() => generationMode.value === 'single'
  ? tr('上传一张清晰正脸照片，建议光线自然、五官无遮挡。', 'Upload one clear portrait with natural light and an unobstructed face.')
  : isGoldenAnniversaryMode.value
    ? tr('上传两张父母或长辈的清晰照片，系统会使用金婚重塑模板生成纪念合照。', 'Upload two clear portraits of parents or elders for a Golden Anniversary remake.')
    : tr('在同一设备上传两张照片，适合情侣、夫妻或纪念照双人生成。', 'Upload two portraits on one device for couple or anniversary portraits.'));
const primaryPhotoInstruction = computed(() => {
  if (generationMode.value === 'single') {
    return tr('主流程只需要 1 张人物照片。上传后可以直接生成；想控制服装或场景时，再补充下方增强项。', 'The core flow only needs 1 portrait. After upload, you can generate directly; add enhancers only when you want outfit or scene control.');
  }
  if (isGoldenAnniversaryMode.value) {
    return tr('金婚重塑需要 2 张人物照片。系统会默认选择纪念合照模板，你也可以在下方切换 80/90 影楼、中式庭院或现代翻拍。', 'Golden Anniversary remake needs 2 portraits. A legacy template is selected by default, and you can switch between studio, courtyard, or modern remake below.');
  }
  return tr('主流程需要 2 张人物照片。两张照片齐了即可生成；人数不会被文字或模板改掉。', 'The core flow needs 2 portraits. Once both are uploaded, generation is ready; text or templates cannot change the subject count.');
});
const selectedTemplate = computed<Template | null>(() => {
  if (!selectedStyleFamily.value) return null;
  return styleCards.value.find((item) => item.familyKey === selectedStyleFamily.value)?.template || null;
});
const styleCards = computed(() => {
  const ordered = templateStore.templates.slice().sort((a, b) => {
    const ar = STYLE_ORDER.indexOf(getTemplateFamilyKey(a));
    const br = STYLE_ORDER.indexOf(getTemplateFamilyKey(b));
    return (ar < 0 ? 999 : ar) - (br < 0 ? 999 : br);
  });
  const seen = new Set<string>();
  return ordered.flatMap((item) => {
    const familyKey = getTemplateFamilyKey(item);
    if (!familyKey || familyKey === 'custom_mode' || familyKey === 'custom' || seen.has(familyKey)) return [];
    if (!isTemplateCategoryAllowedForMode(item.category, generationMode.value)) return [];
    seen.add(familyKey);
    const actual = item;
    return [{
      familyKey,
      template: actual,
      title: getLocalizedTemplateTitle(actual, i18nStore.locale),
      subtitle: STYLE_SUBTITLE[familyKey] ? (i18nStore.locale === 'zh' ? STYLE_SUBTITLE[familyKey].zh : STYLE_SUBTITLE[familyKey].en) : getLocalizedTemplateMarketingSubtitle(actual, i18nStore.locale),
      imageUrl: resolvePublicUrl(actual.image_url),
    }];
  });
});
const hasDirectionText = computed(() => !!(globalStyleText.value.trim() || outfitText.value.trim() || sceneText.value.trim()));
const summaryImageUrl = computed(() => resolvePublicUrl(selectedTemplate.value?.image_url || '/style-previews/custom_mode.jpg'));
const directionPanelTitle = computed(() => tr('增强创作方向（可选）', 'Enhance Direction (optional)'));
const directionPanelDesc = computed(() => tr(
  '已选择稳定参考风格；可用文字补充服装、场景、镜头和氛围。',
  'A stable reference style is selected; use text to refine outfit, scene, lens, and mood.',
));
const stylePanelTitle = computed(() => {
  if (generationMode.value === 'single') return tr('参考风格（单人）', 'Reference Styles (solo)');
  if (isGoldenAnniversaryMode.value) return tr('金婚重塑模板', 'Golden Anniversary Templates');
  return tr('参考风格（双人）', 'Reference Styles (couple)');
});
const stylePanelNote = computed(() => isGoldenAnniversaryMode.value
  ? tr('必选：用于父母/长辈纪念合照的专项模板', 'Required: legacy templates for parents and elders')
  : tr('系统默认选择稳定风格，你也可以切换', 'A stable style is selected by default and can be changed'));
const outputModeLabel = computed(() => generationMode.value === 'single'
  ? tr('单人输出', 'Single Output')
  : isGoldenAnniversaryMode.value
    ? tr('金婚重塑', 'Golden Anniversary')
    : tr('双人同机', 'Couple Local'));
const templateStateLabel = computed(() => tr('已选择稳定模板', 'Stable Style Selected'));
const sceneControlLabel = computed(() => {
  if (sceneText.value.trim() || globalStyleText.value.trim()) return tr('文字主控', 'Text control');
  if (selectedStyleFamily.value) return tr('模板兜底', 'Template fallback');
  return tr('随机兜底', 'Random fallback');
});
const outfitControlLabel = computed(() => {
  if (outfitText.value.trim() || globalStyleText.value.trim()) return tr('文字主控', 'Text control');
  if (selectedStyleFamily.value) return tr('模板兜底', 'Template fallback');
  return tr('随机兜底', 'Random fallback');
});
const priorityGuideItems = computed(() => [
  {
    key: 'identity',
    index: '01',
    title: tr('身份照片', 'Identity upload'),
    copy: tr('锁定人脸、年龄感和人物一致性。', 'Locks face, age impression, and identity consistency.'),
    active: portraitSlots.value.some((slot) => !!slot.localPath),
  },
  {
    key: 'mode',
    index: '02',
    title: tr('单人/双人/金婚', 'Single/Couple/Golden'),
    copy: tr('锁定输出人数，单人不会变双人。', 'Locks subject count, so solo cannot become couple.'),
    active: true,
  },
  {
    key: 'text',
    index: '03',
    title: tr('文字创作', 'Text direction'),
    copy: tr('无参考图时主控；有参考图时微调氛围、镜头和布光。', 'Controls when no reference exists; otherwise refines mood, lens, and lighting.'),
    active: hasDirectionText.value,
  },
  {
    key: 'preset',
    index: '04',
    title: tr('稳定模板', 'Stable preset'),
    copy: tr('系统默认选中，也可以手动切换。', 'Selected by default and can be changed manually.'),
    active: !!selectedStyleFamily.value,
  },
]);
const generationCost = computed(() => {
  return generationMode.value === 'single' ? 2 : 3;
});
const portraitRequirementMet = computed(() => {
  if (generationMode.value === 'single') return !!portraitSlots.value[0].localPath;
  return !!portraitSlots.value[0].localPath && !!portraitSlots.value[1].localPath;
});
const portraitRequirementText = computed(() => {
  if (generationMode.value === 'single') {
    return portraitRequirementMet.value ? tr('已上传 1 张人物照片', '1 portrait uploaded') : tr('需要上传 1 张人物照片', 'Upload 1 portrait');
  }
  return portraitRequirementMet.value ? tr('已上传 2 张人物照片', '2 portraits uploaded') : tr('需要上传 2 张人物照片', 'Upload 2 portraits');
});
const readinessTitle = computed(() => portraitRequirementMet.value
  ? tr('主流程已就绪', 'Core Flow Ready')
  : tr('先完成照片上传', 'Upload Portraits First'));
const readinessSubtitle = computed(() => portraitRequirementMet.value
  ? tr('现在可以直接生成。下方增强项会作为加分信息，不影响主流程提交。', 'You can generate now. Enhancers below are helpful extras and do not block the core submission.')
  : primaryPhotoInstruction.value);
const enhancerStateText = computed(() => {
  if (selectedStyleFamily.value) return tr('已选择参考风格', 'Reference style selected');
  if (hasDirectionText.value) return tr('已填写服装/场景方向', 'Outfit or scene direction added');
  return tr('增强项未填写，可跳过', 'No enhancers added, can skip');
});
const summaryTip = computed(() => selectedTemplate.value
  ? tr('身份和人数始终锁定。文字补充场景和服装，模板只补未指定细节。', 'Identity and subject count stay locked. Text refines scene and outfit; the template fills unspecified details.')
  : tr('正在加载稳定模板。', 'Loading a stable template.'));
const canSubmit = computed(() => {
  if (!legalAccepted.value || submitting.value) return false;
  return portraitRequirementMet.value && selectedTemplate.value !== null;
});
const modeOptions = computed(() => [
  { value: 'single' as GenerationMode, title: tr('单人生成', 'Single'), desc: tr('一张照片，直接生成个人婚纱风格', 'One portrait, direct solo bridal output') },
  { value: 'couple_local' as GenerationMode, title: tr('双人同机', 'Couple Local'), desc: tr('同一设备上传两张照片，立即合成双人作品', 'Upload two portraits on one device') },
  { value: 'golden_anniversary' as GenerationMode, title: tr('金婚重塑', 'Golden Anniversary'), desc: tr('父母/长辈纪念合照，默认使用年代感模板', 'Legacy portraits for parents and elders') },
]);

function currentQuery(): Record<string, string> {
  const pages = getCurrentPages();
  const pageOptions = ((pages[pages.length - 1] as any)?.options || {}) as Record<string, string>;
  let urlOptions: Record<string, string> = {};
  if (typeof window !== 'undefined') {
    const queryText = window.location.search || (window.location.hash.includes('?') ? `?${window.location.hash.split('?')[1]}` : '');
    urlOptions = Object.fromEntries(new URLSearchParams(queryText).entries());
  }
  return { ...pageOptions, ...urlOptions, ...routeQuery.value };
}
function setMode(mode: GenerationMode) {
  if (generationMode.value === mode) return;
  generationMode.value = mode;
  portraitSlots.value[1] = { localPath: '', uploadedAssetId: '' };
  if (selectedStyleFamily.value && !hasStyleForMode(selectedStyleFamily.value, mode)) {
    selectedStyleFamily.value = '';
  }
  selectDefaultStyleForMode();
}
function hasStyleForMode(familyKey: string, mode: GenerationMode) {
  return templateStore.templates.some((item) => {
    if (getTemplateFamilyKey(item) !== familyKey) return false;
    return isTemplateCategoryAllowedForMode(item.category, mode);
  });
}
function selectDefaultStyleForMode() {
  if (selectedStyleFamily.value && hasStyleForMode(selectedStyleFamily.value, generationMode.value)) return;
  const preferredFamily = generationMode.value === 'golden_anniversary'
    ? GOLDEN_STYLE_FAMILIES[0]
    : 'korean_minimal';
  const preferred = styleCards.value.find((item) => item.familyKey === preferredFamily);
  const fallback = styleCards.value[0];
  selectedStyleFamily.value = preferred?.familyKey || fallback?.familyKey || '';
}
function applyRouteQuery(query: Record<string, string>) {
  const mode = String(query.mode || '').toLowerCase();
  if (mode === 'couple' || mode === 'couple_local') generationMode.value = 'couple_local';
  else if (mode === 'golden' || mode === 'golden_anniversary' || mode === 'vintage' || mode === 'legacy') generationMode.value = 'golden_anniversary';

  const requestedId = String(query.id || '').trim();
  if (requestedId) {
    const matched = templateStore.templates.find((item) => item.id === requestedId);
    if (matched) {
      if (String(matched.category || '').toLowerCase() === 'vintage') generationMode.value = 'golden_anniversary';
      selectedStyleFamily.value = getTemplateFamilyKey(matched);
    }
  }

  selectDefaultStyleForMode();
}
function applyStoredTemplateIntent() {
  if (selectedStyleFamily.value || !templateStore.selectedTemplate) return;
  const stored = templateStore.selectedTemplate;
  if (String(stored.category || '').toLowerCase() === 'vintage') generationMode.value = 'golden_anniversary';
  selectedStyleFamily.value = getTemplateFamilyKey(stored);
  selectDefaultStyleForMode();
}
function serializeUploadQuality(verdict: SmartInputVerdict): UploadQuality {
  return {
    quality_score: Math.max(0, Math.min(100, Math.round(Number(verdict.quality_score || 0)))),
    quality_level: verdict.quality_level || 'good',
    reasons: (verdict.reasons || []).map(String).filter(Boolean).slice(0, 12),
    risk_flags: (verdict.risk_flags || []).map(String).filter(Boolean).slice(0, 12),
    metrics: Object.fromEntries(
      Object.entries(verdict.metrics || {})
        .filter(([, value]) => Number.isFinite(Number(value)))
        .slice(0, 20)
        .map(([key, value]) => [key, Number(value)])
    ),
  };
}
function portraitLabel(index: number) {
  if (generationMode.value === 'single') return tr('主人像', 'Main Portrait');
  if (isGoldenAnniversaryMode.value) return index === 0 ? tr('长辈 1', 'Elder 1') : tr('长辈 2', 'Elder 2');
  return index === 0 ? tr('人物 1', 'Portrait 1') : tr('人物 2', 'Portrait 2');
}
function portraitCta(index: number) {
  if (generationMode.value === 'single') return tr('上传人物照片', 'Upload portrait');
  if (isGoldenAnniversaryMode.value) return index === 0
    ? tr('上传第一位长辈照片', 'Upload first elder portrait')
    : tr('上传第二位长辈照片', 'Upload second elder portrait');
  return index === 0 ? tr('上传第一张照片', 'Upload first portrait') : tr('上传第二张照片', 'Upload second portrait');
}
async function pickLocalImage() {
  if (!creationAvailable.value) return { localPath: '', uploadQuality: null };
  const res = await uni.chooseImage({ count: 1, sizeType: ['original'], sourceType: ['album', 'camera'] });
  const localPath = res.tempFilePaths?.[0];
  if (!localPath) return { localPath: '', uploadQuality: null };
  const verdict = await runLocalSmartInputCheck(localPath);
  const uploadQuality = serializeUploadQuality(verdict);
  if (verdict.quality_level !== 'good') {
    const score = Math.max(0, Math.min(100, Number(verdict.quality_score || 0)));
    const confirmed = await new Promise<boolean>((resolve) => {
      uni.showModal({
        title: tr('照片可继续使用', 'Photo Can Continue'),
        content: tr(
          `这张照片可能会影响人物一致性或成片清晰度，建议更换更清晰的正脸/半身照片。你也可以继续尝试生成。（评分 ${score}）`,
          `This photo may affect likeness or final clarity. A clearer front-facing or upper-body portrait is recommended, but you can continue. (Score ${score})`
        ),
        confirmText: tr('继续生成', 'Continue'),
        cancelText: tr('重新上传', 'Re-upload'),
        success: (modalRes) => resolve(!!modalRes.confirm),
        fail: () => resolve(true),
      });
    });
    if (!confirmed) return { localPath: '', uploadQuality: null };
  }
  return { localPath, uploadQuality };
}
async function pickPortrait(index: number) {
  try {
    const { localPath, uploadQuality } = await pickLocalImage();
    if (!localPath) return;
    portraitSlots.value[index] = { localPath, uploadedAssetId: '', uploadQuality };
  } catch (error) {
    console.error(error);
  }
}
function clearPortrait(index: number) {
  portraitSlots.value[index] = { localPath: '', uploadedAssetId: '', uploadQuality: null };
}
function resolveSeedTemplate(): Template {
  if (!selectedTemplate.value) throw new Error('A stable template is required');
  return selectedTemplate.value;
}
async function uploadLocalAsset(localPath: string, uploadQuality?: UploadQuality | null) {
  const startedAt = Date.now();
  await trackEvent({
    eventType: 'asset_upload_started',
    sourcePage: 'create',
    templateId: selectedTemplate.value?.id || null,
  });
  const result = await uploadFile('/media/uploads', localPath, 'file') as UploadBatchResponse;
  const assetId = String(result.assets?.[0]?.asset_id || '').trim();
  if (!assetId || result.assets.length !== 1) {
    throw new Error('Private upload response is invalid');
  }
  await trackEvent({
    eventType: 'asset_upload_completed',
    sourcePage: 'create',
    templateId: selectedTemplate.value?.id || null,
    meta: {
      duration_ms: Date.now() - startedAt,
      has_asset: true,
      quality_score: uploadQuality?.quality_score ?? null,
      quality_level: uploadQuality?.quality_level ?? null,
    },
  });
  if (uploadQuality) {
    await trackEvent({
      eventType: 'asset_upload_quality_scored',
      sourcePage: 'create',
      templateId: selectedTemplate.value?.id || null,
      meta: uploadQuality,
    });
    if (uploadQuality.quality_level !== 'good') {
      await trackEvent({
        eventType: 'asset_upload_quality_warning',
        sourcePage: 'create',
        templateId: selectedTemplate.value?.id || null,
        meta: uploadQuality,
      });
      if (uploadQuality.quality_level === 'poor') {
        await trackEvent({
          eventType: 'asset_upload_quality_poor',
          sourcePage: 'create',
          templateId: selectedTemplate.value?.id || null,
          meta: uploadQuality,
        });
      }
    }
  }
  return assetId;
}
async function ensurePortraitUploaded(index: number) {
  const slot = portraitSlots.value[index];
  if (!slot.localPath) return '';
  if (slot.uploadedAssetId) return slot.uploadedAssetId;
  const assetId = await uploadLocalAsset(slot.localPath, slot.uploadQuality);
  portraitSlots.value[index].uploadedAssetId = assetId;
  return assetId;
}
async function submitCreate() {
  if (!creationAvailable.value) return;
  if (!await ensureSession()) {
    uni.showModal({
      title: i18nStore.locale === 'zh' ? '需要登录' : 'Sign In Required',
      content: i18nStore.locale === 'zh' ? '使用 Google 登录后可获得免费试用积分，立即开始生成。' : 'Sign in with Google to get free trial credits and start generating.',
      confirmText: i18nStore.locale === 'zh' ? '去登录' : 'Sign In',
      cancelText: i18nStore.locale === 'zh' ? '取消' : 'Cancel',
      success: (res) => {
        if (res.confirm) uni.navigateTo({ url: '/pages/auth/login' });
      },
    });
    return;
  }
  if (!legalAccepted.value) {
    uni.showToast({ title: tr('请先勾选隐私政策与服务条款', 'Please accept the Privacy Policy and Terms first'), icon: 'none' });
    return;
  }

  // Cost confirmation
  const cost = generationCost.value;
  const confirmed = await new Promise<boolean>((resolve) => {
    uni.showModal({
      title: i18nStore.locale === 'zh' ? '确认生成' : 'Confirm Generation',
      content: i18nStore.locale === 'zh'
        ? `本次生成将消耗 ${cost} 积分。确认开始？`
        : `This generation will cost ${cost} credits. Continue?`,
      confirmText: i18nStore.locale === 'zh' ? '开始生成' : 'Generate',
      cancelText: i18nStore.locale === 'zh' ? '取消' : 'Cancel',
      success: (res) => resolve(res.confirm),
      fail: () => resolve(false),
    });
  });
  if (!confirmed) return;

  submitting.value = true;
  try {
    const seed = resolveSeedTemplate();
    const assetIds: string[] = [];
    if (generationMode.value === 'single') {
      assetIds.push(await ensurePortraitUploaded(0));
    } else {
      assetIds.push(await ensurePortraitUploaded(0), await ensurePortraitUploaded(1));
    }
    const payload = buildBaseCreatePayload({
      templateId: seed.id,
      assetIds,
      globalStyleText: globalStyleText.value,
      sceneText: sceneText.value,
      outfitText: outfitText.value,
    });
    const { accepted, order } = await orderStore.createOrder(payload);
    await trackEvent({
      eventType: 'generation_order_created',
      sourcePage: 'create',
      templateId: seed.id,
      meta: {
        generation_mode: generationMode.value,
        subject_count: assetIds.length,
        director_mode: payload.director_mode === true,
        credits_cost: generationCost.value,
        order_id: accepted.order_id,
      },
    });
    navBarRef.value?.refreshBalance();
    uni.navigateTo({ url: `/pages/preview/preview?id=${order.id}` });
  } catch (error: any) {
    const apiError = error as ApiError;
    if (apiError.statusCode === 402 && billingAvailable.value) {
      showPaymentModal.value = true;
    } else if (apiError.statusCode === 409 && apiError.detail?.existing_order_id) {
      uni.showToast({
        title: apiError.message || tr('已有任务正在生成', 'A generation is already running'),
        icon: 'none',
      });
      uni.navigateTo({ url: `/pages/preview/preview?id=${apiError.detail.existing_order_id}` });
    } else {
      uni.showToast({ title: error.message || tr('创建任务失败，请稍后重试', 'Failed to create the task'), icon: 'none' });
    }
  } finally {
    submitting.value = false;
  }
}
function onPurchaseComplete() {
  showPaymentModal.value = false;
  navBarRef.value?.refreshBalance();
}

function openPaymentModal() {
  if (!billingAvailable.value) return;
  showPaymentModal.value = true;
}

function goHome() {
  uni.reLaunch({ url: '/pages/index/index' });
}

onLoad((query = {}) => {
  routeQuery.value = Object.fromEntries(
    Object.entries(query as Record<string, unknown>).map(([key, value]) => [key, String(value || '')])
  );
});

onMounted(async () => {
  await opsStore.fetchPublicConfig();
  if (!creationAvailable.value) return;
  if (!templateStore.templates.length) await templateStore.fetchTemplates();
  applyRouteQuery(currentQuery());
  applyStoredTemplateIntent();
  selectDefaultStyleForMode();
  setTimeout(() => applyRouteQuery(currentQuery()), 0);
});
</script>

<style lang="scss" scoped>
.create-page {
  min-height: 100vh;
  background: #f5f7f6;
}

.create-shell {
  width: min(1360px, calc(100% - 48px));
  margin: 0 auto;
  padding: 32px 0 80px;
}

.capability-unavailable {
  max-width: 720px;
  margin: 72px auto;
  padding: 44px;
  border: 1px solid rgba(17, 106, 96, 0.2);
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 20px 50px rgba(23, 25, 31, 0.07);
  text-align: center;
}

.unavailable-kicker,
.unavailable-title,
.unavailable-copy {
  display: block;
}

.unavailable-kicker {
  color: #116a60;
  font-size: 12px;
  font-weight: 900;
}

.unavailable-title {
  margin-top: 12px;
  color: #17191f;
  font-size: 46px;
  line-height: 1.05;
}

.unavailable-copy {
  margin-top: 18px;
  color: #4c5360;
  font-size: 15px;
  line-height: 1.8;
}

.unavailable-action {
  margin-top: 24px;
}

.hero-card,
.panel,
.summary-card {
  border: 1px solid rgba(32, 43, 62, 0.1);
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 16px 44px rgba(23, 25, 31, 0.055);
}

.hero-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 28px;
  padding: 28px;
  margin-bottom: 22px;
  align-items: start;
  background:
    linear-gradient(135deg, #ffffff 0%, #ffffff 58%, #f0f6f4 100%);
}

.hero-main {
  min-width: 0;
}

.hero-aside {
  height: 100%;
  padding: 20px;
  border-radius: 8px;
  border: 1px solid rgba(17, 106, 96, 0.18);
  background: rgba(243, 250, 248, 0.9);
}

.aside-title,
.aside-copy {
  display: block;
  color: #4c5360;
  font-size: 12px;
  line-height: 1.7;
}

.aside-title {
  margin-bottom: 10px;
  color: #116a60;
  font-weight: 900;
}

.aside-mode {
  display: block;
  margin-bottom: 10px;
  color: #17191f;
  font-size: 24px;
  font-weight: 900;
}

.flow-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 18px;
}

.flow-step {
  min-height: 38px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid rgba(17, 106, 96, 0.18);
  background: #ffffff;
  color: #116a60;
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  font-weight: 900;
}

.flow-step.primary {
  border-color: rgba(17, 106, 96, 0.32);
  background: #eaf6f3;
  color: #0b5e55;
}

.flow-step.subtle {
  border-color: #dde1e8;
  color: #4c5360;
  background: #f7f8fa;
}

.hero-kicker,
.summary-kicker {
  display: block;
  margin-bottom: 12px;
  color: #116a60;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
}

.hero-title {
  display: block;
  max-width: 860px;
  margin-bottom: 12px;
  color: #17191f;
  font-size: 44px;
  line-height: 1.08;
  text-wrap: balance;
}

.hero-subtitle,
.panel-desc,
.style-subtitle,
.summary-subtitle,
.summary-block-text,
.mode-desc {
  display: block;
  color: #4c5360;
  font-size: 13px;
  line-height: 1.75;
}

.mode-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px;
}

.mode-card {
  min-height: 104px;
  padding: 16px;
  border-radius: 8px;
  border: 1px solid rgba(32, 43, 62, 0.1);
  background: #fbfcfd;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  cursor: pointer;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}

.mode-card.active,
.style-card.active,
.free-mode-card.active {
  border-color: rgba(17, 106, 96, 0.46);
  background: #f3faf8;
  box-shadow: 0 14px 34px rgba(17, 106, 96, 0.08);
}

.mode-title,
.field-label,
.summary-block-title {
  display: block;
  margin-bottom: 8px;
  color: #17191f;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
}

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 24px;
  align-items: start;
}

.main-col {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.side-col {
  position: sticky;
  top: 88px;
}

.panel {
  padding: 24px;
}

.primary-panel {
  border-color: rgba(17, 106, 96, 0.24);
  background: linear-gradient(180deg, #ffffff 0%, #fbfdfc 100%);
}

.enhancer-panel {
  background: #fcfcfd;
}

.panel-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.badge {
  padding: 8px 12px;
  border-radius: 8px;
  background: #17191f;
  color: #ffffff;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0;
}

.badge.required {
  background: #0f1720;
}

.badge.optional {
  background: #f3faf8;
  color: #116a60;
  border: 1px solid rgba(17, 106, 96, 0.22);
}

.panel-title {
  color: #17191f;
  font-size: 28px;
}

.primary-guidance {
  margin: 16px 0;
  padding: 14px 16px;
  border-radius: 8px;
  border: 1px solid rgba(17, 106, 96, 0.2);
  background: #f3faf8;
}

.guidance-title,
.guidance-copy {
  display: block;
}

.guidance-title {
  margin-bottom: 4px;
  color: #0b5e55;
  font-size: 12px;
  font-weight: 900;
}

.guidance-copy {
  color: #38414d;
  font-size: 13px;
  line-height: 1.7;
}

.collapse-toggle {
  margin-left: auto;
  color: #116a60;
  font-size: 12px;
  font-weight: 800;
}

.portrait-grid,
.dual-inputs,
.reference-grid,
.style-grid {
  display: grid;
  gap: 16px;
}

.portrait-grid {
  grid-template-columns: 1fr 1fr;
}

.portrait-grid.single {
  grid-template-columns: 1fr;
}

.dual-inputs {
  grid-template-columns: 1fr 1fr;
}

.reference-grid {
  grid-template-columns: 1fr 1fr;
  margin-top: 16px;
}

.style-grid {
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
}

.free-mode-card {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  gap: 18px;
  padding: 14px;
  margin: 16px 0;
  border-radius: 8px;
  border: 1px solid #dde1e8;
  background: #ffffff;
}

.free-mode-card.compact {
  display: block;
  grid-template-columns: none;
  margin-top: 12px;
  background: #ffffff;
}

.free-mode-image {
  width: 100%;
  aspect-ratio: 4 / 3;
  border-radius: 8px;
  object-fit: cover;
}

.free-mode-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.free-mode-label {
  display: block;
  margin-bottom: 8px;
  color: #116a60;
  font-size: 12px;
  font-weight: 900;
}

.free-mode-title {
  display: block;
  margin-bottom: 8px;
  color: #17191f;
  font-size: 28px;
}

.free-mode-desc {
  color: #4c5360;
  font-size: 13px;
  line-height: 1.7;
}

.style-section-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin: 18px 0 12px;
  color: #4c5360;
  font-size: 12px;
  font-weight: 800;
}

.style-section-head text:first-child {
  color: #17191f;
  font-size: 14px;
  font-weight: 900;
}

.direction-card {
  margin-top: 16px;
  padding: 16px;
  border-radius: 8px;
  border: 1px solid #dde1e8;
  background: #ffffff;
}

.priority-guide {
  margin-top: 16px;
  padding: 16px;
  border-radius: 8px;
  border: 1px solid rgba(17, 106, 96, 0.18);
  background: #f7fbfa;
}

.priority-guide-head,
.priority-current {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
}

.priority-guide-head text:first-child,
.priority-current text:first-child {
  color: #0b5e55;
  font-size: 12px;
  font-weight: 900;
}

.priority-guide-head text:last-child {
  color: #657080;
  font-size: 12px;
  font-weight: 800;
}

.priority-steps {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.priority-step {
  min-height: 118px;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid #dde1e8;
  background: #ffffff;
}

.priority-step.active {
  border-color: rgba(17, 106, 96, 0.34);
  background: #eff8f6;
}

.priority-index,
.priority-title,
.priority-copy {
  display: block;
}

.priority-index {
  margin-bottom: 8px;
  color: #116a60;
  font-size: 11px;
  font-weight: 900;
}

.priority-title {
  color: #17191f;
  font-size: 13px;
  font-weight: 900;
}

.priority-copy {
  margin-top: 6px;
  color: #4c5360;
  font-size: 12px;
  line-height: 1.55;
}

.priority-current {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid rgba(17, 106, 96, 0.16);
  color: #38414d;
  font-size: 12px;
  font-weight: 800;
}

.ratio-guide {
  margin-top: 12px;
  padding: 14px 16px;
  border-radius: 8px;
  border: 1px solid rgba(17, 106, 96, 0.18);
  background: #ffffff;
}

.ratio-title,
.ratio-copy {
  display: block;
}

.ratio-title {
  color: #17191f;
  font-size: 13px;
  font-weight: 900;
}

.ratio-copy {
  margin-top: 6px;
  color: #4c5360;
  font-size: 12px;
  line-height: 1.55;
}

.direction-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.direction-kicker,
.direction-title {
  display: block;
}

.direction-kicker {
  margin-bottom: 4px;
  color: #116a60;
  font-size: 12px;
  font-weight: 900;
}

.direction-title {
  color: #17191f;
  font-size: 16px;
  font-weight: 900;
}

.direction-status {
  min-height: 30px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid #dde1e8;
  background: #f7f8fa;
  color: #4c5360;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 900;
}

.direction-status.active {
  border-color: rgba(17, 106, 96, 0.3);
  background: #eaf6f3;
  color: #0b5e55;
}

.upload-card {
  border-radius: 8px;
  border: 1px solid rgba(32, 43, 62, 0.1);
  background: #fbfcfd;
  padding: 16px;
}

.main-upload-card {
  background: #ffffff;
  border-color: rgba(17, 106, 96, 0.22);
}

.empty-box {
  min-height: 280px;
  border: 1px dashed #aeb6c2;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.primary-empty {
  min-height: 320px;
  border-color: rgba(17, 106, 96, 0.34);
  background:
    linear-gradient(180deg, #fbfffd 0%, #f4faf8 100%);
}

.empty-box.short {
  min-height: 220px;
}

.empty-plus {
  color: #116a60;
  font-size: 38px;
  font-weight: 300;
}

.empty-title {
  color: #17191f;
  font-size: 14px;
  font-weight: 800;
}

.empty-hint {
  max-width: 240px;
  color: #657080;
  font-size: 12px;
  line-height: 1.6;
  text-align: center;
}

.preview-box {
  position: relative;
  overflow: hidden;
  border-radius: 8px;
  aspect-ratio: 4 / 5;
  background: #d9dde3;
}

.preview-box.ref-box {
  aspect-ratio: 4 / 3;
}

.preview-image,
.summary-image {
  width: 100%;
  display: block;
  object-fit: cover;
  object-position: center top;
}

.preview-image {
  height: 100%;
}

.summary-image {
  aspect-ratio: 4 / 5;
}

.style-image-frame {
  width: 100%;
  aspect-ratio: 4 / 5;
  overflow: hidden;
  border-bottom: 1px solid #edf0f4;
  background: #eef1f4;
}

.style-image {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  object-position: center top;
}

.preview-actions {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 12px;
  display: flex;
  gap: 8px;
}

.mini-btn {
  flex: 1;
  height: 40px;
  border-radius: 8px;
  border: 1px solid rgba(221, 225, 232, 0.8);
  background: rgba(255, 255, 255, 0.94);
  color: #17191f;
  font-size: 12px;
  font-weight: 800;
}

.mini-btn.ghost {
  background: rgba(23, 25, 31, 0.82);
  color: #ffffff;
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 36px;
  padding: 0 14px;
  border-radius: 8px;
  border: 1px solid #dde1e8;
  background: #ffffff;
  color: #17191f;
  font-size: 12px;
  font-weight: 900;
}

.tag.subtle {
  background: #f0f3f6;
  color: #4c5360;
}

.style-card {
  overflow: hidden;
  border-radius: 8px;
  border: 1px solid rgba(32, 43, 62, 0.1);
  background: #ffffff;
  box-shadow: 0 14px 34px rgba(23, 25, 31, 0.055);
  cursor: pointer;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}

.style-copy {
  min-height: 112px;
  padding: 14px 14px 16px;
  background: #ffffff;
}

.style-title,
.summary-title {
  display: block;
  margin-bottom: 6px;
  color: #17191f;
}

.style-title {
  font-size: 20px;
  line-height: 1.2;
}

.style-subtitle {
  min-height: 44px;
}

.summary-title {
  font-size: 32px;
  line-height: 1.08;
}

.text-area {
  width: 100%;
  min-height: 128px;
  padding: 16px;
  box-sizing: border-box;
  border-radius: 8px;
  border: 1px solid #dde1e8;
  background: #ffffff;
  color: #17191f;
  font-size: 14px;
  line-height: 1.8;
}

.text-area.large {
  min-height: 110px;
  margin-bottom: 14px;
}

.summary-card {
  overflow: hidden;
  padding: 0;
}

.summary-body {
  padding: 18px 18px 20px;
}

.summary-block {
  padding: 14px 0;
  border-top: 1px solid #edf0f4;
}

.summary-checklist {
  padding: 14px 0;
  border-top: 1px solid #edf0f4;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.check-row {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #4c5360;
  font-size: 12px;
  line-height: 1.5;
}

.check-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  border: 1px solid #aeb6c2;
  background: #ffffff;
}

.check-row.done {
  color: #17191f;
  font-weight: 800;
}

.check-row.done .check-dot {
  border-color: #116a60;
  background: #116a60;
}

.credit-value {
  display: block;
  color: #116a60;
  font-size: 36px;
  font-weight: 900;
}

.create-btn {
  width: 100%;
}

@media (min-width: 961px) {
  .mode-card:hover,
  .style-card:hover,
  .upload-card:hover,
  .free-mode-card:hover {
    border-color: rgba(17, 106, 96, 0.34);
    box-shadow: 0 20px 46px rgba(23, 25, 31, 0.07);
    transform: translateY(-1px);
  }
}

@media (max-width: 1180px) {
  .hero-card,
  .layout {
    grid-template-columns: 1fr;
  }

  .side-col {
    position: static;
  }

  .style-grid {
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  }
}

@media (max-width: 820px) {
  .create-shell {
    width: calc(100% - 28px);
    max-width: 1360px;
    padding-top: 20px;
  }

  .portrait-grid,
  .dual-inputs,
  .reference-grid,
  .style-grid {
    grid-template-columns: 1fr;
  }

  .mode-grid,
  .free-mode-card {
    grid-template-columns: 1fr;
  }

  .hero-title {
    font-size: 34px;
  }

  .panel-title {
    font-size: 26px;
  }

  .hero-card {
    padding: 20px;
    gap: 18px;
  }

  .panel {
    padding: 20px;
  }

  .flow-strip {
    gap: 6px;
  }

  .flow-step {
    flex: 1 1 44%;
    justify-content: center;
  }
}
</style>
