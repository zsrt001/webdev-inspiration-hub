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
            <CompareSlider
              v-if="userUploadUrl"
              :before-image="userUploadUrl"
              :after-image="afterImageUrl"
            />
            <image
              v-else
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
          <view class="secondary-ritual-entry" @tap="regenerate">
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
            <button v-else class="btn btn-primary e-action-btn primary shadow-glow" @tap="requestUnlockDownload">
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
          </view>

          <view v-if="orderStore.isCompleted && abVariantOptions.length" class="ab-compare-card shadow-md">
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
        <button class="btn btn-primary retry-btn" @tap="regenerate">{{ tr('换图或换模板重试', 'Retry with better input') }}</button>
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
import NavBar from '../../components/NavBar.vue';
import PaymentModal from '../../components/PaymentModal.vue';
import CompareSlider from '../../components/CompareSlider.vue';
import { trackEvent } from '../../utils/analytics';

const orderStore = useOrderStore();
const i18nStore = useI18nStore();
const templateStore = useTemplateStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);
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
const generationStageOrder = ['queued', 'identity_refs_ready', 'provider_submitted', 'qa_checking', 'repairing', 'postprocessing', 'completed'];
const generationStage = computed(() => {
  const direct = orderStore.currentOrder?.generation_stage;
  if (direct) return String(direct);
  const params = orderStore.currentOrder?.generation_params as any;
  return params && typeof params === 'object' ? String(params.generation_stage || '') : '';
});
const generationStageLabel = computed(() => {
  switch (generationStage.value) {
    case 'queued': return tr('任务已排队', 'Queued');
    case 'identity_refs_ready': return tr('身份参考已锁定', 'Identity anchors ready');
    case 'provider_submitted': return tr('已提交生成', 'Submitted to AI');
    case 'qa_checking': return tr('质检中', 'Quality checking');
    case 'repairing': return tr('自动修复中', 'Auto repairing');
    case 'postprocessing': return tr('成片处理中', 'Finishing final images');
    case 'completed': return tr('已完成', 'Completed');
    case 'failed': return tr('生成失败', 'Generation failed');
    default: return '';
  }
});
const generationStageHint = computed(() => {
  switch (generationStage.value) {
    case 'queued': return tr('正在等待生成通道', 'Waiting for the generation channel');
    case 'identity_refs_ready': return tr('人脸与上半身参考已准备好', 'Face and upper-body references are ready');
    case 'provider_submitted': return tr('Gemini 编辑任务已提交', 'Gemini edit job has been submitted');
    case 'qa_checking': return tr('正在检查脸像、构图和伪影', 'Checking identity, composition, and artifacts');
    case 'repairing': return tr('检测到问题，正在自动多轮修复', 'Issue detected, automatic repair is running');
    case 'postprocessing': return tr('正在整理预览、高清与多比例成片', 'Preparing preview, HD, and aspect variants');
    default: return tr('旗舰质检已开启', 'Flagship Quality Control Active');
  }
});
const generationProgressWidth = computed(() => {
  const index = generationStageOrder.indexOf(generationStage.value);
  if (index >= 0) return `${Math.max(8, Math.round(((index + 1) / generationStageOrder.length) * 100))}%`;
  return `${progressStep.value * 25}%`;
});

const userUploadUrl = computed(() => {
  const source = orderStore.currentOrder?.source_image_urls as any;
  if (source && source.images && source.images.length > 0) return source.images[0];
  return null;
});

const deliveryVariantSuffixes = ['portrait_2x3', 'print_3x2', 'xhs_3x4', 'portrait_4x5', 'wallpaper_9x16', 'square_1x1'];
function pickPrimaryDeliveryImage(urls?: Record<string, string> | null): string | null {
  if (!urls) return null;
  if (urls.image_1) return urls.image_1;
  const master = Object.entries(urls).find(([key]) => !deliveryVariantSuffixes.some((suffix) => key.includes(suffix)));
  if (master?.[1]) return master[1];
  return Object.values(urls)[0] || null;
}

const previewImageUrl = computed(() => {
  const master = orderStore.currentOrder?.preview_master_image_url;
  if (master) return master;
  const urls = orderStore.currentOrder?.preview_image_urls;
  const primary = pickPrimaryDeliveryImage(urls);
  if (primary) return primary;
  return '/static/style-previews/royal_castle.jpg';
});

const hdImageUrl = computed(() => {
  const master = orderStore.currentOrder?.final_master_image_url;
  if (master) return master;
  const urls = orderStore.currentOrder?.final_image_urls;
  const primary = pickPrimaryDeliveryImage(urls);
  if (primary) return primary;
  return previewImageUrl.value;
});

