<template>
  <view class="app-container preview-sanctum" style="padding-top: 64px;">
    <NavBar ref="navBarRef" @show-payment="showPaymentModal = true" />
    <PaymentModal :visible="showPaymentModal" @close="showPaymentModal = false" @purchase-complete="onPurchaseComplete" />

    <view v-if="orderStore.isGenerating" class="ritual-loading-view">
      <view class="workflow-ritual-card">
        <view class="exhibition-orb-wrap">
          <view class="orb-pulse"></view>
          <view class="orb-core">✦</view>
        </view>
        <!-- STUDIO 3.0 STORY LOADER -->
        <text class="ritual-status heading-serif">{{ studioLoadingText }}</text>
        <text class="ritual-hint">{{ tr('旗舰质检已开启', 'Flagship Quality Control Active') }}</text>
        <text class="ritual-policy">{{ tr('入队即扣费 · 失败自动退款', 'Charged on queueing · Auto-refund on failure') }}</text>
        <view class="ritual-bar-wrap">
          <view class="ritual-bar-fill" :style="{ width: (progressStep * 25) + '%' }"></view>
        </view>
      </view>
    </view>

    <view v-else-if="hasRenderableOutput" class="exhibition-content">
      <view class="masterpiece-folio">
        <!-- STUDIO 3.0 REVEAL: CURTAIN -> COMPARE SLIDER -->
        <view class="folio-frame shadow-xl">
          <template v-if="revealed">
            <CompareSlider
              v-if="userUploadUrl"
              :before-image="userUploadUrl"
              :after-image="afterImageUrl"
            />
            <image
              v-else
              class="masterpiece-img"
              :src="afterImageUrl"
              mode="aspectFill"
            />
          </template>
          <template v-else>
            <image class="masterpiece-img reveal-blur" :src="afterImageUrl" mode="aspectFill" />
            <view class="reveal-curtain" :class="{ opening: curtainOpening }" @tap="reveal">
              <view class="curtain-panel left"></view>
              <view class="curtain-panel right"></view>
              <view class="curtain-label">
                <text class="ready heading-serif">{{ tr('已就绪', 'Ready') }}</text>
                <text class="tap">{{ tr('点击揭晓', 'Tap to reveal') }}</text>
              </view>
            </view>
          </template>
          <view v-if="!orderStore.isCompleted" class="exhibition-tag draft">{{ tr('预览草稿', 'STUDIO 3.0 DRAFT') }}</view>
          <view v-else class="exhibition-tag hd">{{ tr('高清成片', 'HD MASTERPIECE') }}</view>
          <view v-if="downloadLocked" class="artistic-watermark">
            <text v-for="n in 9" :key="n" class="watermark-item">AI WEDDING PREVIEW</text>
          </view>
        </view>
        
        <view class="folio-credenza">
          <text class="folio-title heading-serif">
            {{ orderStore.isCompleted ? tr('最终成片', 'The Final Vision') : tr('预览图已生成', 'Artistic Draft Ready') }}
          </text>
          <view v-if="effectiveHints.length" class="folio-meta">
            <text v-for="h in effectiveHints" :key="h" class="meta-chip">{{ h }}</text>
          </view>
        </view>
      </view>

      <view class="preview-desktop-layout">
        <view class="preview-main-col">
          <!-- STUDIO 3.0 BUSINESS: LEADS FORM -->
          <view class="leads-capture-ritual shadow-xl">
            <view class="leads-header">
              <text class="l-title">{{ tr('领取 500 元婚摄补贴', 'Claim 500 CNY Wedding Allowance') }}</text>
              <text class="l-subtitle">{{ tr('仅限 Studio 3.0 用户', 'Exclusive for Studio 3.0 Clients') }}</text>
            </view>
            <view class="leads-form">
              <input class="l-input" :placeholder="tr('姓名', 'Your Name')" v-model="leadForm.name" />
              <input class="l-input" :placeholder="tr('手机号', 'Phone Number')" v-model="leadForm.phone" type="number" />
              <LegalConsentInline v-model="leadConsentAccepted" mode="lead" compact />
              <view class="l-row">
                <input class="l-input" :placeholder="tr('城市', 'City')" v-model="leadForm.city" style="flex: 1;" />
                <input class="l-input" :placeholder="tr('婚期（可选）', 'Wedding date (optional)')"
                  v-model="leadForm.wedding_date" style="flex: 1;" />
              </view>
              <view class="l-row">
                <button class="l-submit-btn" @tap="submitLead" :disabled="submittingLead || !leadConsentAccepted">
                  {{ submittingLead ? tr('提交中...', 'Submitting...') : tr('立即领取', 'CLAIM NOW') }}
                </button>
              </view>
            </view>
          </view>

          <view class="secondary-ritual-entry" @tap="regenerate">
            <text class="entry-back">→{{ tr('重新选择风格', 'Reselect Aesthetic') }}</text>
          </view>
        </view>

        <view class="preview-side-col">
          <!-- Exhibition Actions -->
          <view v-if="orderStore.isCompleted" class="exhibition-actions">
            <button v-if="canDownload" class="btn btn-primary e-action-btn primary shadow-glow" @tap="downloadHD">
              {{ tr('下载高清图', 'DEVELOP HD PRINT') }}
            </button>
            <button v-else class="btn btn-primary e-action-btn primary shadow-glow" @tap="showPaymentModal = true">
              {{ tr('充值解锁高清下载', 'Unlock HD Download') }}
            </button>
            <button
              v-for="variant in downloadVariants"
              :key="variant.key"
              class="btn btn-outline e-action-btn secondary variant-btn"
              @tap="downloadImageUrl(variant.url, variant.filename)"
            >
              {{ variant.label }}
            </button>
            <button class="btn btn-outline e-action-btn secondary" :disabled="downloadLocked" @tap="openPosterModal">
              {{ tr('分享海报', 'INVITE POSTER') }}
            </button>
            <button v-if="livePortraitEnabled" class="btn btn-outline e-action-btn secondary" :disabled="livePortraitBusy" @tap="openLivePortrait">
              {{ livePortraitBusy ? tr('动态生成中...', 'ANIMATING...') : tr('动态人像（5秒）', 'LIVE PORTRAIT (5s)') }}
            </button>
          </view>

          <!-- Studio Concierge -->
          <view class="concierge-card shadow-md" @tap="handleBannerClick">
            <view class="concierge-inner">
              <view class="concierge-info">
                <view class="c-tag">{{ tr('高端服务', 'VIP Concierge') }}</view>
                <text class="c-title">{{ tr('线下影棚服务', 'Elite Offline Session') }}</text>
                <text class="c-desc">{{ tr('想要真人服务？立即预约摄影顾问。', 'Prefer the real touch? Book a stylist.') }}</text>
              </view>
              <view class="c-arrow">→</view>
            </view>
          </view>

          <!-- Localized Recommendation (M2) -->
          <view v-if="localRecoEnabled" class="concierge-card shadow-md local-reco">
            <view class="concierge-inner" @tap="handleLocalRecoClick">
              <view class="concierge-info">
                <view class="c-tag">{{ tr('本地推荐', 'Local Picks') }}</view>
                <text class="c-title">{{ tr('你所在城市附近的影楼', 'Studios near') }} {{ cityForReco }}</text>
                <text class="c-desc" v-if="localRecos.length">{{ tr('点击影楼可复制联系方式。', 'Tap a studio to copy contact.') }}</text>
                <text class="c-desc" v-else>{{ tr('点击获取本地推荐。', 'Tap to get curated studios.') }}</text>
              </view>
              <view class="c-arrow">→</view>
            </view>
            <view v-if="localRecos.length" class="reco-list">
              <view
                v-for="r in localRecos"
                :key="r.id"
                class="reco-item"
                @tap.stop="handleLocalRecoItemClick(r)"
              >
                <view class="reco-left">
                  <text class="reco-name">{{ r.name }}</text>
                  <text v-if="r.highlight" class="reco-highlight">{{ r.highlight }}</text>
                  <text v-if="r.match_reason" class="reco-highlight">{{ localRecoMatchReasonLabel(r.match_reason) }}</text>
                  <text v-else-if="r.ranking_factors?.length" class="reco-highlight">{{ localRecoRankingLabel(r.ranking_factors) }}</text>
                  <text v-if="r.lead_count > 0 || r.service_modes?.length || r.tags?.length" class="reco-tags">{{ localRecoSupportLine(r) }}</text>
                </view>
                <view class="reco-cta">{{ r.cta_label || tr('联系', 'Contact') }}</view>
              </view>
            </view>
          </view>

          <view v-if="livePortraitEnabled && livePortraitHistory.length" class="concierge-card shadow-md local-reco live-history-card">
            <view class="concierge-inner">
              <view class="concierge-info">
                <view class="c-tag">{{ tr('动态人像记录', 'Live Portrait Archive') }}</view>
                <text class="c-title">{{ tr('最近生成的视频与状态', 'Recent motion jobs and status') }}</text>
                <text class="c-desc">{{ tr('可随时查看已完成视频或失败原因。', 'Re-open completed videos or inspect failure reasons.') }}</text>
              </view>
            </view>
            <view class="reco-list">
              <view
                v-for="job in livePortraitHistory"
                :key="job.job_id"
                class="reco-item"
                @tap="openLivePortraitJob(job)"
              >
                <view class="reco-left">
                  <text class="reco-name">{{ livePortraitStatusLabel(job.status) }}</text>
                  <text class="reco-highlight">{{ livePortraitJobCaption(job) }}</text>
                  <text v-if="job.failure_code" class="reco-tags">{{ livePortraitFailureMessage(job.failure_code) }}</text>
                </view>
                <view class="reco-cta">
                  {{ job.status === 'COMPLETED' ? tr('查看', 'Open') : tr('状态', 'Status') }}
                </view>
              </view>
            </view>
          </view>
        </view>
      </view>
    </view>

    <!-- Error Ceremony -->
    <view v-else-if="hasError" class="error-ceremony">
      <view class="ceremony-container">
        <text class="c-icon">✦</text>
        <text class="c-heading heading-serif">{{ tr('流程中断', 'Ritual Interrupted') }}</text>
        <text class="c-msg">{{ displayErrorMessage }}</text>
        <view v-if="failureHints.length" class="folio-meta error-meta">
          <text v-for="h in failureHints" :key="h" class="meta-chip">{{ h }}</text>
        </view>
        <view v-if="failureActionHints.length" class="folio-meta error-meta">
          <text v-for="h in failureActionHints" :key="h" class="meta-chip">{{ h }}</text>
        </view>
        <button class="btn btn-primary retry-btn" @tap="retry">{{ tr('重新开始', 'RESTART RITUAL') }}</button>
      </view>
    </view>

    <!-- Poster Sheet Modal -->
    <view v-if="showPosterModal" class="poster-sheet-ritual" @tap="closePosterModal">
      <view class="sheet-ritual-body" @tap.stop>
        <view class="sheet-ritual-header">
          <text class="s-title heading-serif">{{ tr('分享海报', 'Exhibition Invite') }}</text>
          <view class="s-close" @tap="closePosterModal">×</view>
        </view>
        <view class="sheet-ritual-content">
          <view class="exhibit-canvas shadow-xl">
            <view class="canvas-image-wrap">
              <image class="canvas-image" :src="hdImageUrl" mode="aspectFill" />
            </view>
            <view class="canvas-info-wrap">
              <view class="c-left">
                <text class="c-brand heading-serif">{{ tr('AI 婚纱工作室', 'AI Wedding Studio') }}</text>
                <text class="c-edition">{{ tr('Studio 成片 · 2026', 'Studio Masterpiece · 2026') }}</text>
              </view>
              <view class="c-right">
                <image v-if="qrCodeUrl" :src="qrCodeUrl" class="c-qr" />
              </view>
            </view>
          </view>
        </view>
        <view class="sheet-ritual-footer">
          <button class="btn btn-primary s-final-btn" @tap="savePoster">{{ tr('保存海报', 'EXPORT TO JOURNAL') }}</button>
        </view>
      </view>
    </view>

    <canvas
      :id="posterCanvasId"
      :canvas-id="posterCanvasId"
      class="poster-export-canvas"
      :style="{ width: `${posterCanvasCssWidth}px`, height: `${posterCanvasCssHeight}px` }"
    />

    <view style="height: 100px;"></view>
  </view>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useOrderStore } from '../../stores/order';
