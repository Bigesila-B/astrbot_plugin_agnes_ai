# 测试报告 — v2.1.0

验证日期：2026-10-28（Asia/Macau）。

- 自动化回归测试：48/48 通过，无跳过项。
- Ruff 检查、格式检查：通过。
- Python 3.10 语法检查、JSON 配置和版本一致性检查：通过。
- 新增中文简短指令、附图自动改图、设置默认值、开关关闭阻止任务、当前请求工具隔离、人格指引、结构化参数、后台发送、同消息去重、失败通知和卸载取消测试。
- 使用实际 docstring_parser 验证 Agent 两个工具的每个参数均可按 AstrBot 格式导出，包括图片链接数组。
- 原有 HTTP 请求、模型限制、轮询、引用图片、配置迁移和文件生命周期测试保留。
- Agent 会话、消息发送和工具集使用 AstrBot 接口替身；HTTP 使用本地 aiohttp 服务器。
- 未提供真实 Agnes API Key 和 AstrBot 实例，未验证真实聊天模型是否选择工具、真实模型生成、计费或平台消息投递。

复现命令：`python -m unittest discover -s tests -v`。实际参数 schema 测试需要 AstrBot 环境自带的 `docstring_parser`；本次验证环境已单独安装开发依赖，不加入插件运行依赖。

