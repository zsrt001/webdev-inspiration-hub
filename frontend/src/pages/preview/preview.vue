<template>
  <view class="app-container preview-sanctum" style="padding-top: 64px;">
    <NavBar ref="navBarRef" @show-payment="openPaymentModal" />
    <PaymentModal v-if="billingAvailable" :visible="showPaymentModal" @close="showPaymentModal = false" @purchase-complete="onPurchaseComplete" />

    <view v-if="orderStore.isGenerating" class="ritual-loading-view">
      <view class="workflow-ritual-card">
        <view class="exhibition-orb-wrap">
          <view class="orb-pulse"></view>
          <view class="orb-core">✦</view>
        </view>
        <!-- STUDIO 3.0 STORY LOADER -->
        <text class="ritual-status heading-serif">{{ generationStageLabel || studioLoadingText }}</text>
        <text class="ritual-hint">{{ generationStageHint }}</text>
        <text class="ritual-policy">{{ tr('入队即扣费 · 失败自动退款', 'Charged on queueing · Auto-refund on failure') }}</text>
        <view class="ritual-bar-wrap">
          <view class="ritual-bar-fill" :style="{ width: generationProgressWidth }"></view>
        </view>
      </view>
    </view>

    <view v-else-if="hasRenderableOutput" class="exhibition-content">
      <view class="masterpiece-folio">
        <!-- STUDIO 3.0 REVEAL: CURTAIN -> COMPARE SLIDER -->
        <view class="folio-frame shadow-xl">
          <template v-if="revealed">
            <image
              class="masterpiece-img"
              :src="afterImageUrl"
              mode="aspectFit"
            />
          </template>
          <template v-else>
            <image class="masterpiece-img reveal-blur" :src="afterImageUrl" mode="aspectFit" />
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
            <text v-for="n in 9" :key="n" class="watermark-item">{{ tr('AI 婚纱预览', 'AI WEDDING PREVIEW') }}</text>
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
          <view v-if="creationAvailable" class="secondary-ritual-entry" @tap="regenerate">
            <text class="entry-back">→{{ tr('重新选择风格', 'Reselect Aesthetic') }}</text>
          </view>
        </view>

        <view class="preview-side-col">
          <!-- Exhibition Actions -->
          <view v-if="orderStore.isCompleted" class="exhibition-actions">
            <view class="delivery-note">
              <text class="delivery-note-title">{{ tr('最终主成片：3:4 竖版', 'Final master: 3:4 portrait') }}</text>
              <text class="delivery-note-copy">{{ tr('下方比例为下载裁切版本，不作为最终成片验收。', 'Other ratios below are download crops, not final master outputs.') }}</text>
            </view>
            <button v-if="canDownload" class="btn btn-primary e-action-btn primary shadow-glow" @tap="downloadHD">
              {{ tr('下载高清图', 'DEVELOP HD PRINT') }}
            </button>
            <button v-else-if="billingAvailable" class="btn btn-primary e-action-btn primary shadow-glow" @tap="requestUnlockDownload">
              {{ tr('充值解锁高清下载', 'Unlock HD Download') }}
            </button>
            <button v-else class="btn btn-primary e-action-btn primary" disabled>
              {{ tr('高清下载暂未开放', 'HD download unavailable') }}
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
          </view>

          <view v-if="creationAvailable && orderStore.isCompleted && abVariantOptions.length" class="ab-compare-card shadow-md">
            <view class="c-tag">{{ tr('风格对比', 'Style A/B') }}</view>
            <text class="c-title">{{ tr('用同一张图再试 2 个稳定模板', 'Try two stable variants with this photo') }}</text>
            <text class="c-desc">{{ tr('选择会记录到模板排序，后续优先推荐更受欢迎的风格。', 'Your pick feeds future template ranking.') }}</text>
            <view class="ab-variant-list">
              <button
                v-for="variant in abVariantOptions"
                :key="variant.id"
                class="btn btn-outline e-action-btn secondary variant-btn"
                @tap="startAbVariant(variant.id)"
              >
                {{ variant.title }}
              </button>
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
        <button v-if="creationAvailable" class="btn btn-primary retry-btn" @tap="regenerate">{{ tr('换图或换模板重试', 'Retry with better input') }}</button>
        <button class="btn btn-outline retry-btn secondary-retry" @tap="retry">{{ tr('刷新当前任务', 'Refresh current task') }}</button>
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
                <text class="c-brand heading-serif">VowPic Studio</text>
                <text class="c-edition">{{ tr('Studio 成片 · 2026', 'Studio Masterpiece · 2026') }}</text>
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
import { useTemplateStore } from '../../stores/template';
import { useOpsStore } from '../../stores/ops';
import NavBar from '../../components/NavBar.vue';
import PaymentModal from '../../components/PaymentModal.vue';
import {
  deliveryVariantAssets,
  displayAsset,
  finalMasterAsset,
  isOrderManualOrFailed,
  previewAsset,
} from '../../contracts/order';
import { trackEvent } from '../../utils/analytics';
import { resolvePublicUrl } from '../../utils/api';

