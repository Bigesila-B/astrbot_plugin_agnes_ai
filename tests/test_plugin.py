"""离线回归测试：不调用真实 Agnes，不依赖运行中的 AstrBot。"""

import asyncio
import base64
import importlib
import json
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web

PLUGIN = Path(__file__).resolve().parents[1]
PACKAGE = "agnes_test_package"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(PLUGIN)]
sys.modules[PACKAGE] = package


class Image:
    type = "Image"

    def __init__(self, file="", url="", path=""):
        self.file, self.url, self.path = file, url, path

    @classmethod
    def fromURL(cls, url):
        return cls(file=url, url=url)

    @classmethod
    def fromFileSystem(cls, path):
        return cls(file=Path(path).as_uri(), path=path)

    @classmethod
    def fromBase64(cls, data):
        return cls(file="base64://" + data)


class Video(Image):
    type = "Video"


class Star:
    def __init__(self, context):
        self.context = context


# 仅为插件模块导入建立 AstrBot API 替身，验证聊天逻辑与真实 HTTP 契约。
for module_name in (
    "astrbot",
    "astrbot.api",
    "astrbot.api.event",
    "astrbot.api.star",
    "astrbot.api.message_components",
):
    sys.modules[module_name] = types.ModuleType(module_name)
sys.modules["astrbot.api"].AstrBotConfig = dict
sys.modules["astrbot.api"].logger = logging.getLogger("agnes_tests")
sys.modules["astrbot.api.event"].AstrMessageEvent = object
sys.modules["astrbot.api.event"].filter = types.SimpleNamespace(
    command=lambda *a, **k: lambda f: f,
    llm_tool=lambda *a, **k: lambda f: f,
    on_llm_request=lambda *a, **k: lambda f: f,
)
sys.modules["astrbot.api.star"].Context = object
sys.modules["astrbot.api.star"].Star = Star
sys.modules["astrbot.api.star"].register = lambda *a, **k: lambda cls: cls
sys.modules["astrbot.api.message_components"].Image = Image
sys.modules["astrbot.api.message_components"].Video = Video
api = importlib.import_module(PACKAGE + ".agnes_api")
media = importlib.import_module(PACKAGE + ".media_io")
main = importlib.import_module(PACKAGE + ".main")

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/R6sAAAAASUVORK5CYII="
)
URLS = [f"https://example.com/{i}.png" for i in range(9)]


class Event:
    def __init__(self, text, messages=None, platform="test"):
        self.message_str = text
        self.messages = messages or []
        self.platform = platform
        self.extra = {}
        self.sent = []

    def get_extra(self, name, default=None):
        return self.extra.get(name, default)

    def set_extra(self, name, value):
        self.extra[name] = value

    async def send(self, result):
        self.sent.append(result)

    def get_messages(self):
        return self.messages

    def get_platform_name(self):
        return self.platform

    def plain_result(self, text):
        return {"text": text}

    def chain_result(self, chain):
        return {"chain": chain}


async def collect(generator):
    return [item async for item in generator]


def config(**overrides):
    schema = json.loads((PLUGIN / "_conf_schema.json").read_text(encoding="utf-8-sig"))
    result = {key: value.get("default") for key, value in schema.items()}
    result.update(api_key="test-key", download_video=False)
    result.update(overrides)
    return result


