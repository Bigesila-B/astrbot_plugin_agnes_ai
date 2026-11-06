# Agnes AI 模型调用 v2.1.0

AstrBot 图片与视频生成插件。根据 2026-10-28 核对的 Agnes 官方文档升级，保留 `/image`、`/img2img`、`/video`。

## 最简单的用法

```text
/画图 一只戴墨镜的橘猫
/画图 把背景换成海边
/视频 小猫在海边奔跑
/视频 让这张图里的人物转身挥手
```

`/画图` 在没有参考图片时文生图；附图或引用图片时自动改图，无需记忆文生图/图生图两种指令。`/画`、`/生图`、`/绘图` 是同义指令；`/改图` 明确要求提供图片；`/视频` 和 `/做视频` 是原 `/video` 的中文别名。比例、分辨率、时长均可省略，由设置决定。

默认只显示简短进度；需要模型等详细参数时开启“显示生成参数详情”。`/agnes` 或 `/画图帮助` 查看帮助，`/查视频` 查询已提交的视频任务。旧英文指令继续可用。

## 自然语言触发与 Agent 聊天

设置新增 **自然语言触发（Agent工具）**：`enable_natural_language`，默认开启。启用后可直接在聊天里说：

```text
帮我画一张水彩风格的橘猫
把这张照片改成雨夜赛博朋克风格
帮我做一个8秒竖屏视频，内容是海边日落
让这张图里的猫动起来，镜头缓缓推进
```

通过 AstrBot 原生工具调用让 Agent 根据上下文判断是否需要生成，并整理提示词、比例、时长等参数。需要满足：

- 当前聊天模型及 Agent 执行器支持工具调用（function calling）。
- 在 AstrBot 工具管理中启用 `agnes_generate_image` 和 `agnes_generate_video`。
- 如果当前人格使用工具白名单，将上述两个工具加入白名单；插件不会强行改写人格的工具选择。
- 消息正常唤醒 AstrBot 并进入聊天。在群里仍遵守原有 @机器人/唤醒词规则，不会扫描所有群消息。

当前消息附图或引用图会自动传给工具。工具可使用上下文中明确已知的图片链接；较早的图片如果没有当前引用或有效链接，需要引用或重发，插件不会猜测图片地址。只讨论生成方法、分析图片、写提示词或明确表示不要生成时，系统指引会让 Agent 保持正常聊天；实际意图选择仍由聊天模型完成。

工具立即返回排队状态，生成在后台继续，完成后自动发送到原来的聊天，不占用 Agent 的长时间工具等待。工具提示词直接作为生成内容处理，不会把提示词里的 `2K`、`16:9` 或 `--model=...` 重新解析为命令。Agent 仅使用配置选定的模型，不会为了高分辨率自动切换到普通付费视频模型。

关闭此开关后，插件会从当前 LLM 请求中移除自己的生成工具，同时在工具执行入口再次检查开关。手动指令仍可用；关闭开关前已接收的任务继续完成。相同消息中的相同工具请求会去重，减少 Agent 重试导致的重复生成。重载/卸载会取消尚在本地执行的后台等待；已提交的远端视频可用原 `video_id` 继续查询。

## 安装与覆盖升级

1. 备份原插件配置。配置位于 AstrBot 的 `data/config/`，文件名以实际插件目录名为准。
2. 解压 `agnes-ai2.1.0.zip`，将其中的 `agnes-ai` 目录放入 AstrBot 的 `data/plugins/`。
3. 覆盖升级时保持原插件目录名；如果原目录名不同，将新文件放进原目录。不要同时保留两个副本，避免重复注册指令。
4. 在 WebUI 重载插件，填写或确认 `api_key`，保存配置。

ZIP 也可用于支持 ZIP 导入的插件管理页面。仅依赖 `aiohttp` 和标准库，`requirements.txt` 已声明依赖。代码使用 Python 3.10+ 语法；应在当前 AstrBot 所要求的 Python 环境下运行。

