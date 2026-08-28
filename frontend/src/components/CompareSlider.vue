<template>
  <view class="compare-slider-container" @touchstart="handleTouchStart" @touchmove="handleTouchMove">
    <image :src="afterImage" mode="aspectFit" class="slider-image after" />

    <view class="before-wrap" :style="{ width: sliderPos + '%' }">
      <image :src="beforeImage" mode="aspectFit" class="slider-image before" :style="{ width: containerWidth + 'px' }" />
    </view>

    <view class="handle-bar" :style="{ left: sliderPos + '%' }">
      <view class="handle-line"></view>
      <view class="handle-orb">
        <text class="orb-arr">&lt;&gt;</text>
      </view>
    </view>

    <view class="label label-before">{{ tr('原图', 'ORIGINAL') }}</view>
    <view class="label label-after">{{ tr('成片', 'STUDIO 3.0') }}</view>
  </view>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useI18nStore } from '../stores/i18n';

defineProps<{
  beforeImage: string;
  afterImage: string;
}>();

const sliderPos = ref(50);
const containerWidth = ref(0);
const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const handleTouchStart = (e: any) => {
  updatePos(e);
};

const handleTouchMove = (e: any) => {
  updatePos(e);
};

const updatePos = (e: any) => {
  const touch = e.touches[0];
  const query = uni.createSelectorQuery().in(null);
  query.select('.compare-slider-container').boundingClientRect((data: any) => {
    if (data) {
      containerWidth.value = data.width;
      let pos = ((touch.clientX - data.left) / data.width) * 100;
      if (pos < 0) pos = 0;
      if (pos > 100) pos = 100;
      sliderPos.value = pos;
    }
  }).exec();
};

onMounted(() => {
  uni.createSelectorQuery().select('.compare-slider-container').boundingClientRect((data: any) => {
    if (data) containerWidth.value = data.width;
  }).exec();
});
</script>

<style lang="scss" scoped>
.compare-slider-container {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #eef1f4;
}

.slider-image {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: block;
  user-select: none;
  pointer-events: none;
}

.before-wrap {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  overflow: hidden;
  z-index: 2;
  border-right: 1px solid white;
}

.before {
  height: 100%;
}

.handle-bar {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: white;
  z-index: 10;
  transform: translateX(-50%);
  pointer-events: none;
}

.handle-orb {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 40px;
  height: 40px;
  background: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 0 15px rgba(0, 0, 0, 0.3);

  .orb-arr {
    font-size: 12px;
    color: #333;
    font-weight: bold;
  }
}

.label {
  position: absolute;
  bottom: 20px;
  padding: 4px 10px;
  background: rgba(0, 0, 0, 0.4);
  color: white;
  font-size: 10px;
  font-weight: 800;
  border-radius: 4px;
  z-index: 15;
  letter-spacing: 0.1em;
}

.label-before { left: 20px; }
.label-after { right: 20px; }
</style>
