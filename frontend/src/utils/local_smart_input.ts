export type SmartInputVerdict = {
  passed: boolean;
  reasons: string[];
  advice: string[];
  metrics: Record<string, number>;
  risk_flags: string[];
};

const FACE_API_SCRIPT = 'https://cdn.jsdelivr.net/npm/face-api.js@0.22.2/dist/face-api.min.js';
const FACE_API_MODEL_BASE = 'https://justadudewhohacks.github.io/face-api.js/weights';

let faceApiReadyPromise: Promise<boolean> | null = null;

const defaultVerdict = (): SmartInputVerdict => ({
  passed: true,
  reasons: [],
  advice: [],
  metrics: {},
  risk_flags: [],
});

const addIssue = (verdict: SmartInputVerdict, reason: string, advice: string) => {
  if (!verdict.reasons.includes(reason)) verdict.reasons.push(reason);
  if (advice && !verdict.advice.includes(advice)) verdict.advice.push(advice);
  verdict.passed = false;
};

const isWebRuntime = (): boolean => {
  const g = globalThis as any;
  return !!(g?.window && g?.document);
};

const loadScript = async (src: string): Promise<void> => {
  const g = globalThis as any;
  const doc = g?.document;
  if (!doc) return;

  await new Promise<void>((resolve, reject) => {
    const existing = doc.querySelector(`script[data-aiws-src="${src}"]`);
    if (existing) {
      if ((existing as any).dataset?.loaded === '1') return resolve();
      existing.addEventListener('load', () => resolve(), { once: true });
      existing.addEventListener('error', () => reject(new Error(`script_load_failed:${src}`)), { once: true });
      return;
    }
    const script = doc.createElement('script');
    script.src = src;
    script.async = true;
    script.defer = true;
    script.setAttribute('data-aiws-src', src);
    script.onload = () => {
      (script as any).dataset.loaded = '1';
      resolve();
    };
    script.onerror = () => reject(new Error(`script_load_failed:${src}`));
    doc.head.appendChild(script);
  });
};

const ensureFaceApi = async (): Promise<boolean> => {
  if (faceApiReadyPromise) return faceApiReadyPromise;

  faceApiReadyPromise = (async () => {
    const g = globalThis as any;
    if (!g?.window || !g?.document) return false;
    await loadScript(FACE_API_SCRIPT);
    const faceapi = g?.faceapi;
    if (!faceapi) return false;

    if (!faceapi.nets.tinyFaceDetector.isLoaded) {
      await faceapi.nets.tinyFaceDetector.loadFromUri(FACE_API_MODEL_BASE);
    }
    if (!faceapi.nets.faceLandmark68TinyNet.isLoaded) {
      await faceapi.nets.faceLandmark68TinyNet.loadFromUri(FACE_API_MODEL_BASE);
    }
    return true;
  })().catch(() => false);

  return faceApiReadyPromise;
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

  const faceApiReady = await ensureFaceApi();
  const g = globalThis as any;

  if (faceApiReady && g?.faceapi) {
    const faceapi = g.faceapi;
    const detections = await faceapi
      .detectAllFaces(img, new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.45 }))
      .withFaceLandmarks(true);

    const count = detections?.length || 0;
    metrics.face_count = count;
    if (count === 0) {
      reasons.push('no_face');
      return { metrics, reasons };
    }
    if (count > 1) reasons.push('multiple_faces');

    const main = detections[0];
    const box = main?.detection?.box;
    const score = Number(main?.detection?.score || 0);
    metrics.face_score = score;

    if (box) {
      const faceAreaRatio = (box.width * box.height) / Math.max(1, imageWidth * imageHeight);
      const leftMarginRatio = box.x / imageWidth;
      const rightMarginRatio = Math.max(0, imageWidth - (box.x + box.width)) / imageWidth;
      const topMarginRatio = box.y / imageHeight;
      const bottomMarginRatio = Math.max(0, imageHeight - (box.y + box.height)) / imageHeight;
      metrics.face_area_ratio = faceAreaRatio;
      metrics.face_left_margin_ratio = leftMarginRatio;
      metrics.face_right_margin_ratio = rightMarginRatio;
      metrics.face_top_margin_ratio = topMarginRatio;
      metrics.face_bottom_margin_ratio = bottomMarginRatio;
      if (faceAreaRatio < 0.04) reasons.push('face_too_small');
      if (faceAreaRatio > 0.70) reasons.push('face_too_close');
      if (Math.min(leftMarginRatio, rightMarginRatio, topMarginRatio, bottomMarginRatio) < 0.015) reasons.push('face_near_edge');
      if (topMarginRatio < 0.02 && faceAreaRatio > 0.18) reasons.push('head_maybe_cropped');
    }

    const lm = main?.landmarks;
    if (lm?.getLeftEye && lm?.getRightEye && lm?.getNose) {
      const leftEye = lm.getLeftEye();
      const rightEye = lm.getRightEye();
      const nose = lm.getNose();
      if (leftEye?.length && rightEye?.length && nose?.length) {
        const le = leftEye.reduce((acc: any, p: any) => ({ x: acc.x + p.x, y: acc.y + p.y }), { x: 0, y: 0 });
        const re = rightEye.reduce((acc: any, p: any) => ({ x: acc.x + p.x, y: acc.y + p.y }), { x: 0, y: 0 });
        const ne = nose.reduce((acc: any, p: any) => ({ x: acc.x + p.x, y: acc.y + p.y }), { x: 0, y: 0 });
        const lcx = le.x / leftEye.length;
        const lcy = le.y / leftEye.length;
        const rcx = re.x / rightEye.length;
        const rcy = re.y / rightEye.length;
        const ncx = ne.x / nose.length;

        const eyeSlopeDeg = Math.atan2(rcy - lcy, rcx - lcx) * (180 / Math.PI);
        const eyeDistance = Math.sqrt((rcx - lcx) ** 2 + (rcy - lcy) ** 2) || 1;
        const yawRatio = Math.abs(ncx - (lcx + rcx) / 2) / eyeDistance;
        metrics.face_tilt_deg = Math.abs(eyeSlopeDeg);
        metrics.face_yaw_ratio = yawRatio;

        if (Math.abs(eyeSlopeDeg) > 12) reasons.push('face_tilted');
        if (yawRatio > 0.34) reasons.push('not_frontal');
      }
    }

    return { metrics, reasons };
  }

  const FaceDetectorCtor = (g as any).FaceDetector;
  if (FaceDetectorCtor) {
    const detector = new FaceDetectorCtor({ fastMode: true, maxDetectedFaces: 3 });
    const faces = await detector.detect(img);
    const count = faces?.length || 0;
    metrics.face_count = count;
    if (count === 0) reasons.push('no_face');
    if (count > 1) reasons.push('multiple_faces');
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
      addIssue(verdict, 'image_info_unavailable', '无法读取图片信息，请更换照片重试');
      return verdict;
    }
    if (Math.min(width, height) < 512) {
      addIssue(verdict, 'low_resolution', '分辨率过低，请上传更清晰的照片');
    }
    if (ratio < 0.55 || ratio > 1.95) {
      addIssue(verdict, 'abnormal_aspect_ratio', '图片比例异常，请上传常规自拍照片');
    }

    return verdict.reasons.length ? verdict : { ...verdict, passed: true };
  } catch {
    return null;
  }
};