旧视频模型 `agnes-video-v2.0` 自动迁移为 `agnes-video-2.5-flash`。已有的有效图片模型配置保留；新安装默认图片模型为 `agnes-image-2.5-flash`。覆盖升级后如需切换图片模型，可在 WebUI 修改，或在指令中指定 `--model=agnes-image-2.5-flash`。

旧 `default_frame_rate` 已移除。新视频接口不接受 `width`、`height`、`num_frames` 等旧参数；时长改为 4–12 秒整数，不会静默修改用户请求。

## 图片指令

```text
/image 16:9 2K 云海之上的发光城市，日落金色光线
/image 3:2 4K 一只坐在窗台看雨的橘猫
/image --model=agnes-image-2.5-flash 1:1 简洁插画
/img2img 4:3 2K 将场景变为雨夜，保留主体和构图
```

`/img2img` 需要在同一消息附图或引用包含图片的消息，支持多图合成。也可重复指定 `--image=https://...`。图片会在本机读取为 Data URI；任意一张参考图读取失败会报错，不会悄悄漏掉它。

图片分辨率：`1K`、`2K`、`3K`、`4K`。图片比例：`16:9`、`9:16`、`1:1`、`4:3`、`3:4`、`2:3`、`3:2`、`21:9`。实际像素由模型决定，例如 16:9 的 1K 为 1312×736，不是固定 1920×1080。

上述档位和比例适用于 Image 2.1/2.5。旧 Image 2.0 保留兼容模式，仅接受 1K，并映射为旧文档列出的 1024×1024、1024×768、768×1024 中最接近的尺寸；需要完整比例或高分辨率时请选择 2.1/2.5。

图片提示词中的 `5s`、`10秒` 等文字会保留。独立词恰好为 `1K` 等参数词时，会被识别为参数。

## 视频指令

```text
/video 16:9 5s 一只猫在日落海滩上漫步
/video 9:16 8秒 城市夜景，镜头缓慢上升
/video 5s 镜头缓缓推进，人物转身
/video 5s 关键帧 从第一张图平滑过渡到第二张图
/video 5s --mode=reference 使用 <Picture 1> 的人物和 <Picture 2> 的场景
/video 5s --mode=last_frame 缓慢推进并停在尾帧构图
/video 16:9 8s 1080P --model=agnes-video-2.5 雨夜城市的电影镜头
```

比例、时长、分辨率参数可以调整顺序。视频比例支持 `16:9`、`9:16`、`1:1`、`4:3`、`3:4`、`21:9`。

| 输入 | 新接口模式 |
| --- | --- |
| 没有图片 | `text` 文生视频 |
| 一张图片 | `keyframe`，作为 `first_frame` |
| 多张图片 | `reference`，作为 `images` |
| 一到两张图片 + `关键帧` / `keyframes` / `--mode=keyframe` | 第一张首帧、第二张尾帧 |
| 一张图片 + `--mode=last_frame` | 仅约束尾帧 |
| 图片 + `--mode=reference` | 参考图模式，可重新构图 |

直接图片优先于引用图片，引用消息内保持原有顺序；`--image=` 图片排在附图之后。关键帧模式多于两张会报错。

- `agnes-video-2.5-flash`：仅 720P，最多 5 张参考图。
- `agnes-video-2.5`：720P / 1080P / 1K / 2K，最多 8 张参考图。1K 为固定方形输出，其他档位由比例决定。
- 不会因分辨率不匹配自动切换到普通版。计费与可用性以平台为准。
- 本插件仍以图片和视频生成为范围，尚未提供音频参考与视频参考的聊天指令。

## 视频参考图的公网访问

新版视频接口要求 **Agnes 服务可下载的公网 HTTP(S) URL**，不能沿用图片接口的 Base64 输入。

`video_image_source=auto` 时先尝试 AstrBot 自带 `register_to_file_service()`；不可用时使用消息中的公网图片 URL。文件服务需要将 AstrBot 的 `callback_api_base` 配置为外部能访问的地址，并使对应 `/api/file/...` 路径可达。本机或局域网地址不满足要求。

没有公网回调地址时，可在指令中提供有效的公网链接：

