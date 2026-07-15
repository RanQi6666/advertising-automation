# Facebook 广告分析运营结果精简设计

日期：2026-07-14
状态：已确认，进入实施
适用结果版本：`facebook_ad_analysis_v1`

## 1. 目标

在不修改现有异步接口、任务 ID 规则和结果版本名称的前提下，将
`facebook_ad_analysis_v1` 从技术型完整分析报告调整为运营人员可直接理解和执行的决策结果。

结果必须优先回答：

1. 当前最主要的问题是什么；
2. 运营人员首先应该做什么；
3. 为什么需要这样做；
4. 修改后应该观察什么；
5. 哪些结论因数据不足暂时不能判断。

## 2. 不变范围

以下协议和处理流程保持不变：

- `POST /api/v1/integrations/ad-performance/analysis-jobs`；
- `GET /api/v1/integrations/ad-performance/analysis-jobs/{analysis_id}`；
- `external_request_id` 继续作为 POST 幂等键；
- `analysis_id` 继续作为 GET 查询键；
- 外部系统主动轮询，不增加回调；
- 外部系统只提供 `image_url` 或 `video_url`；
- 图片缩略图、视频缩略图和视频关键帧继续由本系统处理；
- Redis/Celery 独立分析队列和任务状态流程保持不变；
- 公开来源相似广告采集继续执行；
- 结果版本名称继续使用 `facebook_ad_analysis_v1`。

## 3. 核心设计原则

### 3.1 面向运营展示

外部系统只展示建议，不会自动修改 Facebook 广告设置。因此不返回机器执行型的
`recommended_changes`，而是用通俗中文说明问题、动作、依据和观察项。

### 3.2 不要求维度成效拆分

外部系统不需要提供按国家、年龄、性别、设备或版位拆分的成效数据。

系统可以分析当前定向设置是否值得关注，但不得把广告整体汇总表现错误归因到某个国家、
年龄、性别、设备或版位，也不得声称某个维度实际表现更好。

缺少拆分数据时，定向建议应优先使用“复制广告组进行小预算测试”，而不是直接修改原广告组。

### 3.3 同一结论只出现一次

- 总体问题放在 `summary` 和 `overall_decision.main_problem`；
- 具体操作统一放在 `adjustment_plans`；
- 定向设置问题放在 `targeting_analysis`；
- 文案和素材的详细改法分别放在对应分析模块；
- 数据不足只放在 `data_gaps`；
- 不保留重复的全局建议、实验计划和技术指标树。

### 3.4 规则事实优先

花费、曝光、点击、落地页浏览、购买和其他 Facebook 指标继续由确定性规则层计算。
大模型负责将规则事实、素材分析和公开来源研究整理成运营语言，但不得改写真实指标或伪造依据。

## 4. 最终结果结构

`data.result` 成功结果严格包含以下字段：

```json
{
  "schema_version": "facebook_ad_analysis_v1",
  "platform": "facebook",
  "summary": "",
  "overall_decision": {},
  "targeting_analysis": [],
  "adjustment_plans": [],
  "copywriting_analysis": {},
  "media_analysis": {},
  "market_intelligence": {},
  "data_gaps": []
}
```

不返回上述字段之外的旧版分析树。

## 5. 字段定义

### 5.1 `summary`

类型：`string`

规则：

- 最多 100 个字符；
- 直接说明最主要的问题和首要动作；
- 不堆砌 CTR、CPC、CPM 等指标名；
- 不描述模型推理过程；
- 禁止“持续优化”“关注数据”等无具体含义的空话。

### 5.2 `overall_decision`

```json
{
  "action": "optimize",
  "priority": "high",
  "main_problem": "点击进入落地页后的流失较高"
}
```

字段约束：

- `action`：`scale | optimize | monitor | pause`；
- `priority`：`high | medium | low`；
- `main_problem`：一句话说明当前最主要的问题。

不返回 `should_pause`、`can_scale` 和 `confidence`。

### 5.3 `targeting_analysis`

类型：数组，最多 3 条。没有值得关注的定向问题时返回 `[]`。

```json
{
  "dimension": "age",
  "current": "18至65岁",
  "decision": "test",
  "problem": "当前年龄范围较宽，可能导致预算分散。",
  "suggestion": "保留原广告组，复制一个年龄范围更集中的广告组进行小预算测试。",
  "reason": "没有年龄段成效对比，因此不建议直接修改原广告组。"
}
```

字段约束：

