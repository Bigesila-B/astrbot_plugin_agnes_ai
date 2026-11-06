"""Agnes Image / Video 2.5 的请求契约，不依赖 AstrBot。"""

import asyncio
import ipaddress
import json
import math
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import aiohttp

DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"
DEFAULT_IMAGE_MODEL = "agnes-image-2.5-flash"
DEFAULT_VIDEO_MODEL = "agnes-video-2.5-flash"
IMAGE_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "2:3", "3:2", "21:9")
VIDEO_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")
IMAGE_SIZES = ("1K", "2K", "3K", "4K")
VIDEO_SIZES = ("720P", "1080P", "1K", "2K")
RETIRED_VIDEO_MODELS = {
    "agnes-video-v2.0",
    "agnes-video-2.0",
    "agnes-video-v2.5",
    "agnes-video-v2.5-flash",
}


def resolve_video_model(model):
    model = str(model or DEFAULT_VIDEO_MODEL).strip()
    if model in {"agnes-video-v2.5", "agnes-video-v2.5-flash"}:
        return model.replace("-v2.5", "-2.5")
    return DEFAULT_VIDEO_MODEL if model in RETIRED_VIDEO_MODELS else model


def public_media_url(value):
    """视频接口仅接受公网 HTTP(S) 地址；拒绝本地路径和 Data URI。"""
    if not isinstance(value, str):
        raise ValueError("参考图片必须是公网 HTTP(S) URL。")
    parsed = urlsplit(value.strip())
    host = parsed.hostname or ""
    if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
        raise ValueError("视频参考图片需要公网 HTTP(S) URL，不能使用 Base64 或本地路径。")
    if host.lower() == "localhost" or host.lower().endswith((".localhost", ".local")):
        raise ValueError("视频参考图片不能使用本机或局域网地址。")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("视频参考图片不能使用本机或局域网地址。")
    return value.strip()


@dataclass
class CommandArgs:
    prompt: str = ""
    ratio: str | None = None
    duration: float | None = None
    size: str | None = None
    model: str | None = None
    mode: str | None = None
    keyframes: bool = False
    images: list[str] = field(default_factory=list)


def parse_args(text, *, video=False):
    """参数以空白分隔；等号参数可放在提示词的前后。"""
    args = CommandArgs()
    words = []
    for token in text.split():
        if re.fullmatch(r"\d+[:：]\d+", token):
            args.ratio = token.replace("：", ":")
        elif video and re.fullmatch(r"-?\d+(?:\.\d+)?(?:s|秒)", token, re.I):
            args.duration = float(re.sub(r"(?:s|秒)$", "", token, flags=re.I))
        elif token.upper() in (VIDEO_SIZES if video else IMAGE_SIZES):
            args.size = token.upper()
        elif token.startswith("--model="):
            args.model = token.partition("=")[2].strip()
            if not args.model:
                raise ValueError("--model= 后需要填写模型名称。")
        elif token.startswith("--image="):
            args.images.append(public_media_url(token.partition("=")[2]))
        elif video and token.startswith("--mode="):
            args.mode = token.partition("=")[2].lower()
            if args.mode not in ("text", "keyframe", "reference", "last_frame"):
                raise ValueError("--mode= 支持 text、keyframe、reference、last_frame。")
        elif video and token.lower() in ("keyframes", "keyframe", "关键帧"):
            args.keyframes = True
            words.append(token)
        else:
            words.append(token)
    args.prompt = " ".join(words).strip()
    # 兼容旧指令中把“关键帧”直接写进中文提示词的用法。
    if video and ("关键帧" in args.prompt or "keyframes" in args.prompt.lower()):
        args.keyframes = True
    return args


