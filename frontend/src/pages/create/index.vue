<template>
  <view class="app-container create-page" style="padding-top: 64px;">
    <NavBar ref="navBarRef" @show-payment="showPaymentModal = true" />
    <view class="create-shell">
      <view class="hero-card">
        <view>
          <text class="hero-kicker">{{ tr('统一创作流', 'Unified Creation Flow') }}</text>
          <text class="hero-title heading-serif">{{ tr('上传人物、设定风格、直接生成', 'Upload portraits, shape the style, generate directly') }}</text>
          <text class="hero-subtitle">{{ tr('模板是默认基线；文字描述优先；若没有模板也没有描述，系统会自动随机保底。', 'Templates are only the default baseline; text direction overrides them; if neither template nor text is provided, the system will fall back safely.') }}</text>
        </view>
        <view class="mode-grid">
          <view v-for="mode in modeOptions" :key="mode.value" class="mode-card" :class="{ active: generationMode === mode.value }" @tap="setMode(mode.value)">
            <text class="mode-title">{{ mode.title }}</text>
            <text class="mode-desc">{{ mode.desc }}</text>
          </view>
        </view>
      </view>

      <view class="layout">
        <view class="main-col">
          <view class="panel">
            <view class="panel-head">
              <text class="badge">STEP 01</text>
              <text class="panel-title heading-serif">{{ tr('人物输入', 'Portrait Input') }}</text>
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
                <text class="field-label">{{ tr('异地邀请', 'Remote Invite') }}</text>
                <text class="remote-desc">{{ tr('先上传你的照片，再创建邀请链接发给对方补第二张。', 'Upload your own portrait first, then create an invite link for the second portrait.') }}</text>
                <view class="remote-actions">
                  <button class="btn btn-outline remote-btn" @tap="createRemoteInvite" :disabled="remoteCreating">
                    {{ remoteSession ? tr('刷新邀请', 'Refresh Invite') : tr('创建邀请', 'Create Invite') }}
                  </button>
                  <button v-if="remoteSession" class="btn btn-outline remote-btn" @tap="copyJoinLink">{{ tr('复制链接', 'Copy Link') }}</button>
                  <button v-if="remoteSession" class="btn btn-outline remote-btn" @tap="openJoinLink">{{ tr('打开访客页', 'Open Guest Page') }}</button>
                </view>
                <view v-if="remoteSession" class="remote-info">
                  <text>{{ tr('会话编号', 'Session ID') }}：{{ remoteSession.session_id }}</text>
                  <text>{{ tr('当前状态', 'Current Status') }}：{{ remoteStatusText }}</text>
                  <text class="remote-link">{{ remoteSession.join_url }}</text>
                </view>
              </view>
            </view>
          </view>

          <view class="panel">
            <view class="panel-head">
              <text class="badge">STEP 02</text>
              <text class="panel-title heading-serif">{{ tr('风格基线', 'Style Baseline') }}</text>
            </view>
            <text class="panel-desc">{{ tr('每个风格家族都支持单人和双人输出。模式影响上传与生成路径，不改变风格本身。', 'Every style family supports single and couple output. Mode changes the path, not the style itself.') }}</text>
            <view class="pill-row">
              <view class="template-pill" :class="{ active: !selectedStyleFamily }" @tap="selectedStyleFamily = ''">{{ tr('不选模板', 'No template') }}</view>
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
              <text class="panel-title heading-serif">{{ tr('文字定向', 'Text Direction') }}</text>
            </view>
            <text class="panel-desc">{{ tr('你可以直接描述整体风格，也可以分别写服装与场景。文字优先于模板默认。', 'Describe the overall style, or split it into outfit and scene. Text overrides template defaults.') }}</text>
            <textarea v-model="globalStyleText" class="text-area large" :placeholder="tr('整体风格描述（可选）：如电影感、法式、高级、胶片', 'Global direction (optional): cinematic, French editorial, minimal, filmic...')" maxlength="400" />
            <view class="dual-inputs">
              <textarea v-model="outfitText" class="text-area" :placeholder="tr('服装描述（可选）：如黑纱、秀禾、缎面白纱', 'Outfit direction (optional): black gown, xiuhe, satin bridal dress...')" maxlength="300" />
              <textarea v-model="sceneText" class="text-area" :placeholder="tr('场景描述（可选）：如城堡阳台、白色画廊、海边落日', 'Scene direction (optional): castle balcony, white gallery, sunset beach...')" maxlength="300" />
            </view>
          </view>

          <view class="panel">
            <view class="panel-head">
              <text class="badge">STEP 04</text>
              <text class="panel-title heading-serif">{{ tr('高级参考', 'Advanced References') }}</text>
              <text class="collapse-toggle" @tap="advancedOpen = !advancedOpen">{{ advancedOpen ? tr('收起', 'Collapse') : tr('展开', 'Expand') }}</text>
            </view>
            <view v-if="advancedOpen" class="dual-inputs">
              <view class="upload-card">
                <text class="field-label">{{ tr('场景参考', 'Scene Reference') }}</text>
                <view v-if="sceneReferencePath" class="preview-box">
                  <image :src="sceneReferencePath" class="preview-image" mode="aspectFill" />
                  <view class="preview-actions">
                    <button class="mini-btn" @tap.stop="pickSceneReference">{{ tr('更换', 'Replace') }}</button>
                    <button class="mini-btn ghost" @tap.stop="sceneReferencePath = ''">{{ tr('移除', 'Remove') }}</button>
                  </view>
                </view>
                <view v-else class="empty-box short" @tap="pickSceneReference">
                  <text class="empty-plus">+</text>
                  <text class="empty-title">{{ tr('上传场景参考图', 'Upload scene reference') }}</text>
                </view>
              </view>
              <view class="upload-card">
                <text class="field-label">{{ tr('服装参考', 'Outfit Reference') }}</text>
                <view v-if="outfitReferencePath" class="preview-box">
                  <image :src="outfitReferencePath" class="preview-image" mode="aspectFill" />
                  <view class="preview-actions">
                    <button class="mini-btn" @tap.stop="pickOutfitReference">{{ tr('更换', 'Replace') }}</button>
                    <button class="mini-btn ghost" @tap.stop="outfitReferencePath = ''">{{ tr('移除', 'Remove') }}</button>
                  </view>
                </view>
                <view v-else class="empty-box short" @tap="pickOutfitReference">
                  <text class="empty-plus">+</text>
                  <text class="empty-title">{{ tr('上传服装参考图', 'Upload outfit reference') }}</text>
                </view>
              </view>
            </view>
          </view>
        </view>

        <view class="side-col">
          <view class="summary-card">
            <image :src="summaryImageUrl" class="summary-image" mode="aspectFill" />
            <view class="summary-body">
              <text class="summary-kicker">{{ tr('当前风格', 'Current Style') }}</text>
              <text class="summary-title heading-serif">{{ summaryTitle }}</text>
              <text class="summary-subtitle">{{ summarySubtitle }}</text>
              <view class="tag-row">
                <view class="tag">{{ outputModeLabel }}</view>
                <view class="tag subtle">{{ templateStateLabel }}</view>
              </view>
              <view class="summary-block">
                <text class="summary-block-title">{{ tr('执行优先级', 'Execution Priority') }}</text>
                <text class="summary-block-text">{{ tr('参考图 > 文字描述 > 模板默认 > 随机保底', 'References > Text Direction > Template Defaults > Random Fallback') }}</text>
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
const advancedOpen = ref(false);
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
  ? tr('上传一张清晰正脸照片，系统会先做本地质检。', 'Upload one clear portrait. A local quality check runs first.')
  : generationMode.value === 'couple_local'
    ? tr('在同一设备上传两张人像，适合同机双传。', 'Upload two portraits on the same device for local couple generation.')
    : tr('异地合拍会先创建邀请会话。你上传主照片后，可复制链接给另一位参与者。', 'Remote join creates an invite session. Upload your portrait first, then send the invite link.'));