import { useI18nStore } from '../../stores/i18n';
import { useOpsStore } from '../../stores/ops';
import NavBar from '../../components/NavBar.vue';
import PaymentModal from '../../components/PaymentModal.vue';
import CompareSlider from '../../components/CompareSlider.vue';
import LegalConsentInline from '../../components/LegalConsentInline.vue';
import { post, get } from '../../utils/api';
// @ts-ignore
import QRCode from 'qrcode';

const orderStore = useOrderStore();
const i18nStore = useI18nStore();
const opsStore = useOpsStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);
const navBarRef = ref<InstanceType<typeof NavBar> | null>(null);
const showPaymentModal = ref(false);
const showPosterModal = ref(false);
const qrCodeUrl = ref('');
const progressStep = ref(1);

const onPurchaseComplete = () => {
  navBarRef.value?.refreshBalance();
  if (orderStore.currentOrder?.id) {
    orderStore.fetchOrder(orderStore.currentOrder.id);
  }
};
const currentTextIndex = ref(0);
const revealed = ref(false);
const curtainOpening = ref(false);
const posterCanvasId = 'posterExportCanvas';
const posterCanvasWidth = 1080;
const posterCanvasHeight = 1620;
const posterCanvasCssWidth = 270;
const posterCanvasCssHeight = 405;
const posterImageHeight = 1350;
const posterQrSize = 156;

// STUDIO 3.0 BUSINESS STATE
const submittingLead = ref(false);
const leadConsentAccepted = ref(false);
const leadForm = ref({
  name: '',
  phone: '',
  city: '',
  wedding_date: ''
});
const leadAttribution = ref({
  source_page: 'preview',
  source_slot: 'lead_form',
  source_reco_id: '',
  source_reco_name: '',
});
const lastCity = ref('');
const localRecos = ref<any[]>([]);
const livePortraitBusy = ref(false);
const livePortraitHistory = ref<any[]>([]);
const livePortraitEnabled = computed(() => opsStore.publicConfig.feature_flags.live_portrait !== false);
const localRecoEnabled = computed(() => opsStore.publicConfig.feature_flags.local_recommendations !== false);

const livePortraitFailureMessage = (failureCode?: string | null) => {
  switch (failureCode) {
    case 'workflow_error':
      return tr('视频工作流配置异常，请稍后重试。', 'The video workflow is temporarily misconfigured.');
    case 'node_error':
      return tr('视频节点处理失败，请稍后重试。', 'A video node failed during processing.');
    case 'model_missing':
      return tr('视频模型暂不可用，请稍后重试。', 'A required video model is unavailable.');
    case 'video_output_empty':
      return tr('本次未生成有效视频，请稍后重试。', 'No valid motion asset was produced this time.');
    case 'delivery_error':
      return tr('视频已生成但交付失败，请稍后重试。', 'The video rendered but delivery failed.');
    default:
      return tr('视频生成失败，请稍后重试。', 'Video generation failed. Please retry later.');
  }
};

const livePortraitFailureActionLabel = (action?: string | null) => {
  switch (action) {
    case 'contact_support':
      return tr('请联系人工支持检查工作流配置。', 'Contact support to inspect the workflow configuration.');
    case 'retry_with_other_image':
      return tr('建议换一张构图更稳定的照片重试。', 'Retry with a different source image for better motion.');
    case 'retry_later':
      return tr('建议稍后重试，系统会自动避开失败任务。', 'Retry later. The system will avoid the failed attempt path.');
    default:
      return '';
  }
};

const livePortraitStatusLabel = (status?: string | null) => {
  switch (status) {
    case 'COMPLETED': return tr('已完成', 'Completed');
    case 'FAILED': return tr('生成失败', 'Failed');
    case 'GENERATING': return tr('生成中', 'Rendering');
    case 'CREATED': return tr('排队中', 'Queued');
    default: return tr('未知状态', 'Unknown status');
  }
};

const livePortraitJobCaption = (job: any) => {
  const seconds = Number(job?.seconds || 5);
  const createdAt = job?.created_at ? String(job.created_at).replace('T', ' ').slice(0, 16) : '';
  const parts = [`${seconds}${tr('秒动态人像', 's motion clip')}`];
  if (createdAt) parts.push(createdAt);
  return parts.join(' · ');
};

const loadingTexts = computed(() => [
  tr('扫描人像特征中...', 'Scanning facial features...'),
  tr('匹配婚纱细节中...', 'Tailoring the wedding dress...'),
  tr('调整影棚光效中...', 'Adjusting studio lighting...'),
  tr('冲洗成片中...', 'Developing film...'),
]);

const studioLoadingText = computed(() => loadingTexts.value[currentTextIndex.value]);

const userUploadUrl = computed(() => {
  const source = orderStore.currentOrder?.source_image_urls as any;
  if (source && source.images && source.images.length > 0) return source.images[0];
  return null;
});

const previewImageUrl = computed(() => {
  const urls = orderStore.currentOrder?.preview_image_urls;
  if (urls) return Object.values(urls)[0];
  return 'https://placehold.co/600x800/FDF2F8/831843?text=Developing';
});

const hdImageUrl = computed(() => {
  const urls = orderStore.currentOrder?.final_image_urls;
  if (urls) return Object.values(urls)[0];
  return previewImageUrl.value;
});

