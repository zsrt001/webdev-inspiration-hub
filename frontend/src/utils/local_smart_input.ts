export type SmartInputVerdict = {
  passed: boolean;
  reasons: string[];
  advice: string[];
  metrics: Record<string, number>;
  risk_flags: string[];
  quality_score: number;
  quality_level: 'good' | 'warning' | 'poor';
};

const defaultVerdict = (): SmartInputVerdict => ({
  passed: true,
  reasons: [],
  advice: [],
  metrics: {},
  risk_flags: [],
  quality_score: 100,
  quality_level: 'good',
});

const addIssue = (verdict: SmartInputVerdict, reason: string, advice: string) => {
  if (!verdict.reasons.includes(reason)) verdict.reasons.push(reason);
  if (advice && !verdict.advice.includes(advice)) verdict.advice.push(advice);
  verdict.passed = false;
};

const finalizeQualityScore = (verdict: SmartInputVerdict): SmartInputVerdict => {
  const penalty: Record<string, number> = {
    no_face: 45,
    multiple_faces: 30,
    face_too_small: 28,
    face_occluded: 24,
    not_frontal: 24,
    face_near_edge: 22,
    head_maybe_cropped: 22,
    too_blurry: 26,
    too_dark: 18,
    overexposed: 20,
    low_resolution: 16,
    abnormal_aspect_ratio: 10,
    face_too_close: 10,
  };
  let score = 100;
  for (const reason of verdict.reasons) score -= penalty[reason] || 8;
  const blur = Number(verdict.metrics.blur_score || 0);
  const faceArea = Number(verdict.metrics.face_area_ratio || 0);
  const yaw = Number(verdict.metrics.face_yaw_ratio || 0);
  if (blur > 0 && blur < 10) score -= 8;
  if (faceArea > 0 && faceArea < 0.055) score -= 10;
  if (yaw > 0.28) score -= 10;
  verdict.quality_score = Math.max(0, Math.min(100, Math.round(score)));
  verdict.quality_level = verdict.quality_score >= 78 ? 'good' : verdict.quality_score >= 55 ? 'warning' : 'poor';
  verdict.passed = true;
  if (verdict.quality_level !== 'good' && !verdict.risk_flags.includes('identity_similarity_risk')) {
    verdict.risk_flags.push('identity_similarity_risk');
  }
  return verdict;
};

const isWebRuntime = (): boolean => {
  const g = globalThis as any;
  return !!(g?.window && g?.document);
};

const loadImageElement = async (src: string): Promise<HTMLImageElement> => {
  return await new Promise<HTMLImageElement>((resolve, reject) => {
    const g = globalThis as any;
    const img = g?.document?.createElement?.('img') as HTMLImageElement;
    if (!img) return reject(new Error('img_unavailable'));
    img.crossOrigin = 'anonymous';
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('image_load_failed'));
    img.src = src;
  });
};

const analyzePixelMetrics = (img: HTMLImageElement): Record<string, number> => {
  const g = globalThis as any;
  const doc = g?.document;
  const canvas = doc?.createElement?.('canvas') as HTMLCanvasElement;
  if (!canvas) return {};

  const maxSide = 512;
  const srcW = Math.max(1, img.naturalWidth || img.width || 1);
  const srcH = Math.max(1, img.naturalHeight || img.height || 1);
  const scale = Math.min(1, maxSide / Math.max(srcW, srcH));
  const w = Math.max(1, Math.round(srcW * scale));
  const h = Math.max(1, Math.round(srcH * scale));
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d', { willReadFrequently: true } as any) as CanvasRenderingContext2D | null;
  if (!ctx) return {};

  ctx.drawImage(img, 0, 0, w, h);
  const imageData = ctx.getImageData(0, 0, w, h);
  const data = imageData.data;
  const gray = new Float32Array(w * h);

  let brightnessSum = 0;
  let satSum = 0;
  for (let i = 0, p = 0; i < data.length; i += 4, p++) {
    const r = data[i];
    const gg = data[i + 1];
    const b = data[i + 2];
    const luminance = 0.299 * r + 0.587 * gg + 0.114 * b;
    gray[p] = luminance;
    brightnessSum += luminance;
    const max = Math.max(r, gg, b);
    const min = Math.min(r, gg, b);
    satSum += max === 0 ? 0 : ((max - min) / max) * 255;
  }
  const brightness = brightnessSum / Math.max(1, gray.length);
  const saturationMean = satSum / Math.max(1, gray.length);

  // Fast focus estimate: gradient energy variance
  const gradients: number[] = [];
  for (let y = 1; y < h - 1; y += 2) {
    for (let x = 1; x < w - 1; x += 2) {
      const idx = y * w + x;
      const gx = gray[idx + 1] - gray[idx - 1];
      const gy = gray[idx + w] - gray[idx - w];
      gradients.push(Math.abs(gx) + Math.abs(gy));
    }
  }

  let gMean = 0;
  if (gradients.length) gMean = gradients.reduce((acc, v) => acc + v, 0) / gradients.length;
  let gVar = 0;
  if (gradients.length) gVar = gradients.reduce((acc, v) => acc + (v - gMean) ** 2, 0) / gradients.length;
  const blurScore = Math.sqrt(gVar);

  return {
    width: srcW,
    height: srcH,
    brightness,
    saturation_mean: saturationMean,
    blur_score: blurScore,
  };
};

