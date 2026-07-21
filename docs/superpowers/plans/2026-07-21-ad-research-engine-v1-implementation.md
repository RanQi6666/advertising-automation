# 广告研究引擎 V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一个可轮询的公开广告研究接口。外部系统传入一次性的 `external_user_id`、国家、类别、可选关键词和目标数量，后台异步召回公开 Meta 广告；只把具有可用短视频、ACTIVE、投放超过两天的候选交给 `gpt-5.4-mini`，最终在 24 小时内可重复轮询读取 Top-N 结果。

**Architecture:** Advertising Automation 继续是任务、结果和权限的唯一事实来源。第一期 Collector 使用 `athm793/meta-ads-scraper` 的公开 Meta Ad Library 抓取能力，但以内部 Bridge 直接调用其 `scrapeAds()` 模块，并把每次抓取标准化为 JSON；不调用上游 UI/API 的 `upsertAd()`，因此不以其 SQLite 作为数据源。`AdResearchJob` 管理外部幂等、进度、24 小时结果；一个独立 Celery 队列执行采集、媒体技术过滤、模型判断、补采和排序。

**Tech Stack:** FastAPI、Pydantic v2、SQLAlchemy Async/Alembic、PostgreSQL、Celery/Redis、httpx、Docker Compose、Playwright/Node collector bridge、GPT-5.4 mini（`reasoning.effort=none`）。

---

## 0. 已确认边界与关键约束

- 对外只提供轮询，不提供 callback 或 acknowledge。
- `external_user_id` 是**一次逻辑研究请求**的幂等键；相同 ID + 相同规范化 payload 返回同一任务，相同 ID + 不同 payload 返回 `409`。
- 首期技术硬条件：`ACTIVE`、有可访问视频、`duration_seconds <= 30`、`active_days > 2`、广告 ID 去重、最终记录保留视频/封面公开 URL。
- 业务类别理解、关键词规划、文本和视觉证据、排序、补采方向由模型负责；后台不固化印度或博彩词包。
- 成效只可表达为 `public_performance_signal_score`（公开持续投放代理信号）；不能声称 CPC、CPA、ROAS、真实 spend 或真实转化。
- 不模拟审核员/真实用户，不用 IP、设备指纹或登录态探测落地页，不确认或复现 Cloaking。
- 用户设定的运行并发：

```env
AD_RESEARCH_WORKER_CONCURRENCY=2
AD_RESEARCH_MODEL_CONCURRENCY=6
```

  两个 Worker 同时处理完整研究任务；所有 Worker 合计的模型 in-flight 必须由 Redis 全局令牌限制为 6。
- 完整结果和临时媒体最多保留 24 小时；过期后轮询返回 `410 Gone`，仅保留不含完整广告内容的任务摘要约 30 天。

---

## 1. 文件结构与职责

| 路径 | 责任 |
| --- | --- |
| `backend/app/db/models/ad_research_job.py` | 外部请求、状态、进度、最终结果、结果有效期。 |
| `backend/app/schemas/ad_research.py` | 创建请求、处理中轮询、完成结果、错误响应和 Collector 标准化 DTO。 |
| `backend/app/services/ad_research_service.py` | `external_user_id` 幂等、任务创建、轮询读模型、过期和安全错误摘要。 |
| `backend/app/services/ad_research_orchestrator.py` | 一轮研究的状态推进、去重、技术过滤、模型批处理、排序和补采。 |
| `backend/app/services/ad_research_collector.py` | `AdSourceAdapter` 抽象及对内部 Collector Bridge 的 HTTP/SSE 客户端。 |
| `backend/app/services/ad_research_media.py` | HEAD/Range 媒体可访问性、时长探测、封面和代表帧的临时文件处理。 |
| `backend/app/services/ad_research_model.py` | GPT-5.4 mini planner/classifier 的严格 JSON contract、Redis 全局模型令牌。 |
| `backend/app/api/v1/endpoints/ad_research.py` | `POST/GET /integrations/ad-research/jobs`。 |
| `backend/app/worker/tasks.py` | `ad_research_jobs.process` 和清理任务入口。 |
| `backend/app/worker/celery_app.py` | `ad_research_queue` 路由及 Beat 的清理调度。 |
| `collector/meta_ads_bridge/` | 内部 Node 服务：固定版本调用上游 `scrapeAds()`，按请求返回标准化候选，不写 SQLite。 |
| `docker-compose.prod.yml` | `meta_ads_collector`（仅内部网络）和 `worker_ad_research`（并发 2）。 |
| `backend/alembic/versions/20260721_0013_add_ad_research_jobs.py` | `ad_research_jobs` 表、唯一幂等约束及索引。 |
| `tests/test_ad_research_api.py` | 外部 HTTP contract 与幂等/过期行为。 |
| `tests/test_ad_research_service.py` | 规范化指纹、状态、清理和读模型。 |
| `tests/test_ad_research_orchestrator.py` | 技术过滤、去重、Top-N、不足补采、模型调用边界。 |
| `tests/test_ad_research_collector.py` | Collector Bridge 事件/字段标准化与错误映射。 |
| `tests/test_ad_research_model.py` | JSON contract、Redis 全局并发令牌和 `reasoning.effort` 配置。 |