const selectedTemplate = computed<Template | null>(() => {
  if (!selectedStyleFamily.value) return null;
  const matched = templateStore.templates.find((item) => getTemplateFamilyKey(item) === selectedStyleFamily.value);
  if (!matched) return null;
  return templateStore.resolveTemplateForMode(matched, generationMode.value === 'single' ? 'single' : 'couple') || matched;
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
    seen.add(familyKey);
    const actual = templateStore.resolveTemplateForMode(item, generationMode.value === 'single' ? 'single' : 'couple') || item;
    return [{
      familyKey,
      title: getLocalizedTemplateTitle(actual, i18nStore.locale),
      subtitle: STYLE_SUBTITLE[familyKey] ? (i18nStore.locale === 'zh' ? STYLE_SUBTITLE[familyKey].zh : STYLE_SUBTITLE[familyKey].en) : getLocalizedTemplateMarketingSubtitle(actual, i18nStore.locale),
      imageUrl: resolvePublicUrl(actual.image_url),
    }];
  });
});
const summaryImageUrl = computed(() => resolvePublicUrl(selectedTemplate.value?.image_url || '/hero_banner.jpg'));
const summaryTitle = computed(() => selectedTemplate.value ? getLocalizedTemplateTitle(selectedTemplate.value, i18nStore.locale) : tr('未锁定模板', 'No Template Locked'));
const summarySubtitle = computed(() => selectedTemplate.value
  ? (getLocalizedTemplateMarketingSubtitle(selectedTemplate.value, i18nStore.locale) || tr('模板提供默认风格基线，文字与参考图仍可覆盖服装和场景方向。', 'The template provides a baseline; text and references can still override outfit and scene.'))
  : tr('你可以直接描述服装与场景；若不提供模板与文字，系统会使用安全的随机保底。', 'You can describe outfit and scene directly; if neither template nor text is provided, the system uses a safe random fallback.'));