def image_payload(prompt, model, size, ratio, images=None, response_format="url"):
    if not prompt.strip():
        raise ValueError("请输入图片提示词。")
    if ratio not in IMAGE_RATIOS:
        raise ValueError("图片比例支持：" + "、".join(IMAGE_RATIOS))
    if size not in IMAGE_SIZES:
        raise ValueError("图片分辨率支持 1K、2K、3K、4K。")
    if response_format not in ("url", "b64_json"):
        raise ValueError("图片返回格式支持 url 或 b64_json。")
    payload = {"model": model, "prompt": prompt, "size": size, "ratio": ratio}
    if model == "agnes-image-2.0-flash":
        # 2.0 文档仍使用旧像素尺寸，不能直接套用 2.1/2.5 的档位请求。
        if size != "1K":
            raise ValueError("旧图片模型 2.0 的兼容模式仅支持 1K；2K–4K 请使用 2.1 或 2.5。")
        width, height = map(int, ratio.split(":"))
        legacy_sizes = ((1024, 1024), (1024, 768), (768, 1024))
        actual = min(legacy_sizes, key=lambda item: abs(item[0] / item[1] - width / height))
        payload["size"] = f"{actual[0]}x{actual[1]}"
        payload.pop("ratio")
    if images:
        payload["extra_body"] = {"image": list(images), "response_format": response_format}
    elif response_format == "b64_json":
        payload["return_base64"] = True
    else:
        payload["extra_body"] = {"response_format": "url"}
    return payload


def video_payload(prompt, model, size, ratio, seconds, images=None, mode=None):
    model = resolve_video_model(model)
    if not prompt.strip():
        raise ValueError("请输入视频提示词。")
    if ratio not in VIDEO_RATIOS:
        raise ValueError("视频比例支持：" + "、".join(VIDEO_RATIOS))
    seconds = float(seconds)
    if not math.isfinite(seconds) or not seconds.is_integer() or not 4 <= seconds <= 12:
        raise ValueError("新版视频时长支持 4–12 秒整数，例如 5s、8秒；不会自动截断或更改时长。")
    if size not in VIDEO_SIZES:
        raise ValueError("视频分辨率支持 720P、1080P、1K、2K。")
    if model == DEFAULT_VIDEO_MODEL and size != "720P":
        raise ValueError(
            "Agnes Video 2.5 Flash 只支持 720P。更高分辨率请显式选择 --model=agnes-video-2.5。"
        )
    images = [public_media_url(url) for url in (images or [])]
    mode = mode or ("keyframe" if len(images) == 1 else "reference" if images else "text")
    payload = {
        "model": model,
        "prompt": prompt,
        "seconds": str(int(seconds)),
        "size": size,
        "aspect_ratio": ratio,
        "mode": "keyframe" if mode == "last_frame" else mode,
        "n": 1,
    }
    if mode == "text":
        if images:
            raise ValueError("text 模式不接受参考图片，请移除图片或改用 keyframe/reference。")
    elif mode in ("keyframe", "last_frame"):
        if not images or len(images) > (1 if mode == "last_frame" else 2):
            raise ValueError("关键帧模式需要 1–2 张图片；last_frame 模式需要恰好 1 张。")
        payload["last_frame" if mode == "last_frame" else "first_frame"] = images[0]
        if len(images) == 2:
            payload["last_frame"] = images[1]
    elif mode == "reference":
        limit = 8 if model == "agnes-video-2.5" else 5
        if not images or len(images) > limit:
            raise ValueError(f"当前模型的参考图模式需要 1–{limit} 张图片。")
        payload["images"] = images
    else:
        raise ValueError("不支持的视频模式。")
    return payload


def extract_error(text):
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            error = data.get("error") or data.get("detail") or data.get("message")
            if isinstance(error, dict):
                error = error.get("message") or error.get("type") or error
            if error:
                return str(error)[:500]
    except (ValueError, TypeError):
        pass
    return str(text)[:500]


class AgnesAPIError(ValueError):
    def __init__(self, message, status=0, retry_after=0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self):
        return self.status in (408, 429) or self.status >= 500


