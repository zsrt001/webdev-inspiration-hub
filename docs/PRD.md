# PRD：AI Wedding Studio（AI 婚纱照生成）v3.0

> 版本：v3.0（Flagship / Studio 3.0）  
> 日期：2026-02-09  
> 文档目标：基于仓库现状（见 `docs/现有功能梳理.md`）给出“产品闭环”与“研发可落地”的需求定义，用于对齐研发/设计/运营。

---

## 0. 一句话定义

为用户提供“可用于打印与社交分享”的高端婚纱照生成体验，并为摄影工作室提供线索收集与转化闭环（Leads → CRM/线下到店）。

---

## 1. 背景与机会

### 1.1 用户痛点（C 端）

- 婚纱照成本高、周期长、试错成本高。
- 用户希望“先看到效果/风格”，再决定是否付费或线下拍摄。
- AI 生成常见问题：塑料感、磨皮感、五官失真、构图“无头”、光照不真实。

### 1.2 商家痛点（摄影工作室/影楼）

- 线上获客成本高，缺少有效线索收集路径。
- 用户在“看到效果”的当下是最高意向时刻，需要即时承接（表单、客服、到店优惠）。

---

## 2. 产品目标

### 2.1 北极星指标（North Star）

- **有效线索数/日（Leads/day）**：提交姓名+电话的线索数

### 2.2 关键业务指标（建议）

- 模板页 → 生成提交转化率
- 生成成功率（preview 产出成功 / 提交数）
- Preview → 付费转化率（或积分扣费转化）
- 人均生成次数、ARPPU（付费用户平均收入）
- 线索表单提交率（在结果页）

### 2.3 质量目标（与“Studio 3.0”一致）

- “塑料感/磨皮感/无头/融脸”等关键失败率显著降低
- 生成结果具备“摄影质感”（肤理、光比、阴影、颗粒等）且构图稳定

---

## 3. 用户画像与场景

### 3.1 角色

1. **访客/普通用户（网页 Web / 小程序）**
   - 选择风格 → 上传自拍 → 生成预览 →（付费）→ 高清下载 → 分享
2. **情侣（双人）**
   - 同设备上传两张，或跨设备远程 Join（扫码上传）
3. **运营/管理员**
   - 查看仪表盘、订单与线索、发放积分、运营活动配置

### 3.2 核心场景

- **单人/情侣婚纱照“快速出片”**：几分钟内拿到“可分享”的预览图
- **远程 Join（大屏/门店）**：主机创建 session，伴侣扫码上传，合成双人作品
- **自由创作（Custom Mode）**：上传/描述自定义场景与服饰参考，生成专属风格
- **导演模式（高级创作）**：用户通过“导演面板”精细控制输入（人像 + 文本 + 参考图/模板）
- **金婚重塑（双人专项）**：为父母/长辈生成“年代感 + 高清质感”的纪念合照
- **线索收集**：结果页领取优惠（如“500 元到店抵扣”）提交信息

---

## 4. 用户旅程（端到端闭环）

### 4.1 单人流程（P0，优化后）

1. 首页提供**单独生成入口**（立即开始）与**风格库入口**（模板卡片）
2. 入口路径 A：直接开始（选择默认模板或快速推荐）  
   入口路径 B：从风格库进入详情页后开始
3. 上传自拍 → **Smart Input Gatekeeper** 即时反馈（人脸/正脸/光线/清晰度）  
   - 不通过：明确原因 + 可执行建议，**不进入生成、不扣费**
4. 通过检测后点击生成 → **入队即扣费**（仅在任务成功入队后扣费）
5. 展示“Ritual Story Loader”（叙事式进度）
6. 生成预览 → **The Reveal**（Ready 遮罩点击揭晓）→ **Before/After 对比**
7. 生成完成即交付高清图 → 下载高清图
8. 生成分享海报（含二维码）
9. 结果页线索承接（优惠领取表单/Banner 伪入口）

### 4.2 双人模式（P0）

**模式 A：本机双传（同一设备上传两张）**

1. 选择双人模板
2. 在同一设备上传两张自拍（新郎/新娘）
3. 触发生成 → 后续同 4.1

**模式 B：异地合拍（Remote Join）**

1. 主设备选择“Couple 模板”并创建 session
2. 展示二维码/Join 链接（伴侣扫码进入 Guest Portal）
3. 伴侣上传自拍 → 主设备轮询/实时更新“已就绪”
4. 主设备触发生成 → 后续同 4.1