const afterImageUrl = computed(() => (orderStore.isCompleted ? hdImageUrl.value : previewImageUrl.value));
const canDownload = computed(() => orderStore.currentOrder?.can_download === true);
const downloadLocked = computed(() => orderStore.currentOrder?.download_locked !== false || !canDownload.value);
const downloadVariants = computed(() => {
  if (!canDownload.value) return [];
  const urls = orderStore.currentOrder?.final_image_urls || {};
  const labels: Record<string, string> = {
    portrait_2x3: tr('2:3 竖图', '2:3 Portrait'),
    xhs_3x4: tr('3:4 小红书', '3:4 Social'),
    wallpaper_9x16: tr('9:16 壁纸', '9:16 Wallpaper'),
  };
  return Object.entries(urls)
    .filter(([key]) => key !== 'image_1')
    .map(([key, url]) => {
      const matched = Object.keys(labels).find((suffix) => key.includes(suffix));
      return matched
        ? { key, url: String(url), label: labels[matched], filename: `ai-wedding-${matched}.jpg` }
        : null;
    })
    .filter(Boolean) as { key: string; url: string; label: string; filename: string }[];
});
const hasRenderableOutput = computed(() => {
  const preview = orderStore.currentOrder?.preview_image_urls;
  const final = orderStore.currentOrder?.final_image_urls;
  const hasPreview = !!(preview && Object.keys(preview).length);
  const hasFinal = !!(final && Object.keys(final).length);
  return orderStore.isCompleted || hasFinal || hasPreview;
});

const hasError = computed(() => orderStore.currentOrder?.error_message != null);
const providerFailureCode = computed(() => {
  const explicitCode = orderStore.currentOrder?.failure_code;
  if (explicitCode) return explicitCode;
  const params = orderStore.currentOrder?.generation_params;
  return params && typeof params === 'object' ? String((params as any).failure_code || '') || null : null;
});

const ignoredInputLabel = (key?: string | null) => {
  switch (key) {
    case 'scene_text': return tr('场景文本', 'Scene text');
    case 'outfit_text': return tr('服装文本', 'Outfit text');
    case 'scene_preset_id': return tr('场景预设', 'Scene preset');
    case 'clothing_preset_id': return tr('服装预设', 'Outfit preset');
    default: return '';
  }
};

const sourceLabel = (src?: string | null) => {
  switch (src) {
    case 'upload': return tr('上传图', 'Upload');
    case 'text': return tr('文本', 'Text');
    case 'preset': return tr('预设', 'Preset');
    case 'random': return tr('随机', 'Random');
    default: return '—';
  }
};

const directorDecisionHintLabel = (hint?: string | null) => {
  const raw = String(hint || '').trim();
  if (!raw) return '';
  if (raw === 'director_mode_enabled') return tr('导演模式已启用', 'Director Mode enabled');
  if (raw.startsWith('ignored:')) {
    const ignored = raw.replace('ignored:', '').split(',').map((item) => ignoredInputLabel(item)).filter(Boolean);
    return ignored.length ? `${tr('已忽略', 'Ignored')}: ${ignored.join(' / ')}` : '';
  }
  if (raw.startsWith('scene:')) {
    const [, source, presetTitle, weightPart] = raw.split(':');
    const parts = [`${tr('场景', 'Scene')}: ${sourceLabel(source)}`];
    if (presetTitle) parts.push(presetTitle);
    if (weightPart?.startsWith('w=')) {
      const numeric = Number(weightPart.slice(2));
      if (!Number.isNaN(numeric)) parts.push(`IP ${numeric.toFixed(2)}`);
    }
    return parts.join(' · ');
  }
  if (raw.startsWith('outfit:')) {
    const [, source, presetTitle, weightPart] = raw.split(':');
    const parts = [`${tr('服装', 'Outfit')}: ${sourceLabel(source)}`];
    if (presetTitle) parts.push(presetTitle);
    if (weightPart?.startsWith('w=')) {
      const numeric = Number(weightPart.slice(2));
      if (!Number.isNaN(numeric)) parts.push(`IP ${numeric.toFixed(2)}`);
    }
    return parts.join(' · ');
  }
  if (raw.startsWith('couple:')) {
    const mode = raw.replace('couple:', '');
    return mode === 'remote'
      ? tr('双人链路: 异地合拍', 'Couple flow: Remote join')
      : tr('双人链路: 本机双传', 'Couple flow: Local dual upload');
  }
  return raw;
};

const qaReasonLabel = (reason?: string | null) => {
  switch (reason) {
    case 'fused_faces': return tr('融脸', 'Fused faces');
    case 'body_fusion': return tr('肢体融合', 'Body fusion');
    case 'subject_missing': return tr('主体缺失', 'Subject missing');
    case 'identity_swap': return tr('身份错位', 'Identity swap');
    case 'identity_mismatch': return tr('脸不像本人', 'Identity mismatch');
    case 'extra_limbs': return tr('多余肢体', 'Extra limbs');
    case 'bad_hands': return tr('手部异常', 'Bad hands');
    case 'dress_exposure_error': return tr('婚纱露出异常', 'Dress exposure issue');
    case 'cropped_face': return tr('裁头', 'Cropped face');
    case 'headless': return tr('无头', 'Headless');
    case 'face_distortion': return tr('脸部变形', 'Face distortion');
    case 'severe_artifacts': return tr('严重伪影', 'Severe artifacts');
    default: return '';
  }
};

const providerFailureMessageLabel = (failureCode?: string | null) => {
  switch (failureCode) {
    case 'cloud_subscription_required':
      return tr('当前 Comfy Cloud 账号未开通排队执行权限。', 'The current Comfy Cloud account cannot queue workflows.');
    case 'cloud_queue_rejected':
      return tr('Comfy Cloud 已拒绝本次排队请求。', 'Comfy Cloud rejected the workflow queue request.');
    case 'cloud_job_failed':
      return tr('Comfy Cloud 任务执行失败。', 'The Comfy Cloud job failed during execution.');
    case 'workflow_error':
      return tr('当前云端工作流与平台能力不兼容。', 'The current cloud workflow is incompatible with the platform runtime.');
    case 'provider_model_unavailable':
      return tr('当前配置的模型在提供商侧不可用。', 'The configured model is not available from the current provider.');
    case 'delivery_error':
      return tr('图片已生成，但交付到存储时失败。', 'Images rendered, but delivery to storage failed.');
    case 'generation_timeout':
      return tr('生成超时，请稍后重试。', 'Generation timed out. Please retry later.');
    default:
      return '';
  }
};

const providerFailureHintLabel = (failureCode?: string | null) => {
  switch (failureCode) {
    case 'cloud_subscription_required':
      return tr('Cloud 队列权限未开通', 'Cloud queue access missing');
    case 'cloud_queue_rejected':
      return tr('Cloud 队列拒绝任务', 'Cloud queue rejected the task');
    case 'cloud_job_failed':
      return tr('云端任务执行失败', 'Cloud job execution failed');
    case 'workflow_error':
      return tr('工作流兼容性异常', 'Workflow compatibility issue');
    case 'provider_model_unavailable':
      return tr('模型通道不可用', 'Model channel unavailable');
    case 'delivery_error':
      return tr('存储交付失败', 'Storage delivery failed');
    case 'generation_timeout':
      return tr('生成超时', 'Generation timeout');
    default:
      return '';
  }
};

const providerFailureActionLabel = (failureCode?: string | null) => {
  switch (failureCode) {
    case 'cloud_subscription_required':
      return tr('请开通 Comfy Cloud 订阅，或更换具备 queue 权限的 API Key。', 'Enable a Comfy Cloud subscription or switch to an API key with queue access.');
    case 'cloud_queue_rejected':
      return tr('请检查 Cloud 额度、订阅状态和 API Key 权限。', 'Check Cloud credits, subscription status, and API key permissions.');
    case 'cloud_job_failed':
      return tr('请稍后重试；若持续失败，请检查工作流与模型兼容性。', 'Retry later. If it persists, inspect workflow and model compatibility.');
    case 'workflow_error':
      return tr('请切换到 Cloud 兼容工作流，或简化当前节点配置。', 'Switch to a Cloud-compatible workflow or simplify the current nodes.');
    case 'provider_model_unavailable':
      return tr('请在提供商后台确认该模型已开通，或改用当前账号已开放的模型名称。', 'Confirm that this model is enabled for the provider account, or switch to a model name already enabled for this key.');
    case 'delivery_error':
      return tr('请检查对象存储配置、公网访问和写入权限。', 'Check object storage configuration, public access, and write permissions.');
    case 'generation_timeout':
      return tr('建议稍后重试，或降低任务复杂度后再生成。', 'Retry later or reduce task complexity before generating again.');
    default:
      return '';
  }
};

const displayErrorMessage = computed(() => {
  const providerMessage = providerFailureMessageLabel(providerFailureCode.value);
  if (providerMessage) return providerMessage;
  return orderStore.currentOrder?.error_message || tr('服务暂不可用，请稍后重试。', 'The AI is currently at rest.');
});