class AgnesClient:
    def __init__(self, api_key, base_url=DEFAULT_BASE_URL, request_timeout=180):
        self.api_key = str(api_key or "").strip()
        base = str(base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        parsed = urlsplit(base)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError(
                "api_base_url 应为 HTTP(S) 基础地址，例如 https://apihub.agnes-ai.com/v1。"
            )
        self.base_url = base if parsed.path.endswith("/v1") else base + "/v1"
        self.root_url = self.base_url[:-3]
        self.request_timeout = max(10, min(600, float(request_timeout)))
        self._session = None

    async def session(self):
        if self._session is None or self._session.closed:
            # 下载媒体时不携带 API 密钥，认证仅用于下面的 API 请求。
            self._session = aiohttp.ClientSession()
        return self._session

    def safe_error(self, error):
        message = str(error).replace(self.api_key, "[密钥已隐藏]") if self.api_key else str(error)
        message = re.sub(r"data:[^\s,]*;base64,[A-Za-z0-9+/=]+", "[图片数据已隐藏]", message)
        return message[:500] or "未知错误"

    async def request(self, method, url, **kwargs):
        if not self.api_key:
            raise ValueError("请先在插件配置中填写 Agnes API 密钥 api_key。")
        session = await self.session()
        try:
            async with session.request(
                method,
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=aiohttp.ClientTimeout(total=self.request_timeout),
                allow_redirects=False,
                **kwargs,
            ) as response:
                body = await response.text()
                if not 200 <= response.status < 300:
                    try:
                        retry_after = min(60, max(0, float(response.headers.get("Retry-After", 0))))
                    except ValueError:
                        retry_after = 0
                    detail = self.safe_error(extract_error(body))
                    raise AgnesAPIError(
                        f"Agnes API HTTP {response.status}：{detail}", response.status, retry_after
                    )
                try:
                    data = json.loads(body)
                except ValueError as exc:
                    raise ValueError(
                        "Agnes API 返回了非 JSON 内容，请检查基础地址和代理配置。"
                    ) from exc
                if not isinstance(data, dict):
                    raise ValueError("Agnes API 响应格式错误：预期 JSON 对象。")
                if data.get("error") and str(data.get("status", "")).lower() != "failed":
                    raise AgnesAPIError(self.safe_error(extract_error(body)))
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise AgnesAPIError("Agnes API 网络异常或请求超时。", 503) from exc

    async def create_image(self, payload):
        # 生成请求不自动重试，避免重复任务和重复扣费。
        return await self.request("POST", self.base_url + "/images/generations", json=payload)

    async def create_video(self, payload):
        return await self.request("POST", self.base_url + "/videos", json=payload)

    async def query_video(self, video_id, model):
        return await self.request(
            "GET",
            self.root_url + "/agnesapi",
            params={"video_id": video_id, "model_name": resolve_video_model(model)},
        )

    async def wait_video(self, video_id, model, interval=2, timeout=600):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.01, float(timeout))
        delay = max(1, float(interval))
        failures = 0
        last_error = None
        while loop.time() < deadline:
            try:
                result = await asyncio.wait_for(
                    self.query_video(video_id, model), deadline - loop.time()
                )
                failures = 0
                last_error = None
                status = str(result.get("status", "")).lower()
                if status == "failed":
                    raise AgnesAPIError(self.safe_error(extract_error(json.dumps(result))))
                if status in ("cancelled", "canceled", "expired"):
                    raise AgnesAPIError("视频任务已取消或已过期。")
                if status == "completed":
                    video_result_url(result)  # 完成状态缺少 URL 时立即报告，不无限轮询。
                    return result
                wait = delay
            except AgnesAPIError as exc:
                if not exc.retryable:
                    raise
                failures += 1
                last_error = self.safe_error(exc)
                wait = max(min(30, delay * (2 ** min(failures, 4))), exc.retry_after)
            except asyncio.TimeoutError:
                break
            remaining = deadline - loop.time()
            if remaining > 0:
                await asyncio.sleep(min(wait, remaining))
        suffix = f" 最近一次查询：{last_error}" if last_error else ""
        raise TimeoutError("等待视频结果超时，任务可能仍在运行。" + suffix)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


def video_result_url(result):
    url = result.get("url") or result.get("video_url")
    if (
        not isinstance(url, str)
        or urlsplit(url).scheme not in ("http", "https")
        or not urlsplit(url).hostname
    ):
        raise ValueError("视频任务已完成，但没有返回有效的 url 地址。")
    # remixed_from_video_id 是来源视频 ID，不能当作下载地址。
    return url
