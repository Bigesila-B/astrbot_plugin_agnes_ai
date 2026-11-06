"""Agnes AI AstrBot 插件，由用户提供的 agnes-ai 1.1 压缩包升级。"""

import asyncio
import copy
import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Video
from astrbot.api.star import Context, Star, register

from .agnes_api import (
    DEFAULT_BASE_URL,
    DEFAULT_IMAGE_MODEL,
    IMAGE_RATIOS,
    VIDEO_RATIOS,
    AgnesClient,
    CommandArgs,
    extract_error,
    image_payload,
    parse_args,
    public_media_url,
    resolve_video_model,
    video_payload,
    video_result_url,
)
from .media_io import (
    download_image,
    extract_images,
    image_data_uri,
    infer_ratio,
    video_image_url,
)

AGENT_TOOL_NAMES = frozenset({"agnes_generate_image", "agnes_generate_video"})
AGENT_GUIDANCE = """[Agnes 媒体生成]
用户要求生成或修改图片时，调用 agnes_generate_image；要求生成视频或让图片动起来时，调用 agnes_generate_video。
结合对话上下文整理完整提示词；未指定比例、分辨率、时长时使用工具默认值，无需要求用户输入命令。
工具会自动读取当前消息及引用消息中的图片。需要较早的图片但当前没有附图/引用或有效URL时，请让用户引用或重发，不要编造图片URL。
仅讨论生成方法、分析图片、编写提示词或明确说不要生成时，不调用生成工具。
工具返回 queued 表示已接收后台任务；结果稍后自动发送到当前聊天，不能声称已完成，也不要重复调用同一生成需求。
"""
HELP = """Agnes AI 2.1
/画图 一只橘猫（附图或引用图片时自动改图）
/视频 小猫在海边奔跑（默认时长和比例）
/改图 把背景改成夜景（附图或引用图片）

可选参数：/画图 9:16 2K 一只橘猫
可选参数：/视频 9:16 8s 小猫在海边奔跑
两图首尾帧：/视频 关键帧 平滑过渡
/查视频 <video_id> [模型名]

设置开启“自然语言触发（Agent工具）”后，可直接聊天：
“帮我画一张橘猫”“把这张图做成5秒视频”。
聊天模型需要支持工具调用，且当前人格允许使用 Agnes 工具。
旧 /image、/img2img、/video 指令保留。
视频图片需公网URL，本地图片需配置公网callback_api_base。"""