不修改用户现有未提交的 `frontend/web-admin/*`、外部投放分析文档、`AGENTS.md`、`.env*` 或临时媒体文件。

---

### Task 1: 固定上游版本并完成 Collector 最小验证

**Files:**
- Create: `collector/meta_ads_bridge/Dockerfile`
- Create: `collector/meta_ads_bridge/package.json`
- Create: `collector/meta_ads_bridge/src/server.ts`
- Create: `collector/meta_ads_bridge/src/contracts.ts`
- Create: `tests/integration/test_meta_ads_collector_contract.py`
- Modify: `docker-compose.prod.yml`
- Modify: `.env.example`

- [ ] **Step 1: 为 Bridge contract 写失败测试。**

```python
def test_collector_normalizes_an_active_video_ad() -> None:
    event = {
        "id": "meta-ad-1", "status": "ACTIVE", "media_type": "video",
        "video_urls": ["https://cdn.example/ad.mp4"], "media_urls": ["https://cdn.example/cover.jpg"],
        "days_running": 3, "body_variants": ["example"],
    }
    assert normalize_collector_ad(event)["ad_library_id"] == "meta-ad-1"
    assert normalize_collector_ad(event)["video_url"] == "https://cdn.example/ad.mp4"
```

- [ ] **Step 2: 运行测试，确认在 Bridge/Adapter 尚未存在时失败。**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_meta_ads_collector_contract.py -q`  
Expected: import/module failure.

- [ ] **Step 3: 实现最小内部 Bridge。**

Bridge 请求必须只接受：

```json
{"request_id":"adr_...","query":"teen patti","country":"IN","limit":50,"active_only":true,"video_only":true}
```

Bridge 必须固定 `athm793/meta-ads-scraper` 的 commit SHA（写入 Docker build arg/镜像 label），从其 `scrapeAds({keyword, country, status:"ACTIVE", ad_type:"video", limit})` 读取 generator，并逐条输出：

```json
{"type":"ad","ad":{"ad_library_id":"...","status":"ACTIVE","days_running":3,"text_variants":[],"headline":null,"cta_text":null,"landing_url":null,"video_url":"...","thumbnail_url":"...","platforms":[],"ad_snapshot_url":null,"reported_spend_range":null}}
```

最后输出：

```json
{"type":"done","collected_count":17}
```

禁止调用上游 `src/app/api/scrape/route.ts`，禁止 `upsertAd()`，禁止挂载永久 SQLite volume。镜像中 `data/` 配置为 tmpfs；Bridge 不提供公网端口，只加入 Compose 内部网络。

- [ ] **Step 4: 用伪造上游 generator 运行单元测试并确认通过。**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_meta_ads_collector_contract.py -q`  
Expected: PASS.

- [ ] **Step 5: 做一次受限 live smoke test。**

启动内部 Collector 后，对 `country=IN`、一个合法关键词、`limit=5` 发出一次请求，记录：HTTP 状态、事件数、是否存在 `ad_library_id`/视频 URL/`days_running`。若 Meta 返回 429/结构变更，记录为外部依赖故障并继续实现可测试的 Adapter，不能把“零结果”静默当作成功。

- [ ] **Step 6: Commit。**

```powershell
git add collector/meta_ads_bridge docker-compose.prod.yml .env.example tests/integration/test_meta_ads_collector_contract.py
git commit -m "feat: add internal Meta ads collector bridge"
```