**模式 C：金婚重塑（双人专项）**

1. 选择“金婚重塑”模板集合（面向父母/长辈）
2. 上传双人近照或旧照（允许年代感/轻度修复）
3. 生成“年代感 + 高清质感”的纪念合照 → 后续同 4.1

**金婚重塑风格包（默认参数，落地建议）**

- 视觉基调：温暖复古、轻胶片颗粒、柔和对比、轻微暗角
- 色调范围：暖棕/米黄/淡褐（避免过度偏黄）
- 光线：柔光侧打，保留皱纹与皮肤真实纹理（禁止“磨皮塑料感”）
- 服饰建议：经典中式礼服/西式礼服（按模板 Prompt Block 固定）
- 场景建议：复古影楼布景/旧时代庭院/简约室内
- 纹理参数（建议值区间）：  
  - 颗粒强度：`0.20–0.35`  
  - 对比度：`0.85–1.00`  
  - 清晰度：`0.70–0.90`（避免过锐导致“假脸”）
- ComfyUI 节点建议（可配置）：  
  - `IP-Adapter` 权重：`0.55–0.75`  
  - `CFG`：`3.0–5.0`（低塑料感）  
  - `Steps`：`24–32`  
  - `Denoise`（旧照修复）：`0.35–0.55`
- Negative Prompt（固定）：`smooth skin, airbrushed, wax, plastic, cgi, 3d render, anime, oversharp`

**金婚重塑风格包字段（用于落地配置）**

```yaml
style_pack:
  id: golden_anniversary_v1
  base_prompt: "elderly couple, dignified, affectionate, authentic skin texture"
  negative_prompt: "smooth skin, airbrushed, wax, plastic, cgi, 3d render, anime, oversharp"
  color_grade:
    palette: warm_brown
    temperature: "+8~+15"
    tint: "-2~+2"
  texture:
    grain_strength: 0.20-0.35
    vignette: 0.10-0.25
    clarity: 0.70-0.90
  retouch:
    skin_smooth: "<=0.15"
    age_preserve: true
  restore:
    noise_reduction: 0.25-0.40
    scratch_repair: 0.20-0.35
  output:
    aspect: "4:5"
    upscale: "2x"
  control_weights:
    face_id: 0.60-0.75
    pose: 0.70-0.85
    ip_adapter: 0.55-0.75
```

**金婚重塑模板清单（P0 预置 3 款）**

- `golden_vintage_studio_8090`：80/90 影楼经典（复古影楼布景）
- `golden_chinese_courtyard`：中式复古庭院（红金色调）
- `golden_modern_remake`：现代翻拍纪念（简约室内）

**金婚重塑模板 Prompt Block 示例（可直接落地）**

```yaml
# 模板示例 A：80/90 影楼经典
clothing_prompt: "couple in classic formal wedding attire, neat hair, modest accessories"
scene_prompt: "retro studio backdrop, soft draped curtain, subtle floral props"
lighting: "soft side key light, gentle fill, low contrast"
texture: "light film grain, natural skin texture, warm tone"

# 模板示例 B：中式复古庭院
clothing_prompt: "couple in traditional Chinese wedding attire, red and gold accents"
scene_prompt: "vintage courtyard, wooden doors, warm lantern glow"
lighting: "warm ambient light, soft shadows"
texture: "fine film grain, slight vignette, warm brown tone"

# 模板示例 C：现代翻拍纪念
clothing_prompt: "couple in elegant modern wedding attire, clean lines"
scene_prompt: "minimal indoor setting, neutral background, subtle decor"
lighting: "even soft light, natural highlights"
texture: "subtle grain, balanced contrast, gentle warmth"
```

### 4.3 Studio 3.0 三段式体验（Smart Input → Ritual Gen → The Reveal）（P0）

> 目标：把“上传-等待-出图”的工具链体验，做成“专业摄影工作室”的仪式化体验，显著降低差评归因（把坏输入拦在生成前）。

1. **Smart Input（智能输入）**
   - 上传后立即给出可理解的反馈：有人脸/正脸/光线/清晰度是否达标
   - 未达标给“可执行建议”（如“太暗了，开灯/换窗边”），并阻断进入生成
2. **Immersive Pick（沉浸式选择）**
   - 选择模板/场景时提供轻量动效与信息结构（分类、标签、推荐理由）
3. **Ritual Gen（仪式感生成）**
   - 用“施工现场”叙事文案替代 spinner（扫描→剪裁→打光→冲洗）
   - 明确“扣费时机”和“失败是否退款/返还积分”
