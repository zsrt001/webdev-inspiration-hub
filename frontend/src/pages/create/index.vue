<template>
  <view class="app-container create-page" style="padding-top: 64px;">
    <NavBar ref="navBarRef" @show-payment="showPaymentModal = true" />
    <view class="create-shell">
      <view class="hero-card">
        <view class="hero-main">
          <text class="hero-kicker">{{ tr('AI 婚纱创作台', 'AI Wedding Studio') }}</text>
          <text class="hero-title heading-serif">{{ tr('先定人数，再选自由创作或模板风格', 'Choose the subject count, then start from free direction or a style') }}</text>
          <text class="hero-subtitle">{{ tr('自由模式优先保留你的描述权；模板用于快速锁定画面基调。必填只有人物照片，其余服装、场景、参考图都是增强项。', 'Free direction keeps creative control first; templates quickly anchor the mood. Portraits are required, while outfit, scene, and references are optional enhancers.') }}</text>
          <view class="flow-strip">
            <view class="flow-step">{{ tr('1 输出人数', '1 Subject count') }}</view>
            <view class="flow-step">{{ tr('2 自由/模板', '2 Free or style') }}</view>
            <view class="flow-step">{{ tr('3 上传人物', '3 Upload portraits') }}</view>
            <view class="flow-step subtle">{{ tr('可选增强', 'Optional enhancers') }}</view>
          </view>
        </view>
        <view class="hero-aside">
          <text class="aside-title">{{ tr('当前流程', 'Current Flow') }}</text>
          <text class="aside-mode">{{ outputModeLabel }}</text>
          <text class="aside-copy">{{ tr('切换单人或双人后，下方风格库会自动切换对应示例图。', 'After switching solo or couple mode, the style gallery shows matching preview images.') }}</text>
        </view>
      </view>

      <view class="layout">
        <view class="main-col">
          <view class="panel">
            <view class="panel-head">
              <text class="badge">STEP 01</text>
              <text class="panel-title heading-serif">{{ tr('选择输出人数', 'Choose Subject Count') }}</text>
            </view>
            <text class="panel-desc">{{ tr('这一步决定需要上传几张人物照片，也决定下方展示单人还是双人风格。', 'This determines how many portraits are needed and whether the gallery shows solo or couple styles.') }}</text>

            <view class="mode-grid">
              <view v-for="mode in modeOptions" :key="mode.value" class="mode-card" :class="{ active: generationMode === mode.value }" @tap="setMode(mode.value)">
                <text class="mode-title">{{ mode.title }}</text>
                <text class="mode-desc">{{ mode.desc }}</text>
              </view>
            </view>
          </view>

          <view class="panel">
            <view class="panel-head">
              <text class="badge">STEP 02</text>
              <text class="panel-title heading-serif">{{ tr('选择自由模式或模板风格', 'Choose Free Direction or a Style') }}</text>
            </view>
            <text class="panel-desc">{{ styleModeHint }}</text>
            <view class="free-mode-card" :class="{ active: !selectedStyleFamily }" @tap="selectedStyleFamily = ''">
              <image :src="resolvePublicUrl('/style-previews/custom_mode.jpg')" class="free-mode-image" mode="aspectFill" />
              <view class="free-mode-copy">
                <text class="free-mode-label">{{ tr('默认推荐', 'Recommended Default') }}</text>
                <text class="free-mode-title heading-serif">{{ tr('自由模式', 'Free Direction') }}</text>
                <text class="free-mode-desc">{{ tr('不锁死模板，优先听从你写的服装、场景和氛围描述。适合有明确想法的用户。', 'No locked template. Your outfit, scene, and mood direction lead the result, ideal when you already have a clear idea.') }}</text>
              </view>
            </view>

            <view class="style-section-head">
              <text>{{ generationMode === 'single' ? tr('单人风格库', 'Solo Styles') : tr('双人风格库', 'Couple Styles') }}</text>
              <text>{{ tr('已按当前输出人数筛选', 'Filtered by current subject count') }}</text>
            </view>
            <view class="style-grid">
              <view v-for="card in styleCards" :key="card.familyKey" class="style-card" :class="{ active: selectedStyleFamily === card.familyKey }" @tap="selectedStyleFamily = card.familyKey">
                <image :src="card.imageUrl" class="style-image" mode="aspectFill" />
                <view class="style-copy">
                  <text class="style-title heading-serif">{{ card.title }}</text>
                  <text class="style-subtitle">{{ card.subtitle }}</text>
                </view>
              </view>
            </view>
          </view>

          <view class="panel">
            <view class="panel-head">
              <text class="badge">STEP 03</text>
              <text class="panel-title heading-serif">{{ tr('上传人物照片', 'Upload Portraits') }}</text>
            </view>
            <text class="panel-desc">{{ portraitHint }}</text>

            <view class="portrait-grid" :class="{ single: generationMode === 'single', remote: generationMode === 'couple_remote' }">
              <view v-for="index in portraitIndexes" :key="index" class="upload-card">
                <text class="field-label">{{ portraitLabel(index) }}</text>
                <view v-if="portraitSlots[index].localPath" class="preview-box">
                  <image :src="portraitSlots[index].localPath" class="preview-image" mode="aspectFill" />
                  <view class="preview-actions">
                    <button class="mini-btn" @tap.stop="pickPortrait(index)">{{ tr('更换', 'Replace') }}</button>
                    <button class="mini-btn ghost" @tap.stop="clearPortrait(index)">{{ tr('移除', 'Remove') }}</button>
                  </view>
                </view>
                <view v-else class="empty-box" @tap="pickPortrait(index)">
                  <text class="empty-plus">+</text>
                  <text class="empty-title">{{ portraitCta(index) }}</text>
                </view>
              </view>

              <view v-if="generationMode === 'couple_remote'" class="remote-card">
                <text class="field-label">{{ tr('异地合拍邀请', 'Remote Couple Invite') }}</text>
                <text class="remote-desc">{{ tr('上传你的照片后创建邀请链接，对方补充照片后再生成。', 'Upload your portrait, create an invite link, then generate after your partner adds theirs.') }}</text>
                <view class="remote-actions">
                  <button class="btn btn-outline remote-btn" @tap="createRemoteInvite" :disabled="remoteCreating">
                    {{ remoteSession ? tr('刷新邀请', 'Refresh Invite') : tr('创建邀请', 'Create Invite') }}
                  </button>
                  <button v-if="remoteSession" class="btn btn-outline remote-btn" @tap="copyJoinLink">{{ tr('复制链接', 'Copy Link') }}</button>
                  <button v-if="remoteSession" class="btn btn-outline remote-btn" @tap="openJoinLink">{{ tr('打开访客页', 'Open Guest Page') }}</button>
                </view>
                <view v-if="remoteSession" class="remote-info">
                  <text>{{ tr('邀请状态', 'Invite Status') }}：{{ remoteStatusText }}</text>
                  <text class="remote-link">{{ remoteSession.join_url }}</text>
                </view>
              </view>
            </view>
          </view>

          <view class="panel optional-panel">
            <view class="panel-head">
              <text class="badge optional">{{ selectedStyleFamily ? 'OPTIONAL' : 'RECOMMENDED' }}</text>
              <text class="panel-title heading-serif">{{ directionPanelTitle }}</text>
            </view>
            <text class="panel-desc">{{ directionPanelDesc }}</text>
            <textarea v-model="globalStyleText" class="text-area large" :placeholder="tr('整体方向：如极简高级、法式电影感、复古胶片、低饱和纪实', 'Overall direction: minimal luxury, French cinematic, vintage film, low-saturation documentary...')" maxlength="400" />
            <view class="dual-inputs">
              <textarea v-model="outfitText" class="text-area" :placeholder="tr('服装方向：如缎面白纱、黑色鱼尾裙、中式秀禾、复古西装', 'Outfit direction: satin white dress, black mermaid gown, Chinese xiuhe, vintage suit...')" maxlength="300" />
              <textarea v-model="sceneText" class="text-area" :placeholder="tr('场景方向：如白色画廊、古堡阳台、海边日落、中式庭院', 'Scene direction: white gallery, castle balcony, sunset beach, Chinese courtyard...')" maxlength="300" />
            </view>
            <view class="reference-grid">
              <view class="upload-card">
                <text class="field-label">{{ tr('场景参考图（可选）', 'Scene Reference (optional)') }}</text>
                <view v-if="sceneReferencePath" class="preview-box ref-box">
                  <image :src="sceneReferencePath" class="preview-image" mode="aspectFill" />
                  <view class="preview-actions">
                    <button class="mini-btn" @tap.stop="pickSceneReference">{{ tr('更换', 'Replace') }}</button>
                    <button class="mini-btn ghost" @tap.stop="sceneReferencePath = ''">{{ tr('移除', 'Remove') }}</button>
                  </view>
                </view>
                <view v-else class="empty-box short" @tap="pickSceneReference">
                  <text class="empty-plus">+</text>
                  <text class="empty-title">{{ tr('上传场景参考', 'Upload scene reference') }}</text>
                </view>
              </view>
              <view class="upload-card">
                <text class="field-label">{{ tr('服装参考图（可选）', 'Outfit Reference (optional)') }}</text>
                <view v-if="outfitReferencePath" class="preview-box ref-box">
                  <image :src="outfitReferencePath" class="preview-image" mode="aspectFill" />
                  <view class="preview-actions">
                    <button class="mini-btn" @tap.stop="pickOutfitReference">{{ tr('更换', 'Replace') }}</button>
                    <button class="mini-btn ghost" @tap.stop="outfitReferencePath = ''">{{ tr('移除', 'Remove') }}</button>
                  </view>
                </view>
                <view v-else class="empty-box short" @tap="pickOutfitReference">
                  <text class="empty-plus">+</text>
                  <text class="empty-title">{{ tr('上传服装参考', 'Upload outfit reference') }}</text>
                </view>
              </view>
            </view>
          </view>
        </view>

        <view class="side-col">
          <view class="summary-card">
            <image :src="summaryImageUrl" class="summary-image" mode="aspectFill" />
            <view class="summary-body">
              <text class="summary-kicker">{{ tr('创作预览', 'Creation Preview') }}</text>
              <text class="summary-title heading-serif">{{ summaryTitle }}</text>
              <text class="summary-subtitle">{{ summarySubtitle }}</text>
              <view class="tag-row">
                <view class="tag">{{ outputModeLabel }}</view>
                <view class="tag subtle">{{ templateStateLabel }}</view>
              </view>
              <view class="summary-block">
                <text class="summary-block-title">{{ tr('创作建议', 'Creation Tip') }}</text>
                <text class="summary-block-text">{{ summaryTip }}</text>
              </view>
              <view class="summary-checklist">
                <view class="check-row done">
                  <view class="check-dot"></view>
                  <text>{{ outputModeLabel }}</text>
                </view>
                <view class="check-row" :class="{ done: selectedStyleFamily || hasDirectionText }">
                  <view class="check-dot"></view>
                  <text>{{ selectedStyleFamily ? tr('已选择模板风格', 'Style selected') : hasDirectionText ? tr('已填写自由方向', 'Free direction added') : tr('自由模式：可直接上传生成', 'Free direction: ready to upload') }}</text>
                </view>
                <view class="check-row" :class="{ done: portraitRequirementMet }">
                  <view class="check-dot"></view>
                  <text>{{ portraitRequirementText }}</text>
                </view>
              </view>
              <view v-if="generationMode === 'couple_remote'" class="summary-block">
                <text class="summary-block-title">{{ tr('异地状态', 'Remote Status') }}</text>
                <text class="summary-block-text">{{ remoteStatusText }}</text>
              </view>
              <view class="summary-block">
                <text class="summary-block-title">{{ tr('所需积分', 'Credits Required') }}</text>
                <text class="credit-value">{{ generationCost }}</text>
              </view>
              <button class="btn btn-primary create-btn shadow-glow" :disabled="!canSubmit || submitting" @tap="submitCreate">
                {{ submitting ? tr('正在提交…', 'Submitting...') : tr('开始生成', 'Create Masterpiece') }}
              </button>
              <LegalConsentInline v-model="legalAccepted" mode="generate" />
            </view>
          </view>
        </view>
      </view>
    </view>

    <LegalFooter />
    <PaymentModal :visible="showPaymentModal" @close="showPaymentModal = false" @purchase-complete="onPurchaseComplete" />
  </view>