const runFaceDetection = async (img: HTMLImageElement): Promise<{ metrics: Record<string, number>; reasons: string[] }> => {
  const metrics: Record<string, number> = {};
  const reasons: string[] = [];
  const imageWidth = Math.max(1, img.naturalWidth || img.width || 1);
  const imageHeight = Math.max(1, img.naturalHeight || img.height || 1);

  const g = globalThis as any;

  const FaceDetectorCtor = (g as any).FaceDetector;
  if (FaceDetectorCtor) {
    const detector = new FaceDetectorCtor({ fastMode: true, maxDetectedFaces: 3 });
    const faces = await detector.detect(img);
    const count = faces?.length || 0;
    metrics.face_count = count;
    if (count === 0) reasons.push('no_face');
    if (count > 1) reasons.push('multiple_faces');
    const box = faces?.[0]?.boundingBox;
    if (box) {
      const faceAreaRatio = (box.width * box.height) / Math.max(1, imageWidth * imageHeight);
      const leftMarginRatio = box.x / imageWidth;
      const rightMarginRatio = Math.max(0, imageWidth - (box.x + box.width)) / imageWidth;
      const topMarginRatio = box.y / imageHeight;
      const bottomMarginRatio = Math.max(0, imageHeight - (box.y + box.height)) / imageHeight;
      metrics.face_area_ratio = faceAreaRatio;
      if (faceAreaRatio < 0.04) reasons.push('face_too_small');
      if (faceAreaRatio > 0.70) reasons.push('face_too_close');
      if (Math.min(leftMarginRatio, rightMarginRatio, topMarginRatio, bottomMarginRatio) < 0.015) {
        reasons.push('face_near_edge');
      }
    }
    return { metrics, reasons };
  }

  metrics.local_face_detector_available = 0;
  return { metrics, reasons };
};

const runUniLightweightCheck = async (imagePath: string): Promise<SmartInputVerdict | null> => {
  const uniApi = (globalThis as any)?.uni;
  if (!uniApi?.getImageInfo) return null;

  try {
    const info = await new Promise<any>((resolve, reject) => {
      uniApi.getImageInfo({
        src: imagePath,
        success: resolve,
        fail: reject,
      });
    });

    const verdict = defaultVerdict();
    const width = Number(info?.width || 0);
    const height = Number(info?.height || 0);
    const ratio = width / Math.max(1, height);
    verdict.metrics.width = width;
    verdict.metrics.height = height;
    verdict.metrics.aspect_ratio = ratio;

    if (!width || !height) {
      addIssue(verdict, 'image_info_unavailable', 'Image info unavailable. Please try another photo.');
      return finalizeQualityScore(verdict);
    }
    if (Math.min(width, height) < 512) {
      addIssue(verdict, 'low_resolution', 'Resolution is low. A clearer photo is recommended.');
    }
    if (ratio < 0.55 || ratio > 1.95) {
      addIssue(verdict, 'abnormal_aspect_ratio', 'Please use a regular portrait photo.');
    }

    return finalizeQualityScore(verdict);
  } catch {
    return null;
  }
};

export const runLocalSmartInputCheck = async (imagePath: string): Promise<SmartInputVerdict> => {
  const verdict = defaultVerdict();
  if (!imagePath) return verdict;

  if (!isWebRuntime()) {
    const uniVerdict = await runUniLightweightCheck(imagePath);
    return uniVerdict ? finalizeQualityScore(uniVerdict) : finalizeQualityScore(verdict);
  }

  try {
    const img = await loadImageElement(imagePath);
    const pixelMetrics = analyzePixelMetrics(img);
    Object.assign(verdict.metrics, pixelMetrics);

    if (pixelMetrics.width && pixelMetrics.height && Math.min(pixelMetrics.width, pixelMetrics.height) < 512) {
      addIssue(verdict, 'low_resolution', 'Resolution is low. A clearer photo is recommended.');
    }
    const aspectRatio = (pixelMetrics.width || 0) / Math.max(1, pixelMetrics.height || 1);
    verdict.metrics.aspect_ratio = aspectRatio;
    if (aspectRatio < 0.55 || aspectRatio > 1.95) {
      addIssue(verdict, 'abnormal_aspect_ratio', 'Please use a regular portrait photo.');
    }
    if ((pixelMetrics.brightness || 0) < 58) {
      addIssue(verdict, 'too_dark', 'The photo is too dark. Better lighting is recommended.');
    }
    if ((pixelMetrics.brightness || 0) > 236) {
      addIssue(verdict, 'overexposed', 'The photo is overexposed. Softer light is recommended.');
    }
    if ((pixelMetrics.blur_score || 0) < 7.2) {
      addIssue(verdict, 'too_blurry', 'The photo is blurry. Hold steady and refocus.');
    }

    const face = await runFaceDetection(img);
    Object.assign(verdict.metrics, face.metrics);
    const reasons = face.reasons;
    for (const reason of reasons || []) {
      if (reason === 'no_face') addIssue(verdict, 'no_face', 'No clear face detected. Use a front-facing portrait.');
      else if (reason === 'multiple_faces') addIssue(verdict, 'multiple_faces', 'Multiple faces detected. Use one clear portrait.');
      else if (reason === 'face_occluded') addIssue(verdict, 'face_occluded', 'The face may be occluded. Use an unobstructed front-facing photo.');
      else if (reason === 'not_frontal' || reason === 'face_tilted') addIssue(verdict, 'not_frontal', 'Face the camera and avoid side profiles or heavy tilt.');
      else if (reason === 'face_too_small') addIssue(verdict, 'face_too_small', 'The face is too small. Move closer to the camera.');
      else if (reason === 'face_too_close') addIssue(verdict, 'face_too_close', 'The face is too close. Step back slightly.');
      else if (reason === 'face_near_edge' || reason === 'head_maybe_cropped') addIssue(verdict, 'face_near_edge', 'Keep the full head and shoulders inside the frame.');
    }
  } catch {
    // Keep local checker best-effort; backend gatekeeper is still authoritative.
    return finalizeQualityScore(defaultVerdict());
  }

  return finalizeQualityScore(verdict);
};
