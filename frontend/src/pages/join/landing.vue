<template>
  <view class="app-container landing-page">
    <view class="landing-header">
      <view class="header-inner">
        <text class="logo heading-serif">{{ tr('AI 婚纱工作室', 'AI Wedding Studio') }}</text>
        <view class="guest-chip">{{ tr('访客上传', 'Guest Upload') }}</view>
      </view>
    </view>

    <view v-if="sessionInvalid" class="landing-shell">
      <view class="status-card shadow-xl">
        <text class="status-title heading-serif">{{ tr('邀请链接无效', 'Invalid Invite Link') }}</text>
        <text class="status-copy">{{ tr('当前邀请已失效或参数缺失，请让主设备重新生成邀请链接。', 'This invite link is missing or has expired. Ask the host to generate a new invite.') }}</text>
      </view>
    </view>

    <view v-else-if="!uploadSuccess" class="landing-shell">
      <view class="landing-hero shadow-xl">
        <view class="hero-copy">
          <text class="hero-kicker">{{ tr('异地合拍', 'Remote Couple Flow') }}</text>
          <text class="hero-title heading-serif">{{ tr('补上传你的照片，完成双人合拍', 'Upload your portrait to complete the couple session') }}</text>
          <text class="hero-subtitle">{{ tr('主设备已经创建邀请。你只需要上传一张清晰照片，系统会在主设备发起生成时自动合成双人作品。', 'The host has already created the invite. Upload one clear portrait and the session will be combined when generation starts on the host device.') }}</text>

          <view class="session-row" v-if="sessionId">
            <view class="session-pill">
              <text class="session-label">{{ tr('会话编号', 'Session ID') }}</text>
              <text class="session-value">{{ sessionId }}</text>
            </view>
            <view class="session-pill">
              <text class="session-label">{{ tr('当前状态', 'Current Status') }}</text>
              <text class="session-value">{{ sessionStatusText }}</text>
            </view>
          </view>
        </view>

        <view class="upload-card">
          <text class="card-title">{{ tr('上传第二张照片', 'Upload the second portrait') }}</text>

          <view v-if="selectedImage" class="preview-frame">
            <image class="preview-image" :src="selectedImage" mode="aspectFill" />
          </view>
          <view v-else class="empty-frame" @tap="selectPhoto">
            <text class="empty-plus">+</text>
            <text class="empty-title">{{ tr('选择照片', 'Choose Portrait') }}</text>
            <text class="empty-copy">{{ tr('建议正脸、自然光、无遮挡', 'Use a clear face, natural light, and no obstruction') }}</text>
          </view>

          <view class="upload-actions">
            <button class="btn btn-outline action-btn" @tap="selectPhoto" :disabled="uploading">
              {{ selectedImage ? tr('重新选择', 'Replace') : tr('选择照片', 'Choose Portrait') }}
            </button>
            <button class="btn btn-primary action-btn shadow-glow" @tap="confirmUpload" :disabled="!selectedImage || uploading">
              {{ uploading ? tr('上传中...', 'Uploading...') : tr('确认上传', 'Confirm Upload') }}
            </button>
          </view>

          <view class="checklist">
            <view class="check-row">
              <view class="check-dot"></view>
              <text class="check-copy">{{ tr('清晰正脸', 'Clear face') }}</text>
            </view>
            <view class="check-row">
              <view class="check-dot"></view>
              <text class="check-copy">{{ tr('自然光线', 'Natural lighting') }}</text>
            </view>
            <view class="check-row">
              <view class="check-dot"></view>
              <text class="check-copy">{{ tr('避免滤镜和夸张美颜', 'Avoid filters and heavy beauty effects') }}</text>
            </view>
          </view>
        </view>
      </view>
    </view>

    <view v-else class="landing-shell">
      <view class="status-card shadow-xl">
        <view class="status-orb">
          <view class="status-ring"></view>
          <view class="status-core"></view>
        </view>
        <text class="status-title heading-serif">{{ tr('上传完成', 'Upload Complete') }}</text>
        <text class="status-copy">{{ tr('你的照片已经加入本次异地合拍。等待主设备开始生成后，即可查看最终结果。', 'Your portrait has been attached to this remote couple session. Once the host starts generation, the final result will be available here.') }}</text>
        <button v-if="orderId" class="btn btn-primary result-btn shadow-glow" @tap="goToResult">
          {{ tr('查看结果', 'View Result') }}
        </button>
        <text v-else class="status-hint">{{ tr('等待主设备发起生成...', 'Waiting for the host to start generation...') }}</text>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { get, post, uploadFile } from '../../utils/api';
import { useI18nStore } from '../../stores/i18n';
import { runLocalSmartInputCheck, type SmartInputVerdict } from '../../utils/local_smart_input';
import { trackEvent } from '../../utils/analytics';