</template>
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import NavBar from '../../components/NavBar.vue';
import PaymentModal from '../../components/PaymentModal.vue';
import LegalConsentInline from '../../components/LegalConsentInline.vue';
import LegalFooter from '../../components/LegalFooter.vue';
import { useI18nStore } from '../../stores/i18n';
import { useOpsStore } from '../../stores/ops';
import { useOrderStore } from '../../stores/order';
import { type Template, getLocalizedTemplateMarketingSubtitle, getLocalizedTemplateTitle, getTemplateFamilyKey, useTemplateStore } from '../../stores/template';
import { get, post, resolvePublicUrl, uploadFile, type ApiError } from '../../utils/api';
import { runLocalSmartInputCheck } from '../../utils/local_smart_input';

type GenerationMode = 'single' | 'couple_local' | 'couple_remote';
type PortraitSlot = { localPath: string; uploadedUrl: string };
type RemoteSessionResponse = { session_id: string; join_url: string; qr_code_url: string; expires_in_minutes: number };
type RemoteSessionStatus = { exists: boolean; status: string; host_ready?: boolean; guest_ready?: boolean; order_id?: string | null; template_id?: string | null };
type RemoteSessionImages = { host_image_url: string; guest_image_url: string; template_id: string };

const STYLE_ORDER = ['chn_xiuhe', 'korean_minimal', 'royal_castle', 'old_money', 'gothic_romance', 'beach_sunset', 'hk_retro', 'twilight_forest', 'japanese_shiromuku', 'cyberpunk_city', 'school_days', 'classic_bw', 'golden_vintage_studio_8090', 'golden_chinese_courtyard', 'golden_modern_remake'];
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
const opsStore = useOpsStore();
const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const navBarRef = ref<InstanceType<typeof NavBar> | null>(null);
const showPaymentModal = ref(false);
const legalAccepted = ref(false);
const submitting = ref(false);