const orderStore = useOrderStore();
const i18nStore = useI18nStore();
const templateStore = useTemplateStore();
const opsStore = useOpsStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);
const creationAvailable = computed(() => opsStore.creationAvailable);
const billingAvailable = computed(() => opsStore.billingAvailable);
const privateDownloadAvailable = computed(() => opsStore.privateDownloadAvailable);
const navBarRef = ref<InstanceType<typeof NavBar> | null>(null);
const showPaymentModal = ref(false);
const showPosterModal = ref(false);
const progressStep = ref(1);
const trackedCompletedOrderId = ref('');

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

const loadingTexts = computed(() => [
  tr('扫描人像特征中...', 'Scanning facial features...'),
  tr('匹配婚纱细节中...', 'Tailoring the wedding dress...'),
  tr('调整影棚光效中...', 'Adjusting studio lighting...'),
  tr('冲洗成片中...', 'Developing film...'),
]);

const studioLoadingText = computed(() => loadingTexts.value[currentTextIndex.value]);
const generationStageOrder = ['CREATED', 'CHECKING', 'QUEUED', 'GENERATING', 'QA_PENDING', 'REPAIRING', 'READY'];
const generationStage = computed(() => orderStore.currentOrder?.status || '');
const generationStageLabel = computed(() => {
  switch (generationStage.value) {
    case 'CREATED': return tr('任务已创建', 'Created');
    case 'CHECKING': return tr('照片检查中', 'Checking portraits');
    case 'QUEUED': return tr('任务已排队', 'Queued');
    case 'GENERATING': return tr('已提交生成', 'Generating');
    case 'QA_PENDING': return tr('质检中', 'Quality checking');
    case 'REPAIRING': return tr('自动修复中', 'Auto repairing');
    case 'READY':
    case 'COMPLETED': return tr('已完成', 'Completed');
    default: return '';
  }
});
const generationStageHint = computed(() => {
  switch (generationStage.value) {
    case 'CREATED':
    case 'CHECKING': return tr('正在安全校验人物照片', 'Validating portrait inputs');
    case 'QUEUED': return tr('正在等待生成通道', 'Waiting for the generation channel');
    case 'GENERATING': return tr('生成任务正在执行', 'The generation task is running');
    case 'QA_PENDING': return tr('正在检查脸像、构图和伪影', 'Checking identity, composition, and artifacts');
    case 'REPAIRING': return tr('检测到问题，正在自动修复', 'An issue was detected and is being repaired');
    default: return tr('旗舰质检已开启', 'Flagship Quality Control Active');
  }
});
const generationProgressWidth = computed(() => {
  const index = generationStageOrder.indexOf(generationStage.value);
  if (index >= 0) return `${Math.max(8, Math.round(((index + 1) / generationStageOrder.length) * 100))}%`;
  return `${progressStep.value * 25}%`;
});