```text
test_real_docstring_parser_exports_every_tool_argument (test_agent.AgentSchemaTests.test_real_docstring_parser_exports_every_tool_argument) ... ok
test_background_generation_failure_is_sent_to_chat (test_agent.AgentTests.test_background_generation_failure_is_sent_to_chat) ... ok
test_different_messages_deliver_to_their_own_event (test_agent.AgentTests.test_different_messages_deliver_to_their_own_event) ... ok
test_explicit_text_video_ignores_attached_picture (test_agent.AgentTests.test_explicit_text_video_ignores_attached_picture) ... ok
test_guidance_preserves_persona_and_is_added_once (test_agent.AgentTests.test_guidance_preserves_persona_and_is_added_once) ... ok
test_image_tool_queues_and_delivers_without_reparsing_prompt (test_agent.AgentTests.test_image_tool_queues_and_delivers_without_reparsing_prompt) ... ok
test_image_tool_reads_reference_or_can_ignore_it (test_agent.AgentTests.test_image_tool_reads_reference_or_can_ignore_it) ... ok
test_invalid_tool_args_do_not_queue_or_change_model (test_agent.AgentTests.test_invalid_tool_args_do_not_queue_or_change_model) ... ok
test_persona_without_agnes_tools_is_not_overridden (test_agent.AgentTests.test_persona_without_agnes_tools_is_not_overridden) ... ok
test_same_tool_retry_for_same_message_does_not_duplicate (test_agent.AgentTests.test_same_tool_retry_for_same_message_does_not_duplicate) ... ok
test_short_draw_auto_edits_attached_picture_and_aliases (test_agent.AgentTests.test_short_draw_auto_edits_attached_picture_and_aliases) ... ok
test_short_draw_uses_defaults_and_brief_progress (test_agent.AgentTests.test_short_draw_uses_defaults_and_brief_progress) ... ok
test_short_video_defaults_and_optional_details (test_agent.AgentTests.test_short_video_defaults_and_optional_details) ... ok
test_switch_off_blocks_agent_but_manual_commands_work (test_agent.AgentTests.test_switch_off_blocks_agent_but_manual_commands_work) ... ok
test_switch_off_hides_own_tools_without_changing_shared_set (test_agent.AgentTests.test_switch_off_hides_own_tools_without_changing_shared_set) ... ok
test_tool_returns_quickly_and_reload_cancels_background_task (test_agent.AgentTests.test_tool_returns_quickly_and_reload_cancels_background_task) ... ok
test_video_tool_uses_structured_args_and_sends_result (test_agent.AgentTests.test_video_tool_uses_structured_args_and_sends_result) ... ok
test_base64_request_differs_for_text_and_edit (test_plugin.ContractTests.test_base64_request_differs_for_text_and_edit) ... ok
test_base_url_proxy_prefix_and_migration (test_plugin.ContractTests.test_base_url_proxy_prefix_and_migration) ... ok
test_completed_uses_url_and_ignores_internal_status (test_plugin.ContractTests.test_completed_uses_url_and_ignores_internal_status) ... ok
test_error_redacts_key_and_image_data (test_plugin.ContractTests.test_error_redacts_key_and_image_data) ... ok
test_flash_limits_and_ordinary_model (test_plugin.ContractTests.test_flash_limits_and_ordinary_model) ... ok
test_image_new_size_ratio_and_reference_contract (test_plugin.ContractTests.test_image_new_size_ratio_and_reference_contract) ... ok
test_image_prompt_duration_words_are_preserved (test_plugin.ContractTests.test_image_prompt_duration_words_are_preserved) ... ok
test_invalid_duration_ratio_or_mode_is_rejected (test_plugin.ContractTests.test_invalid_duration_ratio_or_mode_is_rejected) ... ok
test_legacy_image_model_keeps_its_documented_pixel_sizes (test_plugin.ContractTests.test_legacy_image_model_keeps_its_documented_pixel_sizes) ... ok
test_public_video_urls (test_plugin.ContractTests.test_public_video_urls) ... ok
test_video_modes_and_no_retired_fields (test_plugin.ContractTests.test_video_modes_and_no_retired_fields) ... ok
test_deadline_includes_network_time (test_plugin.HTTPTests.test_deadline_includes_network_time) ... ok
test_failed_task_is_immediate (test_plugin.HTTPTests.test_failed_task_is_immediate) ... ok
test_media_download_never_sends_bearer_and_temp_survives_yield (test_plugin.HTTPTests.test_media_download_never_sends_bearer_and_temp_survives_yield) ... ok
test_poll_retries_429_then_uses_completed_top_level_url (test_plugin.HTTPTests.test_poll_retries_429_then_uses_completed_top_level_url) ... ok
test_post_errors_are_not_retried_and_key_is_redacted (test_plugin.HTTPTests.test_post_errors_are_not_retried_and_key_is_redacted) ... ok
test_real_http_creation_and_encoded_query (test_plugin.HTTPTests.test_real_http_creation_and_encoded_query) ... ok
test_unauthorized_query_fails_without_retry (test_plugin.HTTPTests.test_unauthorized_query_fails_without_retry) ... ok
test_base64_file_uri_and_dimensions (test_plugin.MediaTests.test_base64_file_uri_and_dimensions) ... ok
test_direct_picture_wins_over_quoted (test_plugin.MediaTests.test_direct_picture_wins_over_quoted) ... ok
test_public_file_service_and_fallback (test_plugin.MediaTests.test_public_file_service_and_fallback) ... ok
test_reply_chain_and_onebot_fallback (test_plugin.MediaTests.test_reply_chain_and_onebot_fallback) ... ok
test_image_command_delivers_base64_and_keeps_ratio (test_plugin.PluginTests.test_image_command_delivers_base64_and_keeps_ratio) ... ok
test_invalid_user_parameters_never_create_task (test_plugin.PluginTests.test_invalid_user_parameters_never_create_task) ... ok
test_keyframe_words_do_not_override_explicit_mode_or_text_only (test_plugin.PluginTests.test_keyframe_words_do_not_override_explicit_mode_or_text_only) ... ok
test_missing_api_key_yields_clear_message (test_plugin.PluginTests.test_missing_api_key_yields_clear_message) ... ok
test_old_video_config_migrates_and_valid_image_selection_stays (test_plugin.PluginTests.test_old_video_config_migrates_and_valid_image_selection_stays) ... ok
test_task_id_is_never_used_as_video_id (test_plugin.PluginTests.test_task_id_is_never_used_as_video_id) ... ok
test_timeout_reports_original_task_and_query_does_not_create (test_plugin.PluginTests.test_timeout_reports_original_task_and_query_does_not_create) ... ok
test_two_images_keyframes_and_explicit_model (test_plugin.PluginTests.test_two_images_keyframes_and_explicit_model) ... ok
test_video_command_uses_video_id_and_url (test_plugin.PluginTests.test_video_command_uses_video_id_and_url) ... ok

----------------------------------------------------------------------
Ran 48 tests in 1.120s

OK
```