export const runLocalSmartInputCheck = async (imagePath: string): Promise<SmartInputVerdict> => {
  const verdict = defaultVerdict();
  if (!imagePath) return verdict;

  if (!isWebRuntime()) {
    const uniVerdict = await runUniLightweightCheck(imagePath);
    return uniVerdict || verdict;
  }

  try {
    const img = await loadImageElement(imagePath);
    const pixelMetrics = analyzePixelMetrics(img);
    Object.assign(verdict.metrics, pixelMetrics);

    if (pixelMetrics.width && pixelMetrics.height && Math.min(pixelMetrics.width, pixelMetrics.height) < 512) {
      addIssue(verdict, 'low_resolution', '分辨率过低，请上传更清晰的照片');
    }
    const aspectRatio = (pixelMetrics.width || 0) / Math.max(1, pixelMetrics.height || 1);
    verdict.metrics.aspect_ratio = aspectRatio;
    if (aspectRatio < 0.55 || aspectRatio > 1.95) {
      addIssue(verdict, 'abnormal_aspect_ratio', '图片比例异常，请上传常规自拍照片');
    }
    if ((pixelMetrics.brightness || 0) < 58) {
      addIssue(verdict, 'too_dark', '太暗了，开灯或靠近窗边重拍');
    }
    if ((pixelMetrics.brightness || 0) > 236) {
      addIssue(verdict, 'overexposed', '画面过曝，请降低光照后重拍');
    }
    if ((pixelMetrics.blur_score || 0) < 7.2) {
      addIssue(verdict, 'too_blurry', '照片偏模糊，请保持稳定并重新对焦');
    }

    const face = await runFaceDetection(img);
    Object.assign(verdict.metrics, face.metrics);
    const reasons = face.reasons;
    for (const reason of reasons || []) {
      if (reason === 'no_face') addIssue(verdict, 'no_face', '未检测到人脸，请上传正脸自拍');
      else if (reason === 'multiple_faces') addIssue(verdict, 'multiple_faces', '检测到多人，请仅保留单人自拍');
      else if (reason === 'not_frontal' || reason === 'face_tilted') addIssue(verdict, 'not_frontal', '请正对镜头，避免侧脸或大角度偏头');
      else if (reason === 'face_too_small') addIssue(verdict, 'face_too_small', '人脸过小，请靠近镜头重拍');
      else if (reason === 'face_too_close') addIssue(verdict, 'face_too_close', '人脸过近，请稍微后退重拍');
      else if (reason === 'face_near_edge' || reason === 'head_maybe_cropped') addIssue(verdict, 'face_near_edge', '请完整露出头部和肩部，避免贴边或裁头');
    }
  } catch {
    // Keep local checker best-effort; backend gatekeeper is still authoritative.
    return defaultVerdict();
  }

  if (!verdict.reasons.length) verdict.passed = true;
  return verdict;
};