const afterImageUrl = computed(() => (orderStore.isCompleted ? hdImageUrl.value : previewImageUrl.value));
const canDownload = computed(() => orderStore.currentOrder?.can_download === true);
const downloadLocked = computed(() => orderStore.currentOrder?.download_locked !== false || !canDownload.value);
const downloadVariantLabels: Record<string, string> = {
  portrait_2x3: tr('2:3 下载裁切', '2:3 Download crop'),
  print_3x2: tr('3:2 冲印裁切', '3:2 Print crop'),
  xhs_3x4: tr('3:4 下载版本', '3:4 Download version'),
  portrait_4x5: tr('4:5 下载裁切', '4:5 Download crop'),
  wallpaper_9x16: tr('9:16 下载裁切', '9:16 Download crop'),
  square_1x1: tr('1:1 下载裁切', '1:1 Download crop'),
};
const localizedVariantLabel = (key: string, fallback?: string) => {
  const matched = Object.keys(downloadVariantLabels).find((suffix) => key.includes(suffix));
  return matched ? downloadVariantLabels[matched] : fallback || tr('下载裁切版', 'Download crop');
};
const downloadVariants = computed(() => {
  if (!canDownload.value) return [];
  const explicit = orderStore.currentOrder?.download_variants;
  if (Array.isArray(explicit) && explicit.length) {
    return explicit
      .map((item: any) => ({
        key: String(item.key || item.url || ''),
        url: String(item.url || ''),
        label: localizedVariantLabel(String(item.key || ''), String(item.label || '')),
        filename: `ai-wedding-${String(item.key || 'crop')}.jpg`,
      }))
      .filter((item) => item.key && item.url);
  }
  const urls = orderStore.currentOrder?.final_image_urls || {};
  return Object.entries(urls)
    .filter(([key]) => key !== 'image_1')
    .map(([key, url]) => {
      const matched = Object.keys(downloadVariantLabels).find((suffix) => key.includes(suffix));
      return matched
        ? { key, url: String(url), label: downloadVariantLabels[matched], filename: `ai-wedding-${matched}.jpg` }
        : null;
    })
    .filter(Boolean) as { key: string; url: string; label: string; filename: string }[];
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
      ? tr('双人链路: 历史协作记录', 'Couple flow: archived partner session')
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
    case 'subject_too_small': return tr('人物占比过小', 'Subject too small');
    case 'face_too_small': return tr('脸部不够清晰', 'Face too small');
    case 'background_dominates': return tr('背景抢主体', 'Background dominates');
    case 'excessive_headroom': return tr('头顶留白过多', 'Too much headroom');
    case 'awkward_crop': return tr('裁切不自然', 'Awkward crop');
    case 'dress_cropped': return tr('婚纱裁切不完整', 'Dress cropped');
    case 'poor_subject_separation': return tr('主体层次不足', 'Poor subject separation');
    case 'flat_centered_pose': return tr('姿态过于僵硬', 'Flat centered pose');
    case 'weak_couple_interaction': return tr('双人互动不足', 'Weak couple interaction');
    case 'harsh_backlight': return tr('逆光过强', 'Harsh backlight');
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
  if (o?.couple_flow === 'remote') hints.push(tr('双人链路: 历史协作记录', 'Couple flow: archived partner session'));
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
    const link = doc.createElement('a');
    link.href = url;
    link.download = guessFileName(url, fallbackName);
    link.target = '_blank';
    link.rel = 'noopener';
    doc.body.appendChild(link);
    link.click();
    doc.body.removeChild(link);
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
  const id = String(templateId || orderStore.currentOrder?.template_id || '').trim();
  const query = id ? `?id=${encodeURIComponent(id)}${ab ? '&ab=1' : ''}` : '';
  uni.navigateTo({ url: `/pages/create/index${query}` });
};
const regenerate = () => goCreateWithTemplate(orderStore.currentOrder?.template_id || null);

const requestUnlockDownload = async () => {
  await trackEvent({
    eventType: 'download_locked_clicked',
    sourcePage: 'preview',
    templateId: orderStore.currentOrder?.template_id || null,
    meta: { order_id: orderStore.currentOrder?.id || null, entry: 'unlock_button' },
  });
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

const retry = () => {
  if (orderStore.currentOrder?.id) orderStore.startPolling(orderStore.currentOrder.id);
};

onMounted(() => {
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
