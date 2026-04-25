# ComfyUI 生产依赖清单（节点 / 模型 / 工作流）

> 目标：把仓库内的 ComfyUI 工作流 JSON 跑到**可商用稳定**（避免 “workflow empty”/节点缺失导致自动退款）。

## 1. 工作流文件

- `backend/app/workflows/comfyui_base.json`：商业版基线（文本 + IP-Adapter + ControlNet 预处理）
- `backend/app/workflows/comfyui_couple_inpaint.json`：双人极端近景兜底（遮罩 inpaint 修复）
- `backend/app/workflows/comfyui_live_portrait.json`：Live Portrait（静态图 → 5s 视频，需自定义节点）

## 2. 关键节点依赖（你需要在 ComfyUI 里安装/启用）

### 2.1 IP-Adapter 相关

工作流里出现的 `class_type`：
- `CLIPVisionLoader`
- `IPAdapterModelLoader`
- `IPAdapterApply`

你需要确保 ComfyUI 节点包中存在上述节点（常见来源：IP-Adapter 系列自定义节点包）。

### 2.2 ControlNet 相关

工作流里出现的 `class_type`：
- `ControlNetLoader`
- `ControlNetApplyAdvanced`

这些通常属于 ComfyUI + ControlNet 基础节点（或 ControlNet 扩展包）。

### 2.3 ControlNet 预处理（自动从人像生成 pose/depth/normal）

工作流里出现的 `class_type`：
- `OpenPosePreprocessor`
- `DepthAnythingPreprocessor`
- `NormalBaePreprocessor`

你需要确保 ComfyUI 安装了对应的预处理节点包（常见来源：`comfyui_controlnet_aux`）。

### 2.4 双人兜底 Inpaint（T10 的 close-up 修复）

工作流里出现的 `class_type`：
- `VAEEncodeForInpaint`
- `ImageToMask`

这些节点在不同版本/节点包里命名可能不同；如果你环境里没有 `ImageToMask`，兜底会自动失败并回到标准重试。

### 2.5 Live Portrait（视频输出）

工作流里出现的 `class_type`：
- `LivePortrait`
- `VHS_VideoCombine`

这两类通常来自两个常见扩展：
- LivePortrait/SADTalker 类模型节点包（提供 `LivePortrait`）
- VideoHelperSuite 类视频合成节点包（提供 `VHS_VideoCombine`）

## 3. 模型文件名（当前工作流引用的默认值）

> 你最后会把**实际可用的模型文件名**给我，我再一次性把工作流和 `.env.example` 对齐到你本机 ComfyUI 的真实路径/名称。

### 3.1 Base 工作流（`comfyui_base.json`）

- Checkpoint：`sdxl_base_1.0.safetensors`
- CLIP Vision：`CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`
- IP-Adapter：
  - `ip-adapter_sdxl.safetensors`
  - `ip-adapter-faceid_sdxl.safetensors`
- ControlNet：
  - `controlnet_openpose_sdxl.safetensors`
  - `controlnet_depth_sdxl.safetensors`
  - `controlnet_normal_sdxl.safetensors`
- Upscaler：`4x-UltraSharp.pth`

### 3.2 Live Portrait（`comfyui_live_portrait.json`）

该工作流的模型/节点完全由你安装的 LivePortrait/视频扩展决定（此处先占位）。

## 4. 环境变量（后续统一补齐）

仓库已支持这些关键环境变量（示例见 `.env.example`）：
- `COMFYUI_BASE_URL`
- `COMFYUI_WORKFLOW_PATH`
- `COMFYUI_INPAINT_WORKFLOW_PATH`
- `COMFYUI_LIVE_PORTRAIT_WORKFLOW_PATH`
- `COMFYUI_NODE_MAP`
- `COMFYUI_LIVE_PORTRAIT_NODE_MAP`
- `COMFYUI_REQUIRE_STORAGE_DELIVERY`（建议 `true`，失败时直接报错退款，不再交付 `/view` 临时链接）
- `GATEKEEPER_ALLOW_WITHOUT_PILLOW`（生产建议 `false`）
- `QA_ALLOW_WITHOUT_PILLOW`（生产建议 `false`）
- `QA_REQUIRE_VISION`（高质量场景建议 `true` 并配置 `OPENROUTER_API_KEY`）