class ContractTests(unittest.TestCase):
    def test_legacy_image_model_keeps_its_documented_pixel_sizes(self):
        body = api.image_payload("draw", "agnes-image-2.0-flash", "1K", "16:9")
        self.assertEqual(body["size"], "1024x768")
        self.assertNotIn("ratio", body)
        with self.assertRaises(ValueError):
            api.image_payload("draw", "agnes-image-2.0-flash", "4K", "16:9")

    def test_image_new_size_ratio_and_reference_contract(self):
        body = api.image_payload(
            "draw", api.DEFAULT_IMAGE_MODEL, "2K", "16:9", ["data:image/png;base64,AAA"]
        )
        self.assertEqual(body["ratio"], "16:9")
        self.assertEqual(body["size"], "2K")
        self.assertEqual(body["extra_body"]["image"], ["data:image/png;base64,AAA"])
        self.assertNotIn("response_format", body)

    def test_base64_request_differs_for_text_and_edit(self):
        text = api.image_payload(
            "draw", api.DEFAULT_IMAGE_MODEL, "1K", "1:1", response_format="b64_json"
        )
        edit = api.image_payload(
            "draw", api.DEFAULT_IMAGE_MODEL, "1K", "1:1", ["image"], "b64_json"
        )
        self.assertTrue(text["return_base64"])
        self.assertNotIn("return_base64", edit)
        self.assertEqual(edit["extra_body"]["response_format"], "b64_json")

    def test_video_modes_and_no_retired_fields(self):
        cases = [
            (None, None, "text"),
            (URLS[:1], None, "keyframe"),
            (URLS[:2], None, "reference"),
            (URLS[:2], "keyframe", "keyframe"),
            (URLS[:1], "last_frame", "keyframe"),
        ]
        for images, requested, expected in cases:
            with self.subTest(mode=requested, count=len(images or [])):
                body = api.video_payload(
                    "move", api.DEFAULT_VIDEO_MODEL, "720P", "16:9", 5, images, requested
                )
                self.assertEqual(body["mode"], expected)
                self.assertEqual(body["seconds"], "5")
                self.assertFalse(
                    {"width", "height", "num_frames", "frame_rate", "extra_body", "image"}
                    & body.keys()
                )
                if requested == "last_frame":
                    self.assertEqual(body["last_frame"], URLS[0])
                    self.assertNotIn("first_frame", body)
                if requested == "keyframe":
                    self.assertEqual(body["last_frame"], URLS[1])

    def test_flash_limits_and_ordinary_model(self):
        for size, images, mode in [
            ("1080P", [], None),
            ("720P", URLS[:6], "reference"),
            ("720P", URLS[:3], "keyframe"),
        ]:
            with self.assertRaises(ValueError):
                api.video_payload("move", api.DEFAULT_VIDEO_MODEL, size, "16:9", 5, images, mode)
        body = api.video_payload(
            "move", "agnes-video-2.5", "1080P", "21:9", 12, URLS[:8], "reference"
        )
        self.assertEqual(len(body["images"]), 8)
        self.assertEqual(body["model"], "agnes-video-2.5")
        with self.assertRaises(ValueError):
            api.video_payload("move", "agnes-video-2.5", "2K", "16:9", 12, URLS, "reference")

    def test_invalid_duration_ratio_or_mode_is_rejected(self):
        for seconds in (0, 3, 13, 4.5, float("inf"), float("nan")):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                api.video_payload("move", api.DEFAULT_VIDEO_MODEL, "720P", "16:9", seconds)
        for ratio in ("2:3", "3:2", "7:0"):
            with self.assertRaises(ValueError):
                api.video_payload("move", api.DEFAULT_VIDEO_MODEL, "720P", ratio, 5)
        with self.assertRaises(ValueError):
            api.video_payload("move", api.DEFAULT_VIDEO_MODEL, "720P", "16:9", 5, URLS[:1], "text")
        with self.assertRaises(ValueError):
            api.video_payload("move", api.DEFAULT_VIDEO_MODEL, "720P", "16:9", 5, [], "reference")

    def test_image_prompt_duration_words_are_preserved(self):
        args = api.parse_args("16:9 2k 画面写着 5s 和 10秒")
        self.assertEqual(args.prompt, "画面写着 5s 和 10秒")
        self.assertEqual(args.size, "2K")
        args = api.parse_args("8秒 9：16 关键帧 平滑过渡", video=True)
        self.assertEqual((args.duration, args.ratio, args.prompt), (8, "9:16", "关键帧 平滑过渡"))
        self.assertTrue(args.keyframes)

    def test_public_video_urls(self):
        for value in (
            "data:image/png;base64,AAA",
            "file:///tmp/a.png",
            "http://127.0.0.1/a",
            "http://192.168.1.2/a",
            "http://[::1]/a",
            "http://localhost/a",
        ):
            with self.subTest(url=value), self.assertRaises(ValueError):
                api.public_media_url(value)
        self.assertEqual(api.public_media_url(URLS[0]), URLS[0])

    def test_completed_uses_url_and_ignores_internal_status(self):
        self.assertEqual(
            api.video_result_url({"url": URLS[0], "internal_status": "pending"}), URLS[0]
        )
        with self.assertRaises(ValueError):
            api.video_result_url({"remixed_from_video_id": "video_source"})

    def test_base_url_proxy_prefix_and_migration(self):
        client = api.AgnesClient("key", "https://proxy.example/prefix/")
        self.assertEqual(client.base_url, "https://proxy.example/prefix/v1")
        self.assertEqual(client.root_url, "https://proxy.example/prefix")
        self.assertEqual(api.resolve_video_model("agnes-video-v2.0"), api.DEFAULT_VIDEO_MODEL)
        self.assertEqual(api.resolve_video_model("agnes-video-v2.5"), "agnes-video-2.5")
        self.assertEqual(api.resolve_video_model("agnes-video-v2.5-flash"), api.DEFAULT_VIDEO_MODEL)

    def test_error_redacts_key_and_image_data(self):
        client = api.AgnesClient("secret-test-key")
        message = client.safe_error("secret-test-key data:image/png;base64,AAAA")
        self.assertNotIn("secret-test-key", message)
        self.assertNotIn("AAAA", message)


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.query_responses = []
        self.create_status = 201
        self.create_body = {
            "video_id": "video_expected",
            "id": "task_different",
            "task_id": "task_different",
        }

        async def create(request):
            self.requests.append(
                (request.method, request.path, dict(request.headers), await request.json())
            )
            return web.json_response(self.create_body, status=self.create_status)

        async def query(request):
            self.requests.append(
                (request.method, request.path, dict(request.headers), dict(request.query))
            )
            if self.query_responses:
                return self.query_responses.pop(0)
            return web.json_response(
                {
                    "status": "completed",
                    "url": self.base + "/file.mp4",
                    "internal_status": "pending",
                }
            )

        async def media_file(request):
            self.requests.append((request.method, request.path, dict(request.headers), None))
            return web.Response(body=PNG if request.path.endswith(".png") else b"test-mp4-content")

        app = web.Application()
        app.router.add_post("/prefix/v1/videos", create)
        app.router.add_post("/prefix/v1/images/generations", create)
        app.router.add_get("/prefix/agnesapi", query)
        app.router.add_get("/file.mp4", media_file)
        app.router.add_get("/image.png", media_file)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        self.client = api.AgnesClient("test-auth-secret", self.base + "/prefix/v1")

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()

    async def test_real_http_creation_and_encoded_query(self):
        body = api.video_payload("move", api.DEFAULT_VIDEO_MODEL, "720P", "16:9", 5)
        result = await self.client.create_video(body)
        self.assertEqual(result["video_id"], "video_expected")
        await self.client.query_video("video_a&b", api.DEFAULT_VIDEO_MODEL)
        self.assertEqual(self.requests[0][2]["Authorization"], "Bearer test-auth-secret")
        self.assertEqual(self.requests[0][3], body)
        self.assertEqual(
            self.requests[1][3], {"video_id": "video_a&b", "model_name": api.DEFAULT_VIDEO_MODEL}
        )

    async def test_post_errors_are_not_retried_and_key_is_redacted(self):
        self.create_status = 500
        self.create_body = {"error": {"message": "oops test-auth-secret"}}
        with self.assertRaises(api.AgnesAPIError) as error:
            await self.client.create_video({"prompt": "draw"})
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("test-auth-secret", str(error.exception))

    async def test_poll_retries_429_then_uses_completed_top_level_url(self):
        self.query_responses = [web.json_response({"detail": "slow down"}, status=429)]
        with patch.object(api.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            result = await self.client.wait_video("video_expected", api.DEFAULT_VIDEO_MODEL)
        self.assertEqual(result["url"], self.base + "/file.mp4")
        self.assertEqual(len(self.requests), 2)
        self.assertGreaterEqual(sleep.call_args.args[0], 2)

    async def test_unauthorized_query_fails_without_retry(self):
        self.query_responses = [web.json_response({"detail": "bad key"}, status=401)]
        with self.assertRaises(api.AgnesAPIError):
            await self.client.wait_video("video_expected", api.DEFAULT_VIDEO_MODEL)
        self.assertEqual(len(self.requests), 1)

    async def test_failed_task_is_immediate(self):
        self.query_responses = [
            web.json_response({"status": "failed", "error": {"message": "invalid reference"}})
        ]
        with self.assertRaisesRegex(api.AgnesAPIError, "invalid reference"):
            await self.client.wait_video("video_expected", api.DEFAULT_VIDEO_MODEL)

    async def test_deadline_includes_network_time(self):
        async def slow_query(*_):
            await asyncio.sleep(5)

        with patch.object(self.client, "query_video", slow_query):
            start = asyncio.get_running_loop().time()
            with self.assertRaises(TimeoutError):
                await self.client.wait_video(
                    "video_expected", api.DEFAULT_VIDEO_MODEL, timeout=0.04
                )
            self.assertLess(asyncio.get_running_loop().time() - start, 0.5)

    async def test_media_download_never_sends_bearer_and_temp_survives_yield(self):
        plugin = main.AgnesAIPlugin(None, config(download_video=True))
        plugin._client = self.client
        try:
            messages = await collect(plugin._deliver_video(Event(""), self.base + "/file.mp4"))
            path = messages[0]["chain"][0].path
            self.assertTrue(Path(path).is_file())
            self.assertEqual(Path(path).read_bytes(), b"test-mp4-content")
            self.assertNotIn("Authorization", self.requests[-1][2])
            uri = await media.download_image(await self.client.session(), self.base + "/image.png")
            self.assertEqual(media.image_dimensions(uri), (1, 1))
            self.assertNotIn("Authorization", self.requests[-1][2])
        finally:
            await plugin.terminate()
        self.assertFalse(Path(path).exists())


class MediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_base64_file_uri_and_dimensions(self):
        encoded = base64.b64encode(PNG).decode()
        component = Image.fromBase64(encoded)
        uri = await media.image_data_uri(component, None)
        self.assertEqual(media.image_dimensions(uri), (1, 1))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "中文 file.png"
            path.write_bytes(PNG)
            uri = await media.image_data_uri(Image(file=path.as_uri()), None)
            self.assertEqual(media.infer_ratio(uri), "1:1")

    async def test_reply_chain_and_onebot_fallback(self):
        reply = types.SimpleNamespace(type="Reply", id="123", chain=[Image.fromURL(URLS[0])])
        event = Event("", [reply])
        self.assertEqual((await media.extract_images(event))[0].url, URLS[0])
        reply.chain = []
        event.platform = "aiocqhttp"
        action = AsyncMock(return_value={"message": [{"type": "image", "data": {"url": URLS[1]}}]})
        event.bot = types.SimpleNamespace(api=types.SimpleNamespace(call_action=action))
        self.assertEqual((await media.extract_images(event))[0].url, URLS[1])
        action.assert_awaited_once_with("get_msg", message_id=123)

    async def test_direct_picture_wins_over_quoted(self):
        reply = types.SimpleNamespace(type="Reply", id="123", chain=[Image.fromURL(URLS[1])])
        images = await media.extract_images(Event("", [reply, Image.fromURL(URLS[0])]))
        self.assertEqual([item.url for item in images], URLS[:1])

    async def test_public_file_service_and_fallback(self):
        image = Image.fromBase64(base64.b64encode(PNG).decode())
        image.register_to_file_service = AsyncMock(return_value=URLS[0])
        self.assertEqual(await media.video_image_url(image), URLS[0])
        image.register_to_file_service = AsyncMock(side_effect=ValueError("no callback"))
        with self.assertRaisesRegex(ValueError, "callback_api_base"):
            await media.video_image_url(image)
        image.url = URLS[1]
        self.assertEqual(await media.video_image_url(image), URLS[1])
        with self.assertRaises(ValueError):
            await media.video_image_url(image, "file_service")


class PluginTests(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self, **overrides):
        plugin = main.AgnesAIPlugin(None, config(**overrides))
        client = Mock(spec=api.AgnesClient)
        client.api_key = "test-key"
        client.safe_error = str
        client.create_image = AsyncMock(
            return_value={"data": [{"b64_json": base64.b64encode(PNG).decode()}]}
        )
        client.create_video = AsyncMock(return_value={"video_id": "video_real", "id": "task_wrong"})
        client.wait_video = AsyncMock(
            return_value={"status": "completed", "url": "https://example.com/out.mp4"}
        )
        client.query_video = AsyncMock(return_value={"status": "in_progress", "progress": 30})
        client.close = AsyncMock()
        plugin._client = client
        return plugin, client

    async def test_image_command_delivers_base64_and_keeps_ratio(self):
        plugin, client = self.make_plugin()
        result = await collect(plugin.text_to_image(Event("/image 16:9 2K 画面中写着 5s")))
        body = client.create_image.call_args.args[0]
        self.assertEqual(
            (body["model"], body["size"], body["ratio"]), (api.DEFAULT_IMAGE_MODEL, "2K", "16:9")
        )
        self.assertIn("5s", body["prompt"])
        self.assertTrue(result[-1]["chain"][0].file.startswith("base64://"))

    async def test_video_command_uses_video_id_and_url(self):
        plugin, client = self.make_plugin()
        result = await collect(plugin.generate_video(Event("/video 16:9 5s 猫在走路")))
        self.assertEqual(
            client.wait_video.call_args.args[:2], ("video_real", api.DEFAULT_VIDEO_MODEL)
        )
        self.assertIn("video_real", result[1]["text"])
        self.assertEqual(result[-1]["chain"][0].url, "https://example.com/out.mp4")

    async def test_invalid_user_parameters_never_create_task(self):
        for command in (
            "/video 16:9 3s 猫",
            "/video 16:9 -5s 猫",
            "/video 16:9 1080P 猫",
            "/video 2:3 5s 猫",
        ):
            plugin, client = self.make_plugin()
            result = await collect(plugin.generate_video(Event(command)))
            client.create_video.assert_not_awaited()
            self.assertIn("失败", result[-1]["text"])

    async def test_two_images_keyframes_and_explicit_model(self):
        plugin, client = self.make_plugin(video_image_source="direct")
        event = Event(
            "/video 16:9 5s 1080P --model=agnes-video-2.5 关键帧 平滑过渡",
            [Image.fromURL(url) for url in URLS[:2]],
        )
        await collect(plugin.generate_video(event))
        body = client.create_video.call_args.args[0]
        self.assertEqual(body["first_frame"], URLS[0])
        self.assertEqual(body["last_frame"], URLS[1])
        self.assertEqual(body["model"], "agnes-video-2.5")
        self.assertNotIn("images", body)

    async def test_task_id_is_never_used_as_video_id(self):
        plugin, client = self.make_plugin()
        client.create_video.return_value = {"id": "task_wrong", "task_id": "task_wrong"}
        result = await collect(plugin.generate_video(Event("/video 5s 猫")))
        client.wait_video.assert_not_awaited()
        self.assertIn("缺少 video_id", result[-1]["text"])

    async def test_keyframe_words_do_not_override_explicit_mode_or_text_only(self):
        plugin, client = self.make_plugin(video_image_source="direct")
        await collect(plugin.generate_video(Event("/video 5s 解释关键帧的概念")))
        self.assertEqual(client.create_video.call_args.args[0]["mode"], "text")
        event = Event("/video 16:9 5s --mode=reference 关键帧 平滑移动", [Image.fromURL(URLS[0])])
        await collect(plugin.generate_video(event))
        self.assertEqual(client.create_video.call_args.args[0]["mode"], "reference")

    async def test_timeout_reports_original_task_and_query_does_not_create(self):
        plugin, client = self.make_plugin()
        client.wait_video.side_effect = TimeoutError("timeout")
        result = await collect(plugin.generate_video(Event("/video 5s 猫")))
        self.assertIn("/agnes_query video_real", result[-1]["text"])
        client.create_video.reset_mock()
        result = await collect(plugin.query_video(Event("/agnes_query video_real agnes-video-2.5")))
        client.query_video.assert_awaited_once_with("video_real", "agnes-video-2.5")
        client.create_video.assert_not_awaited()
        self.assertIn("30%", result[0]["text"])

    async def test_old_video_config_migrates_and_valid_image_selection_stays(self):
        class Config(dict):
            save_config = Mock()

        settings = Config(
            config(
                default_video_model="agnes-video-v2.0", default_image_model="agnes-image-2.1-flash"
            )
        )
        main.AgnesAIPlugin(None, settings)
        self.assertEqual(settings["default_video_model"], api.DEFAULT_VIDEO_MODEL)
        self.assertEqual(settings["default_image_model"], "agnes-image-2.1-flash")
        settings.save_config.assert_called_once()

    async def test_missing_api_key_yields_clear_message(self):
        plugin = main.AgnesAIPlugin(None, config(api_key=""))
        result = await collect(plugin.generate_video(Event("/video 5s 猫")))
        self.assertIn("api_key", result[0]["text"])


if __name__ == "__main__":
    unittest.main()