const effectiveHints = computed(() => {
  const o: any = orderStore.currentOrder;
  const hints: string[] = [];
  if (o?.director_mode) hints.push(tr('导演模式', 'Director Mode'));
  if (o?.subject_count) hints.push(`${tr('主体数', 'Subjects')}: ${o.subject_count}`);
  if (o?.couple_flow === 'remote') hints.push(tr('双人链路: 异地合拍', 'Couple flow: Remote join'));
  else if (o?.couple_flow === 'local') hints.push(tr('双人链路: 本机双传', 'Couple flow: Local dual upload'));
  if (o?.effective_scene_source) hints.push(`${tr('场景', 'Scene')}: ${sourceLabel(o.effective_scene_source)}`);
  if (o?.effective_outfit_source) hints.push(`${tr('服装', 'Outfit')}: ${sourceLabel(o.effective_outfit_source)}`);
  const ignored = Array.isArray(o?.ignored_inputs)
    ? o.ignored_inputs.map((item: string) => ignoredInputLabel(item)).filter(Boolean)
    : [];
  if (ignored.length) {
    hints.push(`${tr('已忽略', 'Ignored')}: ${ignored.join(' / ')}`);
  }
  if (o?.effective_scene_preset_title && o?.effective_scene_source && o.effective_scene_source !== 'upload' && o.effective_scene_source !== 'text') {
    hints.push(`${tr('场景预设', 'Scene preset')}: ${o.effective_scene_preset_title}`);
  }
  if (o?.effective_outfit_preset_title && o?.effective_outfit_source && o.effective_outfit_source !== 'upload' && o.effective_outfit_source !== 'text') {
    hints.push(`${tr('服装预设', 'Outfit preset')}: ${o.effective_outfit_preset_title}`);
  }
  if (typeof o?.effective_scene_ip_weight === 'number' && o?.effective_scene_source && o.effective_scene_source !== 'text') {
    hints.push(`${tr('场景 IP 权重', 'Scene IP weight')}: ${o.effective_scene_ip_weight.toFixed(2)}`);
  }
  if (typeof o?.effective_outfit_ip_weight === 'number' && o?.effective_outfit_source && o.effective_outfit_source !== 'text') {
    hints.push(`${tr('服装 IP 权重', 'Outfit IP weight')}: ${o.effective_outfit_ip_weight.toFixed(2)}`);
  }
  if (Array.isArray(o?.director_decision_hints)) {
    hints.push(...o.director_decision_hints.map((item: string) => directorDecisionHintLabel(item)).filter(Boolean));
  }
  return hints;
});

const failureHints = computed(() => {
  const chips: string[] = [];
  const providerHint = providerFailureHintLabel(providerFailureCode.value);
  if (providerHint) chips.push(providerHint);
  const reasons = Array.isArray(orderStore.currentOrder?.qa_last_reasons)
    ? orderStore.currentOrder?.qa_last_reasons || []
    : [];
  chips.push(...reasons.map((item: string) => qaReasonLabel(item)).filter(Boolean));
  if (chips.length && orderStore.currentOrder?.qa_attempt_count) {
    chips.push(`${tr('重试次数', 'Attempts')}: ${orderStore.currentOrder.qa_attempt_count}`);
  }
  return chips;
});

const failureActionHints = computed(() => {
  const advice: string[] = [];
  const providerAdvice = providerFailureActionLabel(providerFailureCode.value);
  if (providerAdvice) advice.push(providerAdvice);
  const reasons = Array.isArray(orderStore.currentOrder?.qa_last_reasons)
    ? orderStore.currentOrder?.qa_last_reasons || []
    : [];
  if (reasons.includes('fused_faces') || reasons.includes('identity_swap') || reasons.includes('identity_mismatch')) {
    advice.push(tr('双人请更换差异更明显的正脸自拍', 'Use two more distinct front-facing selfies for couple mode'));
  }
  if (reasons.includes('body_fusion') || reasons.includes('extra_limbs')) {
    advice.push(tr('重拍半身或全身照，避免遮挡手臂', 'Retake half/full-body photos and keep arms unobstructed'));
  }
  if (reasons.includes('cropped_face') || reasons.includes('headless')) {
    advice.push(tr('请保留完整头部与肩部，不要贴边裁切', 'Keep the full head and shoulders inside the frame'));
  }
  if (reasons.includes('bad_hands')) {
    advice.push(tr('上传更自然的站姿，手部尽量自然下垂', 'Use a more natural standing pose with visible hands'));
  }
  if (reasons.includes('dress_exposure_error')) {
    advice.push(tr('请选择更保守的婚纱模板或换一张遮挡更少的清晰照片', 'Choose a safer dress style or upload a clearer, less occluded photo'));
  }
  return advice;
});

let textInterval: any;
let progressInterval: any;

const startAnimations = () => {
  textInterval = setInterval(() => {
    currentTextIndex.value = (currentTextIndex.value + 1) % loadingTexts.value.length;
  }, 3000);
  progressInterval = setInterval(() => {
    if (progressStep.value < 4) progressStep.value++;
    else progressStep.value = 1;
  }, 1500);
};

const stopAnimations = () => {
  clearInterval(textInterval);
  clearInterval(progressInterval);
};

const reveal = () => {
  if (revealed.value || curtainOpening.value) return;
  curtainOpening.value = true;
  setTimeout(() => {
    revealed.value = true;
    curtainOpening.value = false;
  }, 450);
};

watch(
  () => orderStore.currentOrder?.id,
  () => {
    revealed.value = false;
    curtainOpening.value = false;
  }
);

const submitLead = async () => {
  const payload = {
    name: (leadForm.value.name || '').trim(),
    phone: (leadForm.value.phone || '').trim(),
    city: (leadForm.value.city || '').trim(),
    wedding_date: (leadForm.value.wedding_date || '').trim(),
    source_page: leadAttribution.value.source_page,
    source_slot: leadAttribution.value.source_slot || null,
    source_reco_id: leadAttribution.value.source_reco_id || null,
    source_reco_name: leadAttribution.value.source_reco_name || null,
    template_id: orderStore.currentOrder?.template_id || null,
    order_id: orderStore.currentOrder?.id || null,
  };
  if (!payload.name || !payload.phone || !payload.city) {
    uni.showToast({ title: tr('请填写姓名、手机号和城市', 'Please fill name, phone, city'), icon: 'none' });
    return;
  }
  if (!leadConsentAccepted.value) {
    uni.showToast({ title: tr('请先同意隐私政策与服务条款', 'Accept the legal terms first'), icon: 'none' });
    return;
  }
  if (!/^\+?[0-9\- ]{6,20}$/.test(payload.phone)) {
    uni.showToast({ title: tr('手机号格式不正确', 'Invalid phone format'), icon: 'none' });
    return;
  }
  submittingLead.value = true;
  try {
    lastCity.value = payload.city || lastCity.value;
    await post('/leads/submit', { ...payload, privacy_accepted: true });
    uni.showToast({ title: tr('已领取补贴', 'Allowance Claimed!'), icon: 'success' });
    leadForm.value = { name: '', phone: '', city: '', wedding_date: '' };
    leadConsentAccepted.value = false;
    leadAttribution.value = {
      source_page: 'preview',
      source_slot: 'lead_form',
      source_reco_id: '',
      source_reco_name: '',
    };
  } catch (e) {
    console.error(e);
    uni.showToast({ title: tr('提交失败', 'Submission failed'), icon: 'none' });
  } finally {
    submittingLead.value = false;
  }
};

const viewFullscreen = () => {
  const url = orderStore.isCompleted ? hdImageUrl.value : previewImageUrl.value;
  uni.previewImage({ urls: [url], current: url });
};

const guessFileName = (url: string, fallback = 'ai-wedding-studio-hd.jpg') => {
  try {
    const clean = (url || '').split('?')[0].split('#')[0];
    const maybe = clean.substring(clean.lastIndexOf('/') + 1);
    if (!maybe) return fallback;
    if (maybe.includes('.')) return maybe;
    return fallback;
  } catch {
    return fallback;
  }
};

const getPosterImageUrl = () => hdImageUrl.value || afterImageUrl.value || '';

const getPosterFileName = (url: string) =>
  guessFileName(url, `ai-wedding-studio-poster-${Date.now()}.png`).replace(/\.[^.]+$/, '.png');

const ensurePosterQrCode = async (): Promise<string> => {
  if (qrCodeUrl.value) return qrCodeUrl.value;
  const shareUrl = getShareUrl();
  if (!shareUrl) return '';
  qrCodeUrl.value = await QRCode.toDataURL(shareUrl, { margin: 1, scale: 2 });
  return qrCodeUrl.value;
};

const getImageInfoAsync = (src: string) =>
  new Promise<UniApp.GetImageInfoSuccessData>((resolve, reject) => {
    uni.getImageInfo({
      src,
      success: resolve,
      fail: reject,
    });
  });

