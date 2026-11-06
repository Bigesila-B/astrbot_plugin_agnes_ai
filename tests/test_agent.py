"""中文指令与 Agent 工具集成测试；不调用真实生成服务。"""

import asyncio
import base64
import inspect
import json
import types
import unittest
from unittest.mock import AsyncMock

import test_plugin as fixtures
from test_plugin import PNG, URLS, Event, Image, collect, main

try:
    import docstring_parser
except ImportError:
    docstring_parser = None


class ToolSet:
    """遵循 AstrBot ToolSet 的当前公开接口。"""

    def __init__(self, names):
        self.tools = [types.SimpleNamespace(name=name, active=True) for name in names]

    def remove_tool(self, name):
        self.tools = [tool for tool in self.tools if tool.name != name]

    def names(self):
        return [tool.name for tool in self.tools]


class AgentTests(unittest.IsolatedAsyncioTestCase):
    make_plugin = fixtures.PluginTests.make_plugin

    async def drain(self, plugin):
        tasks = list(plugin._background_tasks)
        if tasks:
            await asyncio.gather(*tasks)

    async def test_short_draw_uses_defaults_and_brief_progress(self):
        plugin, client = self.make_plugin()
        result = await collect(plugin.draw(Event("/画图 一只橘猫")))
        body = client.create_image.call_args.args[0]
        self.assertEqual(body["prompt"], "一只橘猫")
        self.assertEqual(body["size"], "1K")
        self.assertEqual(result[0]["text"], "正在画图，请稍等…")

    async def test_short_draw_auto_edits_attached_picture_and_aliases(self):
        for command in ("画图", "画", "生图", "绘图", "改图"):
            plugin, client = self.make_plugin()
            picture = Image.fromBase64(base64.b64encode(PNG).decode())
            event = Event(f"/{command} 换成夜景", [picture])
            if command == "改图":
                await collect(plugin.image_to_image(event))
            else:
                await collect(plugin.draw(event))
            body = client.create_image.call_args.args[0]
            self.assertEqual(body["prompt"], "换成夜景")
            self.assertEqual(len(body["extra_body"]["image"]), 1)

    async def test_short_video_defaults_and_optional_details(self):
        plugin, client = self.make_plugin(show_generation_details=True)
        result = await collect(plugin.generate_video(Event("/视频 小猫跑步")))
        body = client.create_video.call_args.args[0]
        self.assertEqual(body["prompt"], "小猫跑步")
        self.assertEqual(body["seconds"], "5")
        self.assertIn("模型：", result[0]["text"])

    async def test_switch_off_blocks_agent_but_manual_commands_work(self):
        plugin, client = self.make_plugin(enable_natural_language=False)
        image = await plugin.agnes_generate_image(Event("帮我画猫"), "一只猫")
        video = await plugin.agnes_generate_video(Event("帮我做视频"), "小猫跑步")
        self.assertIn("已关闭", image)
        self.assertIn("已关闭", video)
        client.create_image.assert_not_awaited()
        client.create_video.assert_not_awaited()
        self.assertFalse(plugin._background_tasks)
        await collect(plugin.draw(Event("/画图 一只猫")))
        await collect(plugin.generate_video(Event("/视频 小猫跑步")))
        client.create_image.assert_awaited_once()
        client.create_video.assert_awaited_once()

    async def test_switch_off_hides_own_tools_without_changing_shared_set(self):
        plugin, _ = self.make_plugin(enable_natural_language=False)
        shared = ToolSet(["other_plugin", *main.AGENT_TOOL_NAMES])
        req = types.SimpleNamespace(func_tool=shared, system_prompt="原人格")
        await plugin.configure_agent_tools(Event("你好"), req)
        self.assertIsNot(req.func_tool, shared)
        self.assertEqual(req.func_tool.names(), ["other_plugin"])
        self.assertEqual(set(shared.names()), {"other_plugin", *main.AGENT_TOOL_NAMES})
        self.assertEqual(req.system_prompt, "原人格")

    async def test_guidance_preserves_persona_and_is_added_once(self):
        plugin, _ = self.make_plugin()
        req = types.SimpleNamespace(
            func_tool=ToolSet(main.AGENT_TOOL_NAMES), system_prompt="原人格"
        )
        await plugin.configure_agent_tools(Event("画猫"), req)
        await plugin.configure_agent_tools(Event("画猫"), req)
        self.assertTrue(req.system_prompt.startswith("原人格"))
        self.assertEqual(req.system_prompt.count("[Agnes 媒体生成]"), 1)

    async def test_persona_without_agnes_tools_is_not_overridden(self):
        plugin, _ = self.make_plugin()
        for tools in (None, ToolSet(["other_plugin"])):
            req = types.SimpleNamespace(func_tool=tools, system_prompt="原人格")
            await plugin.configure_agent_tools(Event("你好"), req)
            self.assertEqual(req.system_prompt, "原人格")
            self.assertIs(req.func_tool, tools)

    async def test_image_tool_queues_and_delivers_without_reparsing_prompt(self):
        plugin, client = self.make_plugin()
        event = Event("自然语言，不含斜杠指令")
        prompt = "画面写着 16:9 2K --model=arbitrary 和一只猫"
        status = json.loads(await plugin.agnes_generate_image(event, prompt))
        self.assertEqual(status["status"], "queued")
        client.create_image.assert_not_awaited()
        await self.drain(plugin)
        body = client.create_image.call_args.args[0]
        self.assertEqual(body["prompt"], prompt)
        self.assertEqual(body["model"], "agnes-image-2.5-flash")
        self.assertEqual(len(event.sent), 1)
        self.assertIn("chain", event.sent[0])
        self.assertEqual(event.message_str, "自然语言，不含斜杠指令")

    async def test_image_tool_reads_reference_or_can_ignore_it(self):
        for use_reference in (True, False):
            plugin, client = self.make_plugin()
            event = Event("把这张图改成夜景", [Image.fromBase64(base64.b64encode(PNG).decode())])
            await plugin.agnes_generate_image(event, "改成夜景", use_reference=use_reference)
            await self.drain(plugin)
            body = client.create_image.call_args.args[0]
            self.assertEqual("image" in body.get("extra_body", {}), use_reference)

    async def test_video_tool_uses_structured_args_and_sends_result(self):
        plugin, client = self.make_plugin(video_image_source="direct")
        event = Event("把这张图做成8秒竖屏视频", [Image.fromURL(URLS[0])])
        status = json.loads(
            await plugin.agnes_generate_video(
                event, "人物缓慢转身", duration=8, aspect_ratio="9:16", mode="keyframe"
            )
        )
        self.assertEqual(status["status"], "queued")
        await self.drain(plugin)
        body = client.create_video.call_args.args[0]
        self.assertEqual(
            (body["seconds"], body["aspect_ratio"], body["first_frame"]), ("8", "9:16", URLS[0])
        )
        self.assertEqual(event.sent[0]["chain"][0].url, "https://example.com/out.mp4")

    async def test_explicit_text_video_ignores_attached_picture(self):
        plugin, client = self.make_plugin()
        event = Event("重新制作一个纯文字视频", [Image.fromURL(URLS[0])])
        await plugin.agnes_generate_video(event, "海边日落", mode="text")
        await self.drain(plugin)
        self.assertEqual(client.create_video.call_args.args[0]["mode"], "text")

    async def test_invalid_tool_args_do_not_queue_or_change_model(self):
        plugin, client = self.make_plugin()
        for kwargs in (
            {"duration": 13},
            {"duration": 4.5},
            {"size": "1080P"},
            {"aspect_ratio": "2:3"},
            {"mode": "unknown"},
        ):
            result = await plugin.agnes_generate_video(Event("做视频"), "海边日落", **kwargs)
            self.assertIn("未提交", result)
        self.assertFalse(plugin._background_tasks)
        client.create_video.assert_not_awaited()
        self.assertEqual(plugin.config["default_video_model"], "agnes-video-2.5-flash")

    async def test_same_tool_retry_for_same_message_does_not_duplicate(self):
        plugin, client = self.make_plugin()
        event = Event("帮我画猫")
        first = json.loads(await plugin.agnes_generate_image(event, "一只猫"))
        second = json.loads(await plugin.agnes_generate_image(event, "一只猫"))
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(second["job_id"], first["job_id"])
        await self.drain(plugin)
        client.create_image.assert_awaited_once()
        third = json.loads(await plugin.agnes_generate_image(event, "一只猫"))
        self.assertEqual(third["status"], "duplicate")

    async def test_different_messages_deliver_to_their_own_event(self):
        plugin, client = self.make_plugin()
        first, second = Event("画猫"), Event("画猫")
        await plugin.agnes_generate_image(first, "一只猫")
        await plugin.agnes_generate_image(second, "一只猫")
        await self.drain(plugin)
        self.assertEqual(client.create_image.await_count, 2)
        self.assertEqual((len(first.sent), len(second.sent)), (1, 1))

    async def test_background_generation_failure_is_sent_to_chat(self):
        plugin, client = self.make_plugin()
        client.create_image.side_effect = ValueError("模型不可用")
        event = Event("帮我画猫")
        await plugin.agnes_generate_image(event, "一只猫")
        await self.drain(plugin)
        self.assertIn("图片生成失败", event.sent[0]["text"])
        self.assertIn("模型不可用", event.sent[0]["text"])

    async def test_tool_returns_quickly_and_reload_cancels_background_task(self):
        plugin, client = self.make_plugin()
        blocker = asyncio.Event()

        async def slow(*_):
            await blocker.wait()

        client.create_image = AsyncMock(side_effect=slow)
        event = Event("帮我画猫")
        status = await asyncio.wait_for(plugin.agnes_generate_image(event, "一只猫"), timeout=0.2)
        self.assertEqual(json.loads(status)["status"], "queued")
        await asyncio.sleep(0)
        await plugin.terminate()
        self.assertFalse(plugin._background_tasks)
        self.assertEqual(
            next(iter(event.get_extra("agnes_ai_generation_requests").values()))["status"],
            "cancelled",
        )
        client.close.assert_awaited_once()
        result = await plugin.agnes_generate_image(Event("画猫"), "一只猫")
        self.assertIn("关闭", result)


class AgentSchemaTests(unittest.TestCase):
    @unittest.skipIf(docstring_parser is None, "AstrBot提供的docstring_parser未安装")
    def test_real_docstring_parser_exports_every_tool_argument(self):
        for name in main.AGENT_TOOL_NAMES:
            method = getattr(main.AgnesAIPlugin, name)
            parsed = docstring_parser.parse(method.__doc__)
            expected = set(inspect.signature(method).parameters) - {"self", "event"}
            self.assertEqual({parameter.arg_name for parameter in parsed.params}, expected)
            self.assertTrue(all(parameter.type_name for parameter in parsed.params))
            self.assertIn("array[string]", {parameter.type_name for parameter in parsed.params})


if __name__ == "__main__":
    unittest.main()