const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const sessionId = ref('');
const selectedImage = ref('');
const uploading = ref(false);
const uploadSuccess = ref(false);
const orderId = ref('');
const sessionInvalid = ref(false);
const sessionStatus = ref('waiting');
const selectedQuality = ref<Record<string, any> | null>(null);
let pollTimer: ReturnType<typeof setInterval> | null = null;

function serializeUploadQuality(verdict: SmartInputVerdict): Record<string, any> {
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
    role: 'guest',
  };
}

const sessionStatusText = computed(() => {
  if (sessionStatus.value === 'ready') return tr('已就绪，等待生成', 'Ready for generation');
  if (sessionStatus.value === 'uploading') return tr('上传中', 'Uploading');
  if (sessionStatus.value === 'processing') return tr('生成中', 'Generating');
  if (sessionStatus.value === 'completed') return tr('已完成', 'Completed');
  if (sessionStatus.value === 'expired') return tr('已过期', 'Expired');
  return tr('等待上传', 'Waiting for upload');
});

const stopPolling = () => {
  if (!pollTimer) return;
  clearInterval(pollTimer);
  pollTimer = null;
};

const startPollingOrderId = () => {
  stopPolling();
  pollTimer = setInterval(async () => {
    if (!sessionId.value) return;
    try {
      const res = await get<any>(`/session/${sessionId.value}/status`, {
        showLoading: false,
        showError: false,
      } as any);

      if (res?.status) sessionStatus.value = res.status;
      if (res?.status === 'expired' || res?.exists === false) {
        sessionInvalid.value = true;
        stopPolling();
      }
      if (res?.order_id) {
        orderId.value = res.order_id;
      }
      if (res?.status === 'completed') {
        stopPolling();
      }
    } catch (error) {
      console.error(error);
    }
  }, 2000);
};

const readSessionFromBrowserUrl = () => {
  if (typeof window === 'undefined') return '';
  const fromSearch = new URLSearchParams(window.location.search).get('session');
  if (fromSearch) return fromSearch;

  const hashQuery = window.location.hash.includes('?') ? window.location.hash.split('?').slice(1).join('?') : '';
  if (!hashQuery) return '';
  return new URLSearchParams(hashQuery).get('session') || '';
};

const resolveSessionId = () => {
  const pages = getCurrentPages();
  const currentPage = pages[pages.length - 1];
  const pageSession = String((currentPage as any)?.options?.session || '').trim();
  return pageSession || readSessionFromBrowserUrl().trim();
};

const restoreSessionState = async () => {
  if (!sessionId.value) return;

  try {
    const status = await get<any>(`/session/${sessionId.value}/status`, {
      showLoading: false,
      showError: false,
    } as any);

    if (!status?.exists || status?.status === 'expired') {
      sessionInvalid.value = true;
      return;
    }

    sessionStatus.value = status.status || 'waiting';

    if (status.guest_ready) {
      uploadSuccess.value = true;
      startPollingOrderId();
    }

    if (status.order_id) {
      orderId.value = status.order_id;
      uploadSuccess.value = true;
      startPollingOrderId();
    }
  } catch (error) {
    console.error(error);
    sessionInvalid.value = true;
  }
};

onMounted(() => {
  const session = resolveSessionId();

  if (!session) {
    sessionInvalid.value = true;
    return;
  }

  sessionId.value = session;
  void restoreSessionState();
});

onUnmounted(() => {
  stopPolling();
});

const selectPhoto = async () => {
  try {
    const res = await uni.chooseImage({
      count: 1,
      sizeType: ['original'],
      sourceType: ['album', 'camera'],
    });
    const localPath = res.tempFilePaths?.[0];
    if (!localPath) return;

    const localVerdict = await runLocalSmartInputCheck(localPath);
    selectedQuality.value = serializeUploadQuality(localVerdict);
    if (localVerdict.quality_level !== 'good') {
      const score = Math.max(0, Math.min(100, Number(localVerdict.quality_score || 0)));
      uni.showToast({
        title: tr(`这张可能不像本人，建议换更清晰正脸（${score}分）`, `This may reduce likeness. A clearer front-facing photo is recommended (${score})`),
        icon: 'none',
        duration: 2600,
      });
    }

    selectedImage.value = localPath;
  } catch (error) {
    console.error(error);
  }
};