const canvasToTempFilePathAsync = () =>
  new Promise<string>((resolve, reject) => {
    uni.canvasToTempFilePath(
      {
        canvasId: posterCanvasId,
        width: posterCanvasWidth,
        height: posterCanvasHeight,
        destWidth: posterCanvasWidth,
        destHeight: posterCanvasHeight,
        success: (res) => resolve(res.tempFilePath),
        fail: reject,
      },
      undefined as any
    );
  });

const renderPosterWithUniCanvas = async (imagePath: string, qrPath: string) => {
  const ctx = uni.createCanvasContext(posterCanvasId);
  ctx.setFillStyle('#ffffff');
  ctx.fillRect(0, 0, posterCanvasWidth, posterCanvasHeight);
  ctx.drawImage(imagePath, 0, 0, posterCanvasWidth, posterImageHeight);

  ctx.setFillStyle('#111111');
  ctx.fillRect(0, posterImageHeight, posterCanvasWidth, posterCanvasHeight - posterImageHeight);

  ctx.setFillStyle('#ffffff');
  ctx.setFontSize(44);
  ctx.fillText('AI Wedding Studio', 60, posterImageHeight + 88);
  ctx.setFillStyle('rgba(255,255,255,0.72)');
  ctx.setFontSize(24);
  ctx.fillText(tr('Studio 成片 · 2026', 'Studio Masterpiece · 2026'), 60, posterImageHeight + 134);
  ctx.fillText(tr('扫码查看作品详情', 'Scan to view this showcase'), 60, posterImageHeight + 180);

  if (qrPath) {
    ctx.setFillStyle('#ffffff');
    ctx.fillRect(posterCanvasWidth - 216, posterImageHeight + 30, 180, 180);
    ctx.drawImage(qrPath, posterCanvasWidth - 204, posterImageHeight + 42, posterQrSize, posterQrSize);
  }

  await new Promise<void>((resolve) => ctx.draw(false, () => resolve()));
  return canvasToTempFilePathAsync();
};

// #ifdef H5
const loadPosterAssetForH5 = async (src: string): Promise<{ image: HTMLImageElement; revoke: () => void }> => {
  const loadImage = (url: string, crossOrigin = false) =>
    new Promise<HTMLImageElement>((resolve, reject) => {
      const image = new Image();
      if (crossOrigin) image.crossOrigin = 'anonymous';
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error(`asset_load_failed:${url}`));
      image.src = url;
    });

  try {
    const response = await fetch(src);
    if (!response.ok) throw new Error(`asset_fetch_failed:${response.status}`);
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const image = await loadImage(objectUrl);
    return {
      image,
      revoke: () => URL.revokeObjectURL(objectUrl),
    };
  } catch (error) {
    const image = await loadImage(src, true);
    return {
      image,
      revoke: () => {},
    };
  }
};

const exportPosterForH5 = async (imageUrl: string, qrUrl: string) => {
  const browser = globalThis as any;
  const doc = browser?.document;
  const canvas = doc?.getElementById(posterCanvasId) as HTMLCanvasElement | null;
  if (!canvas) throw new Error('poster_canvas_missing');

  canvas.width = posterCanvasWidth;
  canvas.height = posterCanvasHeight;

  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('poster_context_missing');

  const [imageAsset, qrAsset] = await Promise.all([
    loadPosterAssetForH5(imageUrl),
    qrUrl ? loadPosterAssetForH5(qrUrl) : Promise.resolve(null),
  ]);

  try {
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, posterCanvasWidth, posterCanvasHeight);
    ctx.drawImage(imageAsset.image, 0, 0, posterCanvasWidth, posterImageHeight);

    ctx.fillStyle = '#111111';
    ctx.fillRect(0, posterImageHeight, posterCanvasWidth, posterCanvasHeight - posterImageHeight);

    ctx.fillStyle = '#ffffff';
    ctx.font = '600 44px Georgia, serif';
    ctx.fillText('AI Wedding Studio', 60, posterImageHeight + 88);

    ctx.fillStyle = 'rgba(255,255,255,0.72)';
    ctx.font = '400 24px Arial, sans-serif';
    ctx.fillText(tr('Studio 成片 · 2026', 'Studio Masterpiece · 2026'), 60, posterImageHeight + 134);
    ctx.fillText(tr('扫码查看作品详情', 'Scan to view this showcase'), 60, posterImageHeight + 180);

    if (qrAsset) {
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(posterCanvasWidth - 216, posterImageHeight + 30, 180, 180);
      ctx.drawImage(qrAsset.image, posterCanvasWidth - 204, posterImageHeight + 42, posterQrSize, posterQrSize);
    }

    const dataUrl = canvas.toDataURL('image/png');
    const link = doc.createElement('a');
    link.href = dataUrl;
    link.download = getPosterFileName(imageUrl);
    doc.body.appendChild(link);
    link.click();
    doc.body.removeChild(link);
  } finally {
    imageAsset.revoke();
    qrAsset?.revoke();
  }
};
// #endif

const downloadImageUrl = async (url: string, fallbackName = 'ai-wedding-studio-hd.jpg') => {
  if (!canDownload.value) {
    showPaymentModal.value = true;
    uni.showToast({ title: tr('请先充值解锁高清下载', 'Top up to unlock HD download'), icon: 'none' });
    return;
  }
  if (!url) {
    uni.showToast({ title: tr('暂无可用图片', 'No image available'), icon: 'none' });
    return;
  }

  // #ifdef H5
  try {
    const browser = globalThis as any;
    const doc = browser?.document;
    if (!doc) throw new Error('document_unavailable');
    const link = doc.createElement('a');
    link.href = url;
    link.download = guessFileName(url, fallbackName);
    link.target = '_blank';
    link.rel = 'noopener';
    doc.body.appendChild(link);
    link.click();
    doc.body.removeChild(link);
    uni.showToast({ title: tr('开始下载', 'Download started'), icon: 'success' });
    return;
  } catch (e) {
    console.error(e);
    const browser = globalThis as any;
    browser?.open?.(url, '_blank');
    uni.showToast({ title: tr('已在新标签页打开', 'Opened in new tab'), icon: 'none' });
    return;
  }
  // #endif

  // #ifndef H5
  uni.showLoading({ title: tr('下载中...', 'Downloading...') });
  try {
    const result = await uni.downloadFile({ url });
    if ((result as any)?.statusCode !== 200) {
      throw new Error(`download_failed_${(result as any)?.statusCode}`);
    }
    const tempPath = (result as any)?.tempFilePath;
    if (!tempPath) {
      throw new Error('missing_temp_file');
    }
    await uni.saveImageToPhotosAlbum({ filePath: tempPath });
    uni.showToast({ title: tr('已保存到相册', 'Saved to album'), icon: 'success' });
  } catch (e) {
    console.error(e);
    uni.showModal({
      title: tr('下载', 'Download'),
      content: tr('保存失败，请检查相册权限后重试。', 'Save failed. Please check album permission and retry.'),
      showCancel: false,
    });
  } finally {
    uni.hideLoading();
  }
  // #endif
};

const downloadHD = async () => {
  await downloadImageUrl(hdImageUrl.value || afterImageUrl.value, 'ai-wedding-studio-hd.jpg');
};

const openLivePortraitAssetUrl = (url: string) => {
  const lowerUrl = (url || '').toLowerCase().split('?')[0];
  const isVideoAsset = /(\.mp4|\.mov|\.m4v|\.webm|\.gif)$/.test(lowerUrl);
  if (isVideoAsset) {
    // #ifdef H5
    window.open(url, '_blank');
    return;
    // #endif
    // #ifndef H5
    // @ts-ignore
    if (uni.previewMedia) {
      // @ts-ignore
      uni.previewMedia({ sources: [{ url, type: 'video' }] });
    } else {
      uni.setClipboardData({ data: url });
      uni.showToast({ title: tr('链接已复制', 'Link copied'), icon: 'none' });
    }
    return;
    // #endif
  }

  // #ifdef H5
  window.open(url, '_blank');
  return;
  // #endif
  // #ifndef H5
  uni.previewImage({ urls: [url], current: url });
  // #endif
};

const openLivePortraitJob = async (job: any) => {
  if (job?.status === 'COMPLETED' && job?.video_url) {
    openLivePortraitAssetUrl(String(job.video_url));
    return;
  }
  const refundNotice = job?.refunded_credits
    ? tr(`本次已退回 ${job.refunded_credits} 积分。`, `Refunded ${job.refunded_credits} credits.`)
    : '';
  uni.showModal({
    title: tr('动态人像', 'Live Portrait'),
    content: [
      livePortraitStatusLabel(job?.status),
      job?.failure_code ? livePortraitFailureMessage(job.failure_code) : '',
      job?.failure_action ? livePortraitFailureActionLabel(job.failure_action) : '',
      refundNotice,
    ]
      .filter(Boolean)
      .join('\n'),
    showCancel: false,
  });
};