const previewImageUrl = computed(() => {
  const asset = previewAsset(orderStore.currentOrder);
  return resolvePublicUrl(asset?.download_path || '/style-previews/royal_castle.jpg');
});

const hdImageUrl = computed(() => {
  const asset = finalMasterAsset(orderStore.currentOrder);
  return resolvePublicUrl(asset?.download_path || previewAsset(orderStore.currentOrder)?.download_path || '');
});

const afterImageUrl = computed(() => {
  const asset = displayAsset(orderStore.currentOrder);
  return resolvePublicUrl(asset?.download_path || '');
});
const canDownload = computed(
  () => privateDownloadAvailable.value && orderStore.currentOrder?.can_download === true
);
const downloadLocked = computed(() => !canDownload.value);
const downloadVariants = computed(() => {
  if (!canDownload.value) return [];
  return deliveryVariantAssets(orderStore.currentOrder).map((asset, index) => ({
    key: asset.id,
    url: resolvePublicUrl(asset.download_path),
    label: asset.width && asset.height
      ? `${asset.width}×${asset.height} ${tr('下载版本', 'Download variant')}`
      : `${tr('下载版本', 'Download variant')} ${index + 1}`,
    filename: `vowpic-delivery-${asset.id}.jpg`,
  }));
});
const abVariantOptions = computed(() => {
  const templates = templateStore.templates || [];
  const currentId = String(orderStore.currentOrder?.template_id || '');
  const current = templates.find((item) => item.id === currentId);
  const category = current?.category || (currentId.startsWith('solo_') ? 'single' : 'couple');
  const stableIds = [
    category === 'single' ? 'solo_korean_minimal' : 'korean_minimal',
    category === 'single' ? 'solo_old_money' : 'old_money',
    category === 'single' ? 'solo_royal_castle' : 'royal_castle',
    category === 'single' ? 'solo_chn_xiuhe' : 'chn_xiuhe',
  ];
  return stableIds
    .filter((id) => id !== currentId)
    .map((id) => templates.find((item) => item.id === id) || { id, title: id.replace(/^solo_/, '').replace(/_/g, ' ') })
    .slice(0, 2)
    .map((item: any) => ({ id: String(item.id), title: String(item.marketing_title || item.title || item.id) }));
});
const hasRenderableOutput = computed(() => {
  return displayAsset(orderStore.currentOrder) !== null;
});

const hasError = computed(() => Boolean(
  orderStore.currentOrder?.error_message
  || isOrderManualOrFailed(orderStore.currentOrder?.status)
  || (orderStore.isCompleted && !hasRenderableOutput.value)
));
const displayErrorMessage = computed(() => {
  if (orderStore.currentOrder?.error_message) return orderStore.currentOrder.error_message;
  switch (orderStore.currentOrder?.status) {
    case 'UNKNOWN_EXTERNAL_STATE':
      return tr('任务正在等待人工对账，为避免重复扣费不会自动重提。', 'The task is awaiting manual reconciliation and will not be resubmitted automatically.');
    case 'CONSENT_REVIEW_REQUIRED':
      return tr('任务需要完成授权复核后才能继续。', 'The task requires consent review before it can continue.');
    case 'CANCELLED':
      return tr('任务已取消，未交付的额度会按结算规则处理。', 'The task was cancelled; undelivered credits follow the settlement policy.');
    case 'FAILED':
      return tr('生成失败，请返回创作页重新检查照片和方向。', 'Generation failed. Return to the studio and review the portraits and direction.');
    case 'DELETED':
      return tr('该任务已删除。', 'This task has been deleted.');
    default:
      return tr('成片暂不可读取，请稍后刷新。', 'The delivery is temporarily unavailable. Please refresh later.');
  }
});

const effectiveHints = computed(() => {
  const o = orderStore.currentOrder;
  const hints: string[] = [];
  if (o?.access_tier) hints.push(`${tr('交付等级', 'Access tier')}: ${o.access_tier}`);
  if (o?.settlement_status) hints.push(`${tr('结算', 'Settlement')}: ${o.settlement_status}`);
  if (o?.delivery_status) hints.push(`${tr('交付', 'Delivery')}: ${o.delivery_status}`);
  return hints;
});