@register("agnes_ai", "Bigesila-B", "Agnes 图片视频生成，支持中文指令与 Agent 自然语言调用", "2.1.0")
class AgnesAIPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._client = None
        self._temp_files = {}
        self._background_tasks = set()
        self._closing = False
        old = config.get("default_video_model")
        if old and resolve_video_model(old) != old:
            config["default_video_model"] = resolve_video_model(old)
            logger.info(f"[agnes_ai] 视频模型已迁移为 {config['default_video_model']}")
            save = getattr(config, "save_config", None)
            if save:
                try:
                    save()
                except Exception:
                    logger.warning("[agnes_ai] 模型迁移已在本次运行生效；请在 WebUI 保存插件配置。")

    def _get_client(self):
        if self._client is None:
            self._client = AgnesClient(
                self.config.get("api_key", ""),
                self.config.get("api_base_url") or DEFAULT_BASE_URL,
                self.config.get("request_timeout") or 180,
            )
        if not self._client.api_key:
            raise ValueError("未配置 Agnes API 密钥，请在插件配置中填写 api_key。")
        return self._client

    def _error(self, exc):
        if self._client:
            return self._client.safe_error(exc)
        return str(exc)[:500]

    @staticmethod
    def _command_text(event, command):
        names = {
            "draw": ("画图", "画", "生图", "绘图"),
            "img2img": ("img2img", "改图"),
            "video": ("video", "视频", "做视频"),
            "agnes_query": ("agnes_query", "查视频"),
        }.get(command, (command,))
        pattern = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
        return re.sub(
            rf"^/?(?:{pattern})(?:\s+|$)", "", event.message_str.strip(), count=1, flags=re.I
        ).strip()

    def _ratio(self, specified, *, video=False):
        allowed = VIDEO_RATIOS if video else IMAGE_RATIOS
        ratio = specified or self.config.get("default_aspect_ratio") or "16:9"
        if ratio not in allowed:
            raise ValueError(("视频" if video else "图片") + "比例支持：" + "、".join(allowed))
        return ratio

    async def _image_command(
        self, event, *, edit=False, args=None, auto_reference=False, quiet=False
    ):
        try:
            client = self._get_client()
            if args is None:
                args = parse_args(self._command_text(event, "img2img" if edit else "image"))
            if not args.prompt:
                yield event.plain_result(
                    "用法：/改图 修改要求（附图或引用图片）"
                    if edit
                    else "用法：/画图 提示词，例如：/画图 一只橘猫"
                )
                return
            components = await extract_images(event) if edit or auto_reference else []
            if edit and not components and not args.images:
                raise ValueError(
                    "请附图或引用含图片的消息，或用 --image=https://... 提供参考图片。"
                )
            images = []
            if components or args.images:
                session = await client.session()
                for component in components:
                    images.append(await image_data_uri(component, session))
                for url in args.images:
                    images.append(await download_image(session, url))
            ratio = self._ratio(args.ratio)
            if not args.ratio and images:
                ratio = infer_ratio(images[0]) or ratio
            model = args.model or self.config.get("default_image_model") or DEFAULT_IMAGE_MODEL
            size = args.size or str(self.config.get("default_image_size") or "1K").upper()
            payload = image_payload(
                args.prompt,
                model,
                size,
                ratio,
                images,
                self.config.get("image_response_format") or "url",
            )
            if not quiet:
                message = "正在改图，请稍等…" if images else "正在画图，请稍等…"
                if self.config.get("show_generation_details", False):
                    message += f"\n模型：{model}\n目标比例：{ratio}，输出规格：{payload['size']}\n参考图片：{len(images)} 张"
                yield event.plain_result(message)
            result = await client.create_image(payload)
            data = result.get("data")
            if not isinstance(data, list) or not data:
                raise ValueError("接口未返回图片结果，请检查 API 地址和模型权限。")
            chain = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                if item.get("url"):
                    chain.append(Image.fromURL(item["url"]))
                elif item.get("b64_json"):
                    encoded = item["b64_json"]
                    if encoded.startswith("data:"):
                        encoded = encoded.partition(",")[2]
                    chain.append(Image.fromBase64(encoded))
            if not chain:
                raise ValueError("图片结果缺少 url 和 b64_json。")
            yield event.chain_result(chain)
        except Exception as exc:
            yield event.plain_result(f"图片生成失败：{self._error(exc)}")

    @filter.command("image")
    async def text_to_image(self, event: AstrMessageEvent):
        """文生图：/image [比例] [1K/2K/3K/4K] 提示词。"""
        async for result in self._image_command(event):
            yield result

    @filter.command("画图", alias={"画", "生图", "绘图"})
    async def draw(self, event: AstrMessageEvent):
        """画图或自动改图：/画图 提示词，可附图或引用图片。"""
        try:
            args = parse_args(self._command_text(event, "draw"))
        except ValueError as exc:
            yield event.plain_result(f"图片生成失败：{self._error(exc)}")
            return
        async for result in self._image_command(event, args=args, auto_reference=True):
            yield result

    @filter.command("img2img", alias={"改图"})
    async def image_to_image(self, event: AstrMessageEvent):
        """图生图：附图或引用图片，/img2img [比例] [分辨率] 提示词。"""
        async for result in self._image_command(event, edit=True):
            yield result

    @filter.command("video", alias={"视频", "做视频"})
    async def generate_video(self, event: AstrMessageEvent):
        """视频：/video [比例] [4–12秒] [分辨率] 提示词，支持图片引用。"""
        async for result in self._video_command(event):
            yield result

    async def _video_command(self, event, *, args=None, quiet=False):
        video_id = None
        model = None
        try:
            client = self._get_client()
            if args is None:
                args = parse_args(self._command_text(event, "video"), video=True)
            if not args.prompt:
                yield event.plain_result(
                    "用法：/视频 提示词，例如：/视频 小猫在海边奔跑\n可附图或引用图片。更多用法：/agnes"
                )
                return
            components = [] if args.mode == "text" else await extract_images(event)
            ratio = self._ratio(args.ratio, video=True)
            if not args.ratio and components:
                # 读取原图推断比例，不读取文件服务的临时 URL。
                try:
                    uri = await image_data_uri(components[0], await client.session())
                    ratio = infer_ratio(uri, VIDEO_RATIOS) or ratio
                except Exception:
                    logger.debug("[agnes_ai] 未能识别参考图比例，使用默认视频比例。")
            source = self.config.get("video_image_source") or "auto"
            images = [await video_image_url(component, source) for component in components]
            images.extend(args.images)
            model = resolve_video_model(args.model or self.config.get("default_video_model"))
            size = args.size or str(self.config.get("default_video_size") or "720P").upper()
            seconds = (
                args.duration
                if args.duration is not None
                else self.config.get("default_video_duration", 5)
            )
            mode = args.mode or ("keyframe" if args.keyframes and images else None)
            payload = video_payload(args.prompt, model, size, ratio, seconds, images, mode)
            descriptions = {
                "text": "文生视频",
                "keyframe": "首/尾帧视频",
                "reference": "参考图视频",
            }
            if not quiet:
                message = "正在生成视频，请稍等…"
                if self.config.get("show_generation_details", False):
                    message += f"\n模型：{model}\n模式：{descriptions[payload['mode']]}\n比例：{ratio}，分辨率：{size}，时长：{payload['seconds']}秒"
                yield event.plain_result(message)
            task = await client.create_video(payload)
            # 新接口的 id/task_id 与 video_id 不是同一个标识，禁止混用。
            video_id = task.get("video_id")
            if not video_id and str(task.get("id", "")).startswith("video_"):
                video_id = task["id"]
            if not isinstance(video_id, str) or not video_id:
                raise ValueError(
                    "创建响应缺少 video_id，无法查询。请在 Agnes 平台确认任务，避免重复提交。"
                )
            if not quiet:
                yield event.plain_result(
                    f"视频任务已提交：{video_id}\n完成后自动发送。查询：/查视频 {video_id} {model}"
                )
            result = await client.wait_video(
                video_id,
                model,
                interval=self.config.get("video_poll_interval") or 2,
                timeout=self.config.get("video_poll_timeout") or 600,
            )
            async for message in self._deliver_video(event, video_result_url(result)):
                yield message
        except Exception as exc:
            message = f"视频生成失败：{self._error(exc)}"
            if video_id:
                message += f"\n任务ID：{video_id}\n可查询已有任务：/agnes_query {video_id} {model}"
            yield event.plain_result(message)

    @filter.command("agnes_query", alias={"查视频"})
    async def query_video(self, event: AstrMessageEvent):
        """查询已有任务：/agnes_query <video_id> [模型名]，不创建新任务。"""
        try:
            client = self._get_client()
            parts = self._command_text(event, "agnes_query").split()
            if not 1 <= len(parts) <= 2:
                yield event.plain_result(
                    "用法：/agnes_query <video_id> [模型名]\n请使用提交任务时返回的 video_id 和模型名。"
                )
                return
            video_id = parts[0]
            model = resolve_video_model(
                parts[1] if len(parts) == 2 else self.config.get("default_video_model")
            )
            result = await client.query_video(video_id, model)
            status = str(result.get("status", "unknown")).lower()
            if status == "completed":
                async for message in self._deliver_video(event, video_result_url(result)):
                    yield message
            elif status == "failed":
                yield event.plain_result(
                    f"视频任务失败：{client.safe_error(extract_error(json.dumps(result)))}"
                )
            else:
                yield event.plain_result(
                    f"任务ID：{video_id}\n状态：{status}\n进度：{result.get('progress', 0)}%\n模型：{model}"
                )
        except Exception as exc:
            yield event.plain_result(f"视频查询失败：{self._error(exc)}")

    @filter.command("agnes_help", alias={"agnes", "画图帮助"})
    async def agnes_help(self, event: AstrMessageEvent):
        """查看 Agnes 插件用法。"""
        yield event.plain_result(HELP)

    def _natural_enabled(self):
        return bool(self.config.get("enable_natural_language", True))

    @filter.on_llm_request()
    async def configure_agent_tools(self, event: AstrMessageEvent, req):
        """按本插件开关配置当前请求，不修改共享工具列表或其他插件。"""
        tools = getattr(req, "func_tool", None)
        if tools is None:
            return
        if not self._natural_enabled():
            # ToolSet.remove_tool 会替换列表；复制容器后操作以免影响其他会话。
            local = copy.copy(tools)
            remove = getattr(local, "remove_tool", None) or getattr(local, "remove_func", None)
            if remove:
                for name in AGENT_TOOL_NAMES:
                    remove(name)
                req.func_tool = local
            return
        available = getattr(tools, "tools", None)
        if available is None:
            available = getattr(tools, "func_list", [])
        if any(
            tool.name in AGENT_TOOL_NAMES and getattr(tool, "active", True) for tool in available
        ):
            prompt = getattr(req, "system_prompt", "") or ""
            if "[Agnes 媒体生成]" not in prompt:
                req.system_prompt = prompt + "\n\n" + AGENT_GUIDANCE

    @filter.llm_tool(name="agnes_generate_image")
    async def agnes_generate_image(
        self,
        event: AstrMessageEvent,
        prompt: str,
        aspect_ratio: str = "",
        size: str = "",
        use_reference: bool = True,
        image_urls: list[str] | None = None,
    ) -> str:
        """用户要求生成图片、画画、设计海报或修改图片时调用 Agnes。自动读取当前附图或引用图；没有图则文生图。不要用于图片分析、只写提示词或用户明确说不要生成的情况。立即返回排队结果，图片稍后自动发送，不能宣称已完成。

        Args:
            prompt(string): 结合用户需求及对话上下文整理的完整图片描述或修改要求，不要包含指令或参数开关。
            aspect_ratio(string): 可选比例，如16:9、9:16、1:1；留空使用配置或输入图片比例。
            size(string): 可选1K、2K、3K、4K；留空使用配置。
            use_reference(boolean): 是否自动使用当前附图或引用图，默认true；明确要求全新画面且不使用附图时设false。
            image_urls(array[string]): 可选的明确已知的参考图片HTTP(S)链接；没有则留空数组，禁止编造链接。
        """
        try:
            args = self._tool_args(prompt, aspect_ratio, size, image_urls)
            return self._queue_generation(event, "image", args, use_reference=use_reference)
        except Exception as exc:
            return "未提交图片任务：" + self._error(exc)

    @filter.llm_tool(name="agnes_generate_video")
    async def agnes_generate_video(
        self,
        event: AstrMessageEvent,
        prompt: str,
        duration: float = 0,
        aspect_ratio: str = "",
        size: str = "",
        mode: str = "auto",
        image_urls: list[str] | None = None,
    ) -> str:
        """用户要求制作视频、生成短片或让图片动起来时调用 Agnes。自动读取当前附图或引用图，也支持无图文生视频。只讨论方法或只写脚本时不要调用。立即返回排队结果，视频稍后自动发送，不能宣称已完成。

        Args:
            prompt(string): 结合对话上下文整理的视频内容、动作和镜头描述，不包含聊天指令或参数开关。
            duration(number): 可选4到12秒整数；0表示用户未指定，使用插件默认值。
            aspect_ratio(string): 可选16:9、9:16、1:1、4:3、3:4、21:9；留空自动选择。
            size(string): 留空使用配置；Flash仅支持720P，不要为了高分辨率自动更换付费模型。
            mode(string): auto自动选择；keyframe首尾帧；reference参考图；last_frame仅尾帧；text不用图片。
            image_urls(array[string]): 可选的明确已知的公网参考图片链接；没有则留空数组，禁止编造链接。
        """
        try:
            args = self._tool_args(prompt, aspect_ratio, size, image_urls)
            args.duration = None if duration is None or duration == 0 else float(duration)
            selected = str(mode or "auto").strip().lower()
            if selected not in ("auto", "text", "keyframe", "reference", "last_frame"):
                raise ValueError("视频模式应为auto、text、keyframe、reference或last_frame。")
            args.mode = None if selected == "auto" else selected
            return self._queue_generation(event, "video", args)
        except Exception as exc:
            return "未提交视频任务：" + self._error(exc)

    @staticmethod
    def _tool_args(prompt, ratio, size, urls):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("需要完整的生成提示词。")
        if urls is not None and not isinstance(urls, list):
            raise ValueError("image_urls需要是图片链接数组。")
        return CommandArgs(
            prompt=prompt.strip(),
            ratio=str(ratio or "").strip().replace("：", ":") or None,
            size=str(size or "").strip().upper() or None,
            images=[public_media_url(url) for url in (urls or [])],
        )

    def _queue_generation(self, event, kind, args, *, use_reference=True):
        if not self._natural_enabled():
            return "自然语言触发已关闭，未创建任务。仍可使用 /画图 或 /视频 指令。"
        if self._closing:
            return "插件正在重载或关闭，未创建任务，请稍后重试。"
        self._get_client()
        # 在返回“已排队”之前先校验明显的参数错误，不允许Agent偷偷切换模型。
        if kind == "image":
            image_payload(
                args.prompt,
                self.config.get("default_image_model") or DEFAULT_IMAGE_MODEL,
                args.size or str(self.config.get("default_image_size") or "1K").upper(),
                self._ratio(args.ratio),
                response_format=self.config.get("image_response_format") or "url",
            )
        else:
            media = args.images
            if not media and args.mode in ("keyframe", "reference", "last_frame"):
                # 附图将在后台读取，此处只验证公共参数和模式名称。
                media = ["https://example.com/attached-image.png"]
            video_payload(
                args.prompt,
                resolve_video_model(self.config.get("default_video_model")),
                args.size or str(self.config.get("default_video_size") or "720P").upper(),
                self._ratio(args.ratio, video=True),
                args.duration
                if args.duration is not None
                else self.config.get("default_video_duration", 5),
                media,
                args.mode,
            )
        requests = event.get_extra("agnes_ai_generation_requests")
        if requests is None:
            requests = {}
            event.set_extra("agnes_ai_generation_requests", requests)
        fingerprint = hashlib.sha256(
            json.dumps(
                [kind, asdict(args), use_reference], ensure_ascii=False, sort_keys=True
            ).encode()
        ).hexdigest()
        existing = requests.get(fingerprint)
        if existing:
            return json.dumps(
                {
                    "status": "duplicate",
                    "job_id": existing["job_id"],
                    "message": "同一消息的相同生成需求已处理，请勿重复提交。",
                },
                ensure_ascii=False,
            )
        record = {"job_id": "agnes_" + uuid.uuid4().hex[:12], "status": "queued"}
        requests[fingerprint] = record
        task = asyncio.create_task(self._run_agent_job(event, kind, args, use_reference, record))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return json.dumps(
            {
                "status": "queued",
                "job_id": record["job_id"],
                "message": "已接收生成需求，完成后自动发送到当前聊天。当前尚未生成完成，请勿重复提交。",
            },
            ensure_ascii=False,
        )

    async def _run_agent_job(self, event, kind, args, use_reference, record):
        record["status"] = "running"
        try:
            if kind == "image":
                results = self._image_command(
                    event, args=args, auto_reference=use_reference, quiet=True
                )
            else:
                results = self._video_command(event, args=args, quiet=True)
            async for result in results:
                await event.send(result)
            record["status"] = "finished"
        except asyncio.CancelledError:
            record["status"] = "cancelled"
            raise
        except Exception as exc:
            record["status"] = "failed"
            logger.warning(f"[agnes_ai] 后台生成或发送失败：{type(exc).__name__}")

    async def _download_video(self, url):
        session = await self._get_client().session()
        fd, path = tempfile.mkstemp(prefix="astrbot_agnes_", suffix=".mp4")
        os.close(fd)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as response:
                if response.status != 200:
                    raise ValueError(f"视频下载失败（HTTP {response.status}）。")
                max_bytes = 300 * 1024 * 1024
                if response.content_length and response.content_length > max_bytes:
                    raise ValueError("视频超过 300 MB，改用 URL 发送。")
                written = 0
                with open(path, "wb") as handle:
                    async for chunk in response.content.iter_chunked(65536):
                        written += len(chunk)
                        if written > max_bytes:
                            raise ValueError("视频超过 300 MB，改用 URL 发送。")
                        handle.write(chunk)
                if not written:
                    raise ValueError("视频下载结果为空。")
            return path
        except BaseException:
            self._remove_temp(path)
            raise

    def _remove_temp(self, path):
        self._temp_files.pop(path, None)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("[agnes_ai] 临时视频清理失败，请检查系统临时目录权限。")

    async def _deliver_video(self, event, url):
        if not self.config.get("download_video", True):
            yield event.chain_result([Video.fromURL(url)])
            return
        try:
            path = await self._download_video(url)
        except Exception:
            logger.warning("[agnes_ai] 视频下载失败，回退到 URL 发送。")
            yield event.chain_result([Video.fromURL(url)])
            return
        # 协议端可能在消息处理完成后才取文件，不能在 yield 后立即删除。
        self._temp_files[path] = asyncio.get_running_loop().call_later(
            3600, self._remove_temp, path
        )
        yield event.chain_result([Video.fromFileSystem(path)])

    async def terminate(self):
        self._closing = True
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        if self._client:
            await self._client.close()
        for path, timer in list(self._temp_files.items()):
            timer.cancel()
            self._remove_temp(path)