const fetchLivePortraitHistory = async () => {
  try {
    const jobs = await get<any[]>('/live_portrait/list?limit=3', { showLoading: false, showError: false } as any);
    livePortraitHistory.value = Array.isArray(jobs) ? jobs : [];
  } catch (e) {
    livePortraitHistory.value = [];
  }
};

const openLivePortrait = async () => {
  if (livePortraitBusy.value) return;
  livePortraitBusy.value = true;
  try {
    await post('/analytics/click', {
      event_type: 'live_portrait_click',
      source_page: 'preview',
      template_id: orderStore.currentOrder?.template_id || null,
    }, { showLoading: false, showError: false } as any);
  } catch (e) {
    // silent
  }

  try {
    const res: any = await post('/live_portrait/generate', {
      image_url: afterImageUrl.value,
      seconds: 5,
    }, { showLoading: false, showError: false } as any);
    if (!res?.success || !res?.job_id) {
      uni.showModal({
        title: tr('动态人像', 'Live Portrait'),
        content: res?.message || tr('服务暂未启用。', 'Service is not enabled yet.'),
        showCancel: false,
      });
      return;
    }

    if (res?.reused && res?.status === 'COMPLETED' && res?.video_url) {
      uni.showToast({
        title: tr('已复用最近生成结果', 'Reused recent result'),
        icon: 'none',
      });
      openLivePortraitAssetUrl(String(res.video_url));
      await fetchLivePortraitHistory();
      return;
    }

    try {
      await post('/analytics/click', {
        event_type: 'live_portrait_queued',
        source_page: 'preview',
        template_id: orderStore.currentOrder?.template_id || null,
        meta: {
          job_id: res.job_id,
          credits_cost: res.credits_cost || null,
          status: res.status || null,
          reused: !!res.reused,
        },
      }, { showLoading: false, showError: false } as any);
    } catch (e) {
      // silent
    }

    uni.showLoading({ title: tr('生成中...', 'Animating...') });
    const deadline = Date.now() + 90_000;
    while (Date.now() < deadline) {
      const job: any = await get(`/live_portrait/${encodeURIComponent(res.job_id)}`, { showLoading: false, showError: false } as any);
      if (job?.status === 'COMPLETED' && job?.video_url) {
        uni.hideLoading();
        try {
          await post('/analytics/click', {
            event_type: 'live_portrait_completed',
            source_page: 'preview',
            template_id: orderStore.currentOrder?.template_id || null,
            meta: { job_id: job?.job_id || res.job_id, video_url: job?.video_url || null },
          }, { showLoading: false, showError: false } as any);
        } catch (e) {
          // silent
        }
        const url = job.video_url as string;
        uni.showActionSheet({
          itemList: [tr('查看结果', 'Open result'), tr('复制链接', 'Copy Link')],
          success: async (r) => {
            if (r.tapIndex === 0) {
              openLivePortraitAssetUrl(url);
            } else {
              uni.setClipboardData({ data: url });
              uni.showToast({ title: tr('链接已复制', 'Link copied'), icon: 'none' });
            }
          },
          fail: () => {
            uni.setClipboardData({ data: url });
            uni.showToast({ title: tr('链接已复制', 'Link copied'), icon: 'none' });
          },
        });
        await fetchLivePortraitHistory();
        return;
      }
      if (job?.status === 'FAILED') {
        uni.hideLoading();
        try {
          await post('/analytics/click', {
            event_type: 'live_portrait_failed',
            source_page: 'preview',
            template_id: orderStore.currentOrder?.template_id || null,
            meta: {
              job_id: job?.job_id || res.job_id,
              failure_code: job?.failure_code || null,
              refunded_credits: job?.refunded_credits || 0,
            },
          }, { showLoading: false, showError: false } as any);
        } catch (e) {
          // silent
        }
        const refundNotice = job?.refunded_credits
          ? tr(`本次已退回 ${job.refunded_credits} 积分。`, `Refunded ${job.refunded_credits} credits.`)
          : tr('如已扣除积分，系统将自动退回。', 'If credits were charged, they will be refunded automatically.');
        uni.showModal({
          title: tr('动态人像', 'Live Portrait'),
          content: [livePortraitFailureMessage(job?.failure_code), livePortraitFailureActionLabel(job?.failure_action), refundNotice]
            .filter(Boolean)
            .join('\n'),
          showCancel: false,
        });
        await fetchLivePortraitHistory();
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
    uni.hideLoading();
    try {
      await post('/analytics/click', {
        event_type: 'live_portrait_timeout',
        source_page: 'preview',
        template_id: orderStore.currentOrder?.template_id || null,
        meta: { job_id: res.job_id },
      }, { showLoading: false, showError: false } as any);
    } catch (e) {
      // silent
    }
    uni.showModal({
      title: tr('动态人像', 'Live Portrait'),
      content: tr('仍在处理中，请稍后再试。', 'Still processing. Please try again in a moment.'),
      showCancel: false,
    });
  } catch (e) {
    uni.hideLoading();
    uni.showModal({
      title: tr('动态人像', 'Live Portrait'),
      content: tr('服务暂未启用。', 'Service is not enabled yet.'),
      showCancel: false,
    });
  } finally {
    fetchLivePortraitHistory();
    livePortraitBusy.value = false;
  }
};

const openPosterModal = async () => {
  if (!canDownload.value) {
    showPaymentModal.value = true;
    uni.showToast({ title: tr('请先充值解锁高清下载', 'Top up to unlock HD download'), icon: 'none' });
    return;
  }
  if (!hdImageUrl.value) {
    uni.showToast({ title: tr('暂无可用图片', 'No image available'), icon: 'none' });
    return;
  }
  showPosterModal.value = true;
  try {
    await ensurePosterQrCode();
  } catch (err) { console.error(err); }
};

const closePosterModal = () => showPosterModal.value = false;
const regenerate = () => uni.navigateBack();

const cityForReco = computed(() => {
  const fallback = tr('你的城市', 'your city');
  return (lastCity.value || leadForm.value.city || fallback).trim() || fallback;
});

const localRecoModeLabel = (mode?: string | null) => {
  switch (String(mode || '').trim()) {
    case 'offline': return tr('到店拍摄', 'In-studio');
    case 'travel': return tr('旅拍', 'Travel');
    case 'retouch': return tr('精修服务', 'Retouch');
    case 'remote': return tr('线上服务', 'Remote');
    default: return '';
  }
};

const localRecoMatchReasonLabel = (reason?: string | null) => {
  switch (String(reason || '').trim()) {
    case 'Same city match': return tr('同城匹配', 'Same city match');
    case 'Covers your city': return tr('覆盖你的城市', 'Covers your city');
    case 'High lead conversion': return tr('近期转化较好', 'High lead conversion');
    case 'Best style match': return tr('风格最匹配', 'Best style match');
    case 'Best use-case match': return tr('场景最匹配', 'Best use-case match');
    case 'Fast delivery option': return tr('支持加急交付', 'Fast delivery option');
    case 'Nationwide fallback': return tr('全国兜底服务', 'Nationwide fallback');
    default: return String(reason || '').trim();
  }
};

const localRecoRankingLabel = (factors?: string[]) => {
  const labels = (Array.isArray(factors) ? factors : []).map((item) => {
    switch (item) {
      case 'same_city': return tr('同城优先', 'Same city');
      case 'service_city': return tr('覆盖你的城市', 'Covers your city');
      case 'nationwide': return tr('全国兜底', 'Nationwide');
      case 'style_match': return tr('风格匹配', 'Style match');
      case 'use_case_match': return tr('场景匹配', 'Use-case match');
      case 'style_near_match': return tr('近似风格', 'Near match');
      case 'rush_ready': return tr('支持加急', 'Rush ready');
      case 'near_term': return tr('婚期临近友好', 'Near-term ready');
      case 'lead_conversion': return tr('近期转化较好', 'High conversion');
      case 'manual_boost': return tr('运营推荐', 'Ops boost');
      default: return '';
    }
  }).filter(Boolean);
  return labels.join(' · ');
};

const localRecoSupportLine = (reco: any) => {
  const parts: string[] = [];
  const modes = (Array.isArray(reco?.service_modes) ? reco.service_modes : [])
    .map((item: string) => localRecoModeLabel(item))
    .filter(Boolean);
  if (modes.length) parts.push(modes.join(' / '));
  const leadCount = Number(reco?.lead_count || 0);
  if (leadCount > 0) {
    parts.push(tr(`近 90 天线索 ${leadCount}`, `Leads in 90d: ${leadCount}`));
  }
  const tags = (Array.isArray(reco?.tags) ? reco.tags : []).filter(Boolean);
  if (tags.length) parts.push(tags.slice(0, 2).join(' · '));
  return parts.join(' · ');
};

const handleLocalRecoClick = async () => {
  leadAttribution.value = {
    source_page: 'preview',
    source_slot: 'local_reco_banner',
    source_reco_id: '',
    source_reco_name: '',
  };
  try {
    await post('/analytics/click', {
      event_type: 'local_reco_banner',
      source_page: 'preview',
      template_id: orderStore.currentOrder?.template_id || null,
      meta: { city: cityForReco.value },
    }, { showLoading: false, showError: false } as any);
  } catch (e) {
    // silent
  }
  uni.showModal({
    title: tr('本地推荐', 'Local Picks'),
    content: localRecos.value.length
      ? tr('点击影楼卡片可复制联系方式，我们会逐步扩展城市覆盖。', 'Tap a studio card to copy contact. We will expand coverage city by city.')
      : tr(`在下方填写城市，即可解锁 ${cityForReco.value} 的本地推荐。`, `Tell us your city in the form below to unlock curated picks for ${cityForReco.value}.`),
    showCancel: false,
  });
};

const handleLocalRecoItemClick = async (reco: any) => {
  leadAttribution.value = {
    source_page: 'preview',
    source_slot: 'local_reco_item',
    source_reco_id: reco?.id || '',
    source_reco_name: reco?.name || '',
  };
  try {
    await post('/analytics/click', {
      event_type: 'local_reco_item',
      source_page: 'preview',
      template_id: orderStore.currentOrder?.template_id || null,
      meta: { city: cityForReco.value, reco_id: reco?.id || null, reco_name: reco?.name || null },
    }, { showLoading: false, showError: false } as any);
  } catch (e) {
    // silent
  }

  const value = (reco?.cta_value || '').trim();
  if (value) {
    uni.setClipboardData({ data: value });
    uni.showToast({ title: tr('联系方式已复制', 'Contact copied'), icon: 'none' });
    return;
  }
  uni.showModal({
    title: reco?.name || tr('本地影楼', 'Local Studio'),
    content: reco?.highlight || tr('联系方式即将开放。', 'Contact coming soon.'),
    showCancel: false,
  });
};

const handleBannerClick = async () => {
  leadAttribution.value = {
    source_page: 'preview',
    source_slot: 'vip_studio_banner',
    source_reco_id: '',
    source_reco_name: '',
  };
  try {
    await post('/analytics/click', {
      event_type: 'vip_studio_banner',
      source_page: 'preview',
      template_id: orderStore.currentOrder?.template_id || null,
      meta: { city: cityForReco.value },
    }, { showLoading: false, showError: false } as any);
  } catch (e) {
    // silent
  }
  uni.pageScrollTo({
    selector: '.leads-capture-ritual',
    duration: 280,
  });
  uni.showToast({ title: tr('请先填写联系方式', 'Fill the contact form below'), icon: 'none' });
};

const savePoster = async () => {
  const imageUrl = getPosterImageUrl();
  if (!imageUrl) {
    uni.showToast({ title: tr('暂无可用图片', 'No image available'), icon: 'none' });
    return;
  }

  uni.showLoading({ title: tr('正在导出...', 'Exporting...') });
  try {
    const qrUrl = await ensurePosterQrCode();

    // #ifdef H5
    await exportPosterForH5(imageUrl, qrUrl);
    uni.showToast({ title: tr('海报已保存', 'Poster saved'), icon: 'success' });
    return;
    // #endif

    // #ifndef H5
    const [imageInfo, qrInfo] = await Promise.all([
      getImageInfoAsync(imageUrl),
      qrUrl ? getImageInfoAsync(qrUrl) : Promise.resolve(null),
    ]);
    const tempFilePath = await renderPosterWithUniCanvas(imageInfo.path, qrInfo?.path || '');
    await uni.saveImageToPhotosAlbum({ filePath: tempFilePath });
    uni.showToast({ title: tr('海报已保存到相册', 'Poster saved to album'), icon: 'success' });
    // #endif
  } catch (error) {
    console.error(error);
    uni.showModal({
      title: tr('保存海报', 'Save poster'),
      content: tr('自动导出失败，请改用截图保存海报。', 'Automatic export failed. Please use screenshot instead.'),
      showCancel: false,
    });
  } finally {
    uni.hideLoading();
  }
};

const getShareUrl = (): string => {
  // #ifdef H5
  return window.location.href;
  // #endif
  return '';
};

const fetchLocalRecos = async () => {
  const city = (lastCity.value || leadForm.value.city || '').trim();
  const weddingDate = (leadForm.value.wedding_date || '').trim();
  if (!city) {
    localRecos.value = [];
    return;
  }
  try {
    const templateId = (orderStore.currentOrder?.template_id || '').trim();
    const qs = `city=${encodeURIComponent(city)}&wedding_date=${encodeURIComponent(weddingDate)}&template_id=${encodeURIComponent(templateId)}&limit=3`;
    const res = await get<any[]>(`/recommendations/local_studios?${qs}`, { showLoading: false, showError: false } as any);
    localRecos.value = Array.isArray(res) ? res : [];
  } catch (e) {
    localRecos.value = [];
  }
};

watch(() => (lastCity.value || leadForm.value.city || '').trim(), () => { fetchLocalRecos(); });
watch(() => (leadForm.value.wedding_date || '').trim(), () => { fetchLocalRecos(); });
watch(() => orderStore.currentOrder?.template_id || '', () => { fetchLocalRecos(); });
watch(
  () => orderStore.isGenerating,
  (generating) => {
    if (generating) startAnimations();
    else stopAnimations();
  },
  { immediate: true }
);

const retry = () => {
  if (orderStore.currentOrder?.id) orderStore.startPolling(orderStore.currentOrder.id);
};

onMounted(() => {
  opsStore.fetchPublicConfig();
  const pages = getCurrentPages();
  const pageOptions = (pages[pages.length - 1] as any).options || {};
  const id = pageOptions.id || pageOptions.orderId;
  if (id) {
    orderStore.fetchOrder(id);
    orderStore.startPolling(id);
  }
  if (localRecoEnabled.value) fetchLocalRecos();
  if (livePortraitEnabled.value) fetchLivePortraitHistory();
});

onUnmounted(() => {
  orderStore.stopPolling();
  stopAnimations();
});
</script>

<style lang="scss" scoped>
.preview-sanctum { background-color: $uni-color-background; }

/* Ritual Loading */
.ritual-loading-view {
  display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 100px 32px;
}

.workflow-ritual-card {
  width: 100%; background: white; border-radius: $uni-border-radius-lg; padding: 60px 32px; text-align: center; box-shadow: $uni-shadow-lg;
}

.exhibition-orb-wrap {
  width: 140px; height: 140px; margin: 0 auto 40px; position: relative; display: flex; align-items: center; justify-content: center;
  .orb-pulse { position: absolute; width: 100%; height: 100%; border: 1px solid $uni-color-secondary; border-radius: 50%; animation: orb-expand 3s infinite ease-out; }
  .orb-core { width: 90px; height: 90px; background: linear-gradient(135deg, $uni-color-primary, $uni-color-secondary); border-radius: 50%; display: flex; align-items: center; justify-content: center; color: white; font-size: 40px; box-shadow: 0 10px 30px rgba(219, 39, 119, 0.3); }
}

@keyframes orb-expand {
  0% { transform: scale(0.7); opacity: 0; }
  50% { opacity: 0.5; }
  100% { transform: scale(1.3); opacity: 0; }
}

.ritual-status { font-size: 20px; color: $uni-text-color; margin-bottom: 8px; display: block; font-style: italic; }
.ritual-hint { font-size: 13px; color: $uni-text-color-muted; margin-bottom: 40px; display: block; letter-spacing: 0.05em; }
.ritual-policy { font-size: 11px; color: $uni-text-color-muted; margin-bottom: 14px; display: block; opacity: 0.8; letter-spacing: 0.05em; }
.ritual-bar-wrap { width: 100%; height: 4px; background: rgba(252, 231, 243, 0.8); border-radius: 100px; overflow: hidden; }
.ritual-bar-fill { height: 100%; background: $uni-color-primary; transition: width 0.8s cubic-bezier(0.4, 0, 0.2, 1); }

/* Exhibition Content */
.exhibition-content { padding: 32px 24px; max-width: 1320px; margin: 0 auto; }
.masterpiece-folio { background: white; border-radius: $uni-border-radius-lg; overflow: hidden; box-shadow: $uni-shadow-xl; margin-bottom: 32px; }
.folio-frame {
  aspect-ratio: 4/5;
  position: relative;
  background: $uni-color-background;
  overflow: hidden;

  .masterpiece-img { width: 100%; height: 100%; }
}

.reveal-blur {
  filter: blur(20px) saturate(1.05);
  transform: scale(1.06);
  opacity: 0.92;
}

.reveal-curtain {
  position: absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  z-index: 90;
  display: flex;
  align-items: stretch;
  justify-content: space-between;
  cursor: pointer;
}

.curtain-panel {
  width: 50%;
  background: rgba(255, 255, 255, 0.65);
  backdrop-filter: blur(14px);
  transition: transform 0.45s cubic-bezier(0.2, 0.7, 0.2, 1);

  &.left { transform-origin: left center; }
  &.right { transform-origin: right center; }
}

.reveal-curtain.opening {
  .curtain-panel.left { transform: scaleX(0); }
  .curtain-panel.right { transform: scaleX(0); }
  .curtain-label { opacity: 0; transform: translateY(-6px); }
}

.curtain-label {
  position: absolute;
  top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  color: $uni-text-color;
  transition: opacity 0.2s ease, transform 0.45s cubic-bezier(0.2, 0.7, 0.2, 1);

  .ready {
    font-size: 28px;
    display: block;
    margin-bottom: 6px;
    font-style: italic;
  }

  .tap {
    display: block;
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 0.18em;
    opacity: 0.75;
    text-transform: uppercase;
  }
}

.exhibition-tag {
  position: absolute; top: 20px; left: 20px; padding: 8px 16px; border-radius: 100px; font-size: 11px; font-weight: 800; letter-spacing: 0.1em; z-index: 100;
  &.draft { background: rgba($uni-color-accent, 0.9); color: white; }
  &.hd { background: rgba($uni-color-primary, 0.9); color: white; }
}

.artistic-watermark {
  position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: grid; grid-template-columns: repeat(3, 1fr); align-items: center; justify-items: center; pointer-events: none; opacity: 0.15; transform: rotate(-20deg);
  .watermark-item { font-size: 13px; font-weight: 900; color: white; text-transform: uppercase; }
}

.folio-credenza {
  padding: 32px 24px;
  text-align: center;
  .folio-title { font-size: 22px; color: $uni-text-color; margin-bottom: 12px; display: block; }
  .folio-desc { font-size: 14px; color: $uni-text-color-muted; line-height: 1.7; display: block; }

  .folio-meta {
    display: flex;
    justify-content: center;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 8px;
  }
  .meta-chip {
    padding: 6px 12px;
    border-radius: 999px;
    background: rgba($uni-color-primary, 0.06);
    border: 1px solid rgba($uni-color-primary, 0.15);
    font-size: 11px;
    font-weight: 800;
    color: $uni-text-color;
    opacity: 0.75;
    letter-spacing: 0.05em;
  }
  .error-meta { margin-top: 16px; }
}

/* Actions */
.exhibition-actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 32px;
  .e-action-btn {
    height: 56px;
    border-radius: 100px;
    font-size: 12px;
    letter-spacing: 0.08em;
  }
  .e-action-btn.primary {
    grid-column: 1 / -1;
  }
}
.preview-desktop-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 24px;
  align-items: start;
}