const failureHints = computed(() => {
  const status = orderStore.currentOrder?.status;
  return status && isOrderManualOrFailed(status) ? [`${tr('状态', 'Status')}: ${status}`] : [];
});

const failureActionHints = computed(() => {
  switch (orderStore.currentOrder?.status) {
    case 'UNKNOWN_EXTERNAL_STATE':
      return [tr('请等待系统对账或联系支持，不要重复创建相同任务。', 'Wait for reconciliation or contact support; do not recreate the same task.')];
    case 'CONSENT_REVIEW_REQUIRED':
      return [tr('请完成授权复核。', 'Complete the required consent review.')];
    case 'FAILED':
    case 'CANCELLED':
      return [tr('可返回创作页重新选择人物照片和稳定模板。', 'Return to the studio to select portraits and a stable style again.')];
    default:
      return [];
  }
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

const loadPosterAssetForWeb = async (src: string): Promise<{ image: HTMLImageElement; revoke: () => void }> => {
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

const exportPosterForWeb = async (imageUrl: string) => {
  const browser = globalThis as any;
  const doc = browser?.document;
  const canvas = doc?.getElementById(posterCanvasId) as HTMLCanvasElement | null;
  if (!canvas) throw new Error('poster_canvas_missing');

  canvas.width = posterCanvasWidth;
  canvas.height = posterCanvasHeight;

  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('poster_context_missing');

  const imageAsset = await loadPosterAssetForWeb(imageUrl);

  try {
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, posterCanvasWidth, posterCanvasHeight);
    ctx.drawImage(imageAsset.image, 0, 0, posterCanvasWidth, posterImageHeight);

    ctx.fillStyle = '#111111';
    ctx.fillRect(0, posterImageHeight, posterCanvasWidth, posterCanvasHeight - posterImageHeight);

    ctx.fillStyle = '#ffffff';
    ctx.font = '600 44px Georgia, serif';
    ctx.fillText('VowPic Studio', 60, posterImageHeight + 88);

    ctx.fillStyle = 'rgba(255,255,255,0.72)';
    ctx.font = '400 24px Arial, sans-serif';
    ctx.fillText(tr('Studio 成片 · 2026', 'Studio Masterpiece · 2026'), 60, posterImageHeight + 134);
    ctx.fillText(tr('由 VowPic Web 工作室生成', 'Created with VowPic Web Studio'), 60, posterImageHeight + 180);

    const dataUrl = canvas.toDataURL('image/png');
    const link = doc.createElement('a');
    link.href = dataUrl;
    link.download = getPosterFileName(imageUrl);
    doc.body.appendChild(link);
    link.click();
    doc.body.removeChild(link);
  } finally {
    imageAsset.revoke();
  }
};

const downloadImageUrl = async (url: string, fallbackName = 'ai-wedding-studio-hd.jpg') => {
  if (!canDownload.value) {
    await trackEvent({
      eventType: 'download_locked_clicked',
      sourcePage: 'preview',
      templateId: orderStore.currentOrder?.template_id || null,
      meta: { order_id: orderStore.currentOrder?.id || null },
    });
    showPaymentModal.value = true;
    uni.showToast({ title: tr('请先充值解锁高清下载', 'Top up to unlock HD download'), icon: 'none' });
    return;
  }
  if (!url) {
    uni.showToast({ title: tr('暂无可用图片', 'No image available'), icon: 'none' });
    return;
  }

  try {
    await trackEvent({
      eventType: 'download_started',
      sourcePage: 'preview',
      templateId: orderStore.currentOrder?.template_id || null,
      meta: { order_id: orderStore.currentOrder?.id || null, filename: fallbackName, runtime: 'web' },
    });
    const browser = globalThis as any;
    const doc = browser?.document;
    if (!doc) throw new Error('document_unavailable');
    const response = await browser.fetch(url, { credentials: 'include' });
    if (!response?.ok) {
      throw new Error(`download_failed_${Number(response?.status || 0)}`);
    }
    const blob = await response.blob();
    if (!blob?.size) throw new Error('download_empty');
    const objectUrl = browser.URL.createObjectURL(blob);
    const link = doc.createElement('a');
    link.href = objectUrl;
    link.download = guessFileName(url, fallbackName);
    doc.body.appendChild(link);
    try {
      link.click();
    } finally {
      doc.body.removeChild(link);
        // Chromium may not have consumed the Blob URL when the click handler
        // returns. Keep it alive long enough for the browser download manager
        // to take ownership; immediate revocation cancels an otherwise valid
        // private download.
        browser.setTimeout(() => browser.URL.revokeObjectURL(objectUrl), 60_000);
    }
    await trackEvent({
      eventType: 'download_success',
      sourcePage: 'preview',
      templateId: orderStore.currentOrder?.template_id || null,
      meta: { order_id: orderStore.currentOrder?.id || null, filename: fallbackName, runtime: 'web' },
    });
    uni.showToast({ title: tr('开始下载', 'Download started'), icon: 'success' });
    return;
  } catch (e) {
    console.error(e);
    const browser = globalThis as any;
    browser?.open?.(url, '_blank');
    await trackEvent({
      eventType: 'download_success',
      sourcePage: 'preview',
      templateId: orderStore.currentOrder?.template_id || null,
      meta: { order_id: orderStore.currentOrder?.id || null, filename: fallbackName, runtime: 'web_open' },
    });
    uni.showToast({ title: tr('已在新标签页打开', 'Opened in new tab'), icon: 'none' });
  }
};

const downloadHD = async () => {
  await downloadImageUrl(hdImageUrl.value || afterImageUrl.value, 'ai-wedding-studio-hd.jpg');
};

const openPosterModal = async () => {
  if (!canDownload.value) {
    await requestUnlockDownload();
    uni.showToast({ title: tr('请先充值解锁高清下载', 'Top up to unlock HD download'), icon: 'none' });
    return;
  }
  if (!hdImageUrl.value) {
    uni.showToast({ title: tr('暂无可用图片', 'No image available'), icon: 'none' });
    return;
  }
  showPosterModal.value = true;
};

const closePosterModal = () => showPosterModal.value = false;
const goCreateWithTemplate = (templateId?: string | null, ab = false) => {
  if (!creationAvailable.value) return;
  const id = String(templateId || orderStore.currentOrder?.template_id || '').trim();
  const query = id ? `?id=${encodeURIComponent(id)}${ab ? '&ab=1' : ''}` : '';
  uni.navigateTo({ url: `/pages/create/index${query}` });
};
const regenerate = () => goCreateWithTemplate(orderStore.currentOrder?.template_id || null);

const requestUnlockDownload = async () => {
  if (!billingAvailable.value) return;
  await trackEvent({
    eventType: 'download_locked_clicked',
    sourcePage: 'preview',
    templateId: orderStore.currentOrder?.template_id || null,
    meta: { order_id: orderStore.currentOrder?.id || null, entry: 'unlock_button' },
  });
  showPaymentModal.value = true;
};

const openPaymentModal = () => {
  if (!billingAvailable.value) return;
  showPaymentModal.value = true;
};

const startAbVariant = async (templateId: string) => {
  await trackEvent({
    eventType: 'ab_variant_selected',
    sourcePage: 'preview',
    templateId,
    meta: {
      order_id: orderStore.currentOrder?.id || null,
      current_template_id: orderStore.currentOrder?.template_id || null,
    },
  });
  goCreateWithTemplate(templateId, true);
};

const savePoster = async () => {
  const imageUrl = getPosterImageUrl();
  if (!imageUrl) {
    uni.showToast({ title: tr('暂无可用图片', 'No image available'), icon: 'none' });
    return;
  }

  uni.showLoading({ title: tr('正在导出...', 'Exporting...') });
  try {
    await exportPosterForWeb(imageUrl);
    uni.showToast({ title: tr('海报已保存', 'Poster saved'), icon: 'success' });
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

watch(
  () => orderStore.isGenerating,
  (generating) => {
    if (generating) startAnimations();
    else stopAnimations();
  },
  { immediate: true }
);
watch(
  () => [orderStore.currentOrder?.id || '', orderStore.isCompleted] as const,
  ([orderId, completed]) => {
    if (!orderId || !completed || trackedCompletedOrderId.value === orderId) return;
    trackedCompletedOrderId.value = orderId;
    void trackEvent({
      eventType: 'generation_result_ready_viewed',
      sourcePage: 'preview',
      templateId: orderStore.currentOrder?.template_id || null,
      meta: { order_id: orderId },
    });
  }
);

const retry = async () => {
  if (orderStore.currentOrder?.id) {
    await orderStore.refreshOrder(orderStore.currentOrder.id);
  }
};

onMounted(async () => {
  await opsStore.fetchPublicConfig();
  if (!templateStore.templates.length) {
    void templateStore.fetchTemplates();
  }
  const pages = getCurrentPages();
  const pageOptions = (pages[pages.length - 1] as any).options || {};
  const id = pageOptions.id || pageOptions.orderId;
  if (id) {
    void trackEvent({
      eventType: 'preview_opened',
      sourcePage: 'preview',
      meta: { order_id: String(id) },
    });
    orderStore.fetchOrder(id);
    orderStore.startPolling(id);
  }
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
.masterpiece-folio {
  max-width: 720px;
  margin: 0 auto 32px;
  background: white;
  border-radius: $uni-border-radius-lg;
  overflow: hidden;
  box-shadow: $uni-shadow-xl;
}
.folio-frame {
  aspect-ratio: 3/4;
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

  .delivery-note {
    grid-column: 1 / -1;
    padding: 14px 16px;
    border-radius: 8px;
    border: 1px solid rgba($uni-color-primary, 0.14);
    background: #ffffff;
  }

  .delivery-note-title,
  .delivery-note-copy {
    display: block;
  }

  .delivery-note-title {
    color: $uni-text-color;
    font-size: 13px;
    font-weight: 900;
  }

  .delivery-note-copy {
    margin-top: 6px;
    color: $uni-text-color-muted;
    font-size: 12px;
    line-height: 1.55;
  }

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

.ab-compare-card {
  padding: 20px;
  border: 1px solid rgba($uni-color-primary, 0.12);
  border-radius: 8px;
  background: #ffffff;

  .c-tag {
    font-size: 10px;
    font-weight: 900;
    color: $uni-color-primary;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    display: block;
    margin-bottom: 10px;
  }

  .c-title {
    display: block;
    font-size: 16px;
    font-weight: 800;
    color: $uni-text-color;
    line-height: 1.35;
  }

  .c-desc {
    display: block;
    margin-top: 8px;
    font-size: 12px;
    color: $uni-text-color-muted;
    line-height: 1.5;
  }

  .ab-variant-list {
    display: grid;
    gap: 10px;
    margin-top: 14px;
  }

  .e-action-btn {
    height: 46px;
    border-radius: 8px;
    font-size: 12px;
  }
}

.secondary-ritual-entry { text-align: center; margin-bottom: 40px; .entry-back { font-size: 14px; font-weight: 700; color: $uni-text-color-muted; opacity: 0.6; } }

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
  .secondary-retry { margin-top: 12px; }
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
  .canvas-image-wrap { aspect-ratio: 3/4; width: 100%; overflow: hidden; .canvas-image { width: 100%; height: 100%; } }
}

.canvas-info-wrap {
  padding: 24px; display: flex; justify-content: space-between; align-items: center; background: $uni-text-color; color: white;
  .c-brand { font-size: 16px; letter-spacing: 0.2em; display: block; margin-bottom: 4px; }
  .c-edition { font-size: 10px; opacity: 0.6; text-transform: uppercase; letter-spacing: 0.1em; }
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