4. **The Reveal（惊喜揭晓）**
   - 先展示“Ready/已完成”毛玻璃遮罩，用户点击后揭晓
   - 默认展示 Before/After Slider（原图 vs AI 成片）强化反差与分享动机

---

## 5. 功能需求（按模块拆分）

> 优先级：P0 必须、P1 建议、P2 可选。  
> 备注：本仓库当前已包含大量 UI 与部分后端服务，但存在接口/数据结构不一致，PRD 以“闭环”所需为准。

### 5.1 模板/风格库（P0）

**需求**

- 模板列表可按 `category`（single/couple/custom）过滤
- 一级分类建议（P1）：**单人独美 / 双人合照 / 金婚重塑（父母/长辈）/ 时光照相馆（父母补拍）/ 自由创作**
- 模板可承载营销信息（P1）：如 `marketing_title/marketing_subtitle/recommended_for`
- 模板详情返回：
  - `id/title/category/image_url/tags`
  - prompt 模块化字段（服饰/背景）用于生成
  - 模板参考资源（用于导演模式）：`clothing_ref_image_url`、`scene_ref_image_url`、`prompt_blocks`

**验收**

- 首页模板列表可用、图片可正常加载
- 模板字段在前后端一致（避免旧的 `/templates/list` 与新的 `/templates` 分裂）

**对应代码**

- 后端：`backend/app/services/template_service.py`  
- 前端：`frontend/src/stores/template.ts`、`frontend/src/pages/index/index.vue`

### 5.2 上传与存储（P0）

**需求**

- 客户端上传前做 **ID 证件/敏感图**拦截（至少 stub + 后续可替换为真检测）
- Smart Input（P0→P1）：上传后即时检测并反馈
  - P0：后端 Gatekeeper（LLM Vision）判定“有人脸/光线/清晰度”，不通过则阻断生成
  - P1：前端本地检测（网页 Web：`face-api.js`；小程序：可选轻量规则/后端快速接口）
- 性别识别（P1）：在不做强限制的前提下，输出 `gender` 供“服饰/姿态/模板推荐”优化（允许用户手动覆盖）
- 图片上传到 S3/MinIO（或 Vercel 可访问的对象存储），返回 **公网 URL**
- 后续所有 AI/LLM 只接收公网 URL（不可使用本地临时路径）

**验收**

- 前端上传后拿到 URL，并能在浏览器直接打开
- 后端能基于该 URL 调用 Jiekou Vision / ComfyUI
- 当检测失败时，用户看到明确原因 + 可执行建议（重拍/开灯/正对镜头），且不会进入扣费/生成阶段

**对应代码**

- 后端：`backend/app/routers/upload.py`、`backend/app/services/storage.py`  
- 前端：`frontend/src/utils/api.ts`（`uploadFile`）

### 5.3 生成引擎（P0，生产引擎：ComfyUI）

**需求**

- **统一生产工作流（不做降级版）**：所有输入组合走同一条 ComfyUI 生产管线，未提供参考图时自动跳过对应节点
- 核心节点（建议标准）：
  - `CLIP Text Encode`：总控风格与场景
  - `FaceID/InstantID`：身份一致
  - `OpenPose ControlNet`：姿态稳定，避免无头
  - `Depth/Normal ControlNet`：构图体积感
  - `IP-Adapter (Clothing)`：服装参考
  - `IP-Adapter (Scene)`：场景参考
  - `KSampler`：固定参数 + 受控随机
  - `Hi-Res Fix + Upscale`：高清交付
  - `Negative Prompt Lock`：禁止塑料/CGI/磨皮
- 双人模式（P1）：支持“双传/异地合拍”的两张人脸输入，并避免肢体融合
  - 方案候选：双 FaceID 并行 + 双 Pose；或“先合成构图再 inpaint”
- 生成前链路：
  - Gatekeeper：自拍质量检测（脸/光照/清晰度）
  - Prompt Brain：将“服饰+背景”提纯为摄影协议 prompt
- 生成后链路：
  - QA：无头/融脸/黑图检测（失败自动重试一定次数）
  - 姿态检测失败处理：OpenPose 失败即阻断生成并提示重拍（或给出可执行建议）

**验收**

- 生成请求返回可追踪的 `order_id` + `job_id`（ComfyUI 任务）
- 有明确失败提示与可重试机制
- 失败/拦截时不扣费；或按策略返还积分（需在产品规则里明确）

**对应代码**