### Task 2: `AdResearchJob` 数据模型、迁移与任务状态

**Files:**
- Create: `backend/app/db/models/ad_research_job.py`
- Modify: `backend/app/db/models/__init__.py`
- Create: `backend/alembic/versions/20260721_0013_add_ad_research_jobs.py`
- Create: `tests/test_ad_research_service.py`

- [ ] **Step 1: 写失败测试，定义状态与指纹不变量。**

```python
def test_request_fingerprint_is_insensitive_to_keyword_order_and_whitespace() -> None:
    assert fingerprint("IN", " gambling ", ["Rummy", "casino"], 25) == fingerprint(
        "in", "gambling", [" casino ", "rummy"], 25
    )
```

```python
def test_expired_job_cannot_return_full_result() -> None:
    job.result_expires_at = utcnow() - timedelta(seconds=1)
    assert job.is_result_expired is True
```

- [ ] **Step 2: 运行测试，确认失败。**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_research_service.py -q`  
Expected: import failure.

- [ ] **Step 3: 新建模型和 migration。**

表至少包含：`id`、`external_user_id`、`request_fingerprint`、`country`、`category`、`seed_keywords_json`、`target_count`、`status`、`stage`、`current_round`、`progress_json`、`summary_json`、`result_json`、`result_expires_at`、`error_code`、`error_message_summary`、时间戳。建立唯一索引 `(external_user_id)` 和查询索引 `(status, result_expires_at)`。

允许状态：`queued`、`processing`、`completed`、`insufficient`、`failed`、`expired`。不得把失败的原始上游响应或完整媒体写进错误摘要。

- [ ] **Step 4: 重跑测试并执行 migration upgrade。**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ad_research_service.py -q
.venv\Scripts\alembic.exe -c backend/alembic.ini upgrade head
```

Expected: tests PASS; `ad_research_jobs` 表创建成功。

- [ ] **Step 5: Commit。**

```powershell
git add backend/app/db/models backend/alembic/versions/20260721_0013_add_ad_research_jobs.py tests/test_ad_research_service.py
git commit -m "feat: add ad research job persistence"
```

### Task 3: 创建/轮询 API 与 `external_user_id` 幂等

**Files:**
- Create: `backend/app/schemas/ad_research.py`
- Create: `backend/app/services/ad_research_service.py`
- Create: `backend/app/api/v1/endpoints/ad_research.py`
- Modify: `backend/app/api/v1/router.py`
- Create: `tests/test_ad_research_api.py`

- [ ] **Step 1: 写失败 API contract 测试。**

```python
payload = {"external_user_id":"research-001","country":"IN","category":"gambling","keywords":["rummy"],"target_count":25}
response = client.post("/api/v1/integrations/ad-research/jobs", json=payload, headers=auth_headers)
assert response.status_code == 202
assert response.json()["status"] == "queued"
assert response.json()["poll_url"].endswith(response.json()["task_id"])
```

同时覆盖：重复相同 payload 返回同一 `task_id`；同 ID 改国家/类别/关键词/目标数返回 `409 external_user_id_payload_conflict`；未知任务 `404`；过期任务 `410`；`target_count` 限制 `1..50`；国家为两位 ISO 代码。

- [ ] **Step 2: 运行测试并确认失败。**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_research_api.py -q`  
Expected: 404/route missing.

- [ ] **Step 3: 实现 Pydantic 和服务层。**

创建成功响应固定为：

```json
{"task_id":"adr_...","external_user_id":"research-001","status":"queued","poll_url":"/api/v1/integrations/ad-research/jobs/adr_...","poll_after_seconds":3}
```

处理中 `GET` 只返回 `status/stage/round/raw_collected/deduplicated/technical_qualified/model_relevant/selected_count/poll_after_seconds`。完成/不足时返回 `ads`、`research_summary`、`result_expires_at`。路由挂在现有 `protected_dependencies` 下，沿用 `Authorization: Bearer` 鉴权。

- [ ] **Step 4: 跑 API 测试并确认通过。**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_research_api.py tests/test_ad_research_service.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit。**

```powershell
git add backend/app/schemas/ad_research.py backend/app/services/ad_research_service.py backend/app/api/v1/endpoints/ad_research.py backend/app/api/v1/router.py tests/test_ad_research_api.py
git commit -m "feat: add polling ad research API"
```

### Task 4: Celery 队列、Worker 并发和 24 小时清理

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/worker/celery_app.py`
- Modify: `backend/app/worker/tasks.py`
- Modify: `docker-compose.prod.yml`
- Create: `backend/app/services/ad_research_cleanup.py`
- Create: `tests/test_ad_research_worker.py`