.preview-main-col {
  min-width: 0;
}

.preview-side-col {
  position: sticky;
  top: 88px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.secondary-ritual-entry { text-align: center; margin-bottom: 40px; .entry-back { font-size: 14px; font-weight: 700; color: $uni-text-color-muted; opacity: 0.6; } }

/* STUDIO 3.0 LEADS FORM */
.leads-capture-ritual {
  background: linear-gradient(135deg, white, #fffafa); border-radius: $uni-border-radius-lg; padding: 32px; margin-bottom: 40px; border: 1px solid rgba($uni-color-primary, 0.1);
  .leads-header { margin-bottom: 24px; text-align: center; .l-title { font-size: 18px; font-weight: 800; color: $uni-text-color; display: block; margin-bottom: 4px; } .l-subtitle { font-size: 12px; color: $uni-color-primary; font-weight: 600; text-transform: uppercase; } }
}

.leads-form {
  display: flex; flex-direction: column; gap: 16px;
  .l-input { height: 50px; background: #f8f8f8; border-radius: 8px; padding: 0 16px; font-size: 14px; border: 1px solid transparent; transition: all 0.3s; &:focus { border-color: $uni-color-primary; background: white; } }
  .l-row { display: flex; gap: 12px; }
  .l-submit-btn { background: $uni-text-color; color: white; border-radius: 8px; padding: 0 24px; font-size: 12px; font-weight: 800; height: 50px; display: flex; align-items: center; justify-content: center; flex: 1; }
}

/* Concierge */
.concierge-card {
  background: linear-gradient(135deg, $uni-text-color, #2c0616); border-radius: $uni-border-radius-lg; padding: 28px; color: white;
  .concierge-inner { display: flex; justify-content: space-between; align-items: center; }
  .c-tag { font-size: 10px; background: rgba(255,255,255,0.15); padding: 4px 12px; border-radius: 100px; display: inline-block; margin-bottom: 12px; letter-spacing: 0.1em; text-transform: uppercase; }
  .c-title { font-size: 18px; font-weight: 600; font-family: $uni-font-family; display: block; margin-bottom: 4px; }
  .c-desc { font-size: 12px; opacity: 0.7; }
  .c-arrow { font-size: 24px; color: $uni-color-secondary; }
}
.concierge-card.local-reco {
  margin-top: 14px;
  background: linear-gradient(135deg, #1b0d14, #3b0b1f);

  .reco-list {
    margin-top: 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .reco-item {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    padding: 12px 14px;
    border-radius: 14px;
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.10);
  }

  .reco-left { flex: 1; min-width: 0; }
  .reco-name {
    font-size: 13px;
    font-weight: 900;
    color: white;
    display: block;
    margin-bottom: 4px;
    line-height: 1.2;
  }
  .reco-highlight,
  .reco-tags {
    font-size: 11px;
    color: rgba(255, 255, 255, 0.70);
    display: block;
    line-height: 1.4;
  }

  .reco-cta {
    font-size: 11px;
    font-weight: 900;
    letter-spacing: 0.06em;
    color: $uni-color-accent;
    background: rgba($uni-color-accent, 0.16);
    border: 1px solid rgba($uni-color-accent, 0.22);
    padding: 6px 10px;
    border-radius: 999px;
    align-self: center;
    white-space: nowrap;
  }
}
.live-history-card { margin-top: 14px; }

/* Error Ceremony */
.error-ceremony {
  padding: 100px 40px; text-align: center;
  .c-icon { font-size: 60px; color: $uni-color-primary; opacity: 0.2; display: block; margin-bottom: 32px; }
  .c-heading { font-size: 24px; color: $uni-text-color; margin-bottom: 16px; display: block; }
  .c-msg { font-size: 14px; color: $uni-text-color-muted; margin-bottom: 40px; display: block; line-height: 1.6; }
  .error-meta {
    display: flex;
    justify-content: center;
    flex-wrap: wrap;
    gap: 10px;
    margin: -18px 0 24px;
    .meta-chip {
      padding: 6px 12px;
      border-radius: 999px;
      background: rgba($uni-color-primary, 0.06);
      border: 1px solid rgba($uni-color-primary, 0.15);
      font-size: 11px;
      font-weight: 800;
      color: $uni-text-color;
      opacity: 0.75;
    }
  }
  .retry-btn { border-radius: 100px; width: 100%; height: 56px; }
}

/* Poster Ritual */
.poster-sheet-ritual {
  position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.85); backdrop-filter: blur(10px); z-index: 2000; display: flex; align-items: flex-end;
}

.sheet-ritual-body {
  width: 100%; background: white; border-top-left-radius: 32px; border-top-right-radius: 32px; padding: 40px 32px; animation: sheet-slide-up 0.5s cubic-bezier(0.4, 0, 0.2, 1);
}

@keyframes sheet-slide-up { from { transform: translateY(100%); } to { transform: translateY(0); } }

.sheet-ritual-header {
  display: flex; justify-content: space-between; align-items: center; margin-bottom: 32px;
  .s-title { font-size: 22px; color: $uni-text-color; }
  .s-close { font-size: 32px; color: $uni-color-border; padding: 10px; }
}

.exhibit-canvas {
  background: white; border-radius: 16px; overflow: hidden; margin-bottom: 40px; border: 1px solid $uni-color-border;
  .canvas-image-wrap { aspect-ratio: 4/5; width: 100%; overflow: hidden; .canvas-image { width: 100%; height: 100%; } }
}

.canvas-info-wrap {
  padding: 24px; display: flex; justify-content: space-between; align-items: center; background: $uni-text-color; color: white;
  .c-brand { font-size: 16px; letter-spacing: 0.2em; display: block; margin-bottom: 4px; }
  .c-edition { font-size: 10px; opacity: 0.6; text-transform: uppercase; letter-spacing: 0.1em; }
  .c-qr { width: 56px; height: 56px; border-radius: 6px; background: white; padding: 3px; }
}

.s-final-btn { width: 100%; height: 60px; border-radius: 100px; font-size: 14px; letter-spacing: 0.15em; }
.shadow-glow { box-shadow: 0 12px 30px rgba(219, 39, 119, 0.25); }

.poster-export-canvas {
  position: fixed;
  left: -9999px;
  top: -9999px;
  opacity: 0;
  pointer-events: none;
}

@media (max-width: 1120px) {
  .preview-desktop-layout {
    grid-template-columns: 1fr;
  }

  .preview-side-col {
    position: static;
  }
}
</style>