- 生产链路：`backend/app/services/comfyui_service.py`（已作为主引擎）
- 历史兼容：旧生成链路已归档，不作为商用主链路依赖

### 5.4 订单与状态机（P0）

**需求**

- 订单状态必须遵循商用主状态机：
  - `CREATED → CHECKING → GENERATING → COMPLETED`
  - 说明：`PREVIEW_READY/PAID/UPSCALING` 作为历史兼容状态保留，不作为主链路依赖
- 订单数据至少包含：
  - `id/user_id/status/template_id/source_image_urls/preview_image_urls/final_image_urls/price/error_message/created_at/updated_at`
- 生成异步化：以 Worker 队列（ARQ）为主，Webhook 仅做兼容

**验收**

- 预览页可轮询订单，状态变化驱动 UI
- Worker 能更新对应 order 并写入 `final_image_urls`（可同步回写 `preview_image_urls`）
- 扣费时机明确：Gatekeeper 通过且 ComfyUI 任务入队后扣费；失败/超时自动返还

**对应代码（现状）**

- 数据模型：`backend/app/models/order.py`
- 队列执行：`backend/app/worker.py`、`backend/app/worker_tasks.py`
- 下单入口：`backend/app/routers/orders.py`

### 5.5 付费/积分体系（P0）

**需求**

- 积分余额查询、套餐列表、购买（可先 mock）
- 生成扣费策略：
  - 每次生成按模式扣固定积分（入队即扣）
  - 失败自动按订单 `credits_cost` 退回
- 定价策略（产品口径，P1）：单人 9.9、双人（异地合拍）19.9；实现上可映射为不同积分消耗

**验收**

- 导航栏实时显示积分余额
- 购买后余额更新，生成按钮在余额不足时有明确引导

**对应代码**

- 后端：`backend/app/routers/credits.py`、`backend/app/services/credit_service.py`
- 前端：`frontend/src/components/PaymentModal.vue`、`frontend/src/components/NavBar.vue`

### 5.6 双人模式与 Session（P0）

**需求**

- 本机双传（模式 A）：单页两个上传槽位（新郎/新娘），上传后进入生成
- 异地合拍（模式 B）：创建 session（返回 `session_id/join_url/qr_code_url/ttl`）
- 金婚重塑（模式 C）：独立模板集合 + 年代感风格包（默认滤镜与修复策略）
- host/guest 上传各自自拍（应为公网 URL）
- host 轮询 session 状态：`WAITING/UPLOADING/READY/PROCESSING/COMPLETED/EXPIRED`
- 邀请合拍（V1.1 / P1）：把 session 变成“分享卡片”发给对方（带文案与封面）

**验收**

- 本机双传：两张图片上传完成后才可点击“生成”
- guest 上传成功后，host 端 UI 明确提示“已就绪”
- session 超时后有清晰提示
 - 金婚重塑：输出带“年代感风格一致性”的双人合照（可配置纹理/颗粒/色调）

**对应代码**

- 后端：`backend/app/routers/session.py`、`backend/app/services/session_service.py`
- 前端：`frontend/src/pages/detail/detail.vue`、`frontend/src/pages/join/landing.vue`

### 5.7 结果页体验（Ritual UX）（P0）

**需求**

- 叙事式加载文案轮播（Story Loader）
- Before/After 对比组件
- The Reveal（P1）：增加“毛玻璃 Ready → 点击揭晓”的 Curtain Reveal 动效
- 高清下载与分享海报（含二维码）
- 交付页广告位（MVP / P0）：高清按钮下方放“本地摄影服务/人工精修”伪入口，先统计 CTR（后续再接真实表单）

**验收**

- 网页 Web 上可下载（blob 下载或新窗口兜底）
- 分享海报可生成二维码并可保存
- Banner 点击可追踪（analytics），为后续商务合作提供数据

**对应代码**

- `frontend/src/pages/preview/preview.vue`
- `frontend/src/pages/result/download.vue`
- `frontend/src/components/CompareSlider.vue`

### 5.8 Director Mode（导演模式，P1）

**需求（前端面板结构，必须按行）**

- 入口：Studio 页面新增“Director Mode”入口/Tab
- **适用范围**：单人模式与双人模式（本机双传/异地合拍）均支持导演模式的流程逻辑与 Cascade 规则
- 第 1 行：我是谁（Mandatory）
  - 上传用户半身/全身照（用于人脸与姿势提取）
