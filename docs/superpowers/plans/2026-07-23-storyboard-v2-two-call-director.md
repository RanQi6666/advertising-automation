# Storyboard V2 两次模型调用导演脚本实施计划

> 使用内联执行和测试驱动开发；每个生产代码改动前先运行对应失败测试。

**目标：** 将 Storyboard V2 从“视觉分析 + 导演计划 + 分镜草稿 + 编译/语义校验”改为“视觉分析 + 最终导演脚本”两次模型调用，同时保持外部异步接口与 `storyboard_text` 契约不变。

**技术栈：** Python 3.12、Pydantic v2、FastAPI、Celery、pytest、OpenAI 兼容 strict JSON Schema。

## 全局约束

- 不增加语义审稿或修复调用。
- 不修改外部请求字段、任务类型、队列名和轮询结果形状。
- 不提交 `AGENTS.md`、`.env*`、密钥或无关改动。
- 保留联合视觉分析与参考视频本地时间线适配。
- 旧编译器和导演计划代码不做无关的大范围删除，只从 V2 运行路径移除。

## Task 1：最小最终文本 Schema 与 Provider 契约

**文件：**
- `backend/app/schemas/ai.py`
- `backend/app/integrations/llm/base.py`
- `backend/app/integrations/llm/openai_provider.py`
- `backend/app/integrations/llm/mock_provider.py`
- `tests/test_frame_anchored_storyboard_provider.py`

- [x] 先写失败测试：最终调用使用只含 `storyboard_text` 的 strict JSON Schema。
- [x] 先写失败测试：最终提示词不要求场景数组、阶段、return 或内部证据 ID，并保留首尾锚点、参考动作/运镜/VFX与安全原则。
- [x] 运行测试确认 RED。
- [x] 新增 `FrameAnchoredStoryboardTextCandidate`，纯空白无效。
- [x] 新增 `generate_frame_anchored_video_storyboard_text()` Provider 方法。
- [x] 为 OpenAI 和 Mock Provider 实现最小输出。
- [x] 运行测试确认 GREEN。

## Task 2：服务层切换为两调用路径

**文件：**
- `backend/app/services/external_ai_generation_service.py`
- `tests/test_external_ai_generation.py`

- [x] 先写/调整失败测试：未缓存任务调用顺序仅为分析与最终文本生成。
- [x] 先写失败测试：没有导演计划、动作阶段、return 和内部证据 ID 仍成功。
- [x] 先写失败测试：纯空白最终文本按技术失败处理。
- [x] 先写失败测试：公开轮询仍仅返回原有字段且不泄露 `__sbv2_*__`。
- [x] 运行测试确认 RED。
- [x] 从 V2 路径移除独立导演计划、覆盖审查、编译器和语义校验调用。
- [x] 保存分析和最终文本候选私有 metadata。
- [x] 对最终文本执行非空检查与私有标记清理。
- [x] 运行测试确认 GREEN。

## Task 3：回归验证与范围检查

- [x] 运行 `tests/test_frame_anchored_storyboard_provider.py`。
- [x] 运行 `tests/test_external_ai_generation.py`。
- [x] 运行其他 Storyboard V2 相关测试。
- [x] 运行完整 pytest 与 Ruff（若耗时允许，至少覆盖 backend 和 tests）。
- [x] 运行 `git diff --check`、`git status --short`，确认不含私密和无关文件。
- [x] 使用简体中文提交；本轮不自动推送或部署，除非用户另行要求。