- [ ] **Step 1: 写失败测试。**

```python
def test_ad_research_task_is_sent_to_dedicated_queue() -> None:
    signature = build_ad_research_signature("adr_123")
    assert signature.options["queue"] == "ad_research_queue"
```

```python
def test_cleanup_erases_full_result_and_marks_expired(expired_job):
    cleanup(expired_job)
    assert expired_job.result_json is None
    assert expired_job.status == "expired"
```

- [ ] **Step 2: 运行测试并确认失败。**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_research_worker.py -q`  
Expected: missing task/config/cleanup implementation.

- [ ] **Step 3: 实现队列和调度。**

增加配置：

```python
ad_research_worker_concurrency: int = Field(default=2, ge=1, le=16)
ad_research_model_concurrency: int = Field(default=6, ge=1, le=64)
ad_research_result_ttl_hours: int = Field(default=24, ge=1, le=168)
ad_research_max_rounds: int = Field(default=4, ge=1, le=10)
ad_research_max_raw_candidates: int = Field(default=500, ge=50, le=5000)
```

`worker_ad_research` 命令必须为：

```text
celery -A backend.app.worker.celery_app:celery_app worker --queues=ad_research_queue --concurrency=${AD_RESEARCH_WORKER_CONCURRENCY:-2} --hostname=ad-research@%h
```

Celery Beat 每 15 分钟投递 `ad_research_jobs.cleanup_expired`。清理必须删除 `result_json`、临时媒体/代表帧、候选及模型中间明细；保留任务 ID、非敏感统计、状态、时间和错误代码。

- [ ] **Step 4: 跑 worker 测试。**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_research_worker.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit。**

```powershell
git add backend/app/core/config.py backend/app/worker backend/app/services/ad_research_cleanup.py docker-compose.prod.yml tests/test_ad_research_worker.py
git commit -m "feat: add ad research worker lifecycle"
```

### Task 5: Collector Adapter、广告去重和技术资格过滤

**Files:**
- Create: `backend/app/services/ad_research_collector.py`
- Create: `backend/app/services/ad_research_media.py`
- Create: `backend/app/services/ad_research_qualification.py`
- Create: `tests/test_ad_research_collector.py`
- Create: `tests/test_ad_research_orchestrator.py`

- [ ] **Step 1: 写失败测试。**

```python
def test_technical_qualification_requires_active_short_video_and_more_than_two_days():
    assert qualify(ad(status="ACTIVE", duration=30, days=3, video_url="https://example/ad.mp4")).accepted
    assert not qualify(ad(status="ACTIVE", duration=31, days=3, video_url="https://example/ad.mp4")).accepted
    assert not qualify(ad(status="ACTIVE", duration=30, days=2, video_url="https://example/ad.mp4")).accepted
```

另覆盖：同 `ad_library_id` 跨 query 只保留一次、视频 URL 无法访问被拒绝、缺失 `days_running` 可以由 `started_at` 推导，否则为 `insufficient_technical_data`。

- [ ] **Step 2: 运行测试并确认失败。**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_research_collector.py tests/test_ad_research_orchestrator.py -q`  
Expected: missing adapter/qualification implementation.

- [ ] **Step 3: 实现桥接客户端与技术过滤。**

`AdSourceAdapter.collect()` 每个 query 最大请求 50 条，传入用户 country、不拼接后台业务词。后端只在技术事实层过滤：状态、公开视频 URL、时长、投放天数、去重。媒体探测优先使用 `Content-Length`/Range；需要解码时用 `ffprobe`；不得下载完整视频到最终结果目录。

标准拒绝原因仅限技术性质，如 `not_active`、`no_video`、`video_unreachable`、`video_duration_exceeded`、`insufficient_active_days`、`duplicate_ad_library_id`、`collector_schema_invalid`。

- [ ] **Step 4: 跑过滤测试。**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_research_collector.py tests/test_ad_research_orchestrator.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit。**