- `dimension`：`country | audience | age | gender | device | placement`；
- `decision`：`adjust | test | monitor`；
- `current`：运营可读的当前设置文本；
- `problem`：当前设置值得关注的原因；
- `suggestion`：具体操作；
- `reason`：数据或策略依据。

只有真正值得关注的问题才进入数组。缺少某个普通字段但不影响当前主要结论时，不生成一条
“数据不足、暂不调整”的占位内容。

### 5.4 `adjustment_plans`

类型：数组，最少 1 条、最多 5 条，按 `high -> medium -> low` 排序。

```json
{
  "priority": "high",
  "category": "landing_page",
  "title": "检查点击到落地页的流失",
  "action": "检查移动端页面加载速度、广告链接跳转、重定向链路和 Meta Pixel 事件回传。",
  "reason": "广告产生86次链接点击，但只有23次落地页浏览，大量用户没有成功进入页面。",
  "expected_effect": "减少无效点击花费，提高有效落地页访问量。",
  "what_to_watch": "调整后观察成功进入落地页的人数是否提高，以及购买或注册成本是否下降。"
}
```

字段约束：

- `priority`：`high | medium | low`；
- `category`：`landing_page | tracking | copywriting | media | targeting | budget | campaign_setup`；
- `title`：简短动作标题；
- `action`：运营人员可以执行的具体操作；
- `reason`：通俗的数据、素材或公开来源依据；
- `expected_effect`：预计改善的业务结果；
- `what_to_watch`：修改后需要观察的现象或业务结果。

第一条必须对应 `overall_decision.main_problem`。不返回旧字段 `scope` 和 `success_metric`。

### 5.5 `copywriting_analysis`

```json
{
  "summary": "当前文案具有挑战感，但核心玩法表达不够直接。",
  "problems": [],
  "suggestions": [],
  "recommended_primary_text": null,
  "recommended_headline": null,
  "recommended_description": null
}
```

规则：

- `summary` 最多 100 个字符；
- `problems` 最多 3 条；
- `suggestions` 最多 3 条；
- 推荐文案必须与原广告保持相同语言；
- 不得捏造产品能力、价格、优惠、收益或承诺；
- 当前文案没有明显问题或没有提供原始文案时，三个推荐字段统一返回 `null`，不用空字符串。

### 5.6 `media_analysis`

```json
{
  "media_type": "video",
  "summary": "视频开场较慢，核心挑战没有在前三秒体现。",
  "improvements": [
    {
      "location": "0至3秒",
      "problem": "没有立即展示最紧张的战斗画面。",
      "action": "把角色被敌人包围的画面提前到第一秒。"
    }
  ]
}
```

规则：

- `media_type`：`image | video`；
- `summary` 最多 100 个字符；
- `improvements` 最多 3 条；
- 每一条问题必须和一个具体修改动作一一对应；
- 图片使用“主视觉区域”“文字区域”等位置；
- 视频使用“0至3秒”“视频中段”“视频结尾”等位置；
- 没有明显问题时返回 `[]`；
- 素材下载或处理失败时，不猜测画面内容，并在 `data_gaps` 中说明。

### 5.7 `market_intelligence`

```json
{
  "status": "completed",
  "summary": "公开来源中的相似广告常使用生存挑战和升级反馈作为创意钩子。",
  "references": [
    {
      "advertiser_name": "参考广告主名称",
      "source_url": "https://example.com/reference-ad",
      "observed_pattern": "开场直接展示角色被大量敌人包围。",
      "applicable_idea": "将最紧张的战斗画面提前到前三秒。"
    }
  ],
  "limitation": "公开来源只能用于参考广告内容和创意模式，无法验证真实花费、购买量或ROAS。"
}
```

字段约束：

- `status`：`completed | partial | unavailable`；
- `summary` 最多 100 个字符；
- `references` 最多 3 条；
- 每条参考只包含 `advertiser_name`、`source_url`、`observed_pattern`、`applicable_idea`；
- 不返回相似度、置信度、采集时间、代理指标和技术元数据；
- 不得把公开来源描述为已验证的高成效广告；
- 公开来源不足或采集失败时，返回 `unavailable` 和空数组，而不是制造参考内容。

### 5.8 `data_gaps`

类型：中文字符串数组，最多 3 条，没有关键缺口时返回 `[]`。

每条必须同时说明：

1. 缺少什么；
2. 影响什么判断。

只返回会实质影响当前结论的关键缺口，不罗列无关字段名、拆分维度、技术错误或内部异常。

## 6. 删除的公开字段

新的 `facebook_ad_analysis_v1` 不再公开返回：

