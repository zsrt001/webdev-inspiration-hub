<template>
  <view class="app-container create-page" style="padding-top: 64px;">
    <NavBar ref="navBarRef" @show-payment="showPaymentModal = true" />
    <view class="create-shell">
      <view class="hero-card">
        <view class="hero-main">
          <text class="hero-kicker">{{ tr('AI 婚纱创作台', 'AI Wedding Studio') }}</text>
          <text class="hero-title heading-serif">{{ tr('先把照片上传好，再决定要不要加风格', 'Upload portraits first, then add style if needed') }}</text>
          <text class="hero-subtitle">{{ tr('主线只有两步：选择生成对象、上传人物照片。自由文字、模板风格和参考图都是增强项，用来让结果更贴近你的想法。', 'The core path has two steps: choose the subject count and upload portraits. Text direction, style templates, and references are enhancers that refine the result.') }}</text>
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
          <text class="aside-copy">{{ tr('照片齐了就能生成；风格、文字和参考图只负责提升命中率，不再挡住主流程。', 'Once portraits are ready, generation can start. Style, text, and references only improve precision without blocking the core path.') }}</text>
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

            <view class="portrait-grid" :class="{ single: generationMode === 'single', remote: generationMode === 'couple_remote' }">
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

              <view v-if="generationMode === 'couple_remote'" class="remote-card">
                <text class="field-label">{{ tr('异地合拍邀请', 'Remote Couple Invite') }}</text>
                <text class="remote-desc">{{ tr('先上传你的照片，再创建邀请链接。对方补充照片后，右侧检查项会变为就绪。', 'Upload your portrait first, then create an invite link. Once your partner uploads theirs, the checklist will be ready.') }}</text>
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

          <view class="panel enhancer-panel">
            <view class="panel-head">
              <text class="badge optional">{{ tr('增强 03', 'Enhance 03') }}</text>
              <text class="panel-title heading-serif">{{ directionPanelTitle }}</text>
            </view>
            <text class="panel-desc">{{ directionPanelDesc }}</text>
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
              <text>{{ generationMode === 'single' ? tr('参考风格（单人）', 'Reference Styles (solo)') : tr('参考风格（双人）', 'Reference Styles (couple)') }}</text>
              <text>{{ tr('可选：不选模板时按文字自由生成', 'Optional: skip templates to follow text freely') }}</text>
            </view>
            <view class="free-mode-card compact" :class="{ active: !selectedStyleFamily }" @tap="selectedStyleFamily = ''">
              <view class="free-mode-copy">
                <text class="free-mode-label">{{ tr('默认', 'Default') }}</text>
                <text class="free-mode-title heading-serif">{{ tr('不套模板，按文字自由生成', 'No template, follow text freely') }}</text>
                <text class="free-mode-desc">{{ tr('适合已经有明确服装、场景或氛围想法的用户。', 'Best when you already know the outfit, scene, or mood you want.') }}</text>
              </view>
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

            <view class="style-section-head reference-head">
              <text>{{ tr('参考图（可选）', 'Reference Images (optional)') }}</text>
              <text>{{ tr('只有需要精准复刻服装或场景时再上传', 'Only upload when outfit or scene needs to be very specific') }}</text>
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
              <text class="summary-kicker">{{ tr('提交检查', 'Submit Checklist') }}</text>
              <text class="summary-title heading-serif">{{ readinessTitle }}</text>
              <text class="summary-subtitle">{{ readinessSubtitle }}</text>
              <view class="tag-row">
                <view class="tag">{{ outputModeLabel }}</view>
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
                <view class="check-row" :class="{ done: selectedStyleFamily || hasDirectionText || sceneReferencePath || outfitReferencePath }">
                  <view class="check-dot"></view>
                  <text>{{ enhancerStateText }}</text>
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
                {{ submitting ? tr('正在提交…', 'Submitting...') : tr('照片齐了，开始生成', 'Generate When Ready') }}
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
import { isSupabaseLoggedIn } from '../../utils/auth';
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
const primaryPhotoInstruction = computed(() => {
  if (generationMode.value === 'single') {
    return tr('主流程只需要 1 张人物照片。上传后可以直接生成；想控制服装或场景时，再补充下方增强项。', 'The core flow only needs 1 portrait. After upload, you can generate directly; add enhancers only when you want outfit or scene control.');
  }
  if (generationMode.value === 'couple_local') {
    return tr('主流程需要 2 张人物照片。两张照片齐了即可生成；模板、文字和参考图只是帮助画面更精准。', 'The core flow needs 2 portraits. Once both are uploaded, generation is ready; templates, text, and references only improve precision.');
  }
  return tr('主流程是：上传你的照片，创建邀请，等待对方补照片。双方照片齐了才能提交生成。', 'The core flow is: upload your portrait, create an invite, and wait for your partner. Generation starts only when both portraits are ready.');
});
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
const directionPanelTitle = computed(() => selectedStyleFamily.value
  ? tr('增强创作方向（可选）', 'Enhance Direction (optional)')
  : tr('补充服装、场景与氛围（可选）', 'Add Outfit, Scene, and Mood (optional)'));