const generationMode = ref<GenerationMode>('single');
const selectedStyleFamily = ref('');
const portraitSlots = ref<PortraitSlot[]>([{ localPath: '', uploadedUrl: '' }, { localPath: '', uploadedUrl: '' }]);
const sceneReferencePath = ref('');
const outfitReferencePath = ref('');
const globalStyleText = ref('');
const outfitText = ref('');
const sceneText = ref('');
const remoteCreating = ref(false);
const remoteSession = ref<RemoteSessionResponse | null>(null);
const remoteStatus = ref<RemoteSessionStatus | null>(null);
let remotePollTimer: ReturnType<typeof setInterval> | null = null;

const portraitIndexes = computed(() => (generationMode.value === 'single' ? [0] : generationMode.value === 'couple_local' ? [0, 1] : [0]));
const portraitHint = computed(() => generationMode.value === 'single'
  ? tr('上传一张清晰正脸照片，建议光线自然、五官无遮挡。', 'Upload one clear portrait with natural light and an unobstructed face.')
  : generationMode.value === 'couple_local'
    ? tr('在同一设备上传两张照片，适合情侣、夫妻或纪念照双人生成。', 'Upload two portraits on one device for couple or anniversary portraits.')
    : tr('你先上传自己的照片，再复制邀请链接给对方补充第二张照片。', 'Upload your portrait first, then send the invite link so your partner can add theirs.'));
