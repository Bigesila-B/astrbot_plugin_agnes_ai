"""AstrBot 图片读取：消息、引用消息、OneBot get_msg 与公网文件服务。"""

import asyncio
import base64
import os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

import aiohttp
from astrbot.api import logger
from astrbot.api.message_components import Image

from .agnes_api import IMAGE_RATIOS, public_media_url

MAX_IMAGE_BYTES = 20 * 1024 * 1024


def component_kind(component):
    if isinstance(component, dict):
        return str(component.get("type", "")).lower()
    kind = getattr(component, "type", type(component).__name__)
    return str(getattr(kind, "value", kind)).rsplit(".", 1)[-1].lower()


def image_component(value):
    if isinstance(value, dict):
        data = value.get("data") or {}
        return Image(file=data.get("file") or data.get("url") or "", url=data.get("url") or "")
    return value


async def extract_images(event):
    """直接图片优先；其次 Reply.chain；OneBot 缺少 chain 时主动获取原消息。"""
    chain = event.get_messages()
    direct = [image_component(item) for item in chain if component_kind(item) == "image"]
    if direct:
        return direct
    for reply in chain:
        if component_kind(reply) != "reply":
            continue
        cached = getattr(reply, "chain", None) or []
        found = [image_component(item) for item in cached if component_kind(item) == "image"]
        if found:
            return found
        reply_id = getattr(reply, "id", None)
        if reply_id and event.get_platform_name() == "aiocqhttp":
            try:
                bot = event.bot
                action = getattr(bot, "api", bot)
                message = await action.call_action("get_msg", message_id=int(reply_id))
                if isinstance(message, dict):
                    if isinstance(message.get("data"), dict):
                        message = message["data"]
                    segments = message.get("message") or []
                    if isinstance(segments, list):
                        found = [
                            image_component(item)
                            for item in segments
                            if component_kind(item) == "image"
                        ]
                        if found:
                            return found
            except Exception as exc:
                logger.warning(f"[agnes_ai] 获取引用图片失败：{type(exc).__name__}")
                raise ValueError("无法读取引用消息，请重新发送图片后重试。") from exc
    return []


def local_path(value):
    if value.startswith("file://"):
        parsed = urlsplit(value)
        value = url2pathname(parsed.path)
        if parsed.netloc:
            value = "//" + parsed.netloc + value
    return value


def image_mime(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    raise ValueError("图片内容不是支持的 PNG、JPEG、GIF、WebP 或 BMP 文件。")


def as_data_uri(data):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片为空或超过 20 MB，请压缩后重试。")
    return f"data:{image_mime(data)};base64,{base64.b64encode(data).decode('ascii')}"


def decode_image(value):
    encoded = (
        value.partition(",")[2] if value.startswith("data:") else value.removeprefix("base64://")
    )
    if len(encoded) > (MAX_IMAGE_BYTES * 4 // 3 + 8):
        raise ValueError("图片超过 20 MB，请压缩后重试。")
    try:
        return as_data_uri(base64.b64decode(encoded, validate=True))
    except (ValueError, TypeError) as exc:
        raise ValueError("图片 Base64 数据无效或格式不受支持。") from exc


async def download_image(session, url):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
            if response.status != 200:
                raise ValueError(f"图片下载失败（HTTP {response.status}），请重新发送图片。")
            if response.content_length and response.content_length > MAX_IMAGE_BYTES:
                raise ValueError("图片超过 20 MB，请压缩后重试。")
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError("图片超过 20 MB，请压缩后重试。")
            return as_data_uri(bytes(data))
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise ValueError("图片下载失败或超时，请重新发送图片或使用可访问的图片链接。") from exc


def read_image(path):
    with open(path, "rb") as handle:
        return as_data_uri(handle.read(MAX_IMAGE_BYTES + 1))


def image_sources(component):
    return [
        str(value).strip()
        for name in ("path", "url", "file")
        if (value := getattr(component, name, None)) and isinstance(value, (str, Path))
    ]


async def image_data_uri(component, session):
    sources = image_sources(component)
    for value in sources:
        if value.startswith(("data:image/", "base64://")):
            return decode_image(value)
        if not value.startswith(("http://", "https://")):
            path = local_path(value)
            if os.path.isfile(path):
                return await asyncio.to_thread(read_image, path)
    for value in sources:
        if value.startswith(("http://", "https://")):
            return await download_image(session, value)
    converter = getattr(component, "convert_to_file_path", None)
    if converter:
        try:
            return await asyncio.to_thread(read_image, await converter())
        except Exception as exc:
            raise ValueError("AstrBot 无法读取此图片，请重新发送图片后重试。") from exc
    raise ValueError("无法读取图片，请重新发送图片后重试。")


async def video_image_url(component, source="auto"):
    """优先使用已配置的文件服务；否则保留平台公网 URL，不发送 Data URI。"""
    if source not in ("auto", "direct", "file_service"):
        raise ValueError("video_image_source 应为 auto、direct 或 file_service。")
    if source != "direct":
        register = getattr(component, "register_to_file_service", None)
        if register:
            try:
                return public_media_url(await register())
            except Exception as exc:
                logger.debug(f"[agnes_ai] 图片文件服务不可用：{type(exc).__name__}")
        if source == "file_service":
            raise ValueError(
                "视频图片文件服务不可用，请将 AstrBot 的 callback_api_base 配置为 Agnes 可访问的公网地址。"
            )
    for value in image_sources(component):
        if value.startswith(("http://", "https://")):
            try:
                return public_media_url(value)
            except ValueError:
                continue
    raise ValueError(
        "新版视频模型需要公网图片 URL。请配置 AstrBot 公网 callback_api_base，或用 --image=https://... 提供图片。"
    )


def image_dimensions(uri):
    """读取常见图片头，不引入 Pillow；无法识别时保留默认比例。"""
    try:
        data = base64.b64decode(uri.partition(",")[2])
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
        if data[:2] == b"BM" and len(data) >= 26:
            return int.from_bytes(data[18:22], "little"), abs(
                int.from_bytes(data[22:26], "little", signed=True)
            )
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
            if data[12:16] == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(
                    data[27:30], "little"
                )
            if data[12:16] == b"VP8 ":
                return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(
                    data[28:30], "little"
                ) & 0x3FFF
            if data[12:16] == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if data[:2] == b"\xff\xd8":
            pos = 2
            while pos + 4 <= len(data):
                if data[pos] != 0xFF:
                    pos += 1
                    continue
                marker = data[pos + 1]
                if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
                    pos += 2
                    continue
                length = int.from_bytes(data[pos + 2 : pos + 4], "big")
                if marker in (0xC0, 0xC1, 0xC2, 0xC3) and pos + 9 <= len(data):
                    return int.from_bytes(data[pos + 7 : pos + 9], "big"), int.from_bytes(
                        data[pos + 5 : pos + 7], "big"
                    )
                if length < 2:
                    break
                pos += length + 2
    except (ValueError, IndexError):
        pass
    return None


def infer_ratio(uri, ratios=IMAGE_RATIOS):
    dimensions = image_dimensions(uri)
    if dimensions and min(dimensions) > 0:
        ratio = dimensions[0] / dimensions[1]
        return min(
            ratios, key=lambda item: abs(int(item.split(":")[0]) / int(item.split(":")[1]) - ratio)
        )
    return None