```powershell
git add backend/app/services/ad_research_collector.py backend/app/services/ad_research_media.py backend/app/services/ad_research_qualification.py tests/test_ad_research_collector.py tests/test_ad_research_orchestrator.py
git commit -m "feat: qualify collected ad research candidates"
```

### Task 6: GPT-5.4 mini 规划、分类和 Redis 全局模型限流

**Files:**
- Create: `backend/app/services/ad_research_model.py`
- Modify: `backend/app/core/config.py`
- Create: `tests/test_ad_research_model.py`

- [ ] **Step 1: 写失败测试。**

```python
async def test_model_planner_uses_gateway_model_and_no_reasoning(fake_gateway):
    await planner.plan_queries(country="IN", category="gambling", seed_keywords=["rummy"], round_number=1)
    assert fake_gateway.request["model"] == "gpt-5.4-mini"
    assert fake_gateway.request["reasoning"] == {"effort": "none"}
```

```python
async def test_global_model_limit_uses_redis_lease_not_local_semaphore(redis):
    lease = await limiter.acquire("ad-research:model", limit=6, ttl_seconds=90)
    assert lease is not None
```

- [ ] **Step 2: 运行测试并确认失败。**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_research_model.py -q`  
Expected: missing planner/classifier/limiter.

- [ ] **Step 3: 实现严格 JSON contract。**

Planner 一次输出最多 12 个独立短 query；必须解释本轮缺口并且不得输出绕过审核、规避追踪或落地页探测指令。Classifier 输入仅技术合格候选的公开文本、CTA、封面 URL 和代表帧 URL/bytes；固定返回：

```json
{"category_match":true,"category_confidence":0.91,"business_type":"...","creative_relevance_score":88,"public_performance_signal_score":72,"real_money_signal_score":0.84,"is_obviously_unrelated":false,"text_evidence":[],"visual_evidence":[],"public_signal_evidence":[],"public_risk_signals":[],"recommendation":"keep"}
```

`public_performance_signal_score` 的 prompt 必须声明它不是实际成本/转化。模型不能确认 Cloaking，只能输出 `suspected_public_mismatch` 等公开可见风险信号。低置信/证据冲突/最终边缘候选可复核一次，配置为 `reasoning.effort="medium"`。

- [ ] **Step 4: 用真实 CPA Gateway 做一次最小非广告内容验证。**

仅发送固定无敏感 JSON schema 提示，确认 `gpt-5.4-mini` + `reasoning.effort=none` 返回可解析 JSON；记录总耗时和响应模型版本，不写入代码库或日志中的密钥。

- [ ] **Step 5: 重跑模型测试。**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_research_model.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit。**

```powershell
git add backend/app/services/ad_research_model.py backend/app/core/config.py tests/test_ad_research_model.py
git commit -m "feat: add model-driven ad research evaluation"
```

### Task 7: 编排、自动补采、Top-N 和最终轮询结果

**Files:**
- Create: `backend/app/services/ad_research_orchestrator.py`
- Modify: `backend/app/worker/tasks.py`
- Modify: `backend/app/services/ad_research_service.py`
- Modify: `tests/test_ad_research_orchestrator.py`
- Modify: `tests/test_ad_research_api.py`

- [ ] **Step 1: 写失败编排测试。**

```python
async def test_orchestrator_collects_again_when_first_round_selects_fewer_than_target(fake_collector, fake_model):
    result = await orchestrator.run(job(target_count=25))
    assert fake_collector.calls == 2
    assert result.status == "completed"
    assert len(result.ads) == 25
```

```python
async def test_orchestrator_returns_insufficient_without_padding_when_budget_exhausted(fake_collector, fake_model):
    result = await orchestrator.run(job(target_count=25, max_rounds=2))
    assert result.status == "insufficient"
    assert len(result.ads) < 25
    assert result.summary["reason"] == "insufficient_qualified_ads"
```

- [ ] **Step 2: 运行测试，确认失败。**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_research_orchestrator.py tests/test_ad_research_api.py -q`  
Expected: missing orchestrator.

- [ ] **Step 3: 实现轮次状态机。**

