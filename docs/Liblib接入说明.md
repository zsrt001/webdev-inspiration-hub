# Liblib Workflow / Template API 接入说明

本文档说明当前项目如何切换到 `Liblib` 作为主生成引擎。

## 1. 当前接入方式

项目已经支持第三种生成 provider：

- `comfyui`：自建 ComfyUI
- `comfyui + cloud`：Comfy Cloud
- `liblib`：Liblib Workflow / Template API

切换到 `Liblib` 时，后端会走：

- `backend/app/services/liblib_service.py`
- `backend/app/services/generation_service.py`

## 2. 你需要提供的配置

至少需要以下 4 项：

```env
GENERATION_ENGINE=liblib
LIBLIB_BASE_URL=https://openapi.liblibai.cloud
LIBLIB_ACCESS_KEY=
LIBLIB_SECRET_KEY=
```

除此之外，还需要：

```env
LIBLIB_TEMPLATE_MAP={}
```

`LIBLIB_TEMPLATE_MAP` 用来把项目中的模板 ID / 风格家族，映射到 Liblib 的 `templateUuid` 或工作流参数。

## 3. 模板映射文件

仓库内已提供一个示例：

- `backend/artifacts/liblib_template_map.example.json`

你可以先按这个文件复制一份，填上真实的 `templateUuid`。

## 4. 推荐填法

最小可用示例：

```json
{
  "chn_xiuhe": {
    "template_uuid": "替换为真实Liblib模板UUID"
  },
  "royal_castle": {
    "template_uuid": "替换为真实Liblib模板UUID"
  },
  "custom_mode": {
    "template_uuid": "替换为真实Liblib模板UUID"
  }
}
```

更完整的写法：

```json
{
  "chn_xiuhe": {
    "template_uuid": "xxxx",
    "generate_params": {
      "num_images": 1
    }
  },
  "solo_chn_xiuhe": {
    "template_uuid": "xxxx"
  },
  "royal_castle": {
    "template_uuid": "yyyy"
  }
}
```

映射优先级：

1. `template.id`
2. `template.style_family`
3. `template.title`

也就是说，如果你没有给每个 `template.id` 单独配，只给 `style_family` 也能跑。

## 5. 当前项目里的模板键

主要风格家族如下：

- `chn_xiuhe`
- `korean_minimal`
- `royal_castle`
- `old_money`
- `gothic_romance`
- `beach_sunset`
- `hk_retro`
- `twilight_forest`
- `japanese_shiromuku`
- `cyberpunk_city`
- `school_days`
- `classic_bw`
- `golden_vintage_studio_8090`
- `golden_chinese_courtyard`
- `golden_modern_remake`
- `custom_mode`

## 6. 本地联调脚本

仓库已提供一个最小联调脚本：

- `backend/scripts/liblib_smoke_test.py`

使用方式：

```bash
python backend/scripts/liblib_smoke_test.py
```

它会校验：

- Key 是否存在
- 签名是否可生成
- `LIBLIB_BASE_URL` 是否可访问
- `/api/generate/webui/status` 是否返回可解析响应

## 7. 重要说明

- 当前聊天中出现过旧的 `SecretKey`，正式环境请务必轮换
- 没有 `LIBLIB_ACCESS_KEY` 时，项目无法真正发起 Liblib 请求
- 没有 `LIBLIB_TEMPLATE_MAP` 时，模板生成无法绑定到具体 Liblib 工作流 / 模板

## 8. 推荐上线顺序

1. 先配置 `LIBLIB_ACCESS_KEY / LIBLIB_SECRET_KEY`
2. 先给 3 个核心风格绑定 `templateUuid`
   - `chn_xiuhe`
   - `royal_castle`
   - `custom_mode`
3. 跑通单人生成
4. 再补双人模板
5. 最后补金婚系列与剩余风格