const confirmUpload = async () => {
  if (!selectedImage.value || !sessionId.value) return;
  uploading.value = true;
  const startedAt = Date.now();
  await trackEvent({
    eventType: 'asset_upload_started',
    sourcePage: 'remote_join',
    templateId: null,
    meta: { session_id: sessionId.value, role: 'guest' },
  });
  try {
    const res = await uploadFile('/upload', selectedImage.value, 'file');
    const imageUrl = String(res.url || '').trim();
    await trackEvent({
      eventType: 'asset_upload_completed',
      sourcePage: 'remote_join',
      templateId: null,
      meta: {
        session_id: sessionId.value,
        role: 'guest',
        duration_ms: Date.now() - startedAt,
        has_url: !!imageUrl,
        quality_score: selectedQuality.value?.quality_score ?? null,
        quality_level: selectedQuality.value?.quality_level ?? null,
      },
    });
    if (selectedQuality.value) {
      await trackEvent({
        eventType: 'asset_upload_quality_scored',
        sourcePage: 'remote_join',
        templateId: null,
        meta: { ...selectedQuality.value, session_id: sessionId.value },
      });
      if (selectedQuality.value.quality_level !== 'good') {
        await trackEvent({
          eventType: 'asset_upload_quality_warning',
          sourcePage: 'remote_join',
          templateId: null,
          meta: { ...selectedQuality.value, session_id: sessionId.value },
        });
        if (selectedQuality.value.quality_level === 'poor') {
          await trackEvent({
            eventType: 'asset_upload_quality_poor',
            sourcePage: 'remote_join',
            templateId: null,
            meta: { ...selectedQuality.value, session_id: sessionId.value },
          });
        }
      }
    }
    await post(`/session/${sessionId.value}/upload/guest?image_url=${encodeURIComponent(imageUrl)}`, {});
    uploadSuccess.value = true;
    sessionStatus.value = 'ready';
    startPollingOrderId();
  } catch (error: any) {
    uni.showToast({ title: error.message || tr('上传失败', 'Upload failed'), icon: 'none' });
  } finally {
    uploading.value = false;
  }
};

const goToResult = () => {
  if (!orderId.value) return;
  uni.navigateTo({ url: `/pages/preview/preview?id=${orderId.value}` });
};
</script>

<style lang="scss" scoped>
.landing-page {
  min-height: 100vh;
  background: linear-gradient(180deg, #120710 0%, #2a0d1d 100%);
  color: #fff;
}

.landing-header {
  padding: 48px 28px 18px;
}

.header-inner {
  max-width: 1320px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.logo {
  font-size: 28px;
  color: #fff6fb;
}

.guest-chip,
.session-pill {
  display: inline-flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 16px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
}

.guest-chip {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #f9a8d4;
}

.landing-shell {
  max-width: 1320px;
  margin: 0 auto;
  padding: 16px 28px 80px;
}

.landing-hero,
.status-card {
  background: rgba(18, 7, 16, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 32px;
  backdrop-filter: blur(18px);
}

.landing-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 420px;
  gap: 24px;
  padding: 28px;

  @media (max-width: 1080px) {
    grid-template-columns: 1fr;
  }
}

.hero-kicker,
.card-title,
.session-label {
  display: block;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #f9a8d4;
}

.hero-kicker {
  margin-bottom: 14px;
}

.hero-title,
.status-title {
  display: block;
  font-size: 54px;
  line-height: 1.04;
  margin-bottom: 14px;
  color: #fff8fc;
}

.hero-subtitle,
.status-copy,
.status-hint,
.session-value,
.check-copy,
.empty-copy {
  display: block;
  font-size: 14px;
  line-height: 1.8;
  color: rgba(255, 255, 255, 0.74);
}

.session-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 24px;
}

.session-value {
  color: #fff;
  word-break: break-all;
}

.upload-card {
  padding: 22px;
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.card-title {
  margin-bottom: 14px;
}

.preview-frame,
.empty-frame {
  width: 100%;
  aspect-ratio: 4 / 5;
  border-radius: 22px;
  overflow: hidden;
  margin-bottom: 16px;
}

.preview-frame {
  background: rgba(255, 255, 255, 0.06);
}

.preview-image {
  width: 100%;
  height: 100%;
}

.empty-frame {
  border: 1px dashed rgba(249, 168, 212, 0.32);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
  padding: 24px;
}

.empty-plus {
  font-size: 42px;
  color: #f472b6;
  line-height: 1;
}

.empty-title {
  display: block;
  font-size: 16px;
  font-weight: 700;
  color: #fff8fc;
}

.upload-actions {
  display: flex;
  gap: 12px;
  margin-bottom: 18px;

  @media (max-width: 640px) {
    flex-direction: column;
  }
}

.action-btn,
.result-btn {
  width: 100%;
}

.checklist {
  display: grid;
  gap: 10px;
}

.check-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.check-dot,
.status-core {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #f472b6;
  flex-shrink: 0;
}

.status-card {
  padding: 60px 32px;
  text-align: center;
}

.status-orb {
  position: relative;
  width: 120px;
  height: 120px;
  margin: 0 auto 24px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.status-ring {
  position: absolute;
  inset: 0;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  animation: pulse 2.8s infinite ease-in-out;
}

.status-core {
  width: 16px;
  height: 16px;
  box-shadow: 0 0 24px rgba(244, 114, 182, 0.55);
}

@keyframes pulse {
  0% { transform: scale(0.94); opacity: 0.36; }
  50% { transform: scale(1.08); opacity: 0.9; }
  100% { transform: scale(0.94); opacity: 0.36; }
}
</style>