```text
/video 5s --image=https://your-domain.example/first.png 镜头缓缓推进
/video 5s 关键帧 --image=https://your-domain.example/first.png --image=https://your-domain.example/last.png 保持人物一致
```

请替换示例域名。平台 CDN 地址能否被 Agnes 访问取决于防盗链和有效期；如果报参考媒体无效，请换成文件服务或可直接下载的链接。链接应保持有效直到任务完成。插件不会将图片上传到第三方图床。

## 查询与帮助

```text
/agnes_help
/agnes_query video_你的任务ID
/agnes_query video_你的任务ID agnes-video-2.5
```

任务创建后会显示 `video_id` 和模型名。超时后可查询原任务，避免重新生成。查询立即返回当前状态；完成后发送视频。查询普通版任务应带上对应模型名。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enable_natural_language` | `true` | 允许 Agent 自然语言调用；关闭保留手动指令 |
| `show_generation_details` | `false` | 显示手动生成的模型等参数详情 |
| `api_key` | 空 | 必填，Agnes API 密钥 |
| `api_base_url` | `https://apihub.agnes-ai.com/v1` | 根地址自动补 `/v1`；代理需实现新版接口 |
| `default_image_model` | `agnes-image-2.5-flash` | 保留 2.1 / 2.0 可选 |
| `default_image_size` | `1K` | 图片档位，支持 1K–4K |
| `image_response_format` | `url` | 可选 `b64_json`，同时兼容两种返回 |
| `default_video_model` | `agnes-video-2.5-flash` | 或普通 `agnes-video-2.5` |
| `default_video_size` | `720P` | Flash 只能选 720P |
| `default_aspect_ratio` | `16:9` | 附图且未显式指定时尝试推断比例 |
| `default_video_duration` | `5` | 4–12 秒整数 |
| `video_image_source` | `auto` | `auto` / `direct` / `file_service` |
| `download_video` | `true` | 下载后发送；跨机器协议端可关闭 |
| `request_timeout` | `180` | 单次 API 请求超时秒数 |
| `video_poll_interval` | `2` | 轮询间隔秒数，最低 1 秒 |
| `video_poll_timeout` | `600` | 实际等待上限，单位秒 |

视频临时文件最多保留一小时，卸载时也会清理。下载失败会改用视频 URL 发送；平台发送能力和协议端文件访问由 AstrBot 适配器决定。

创建图片/视频的 POST 请求不会自动重试。如果创建阶段网络超时，服务端可能已收到请求，请先查看 Agnes 平台任务。查询阶段的网络错误、429、5xx 会退避重试，401/403 等错误直接提示。

## 测试与验证范围

附带 `tests/`，使用标准库 unittest、AstrBot 接口替身和本地 aiohttp 测试服务器验证中文指令、工具参数 schema、开关与工具隔离、后台投递、去重与取消，以及请求体、HTTP 认证、视频限制、轮询、配置迁移、引用图片和文件生命周期：

```bash
python -m unittest discover -s tests -v
```

未获得 Agnes API Key 和运行中的 AstrBot 环境，未进行真实模型生成、额度计费或真实 QQ/其他平台投递测试。部署后可用 `/image 1:1 一只橘猫` 和 `/video 16:9 4s 海边日落，镜头固定` 做端到端验收。

## 官方接口依据与来源

- [Agnes Image 2.5 Flash](https://wiki.agnes-ai.com/en/docs/agnes-image-25-flash)
- [Agnes Video 2.5](https://wiki.agnes-ai.com/en/docs/agnes-video-25)
- [Agnes Video 2.5 Flash](https://wiki.agnes-ai.com/en/docs/agnes-video-25-flash)
- [AstrBot Agent 工具开发](https://docs.astrbot.app/dev/star/guides/ai.html)
- [AstrBot 插件开发](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot 消息组件源码](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/message/components.py)

基于用户提供的 `agnes-ai1.1.zip` 修改，保留原插件标识 `agnes_ai`，作者字段使用 `Bigesila-B`。原包内部版本为 v1.0.0，本升级统一为 v2.1.0。