const remoteJoinEnabled = computed(() => opsStore.publicConfig.feature_flags.remote_join !== false);
const outputModeLabel = computed(() => generationMode.value === 'single' ? tr('单人输出', 'Single Output') : generationMode.value === 'couple_local' ? tr('双人同机', 'Couple Local') : tr('双人异地', 'Couple Remote'));
const templateStateLabel = computed(() => selectedStyleFamily.value ? tr('模板基线已锁定', 'Template Baseline Ready') : tr('随机保底已启用', 'Random Fallback Ready'));
const generationCost = computed(() => selectedTemplate.value?.category === 'vintage' || generationMode.value === 'couple_remote' ? 4 : 2);
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
  if (generationMode.value === 'single') return !!portraitSlots.value[0].localPath;
  if (generationMode.value === 'couple_local') return !!portraitSlots.value[0].localPath && !!portraitSlots.value[1].localPath;
  return !!portraitSlots.value[0].localPath && !!remoteSession.value && remoteStatus.value?.status === 'ready';
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
  if (mode !== 'couple_remote') resetRemote();
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
.create-page { min-height: 100vh; background: linear-gradient(180deg, #fffdfd 0%, #fdf2f8 100%); }
.create-shell { max-width: 1440px; margin: 0 auto; padding: 32px 28px 80px; }
.hero-card,.panel,.summary-card { background: rgba(255,255,255,.9); border: 1px solid rgba(131,24,67,.08); border-radius: 28px; box-shadow: 0 18px 38px rgba(131,24,67,.05); }
.hero-card { display: grid; grid-template-columns: minmax(0,1fr) 340px; gap: 24px; padding: 24px 26px; margin-bottom: 20px; align-items: start; }
.hero-kicker,.summary-kicker { display: block; font-size: 12px; font-weight: 800; letter-spacing: .16em; text-transform: uppercase; color: #ca8a04; margin-bottom: 12px; }
.hero-title { display: block; max-width: 860px; font-size: 44px; line-height: 1.04; color: #831843; margin-bottom: 12px; }
.hero-subtitle,.panel-desc,.style-subtitle,.summary-subtitle,.summary-block-text,.mode-desc,.remote-desc,.remote-info { display: block; font-size: 13px; line-height: 1.75; color: rgba(131,24,67,.7); }
.remote-link { word-break: break-all; padding: 10px 12px; border-radius: 14px; background: rgba(255,255,255,.72); border: 1px solid rgba(219,39,119,.12); }
.mode-grid { display: grid; grid-template-columns: 1fr; gap: 10px; }
.mode-card { min-height: 92px; padding: 14px 16px; border-radius: 18px; border: 1px solid rgba(131,24,67,.1); background: rgba(253,242,248,.46); display: flex; flex-direction: column; justify-content: space-between; }
.mode-card.active,.style-card.active,.template-pill.active { border-color: rgba(219,39,119,.36); background: white; box-shadow: 0 14px 34px rgba(131,24,67,.08); }
.mode-title,.field-label,.summary-block-title { display: block; font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: #831843; margin-bottom: 8px; }
.layout { display: grid; grid-template-columns: minmax(0,1fr) 360px; gap: 20px; align-items: start; }
.main-col { display: flex; flex-direction: column; gap: 20px; }
.side-col { position: sticky; top: 88px; }
.panel { padding: 20px; }
.panel-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.badge { padding: 8px 12px; border-radius: 999px; background: #db2777; color: white; font-size: 11px; font-weight: 800; letter-spacing: .08em; }
.panel-title { font-size: 30px; color: #831843; }
.collapse-toggle { margin-left: auto; font-size: 12px; font-weight: 700; color: #db2777; }
.portrait-grid,.dual-inputs,.style-grid { display: grid; gap: 16px; }
.portrait-grid { grid-template-columns: 1fr 1fr; }
.portrait-grid.single { grid-template-columns: 1fr; }
.portrait-grid.remote { grid-template-columns: minmax(0,1fr) 320px; }
.dual-inputs { grid-template-columns: 1fr 1fr; }
.style-grid { grid-template-columns: repeat(3,minmax(0,1fr)); }
.upload-card,.remote-card { border-radius: 24px; border: 1px solid rgba(219,39,119,.14); background: rgba(253,242,248,.45); padding: 16px; }
.empty-box { min-height: 280px; border: 1px dashed rgba(219,39,119,.32); border-radius: 20px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; }
.empty-box.short { min-height: 220px; }
.empty-plus { font-size: 38px; font-weight: 200; color: #db2777; }
.empty-title { font-size: 14px; font-weight: 700; color: #831843; }
.preview-box { position: relative; overflow: hidden; border-radius: 20px; }
.preview-image,.style-image { width: 100%; aspect-ratio: 4 / 5; }
.summary-image { width: 100%; aspect-ratio: 16 / 10; }
.preview-actions { position: absolute; left: 12px; right: 12px; bottom: 12px; display: flex; gap: 8px; }
.mini-btn { flex: 1; height: 40px; border-radius: 999px; border: none; background: rgba(255,255,255,.94); color: #831843; font-size: 12px; font-weight: 700; }
.mini-btn.ghost { background: rgba(131,24,67,.78); color: white; }
.remote-actions,.pill-row,.tag-row { display: flex; flex-wrap: wrap; gap: 8px; }
.template-pill,.tag { display: inline-flex; align-items: center; justify-content: center; min-height: 36px; padding: 0 14px; border-radius: 999px; border: 1px solid rgba(219,39,119,.18); background: white; font-size: 12px; font-weight: 800; color: #831843; }
.tag.subtle { background: rgba(131,24,67,.05); }
.style-card { overflow: hidden; border-radius: 22px; border: 1px solid rgba(131,24,67,.08); background: white; }
.style-copy { padding: 14px 14px 16px; }
.style-title,.summary-title { display: block; color: #831843; margin-bottom: 6px; }
.style-title { font-size: 22px; }
.summary-title { font-size: 34px; line-height: 1.06; }
.text-area { width: 100%; min-height: 128px; padding: 18px; box-sizing: border-box; border-radius: 20px; border: 1px solid rgba(219,39,119,.14); background: rgba(253,242,248,.42); font-size: 14px; line-height: 1.8; color: #831843; }
.text-area.large { min-height: 110px; margin-bottom: 14px; }
.summary-card { overflow: hidden; padding: 0; }
.summary-body { padding: 18px 18px 20px; }
.summary-block { padding: 14px 0; border-top: 1px solid rgba(131,24,67,.08); }
.credit-value { display: block; font-size: 36px; font-weight: 800; color: #db2777; }
.create-btn,.remote-btn { width: 100%; }
@media (max-width: 1180px) { .hero-card { grid-template-columns: 1fr; } .layout { grid-template-columns: 1fr; } .side-col { position: static; } .style-grid { grid-template-columns: repeat(2,minmax(0,1fr)); } }
@media (max-width: 820px) { .portrait-grid,.portrait-grid.remote,.dual-inputs,.style-grid { grid-template-columns: 1fr; } .hero-title { font-size: 36px; } .panel-title { font-size: 28px; } .hero-card { padding: 20px; } }
</style>