- 第 2 行：我要什么（Mandatory，核心）
  - 文本输入框：用户输入完整风格描述（如“黑色婚纱，哥特风，古堡背景，吸血鬼新娘氛围”）
- 第 3 行：参考图（Optional，高阶）
  - 上传服装参考图（IP-Adapter）
  - 上传场景参考图（IP-Adapter）
  - 选择服装模板/场景模板（使用模板默认参考图与 Prompt Block）
  - 提示文案：“没灵感？上传一张你喜欢的图给 AI 参考，不传则按文字生成。”

**需求（Director Mode Logic，v3.0 Cascade Logic）**

- **绝对优先级**：Upload（强控） > Text（创作） > Preset（点选） > Random（盲盒）
- 同一条 ComfyUI 工作流，未提供输入则对应节点禁用/或由系统随机补足（不是低配版）

**1）输入层（UI）**

- [A] 场景/Scene：上传框 OR 预设库选择  
- [B] 服装/Outfit：上传框 OR 预设库选择  
- [C] 文本/Text：输入框  
- [D] 随机/Random：如果上述全空，系统接管

**前端提示（冲突说明）**

- 当“上传图”生效：提示“已使用上传图控制场景/服装，文本与预设将不生效”
- 当“文本”生效且选了预设：提示“文本优先生效，预设已忽略”
- 当“系统随机”生效：提示“未提供参考，已随机选择预设”

**2）后端执行逻辑（The Cascade）**

后端在组装 ComfyUI 参数时，必须对“场景”和“服装”分别执行判定循环。

**A. 场景控制（Scene Logic）**

1. 判定 1（最高级）：用户传图了吗？  
   - YES → 启用 `IP-Adapter (Scene)`，载入用户上传图（权重 `0.6`），**忽略文本中的场景描述与预设**
   - NO → 进入判定 2
2. 判定 2：用户写字了吗？  
   - YES → 禁用 `IP-Adapter (Scene)`，完全依赖 `CLIP Text Encode` 生成场景
   - NO → 进入判定 3
3. 判定 3：用户选预设了吗？  
   - YES → 启用 `IP-Adapter (Scene)`，载入预设库高清底图（权重 `0.5`）
   - NO → 进入判定 4
4. 判定 4（保底）：系统随机  
   - ACTION → 从预设库随机抽取一张场景图，启用 `IP-Adapter (Scene)` 并载入该图

**B. 服装控制（Outfit Logic）**

1. 判定 1（最高级）：用户传图了吗？  
   - YES → 启用 `IP-Adapter (Clothing)`，载入用户上传图（权重 `0.6`），**忽略文本中的服装描述与预设**
   - NO → 进入判定 2
2. 判定 2：用户写字了吗？  
   - YES → 禁用 `IP-Adapter (Clothing)`，由 `CLIP Text Encode` 决定服装风格
   - NO → 进入判定 3
3. 判定 3：用户选预设了吗？  
   - YES → 启用 `IP-Adapter (Clothing)`，载入预设库服装参考图（权重 `0.5`）
   - NO → 进入判定 4
4. 判定 4（保底）：系统随机  
   - ACTION → 从预设库随机抽取一张服装图，启用 `IP-Adapter (Clothing)` 并载入该图

**可配置参数（默认值，可后台配置）**

- `scene_ip_weight_user`: `0.6`
- `scene_ip_weight_preset`: `0.5`
- `scene_ip_weight_random`: `0.5`
- `outfit_ip_weight_user`: `0.6`
- `outfit_ip_weight_preset`: `0.5`
- `outfit_ip_weight_random`: `0.5`

**验收**

- 前端面板顺序固定且字段齐全，必填项未完成时禁止生成
- 后端根据输入组合自动选择降级路径（无图不报错）

### 5.9 Leads（线索收集）（P0）

**需求**

- MVP（P0）：可先“伪入口”只记录 CTR；或直接上线简表（两者择一，见未决问题）
- V1.2（P1）：上线真实表单“领取 500 元婚摄津贴/到店抵扣”
- 字段（建议最低）：`name/phone/city`（可选：`wedding_date/notes`）
- 后端写入数据库表 `leads`（或先写入 JSON/第三方表）
- 管理端可查看线索列表、导出

**验收**

- 提交成功有明确反馈
- 管理端可看到新增线索（至少 API 层可查）

**对应代码（现状）**

- DB model：`backend/app/models/lead.py`
- 前端表单：`frontend/src/pages/preview/preview.vue`
- 注意：后端 leads 路由未实现，前端提交路径也需统一（见 `docs/现有功能梳理.md`）