const selectedTemplate = computed<Template | null>(() => {
  if (!selectedStyleFamily.value) return null;
  return styleCards.value.find((item) => item.familyKey === selectedStyleFamily.value)?.template || null;
});
const styleCards = computed(() => {
  const desiredCategories = generationMode.value === 'single' ? ['single'] : ['couple', 'vintage'];
  const ordered = templateStore.templates.slice().sort((a, b) => {
    const ar = STYLE_ORDER.indexOf(getTemplateFamilyKey(a));
    const br = STYLE_ORDER.indexOf(getTemplateFamilyKey(b));
    return (ar < 0 ? 999 : ar) - (br < 0 ? 999 : br);
  });
  const seen = new Set<string>();
  return ordered.flatMap((item) => {
    const familyKey = getTemplateFamilyKey(item);
    if (!familyKey || familyKey === 'custom_mode' || familyKey === 'custom' || seen.has(familyKey)) return [];
    const category = String(item.category || '').toLowerCase();
    if (!desiredCategories.includes(category)) return [];
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
const summaryTitle = computed(() => selectedTemplate.value ? getLocalizedTemplateTitle(selectedTemplate.value, i18nStore.locale) : tr('自由模式', 'Free Direction'));
const summarySubtitle = computed(() => selectedTemplate.value
  ? (getLocalizedTemplateMarketingSubtitle(selectedTemplate.value, i18nStore.locale) || tr('可以继续补充服装、场景和氛围，让结果更接近你的审美。', 'You can keep refining outfit, scene, and mood to match your taste.'))
  : tr('不套固定模板，优先按照你的文字描述、人物照片和参考图生成。', 'No fixed template. The result follows your text direction, portraits, and optional references first.'));
const styleModeHint = computed(() => generationMode.value === 'single'
  ? tr('当前为单人输出，只展示单人婚纱风格。也可以保持自由模式，用文字直接描述想要的画面。', 'Solo output is selected, so only solo bridal styles are shown. You can also stay in free direction and describe the result directly.')
  : tr('当前为双人输出，只展示双人合拍与纪念照风格。异地合拍也会沿用同一套双人风格。', 'Couple output is selected, so only couple and anniversary styles are shown. Remote couple uses the same couple style set.'));
const directionPanelTitle = computed(() => selectedStyleFamily.value
  ? tr('微调服装、场景与参考', 'Refine Outfit, Scene, and References')
  : tr('自由描述服装、场景与氛围', 'Describe Outfit, Scene, and Mood Freely'));
const directionPanelDesc = computed(() => selectedStyleFamily.value
  ? tr('已选择模板时，这些内容用于微调画面：服装、场景和参考图会影响细节，但不会覆盖模板的基础风格。', 'With a selected style, these fields refine the result. Outfit, scene, and references influence details without overriding the base style.')
  : tr('自由模式下，这里是关键输入。建议写清楚服装、场景和整体氛围；不写也能生成，但结果会更依赖 AI 默认审美。', 'In free direction, this is the key input. Describe outfit, scene, and overall mood for better control; leaving it blank relies more on the AI default taste.'));
const remoteJoinEnabled = computed(() => opsStore.publicConfig.feature_flags.remote_join !== false);
const outputModeLabel = computed(() => generationMode.value === 'single' ? tr('单人输出', 'Single Output') : generationMode.value === 'couple_local' ? tr('双人同机', 'Couple Local') : tr('双人异地', 'Couple Remote'));
const templateStateLabel = computed(() => selectedStyleFamily.value ? tr('已选择模板', 'Style Selected') : tr('自由模式优先', 'Free Direction First'));
const generationCost = computed(() => selectedTemplate.value?.category === 'vintage' || generationMode.value === 'couple_remote' ? 4 : 2);
const portraitRequirementMet = computed(() => {
  if (generationMode.value === 'single') return !!portraitSlots.value[0].localPath;
  if (generationMode.value === 'couple_local') return !!portraitSlots.value[0].localPath && !!portraitSlots.value[1].localPath;
  return !!portraitSlots.value[0].localPath && !!remoteSession.value && remoteStatus.value?.status === 'ready';
});
const portraitRequirementText = computed(() => {
  if (generationMode.value === 'single') {
    return portraitRequirementMet.value ? tr('已上传 1 张人物照片', '1 portrait uploaded') : tr('需要上传 1 张人物照片', 'Upload 1 portrait');
  }
  if (generationMode.value === 'couple_local') {
    return portraitRequirementMet.value ? tr('已上传 2 张人物照片', '2 portraits uploaded') : tr('需要上传 2 张人物照片', 'Upload 2 portraits');
  }
  return portraitRequirementMet.value ? tr('双方照片已就绪', 'Both portraits ready') : tr('需要你的照片和对方上传完成', 'Your portrait and guest upload are required');
});
const summaryTip = computed(() => selectedTemplate.value
  ? tr('模板决定基础风格；下方增强项会作为细节修正，不会覆盖人物照片的核心身份。', 'The template anchors the base look. Optional enhancers refine details without replacing the portrait identity.')
  : tr('自由模式下，人物照片是基础；文字方向和参考图会获得更高优先级，用来决定服装、场景和氛围。', 'In free direction, portraits are the base. Text direction and references get higher priority for outfit, scene, and mood.'));
const remoteStatusText = computed(() => {
  if (generationMode.value !== 'couple_remote') return '';
  const status = remoteStatus.value?.status || '';
  if (!remoteSession.value) return tr('尚未创建邀请', 'Invite not created');
  if (status === 'ready') return tr('对方已就绪，可开始生成', 'Guest ready. Generation can start.');
  if (status === 'uploading') return tr('对方正在上传', 'Guest is uploading');
  if (status === 'processing') return tr('已进入生成队列', 'Generation has started');
  if (status === 'completed') return tr('会话已完成', 'Session completed');
  if (status === 'expired') return tr('邀请已过期，请重新创建', 'Invite expired. Create a new one.');
  return tr('等待对方上传', 'Waiting for guest upload');
});
const canSubmit = computed(() => {
  if (!legalAccepted.value || submitting.value) return false;
  return portraitRequirementMet.value;
});
const modeOptions = computed(() => [
  { value: 'single' as GenerationMode, title: tr('单人生成', 'Single'), desc: tr('一张照片，直接生成个人婚纱风格', 'One portrait, direct solo bridal output') },
  { value: 'couple_local' as GenerationMode, title: tr('双人同机', 'Couple Local'), desc: tr('同一设备上传两张照片，立即合成双人作品', 'Upload two portraits on one device') },
  ...(remoteJoinEnabled.value
    ? [{ value: 'couple_remote' as GenerationMode, title: tr('双人异地', 'Couple Remote'), desc: tr('你先上传，再邀请对方远程补第二张', 'Upload yours first, then invite remotely') }]
    : []),
]);

function currentQuery(): Record<string, string> {
  const pages = getCurrentPages();
  return ((pages[pages.length - 1] as any)?.options || {}) as Record<string, string>;
}
function setMode(mode: GenerationMode) {
  if (mode === 'couple_remote' && !remoteJoinEnabled.value) return;
  if (generationMode.value === mode) return;
  generationMode.value = mode;
  portraitSlots.value[1] = { localPath: '', uploadedUrl: '' };
  if (selectedStyleFamily.value && !hasStyleForMode(selectedStyleFamily.value, mode)) {
    selectedStyleFamily.value = '';
  }
  if (mode !== 'couple_remote') resetRemote();
}
function hasStyleForMode(familyKey: string, mode: GenerationMode) {
  const desiredCategories = mode === 'single' ? ['single'] : ['couple', 'vintage'];
  return templateStore.templates.some((item) => {
    if (getTemplateFamilyKey(item) !== familyKey) return false;
    return desiredCategories.includes(String(item.category || '').toLowerCase());
  });
}
function stopRemotePolling() {
  if (remotePollTimer) clearInterval(remotePollTimer);
  remotePollTimer = null;
}
function resetRemote() {
  stopRemotePolling();
  remoteSession.value = null;
  remoteStatus.value = null;
}
function portraitLabel(index: number) {
  return generationMode.value === 'single' ? tr('主人像', 'Main Portrait') : generationMode.value === 'couple_local' ? (index === 0 ? tr('人物 1', 'Portrait 1') : tr('人物 2', 'Portrait 2')) : tr('你的照片', 'Your Portrait');
}
function portraitCta(index: number) {
  return generationMode.value === 'single' ? tr('上传人物照片', 'Upload portrait') : generationMode.value === 'couple_local' ? (index === 0 ? tr('上传第一张照片', 'Upload first portrait') : tr('上传第二张照片', 'Upload second portrait')) : tr('上传你的照片', 'Upload your portrait');
}
async function pickLocalImage() {
  const res = await uni.chooseImage({ count: 1, sizeType: ['compressed'], sourceType: ['album', 'camera'] });
  const localPath = res.tempFilePaths?.[0];
  if (!localPath) return '';
  const verdict = await runLocalSmartInputCheck(localPath);
  if (!verdict.passed) {
    uni.showToast({ title: verdict.advice?.[0] || tr('照片质量不符合要求，请重新上传', 'Image quality check failed, please retry'), icon: 'none' });
    return '';
  }
  return localPath;
}
async function pickPortrait(index: number) {
  try {
    const localPath = await pickLocalImage();
    if (!localPath) return;
    portraitSlots.value[index] = { localPath, uploadedUrl: '' };
    if (generationMode.value === 'couple_remote' && remoteSession.value) remoteStatus.value = { ...(remoteStatus.value || { exists: true, status: 'waiting' }), host_ready: false };
  } catch (error) {
    console.error(error);
  }
}
function clearPortrait(index: number) {
  portraitSlots.value[index] = { localPath: '', uploadedUrl: '' };
  if (generationMode.value === 'couple_remote' && index === 0) resetRemote();
}
async function pickSceneReference() { sceneReferencePath.value = await pickLocalImage(); }
async function pickOutfitReference() { outfitReferencePath.value = await pickLocalImage(); }
function resolveSeedTemplate(): Template {
  if (selectedTemplate.value) return selectedTemplate.value;
  return templateStore.templates.find((item) => item.id === 'custom' || item.is_custom) || {
    id: 'custom', category: 'custom', title: 'Custom Mode', image_url: '/custom_mode.jpg', style_family: 'custom_mode', is_custom: true,
  };
}
async function uploadLocalAsset(localPath: string) {
  const result = await uploadFile('/upload', localPath, 'file');
  return String(result.url || '').trim();
}
async function ensurePortraitUploaded(index: number) {
  const slot = portraitSlots.value[index];
  if (!slot.localPath) return '';
  if (slot.uploadedUrl) return slot.uploadedUrl;
  const url = await uploadLocalAsset(slot.localPath);
  portraitSlots.value[index].uploadedUrl = url;
  return url;
}
async function refreshRemoteStatus(showError = false) {
  if (!remoteSession.value) return null;
  const status = await get<RemoteSessionStatus>(`/session/${remoteSession.value.session_id}/status`, { showLoading: false, showError });
  remoteStatus.value = status;
  if (['ready', 'completed', 'expired'].includes(status.status)) stopRemotePolling();
  return status;
}
function startRemotePolling() {
  stopRemotePolling();
  remotePollTimer = setInterval(() => { void refreshRemoteStatus(false); }, 2000);
}
async function createRemoteInvite() {
  if (!portraitSlots.value[0].localPath) {
    uni.showToast({ title: tr('请先上传你的照片，再创建邀请', 'Upload your portrait before creating an invite'), icon: 'none' });
    return;
  }
  remoteCreating.value = true;
  try {
    const seed = resolveSeedTemplate();
    const hostImageUrl = await ensurePortraitUploaded(0);
    remoteSession.value = await post<RemoteSessionResponse>('/session/create', { template_id: seed.id, host_image_url: hostImageUrl });
    remoteStatus.value = { exists: true, status: 'waiting', host_ready: true, guest_ready: false, template_id: seed.id };
    startRemotePolling();
    uni.showToast({ title: tr('邀请已创建，可复制链接给对方', 'Invite created. Send the link to your partner.'), icon: 'none' });
  } catch (error: any) {
    uni.showToast({ title: error.message || tr('创建邀请失败', 'Failed to create invite'), icon: 'none' });
  } finally {
    remoteCreating.value = false;
  }
}
function copyJoinLink() {
  if (remoteSession.value?.join_url) uni.setClipboardData({ data: remoteSession.value.join_url, showToast: true });
}
function openJoinLink() {
  if (!remoteSession.value?.join_url) return;
  // #ifdef H5
  window.open(remoteSession.value.join_url, '_blank');
  // #endif
  // #ifndef H5
  uni.setClipboardData({ data: remoteSession.value.join_url, showToast: true });
  // #endif
}
async function syncRemoteHost() {
  if (!remoteSession.value) return;
  const hostUrl = await ensurePortraitUploaded(0);
  if (!hostUrl) throw new Error(tr('请先上传你的照片', 'Upload your portrait first'));
  await post(`/session/${remoteSession.value.session_id}/upload/host?image_url=${encodeURIComponent(hostUrl)}`, {}, { showLoading: false, showError: false });
}
async function submitCreate() {
  if (!legalAccepted.value) {
    uni.showToast({ title: tr('请先勾选隐私政策与服务条款', 'Please accept the Privacy Policy and Terms first'), icon: 'none' });
    return;
  }
  submitting.value = true;
  try {
    const seed = resolveSeedTemplate();
    const images: string[] = [];
    if (generationMode.value === 'single') {
      images.push(await ensurePortraitUploaded(0));
    } else if (generationMode.value === 'couple_local') {
      images.push(await ensurePortraitUploaded(0), await ensurePortraitUploaded(1));
    } else {
      if (!remoteSession.value) throw new Error(tr('请先创建异地邀请', 'Create the remote invite first'));
      await syncRemoteHost();
      const status = await refreshRemoteStatus(true);
      if (!status || status.status != 'ready') throw new Error(tr('对方还未完成上传，暂时不能生成', 'The guest has not finished uploading yet'));
      const remoteImages = await get<RemoteSessionImages>(`/session/${remoteSession.value.session_id}/images`, { showLoading: false, showError: false });
      images.push(remoteImages.host_image_url, remoteImages.guest_image_url);
    }
    const sceneImageUrl = sceneReferencePath.value ? await uploadLocalAsset(sceneReferencePath.value) : undefined;
    const clothingImageUrl = outfitReferencePath.value ? await uploadLocalAsset(outfitReferencePath.value) : undefined;
    const order = await orderStore.createOrder(seed.id, images, {
      legal_accepted: true,
      director_mode: !!(globalStyleText.value.trim() || outfitText.value.trim() || sceneText.value.trim() || sceneImageUrl || clothingImageUrl),
      remote_join: generationMode.value === 'couple_remote',
      global_style_text: globalStyleText.value.trim() || undefined,
      scene_text: sceneText.value.trim() || undefined,
      outfit_text: outfitText.value.trim() || undefined,
      scene_image_url: sceneImageUrl,
      clothing_image_url: clothingImageUrl,
    });
    if (generationMode.value === 'couple_remote' && remoteSession.value) {
      await post(`/session/${remoteSession.value.session_id}/bind_order`, { order_id: order.id }, { showLoading: false, showError: false });
      await post(`/session/${remoteSession.value.session_id}/processing`, {}, { showLoading: false, showError: false });
    }
    navBarRef.value?.refreshBalance();
    uni.navigateTo({ url: `/pages/preview/preview?id=${order.id}` });
  } catch (error: any) {
    const apiError = error as ApiError;
    if (apiError.statusCode === 402) {
      showPaymentModal.value = true;
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

onMounted(async () => {
  if (!templateStore.templates.length) await templateStore.fetchTemplates();
  await opsStore.fetchPublicConfig();
  const query = currentQuery();
  const mode = String(query.mode || '').toLowerCase();
  if (mode === 'couple' || mode === 'couple_local') generationMode.value = 'couple_local';
  else if ((mode === 'couple_remote' || mode === 'remote') && remoteJoinEnabled.value) generationMode.value = 'couple_remote';
  const requestedId = String(query.id || '').trim();
  if (requestedId) {
    const matched = templateStore.templates.find((item) => item.id === requestedId);
    if (matched) selectedStyleFamily.value = getTemplateFamilyKey(matched);
  }
  if (generationMode.value === 'couple_remote' && !remoteJoinEnabled.value) {
    generationMode.value = 'couple_local';
  }
});
onUnmounted(() => stopRemotePolling());
</script>

<style lang="scss" scoped>
.create-page {
  min-height: 100vh;
  background: #f7f8fa;
}

.create-shell {
  width: min(1360px, calc(100% - 48px));
  margin: 0 auto;
  padding: 32px 0 80px;
}

.hero-card,
.panel,
.summary-card {
  border: 1px solid #dde1e8;
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 14px 38px rgba(23, 25, 31, 0.06);
}

.hero-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 24px;
  padding: 24px;
  margin-bottom: 20px;
  align-items: start;
}

.hero-main {
  min-width: 0;
}

.hero-aside {
  height: 100%;
  padding: 18px;
  border-radius: 8px;
  border: 1px solid rgba(17, 106, 96, 0.18);
  background: #f3faf8;
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
  min-height: 34px;
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
  font-size: 42px;
  line-height: 1.08;
}

.hero-subtitle,
.panel-desc,
.style-subtitle,
.summary-subtitle,
.summary-block-text,
.mode-desc,
.remote-desc,
.remote-info {
  display: block;
  color: #4c5360;
  font-size: 13px;
  line-height: 1.75;
}

.remote-link {
  word-break: break-all;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f7f8fa;
  border: 1px solid #dde1e8;
}

.mode-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.mode-card {
  min-height: 92px;
  padding: 14px 16px;
  border-radius: 8px;
  border: 1px solid #dde1e8;
  background: #f7f8fa;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
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
  gap: 20px;
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
  padding: 20px;
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

.badge.optional {
  background: #f3faf8;
  color: #116a60;
  border: 1px solid rgba(17, 106, 96, 0.22);
}

.panel-title {
  color: #17191f;
  font-size: 28px;
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

.portrait-grid.remote {
  grid-template-columns: minmax(0, 1fr) 320px;
}

.dual-inputs {
  grid-template-columns: 1fr 1fr;
}

.reference-grid {
  grid-template-columns: 1fr 1fr;
  margin-top: 16px;
}

.style-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
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

.upload-card,
.remote-card {
  border-radius: 8px;
  border: 1px solid #dde1e8;
  background: #fafbfc;
  padding: 16px;
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
.style-image,
.summary-image {
  width: 100%;
  display: block;
  object-fit: cover;
  object-position: center top;
}

.preview-image {
  height: 100%;
}

.style-image,
.summary-image {
  aspect-ratio: 4 / 5;
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

.remote-actions,
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
  border: 1px solid #dde1e8;
  background: #ffffff;
}

.style-copy {
  padding: 14px 14px 16px;
}

.style-title,
.summary-title {
  display: block;
  margin-bottom: 6px;
  color: #17191f;
}

.style-title {
  font-size: 20px;
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

.create-btn,
.remote-btn {
  width: 100%;
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
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 820px) {
  .create-shell {
    width: min(100% - 28px, 1360px);
    padding-top: 20px;
  }

  .portrait-grid,
  .portrait-grid.remote,
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
  }
}
</style>