每轮顺序固定：模型规划 query → 每 query 收集最多 50 条 → ID 去重 → 技术资格 → 先封面初筛 → 只对高潜力/边界候选抽取视频代表帧 → 模型批分类/评分 → 稳定排序。若合格 Top-N 不足，给模型提供**汇总缺口**而非原始淘汰广告，最多 4 轮、最多 500 个原始候选。超过预算返回 `insufficient`，绝不用低置信广告凑数。

完成结果中的每条广告至少有：`ad_library_id`、`advertiser_name`、`ad_snapshot_url`、`text`、`cta_text`、`video_url`、`thumbnail_url`、`duration_seconds`、`active_days`、模型分数与证据。结果中不得有 Collector SQLite 路径、内部 URL、令牌、原始模型 prompt 或原始响应。

- [ ] **Step 4: 跑编排/API 测试。**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_research_orchestrator.py tests/test_ad_research_api.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit。**

```powershell
git add backend/app/services/ad_research_orchestrator.py backend/app/worker/tasks.py backend/app/services/ad_research_service.py tests/test_ad_research_orchestrator.py tests/test_ad_research_api.py
git commit -m "feat: complete ad research orchestration"
```

### Task 8: 端到端验证、测试服务器部署和外部接口文档

**Files:**
- Modify: `docs/外部系统投放数据分析对接说明.md`（仅新增广告研究章节，不覆盖用户现有内容）
- Create: `docs/ad-research-api.md`
- Modify: `README.md`（仅运行配置章节）

- [ ] **Step 1: 编写 API 文档的固定示例。**

```http
POST /api/v1/integrations/ad-research/jobs
Authorization: Bearer <token>
Content-Type: application/json

{"external_user_id":"research-20260721-001","country":"IN","category":"gambling","keywords":["rummy"],"target_count":25}
```

文档必须写明：`external_user_id` 不是长期用户 ID、轮询等待建议、24 小时有效期、`410` 的含义、无法获得实际 CPA/ROAS/spend，以及只研究公开材料的边界。

- [ ] **Step 2: 执行完整自动化测试。**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check backend tests
.venv\Scripts\python.exe -m pytest
cd frontend\web-admin; npm run build
```

Expected: all PASS; 不以先前存在的用户未提交改动作为本功能变更。

- [ ] **Step 3: 本地 production-like Docker 验证。**

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.production ps
curl.exe -i http://127.0.0.1/api/v1/health/live
```

确认 `backend`、`redis`、`worker_ad_research`、`meta_ads_collector` 健康；验证创建一次任务、重复 POST 幂等、轮询进入 `processing`、在受限 live 环境完成或明确 `insufficient`，再验证手动设置过期的任务返回 `410`。

- [ ] **Step 4: 测试服务器部署前检查。**

在 `/www/wwwroot/advertising-automation` 执行：

```bash
git status --short
git fetch origin
git pull origin main
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
curl -i https://ai.ggcss.xyz/api/v1/health/live
```

只在确认数据库 migration 和 `MODEL_GATEWAY_BASE_URL` 已存在后部署。不得把 `.env.production`、真实 Token/Key、CPA 凭据或 Collector 临时媒体提交。

- [ ] **Step 5: 最终 scoped commit。**

```powershell
git status --short
git add <仅本计划新建和修改的文件>
git commit -m "feat: add public ad research engine"
```

提交前明确确认 `AGENTS.md`、`.env*`、用户既有未提交文件、`runtime-*`、`tmp_*` 均未进入暂存区。

---

## 验收清单

- [ ] 外部可创建、重复创建和轮询一个研究任务；幂等和冲突语义正确。
- [ ] `country` 原样传到 Collector，后台没有印度/博彩固定词包。
- [ ] Collector 与当前 `athm793/meta-ads-scraper` commit 的字段与 SSE contract 已验证；其 SQLite 不被用作长期数据源。
- [ ] 只有技术合格的视频候选进入模型；所有最终视频时长 `<=30s`、投放天数 `>2`。
- [ ] 模型使用 `gpt-5.4-mini`，默认 `reasoning.effort=none`；Redis 令牌实现全局模型并发上限 6。
- [ ] 不足 25 条时自动换词补采；耗尽上限后返回 `insufficient`，不凑数。
- [ ] 最终结果可轮询 24 小时；清理后 `410` 且不存在完整广告结果、临时媒体和中间模型明细。
- [ ] 未实现落地页 IP/设备探测、Cloak 复现、审核规避或真实成本推断。