```text
executive_summary
objective_alignment
performance_funnel
diagnoses
creative_analysis
audience_and_delivery_analysis
benchmark_comparison
recommended_actions
experiment_plan
data_quality
analysis_metadata
recommended_changes
region_change_required
region_analysis
country_suggestions
global_suggestions
```

这些内部分析事实仍可用于生成运营结果，但不再出现在最终外部响应中。

## 7. 数据流与职责

### 7.1 确定性规则层

继续负责：

- 解析 Facebook 汇总指标和 actions；
- 计算漏斗比率；
- 判断样本量和数据完整度；
- 确定主要瓶颈、总体动作和优先级；
- 为调整计划提供不可被模型改写的事实依据。

### 7.2 媒体处理层

继续负责下载图片或视频、生成缩略图和关键帧。只将可公开的媒体处理状态和模型视觉结论用于
最终结果，不公开本地文件路径。

### 7.3 公开来源研究层

继续采集相似广告参考，但最终只输出最多 3 条运营可用信息。研究层失败不导致整个分析任务失败。

### 7.4 大模型层

大模型输出应直接面向新的运营结果结构，重点生成：

- 简洁总体结论；
- 值得关注的定向设置；
- 具体调整计划；
- 文案分析与必要时的推荐文案；
- 素材问题与对应修改动作；
- 公开参考的创意规律；
- 关键数据缺口。

模型输出不完整、字段无效或调用失败时，组装层必须使用规则事实生成合法的降级结果。

### 7.5 最终组装层

最终组装层负责：

- 用规则事实覆盖模型可能冲突的总体结论；
- 限制数组数量和文本长度；
- 去重、排序和类别归一化；
- 将旧版模型输出兼容映射为新结构，避免模型供应商短期缓存或降级格式导致任务失败；
- 严格校验 `facebook_ad_analysis_v1`；
- 只在校验成功后持久化并返回结果。

## 8. 降级与异常处理

### 8.1 大模型失败

任务继续成功，由规则层生成：

- `summary`；
- `overall_decision`；
- 至少 1 条 `adjustment_plans`；
- 空或保守的文案、素材和定向分析；
- 关键 `data_gaps`。

### 8.2 媒体处理失败

- 不描述未实际看到的画面；
- `media_analysis.improvements` 返回 `[]`；
- `media_analysis.summary` 说明素材未能完成画面分析；
- `data_gaps` 增加一条运营可读的素材分析限制。

### 8.3 公开来源研究失败

```json
{
  "status": "unavailable",
  "summary": "本次没有找到足够可靠的相似广告参考，建议主要依据当前广告数据和素材进行优化。",
  "references": [],
  "limitation": "未使用来源不可靠或与当前广告关联度过低的公开内容。"
}
```

研究失败不改变任务的 `succeeded` 状态。

### 8.4 数据不足

数据不足时使用 `monitor`、测试建议或检查事件回传，不得为了填满结构而生成具体定向修改值。

## 9. 兼容性

接口外层 envelope、任务状态和轮询协议完全兼容。`data.result` 内部结构属于原版本名称下的直接替换。

外部系统如果已经读取旧字段，需要同步改为读取新字段。旧结构与新结构不并行返回，否则会重新造成
响应过大和结论重复。

数据库中已经完成的历史任务可以保留原结果，不做批量迁移；新完成的任务返回新结构。

## 10. 测试与验收

至少覆盖以下场景：

1. 严格 Schema 只允许最终字段；
2. `summary` 和各数组长度限制生效；
3. 规则层总体动作不能被大模型篡改；
4. 第一条调整计划对应主要瓶颈；
5. 缺少落地页或购买事件时，生成检查事件回传的运营建议；
6. 没有维度拆分数据时，不声称某个国家、年龄、性别或设备实际表现更好；
7. 文案无须重写时，推荐文案字段返回 `null`；
8. 素材处理失败时不生成虚构画面描述；
9. 公开来源最多 3 条且不包含已验证成效声明；
10. 最终结果不包含本地媒体路径或内部技术元数据；
11. LLM 失败时仍能生成合法且至少包含一条调整计划的结果；
12. POST/GET 异步任务协议和现有幂等行为保持通过；
13. 外部系统对接文档中的所有 JSON 示例可解析；
14. Ruff、相关 pytest 和完整 pytest 通过。

## 11. 非目标

本次不做：

- 国家、年龄、性别、设备或版位成效拆分；
- 一键自动修改 Facebook 广告；
- 新增结果版本名称；
- 新增查询接口或回调接口；
- 修改媒体文件限制或 FFmpeg 处理规格；
- 迁移历史任务结果；
- 将公开相似广告描述为真实高成效广告。