const directionPanelDesc = computed(() => selectedStyleFamily.value
  ? tr('你已经选择了参考风格。这里负责补充细节，例如指定礼服、地点、光线或想避开的元素。', 'You already selected a reference style. Use this area only to refine details such as outfit, location, lighting, or what to avoid.')
  : tr('自由模式不强制选模板。想要更可控的结果时，优先写清楚服装、场景和整体氛围。', 'Free mode does not require a template. For more control, describe outfit, scene, and overall mood first.'));
const remoteJoinEnabled = computed(() => opsStore.publicConfig.feature_flags.remote_join !== false);
const outputModeLabel = computed(() => generationMode.value === 'single' ? tr('单人输出', 'Single Output') : generationMode.value === 'couple_local' ? tr('双人同机', 'Couple Local') : tr('双人异地', 'Couple Remote'));
const templateStateLabel = computed(() => selectedStyleFamily.value ? tr('已选择模板', 'Style Selected') : tr('自由模式优先', 'Free Direction First'));
const generationCost = computed(() => {
  if (selectedTemplate.value?.category === 'vintage') return 5;
  if (generationMode.value === 'couple_remote') return 4;
  if (generationMode.value === 'couple_local') return 3;
  if (hasDirectionText.value || sceneReferencePath.value || outfitReferencePath.value) return 3;
  return 2;
});
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
const readinessTitle = computed(() => portraitRequirementMet.value
  ? tr('主流程已就绪', 'Core Flow Ready')
  : tr('先完成照片上传', 'Upload Portraits First'));
const readinessSubtitle = computed(() => portraitRequirementMet.value
  ? tr('现在可以直接生成。下方增强项会作为加分信息，不影响主流程提交。', 'You can generate now. Enhancers below are helpful extras and do not block the core submission.')
  : primaryPhotoInstruction.value);
const enhancerStateText = computed(() => {
  if (selectedStyleFamily.value) return tr('已选择参考风格', 'Reference style selected');
  if (hasDirectionText.value) return tr('已填写服装/场景方向', 'Outfit or scene direction added');
  if (sceneReferencePath.value || outfitReferencePath.value) return tr('已上传参考图', 'Reference image added');
  return tr('增强项未填写，可跳过', 'No enhancers added, can skip');
});
const summaryTip = computed(() => selectedTemplate.value
  ? tr('参考风格只用来确定画面基调；人物照片仍是主体。服装、场景文字和参考图会作为细节补充。', 'The reference style only anchors the look; portraits remain the subject. Outfit, scene text, and references refine the details.')
  : tr('不选择模板也可以生成。若想减少随机性，优先填写服装方向和场景方向，再上传参考图。', 'You can generate without a template. To reduce randomness, add outfit and scene direction first, then reference images if needed.'));
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
    id: 'custom', category: 'custom', title: 'Custom Mode', image_url: '/style-previews/custom_mode.jpg', style_family: 'custom_mode', is_custom: true,
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
  if (!isSupabaseLoggedIn()) {
    uni.showModal({
      title: i18nStore.locale === 'zh' ? '需要登录' : 'Sign In Required',
      content: i18nStore.locale === 'zh' ? '使用 Google 登录后可创建异地合拍邀请。' : 'Sign in with Google to create remote couple invites.',
      confirmText: i18nStore.locale === 'zh' ? '去登录' : 'Sign In',
      cancelText: i18nStore.locale === 'zh' ? '取消' : 'Cancel',
      success: (res) => {
        if (res.confirm) uni.navigateTo({ url: '/pages/auth/login' });
      },
    });
    return;
  }
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
  if (!isSupabaseLoggedIn()) {
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

.primary-panel {
  border-color: rgba(17, 106, 96, 0.24);
  background: linear-gradient(180deg, #ffffff 0%, #fbfefd 100%);
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

.upload-card,
.remote-card {
  border-radius: 8px;
  border: 1px solid #dde1e8;
  background: #fafbfc;
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
  background: #f8fcfb;
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