### 5.10 Admin Dashboard（P1）

**需求**

- KPI：订单数、活跃用户、积分流通、模板分布、近期活动
- 用户列表与发放积分

**对应代码**

- 后端：`backend/app/routers/admin.py`、`backend/app/services/admin_service.py`
- 前端：`frontend/src/pages/admin/index.vue`

### 5.11 Analytics（P2）

**需求**

- 埋点：banner 点击、模板点击、生成点击、支付点击、表单提交等
- 后台查看统计
- MVP 必埋（建议上收敛到 P0）：Banner CTR（伪入口）、模板点击、生成发起/失败原因分布

**对应代码**

- `backend/app/routers/analytics.py`（当前为内存计数，生产建议落库）

---

## 6. 设计与交互（Design System）

**设计系统来源（仓库内已存在）**

- 设计主文档：`design-system/ai-wedding-studio/MASTER.md`
- 前端变量：`frontend/src/uni.scss`

**关键体验原则（建议作为硬约束）**

- 叙事式等待（替代传统 spinner），强化“仪式感”
- 前后对比（CompareSlider）作为转化关键组件
- “摄影协议式”生成：强调质感与真实纹理，弱化“AI 工具感”
- 明确的失败提示与重试路径（避免用户无反馈）

---

## 7. 非功能性需求（NFR）

### 7.1 性能与可用性

- 生成请求响应：应在 1s 内返回追踪 ID（异步），避免前端长等待阻塞
- 超时策略：Jiekou/ComfyUI 调用超时有兜底提示
 - ComfyUI 生产队列：支持并发与重试，确保峰值时段稳定出图

### 7.2 安全与隐私（必须）

- 客户端：禁止上传证件照/敏感文档（至少 stub + 后续替换为真检测）
- 原图生命周期：原图应在特征提取/生成完成后尽快清理（存储侧提供删除能力）
- 不在前端日志/接口响应中泄露密钥（Jiekou/ComfyUI/AWS）

### 7.3 合规与风控（建议）

- 内容安全：对生成结果进行基础审核（如不当内容拦截）
- 用户数据：电话等敏感字段加密存储/最小化访问权限

---

## 8. 里程碑建议（落地顺序）

> 以“闭环优先”为原则，先打通模板→上传→智能检测→生成→揭晓→分享/转化。

### 阶段一：MVP（P0 闭环）

- 统一后端入口与接口（模板、上传、生成、订单、回调/异步状态同步）
- ComfyUI 生产环境搭建（GPU、工作流版本、队列与回调）
- 前端把所有“本地路径”改为“先上传拿 URL 再生成”
- Smart Input 最小可用：失败原因可解释、可执行建议、失败不扣费
- 交付页伪入口 Banner + CTR 埋点（用于验证本地服务需求）

### 阶段二：V1.1 社交裂变版（拉新 + 提高客单价）

- 邀请合拍（Remote Join 邀请卡片、受邀者上传、双端结果同步）
- 双人定价上调（产品口径：19.9；实现映射积分消耗）
- 模板体系扩展：时光照相馆/父母补拍营销入口（Banner + 模板集合）

### 阶段三：V1.2 变现增强版（榨取流量价值）

- 伪入口升级为真 Leads：表单落库、Admin 查看与导出、CRM 对接
- 本地化服务推荐（按城市/婚期分发）
- Live Portrait（可选增购/会员权益）：静态图转 5 秒视频

---

## 9. 主要风险与依赖

- 第三方依赖：Jiekou/ComfyUI/S3 可用性与成本波动
- 图片 URL 可访问性：若用内网/临时 URL，会导致生成失败
- 现有代码存在“双入口 + 双数据源（DB/JSON）”分裂，需尽快收敛
 - ComfyUI 运维：GPU 成本、模型版本管理、队列稳定性与监控

---

## 10. 决策与未决（需要产品/技术决策）

**已定口径（已落地）**

1. 扣费策略：采用“入队即扣费，失败自动退款”。
2. 订单落库：采用 Postgres 主链路。
3. 异步策略：采用 Worker（ARQ）主链路，Webhook 仅兼容。
4. Leads：采用真实表单落库 + Admin 导出。
5. Smart Input：采用“网页 `face-api.js` 本地检测 + 小程序轻量规则 + 后端 Gatekeeper 权威兜底”。

**仍需决策（待定）**

1. leads 的字段范围与隐私合规策略（加密/脱敏/访问控制）是否升级到强制加密版本？
