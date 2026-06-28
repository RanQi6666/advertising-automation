# 全链路 Creative Strategy 设计

## 背景

当前系统已经在选题、文案、图片、分镜和视频阶段传递 `creative_strategy`，但现有策略更偏向特定 GAJA/小游戏场景。后续工单会持续变化，且主要分为游戏类和电商类，因此策略层需要升级为通用的内部创意上下文。

本设计只增强内部生成逻辑，不修改外部系统接口、鉴权、回跳、工单创建入参或现有 API 路由。外部系统仍按当前方式传工单和落地页信息。

## 目标

1. 用国家、人群、落地页、投放链接、当前日期生成统一的 `creative_strategy`。
2. 让 `creative_strategy` 同时贯穿选题、文案、生图、创意脚本/分镜和生视频。
3. 让 3 个选题天然来自不同角度，而不是同质化生成。
4. 视频脚本按前端传入的 `duration_seconds` 自适应，不写死 30 秒结构。
5. 保持 Meta/Facebook 合规：人群画像只做内部策略，不在用户可见文案里直接指认敏感或个人属性。

## 非目标

1. 不修改外部系统调用方式。
2. 不要求外部系统新增字段。
3. 不把 GAJA、小游戏或任何单一品牌写成默认策略。
4. 不让大模型凭空猜测实时热点；缺少可靠来源时只使用已知节日、季节性节点和落地页事实。

## 策略结构

建议统一结构如下，存入 `campaign.metadata_json.creative_strategy`，并继续向 topic、draft、creative asset、video metadata 传递：

```json
{
  "schema_version": "creative_strategy.v2",
  "vertical": "game | ecommerce | unknown",
  "classification": {
    "source": "landing_url | landing_page | work_order | mixed",
    "confidence": 0.0,
    "evidence": []
  },
  "market_context": {
    "country": "Singapore",
    "country_code": "SG",
    "language": "English",
    "culture_notes": [],
    "buying_power": "high | medium | value_sensitive | unknown",
    "religion_or_customs": [],
    "nearby_holidays": [],
    "local_seasonal_hooks": [],
    "local_trend_notes": []
  },
  "audience_lens": {
    "gender": "Female",
    "age_range": "25-34",
    "life_stage": [],
    "pain_points": [],
    "buying_motivations": [],
    "expression_style": [],
    "compliance_notes": []
  },
  "topic_angle_plan": [
    {
      "slot": 1,
      "angle_type": "challenge_failure | pain_point | scenario_resonance",
      "purpose": "What this topic should test",
      "avoid_repeating": []
    }
  ],
  "copy_guidance": {
    "tone": [],
    "benefit_language": [],
    "cta_style": [],
    "forbidden_claims": []
  },
  "image_guidance": {
    "visual_hooks": [],
    "composition_rules": [],
    "must_avoid": []
  },
  "video_guidance": {
    "duration_adaptive": true,
    "short_video_rules": [],
    "medium_video_rules": [],
    "long_video_rules": [],
    "beats_by_vertical": []
  },
  "compliance_guardrails": []
}
```

## Vertical 判断

`vertical` 优先通过落地页和投放链接判断，其次参考工单文本：

- 游戏类信号：game、play、level、challenge、reward、character、battle、puzzle、slot、casino-like game wording、app/game lobby 等。
- 电商类信号：price、discount、cart、shop、product、shipping、COD、reviews、before/after、skincare、fitness、home goods 等。
- 判断不清时用 `unknown`，生成策略采用更保守的通用广告结构，并在 `classification.evidence` 里记录原因。

GAJA 或其他品牌只作为品牌/落地页事实，不作为默认策略模板。

## 国家和人群逻辑

国家影响：

- 用户可见语言和本地表达。
- 文化、宗教习俗和禁忌。
- 消费能力和促销敏感度。
- 节日、购物季、季节性节点。
- 广告审美和可信度表达方式。

人群影响：

- 痛点。
- 购买或点击动机。
- 表达方式。
- 画面角色和场景选择。
- Hook 的节奏和强度。

年龄段可作为默认启发：

- 18-24：颜值、潮流、社交、好玩、新鲜感。
- 25-34：效率、工作压力、生活品质、性价比、快速反馈。
- 35-50：家庭、健康、稳定、可信度、长期价值。

这些启发只用于内部策略。面向用户的文案不能直接写成“你是 25-34 岁女性”或暗示敏感个人属性。

## 时间、节日和热点

策略生成时使用当前日期和国家生成 `market_context.nearby_holidays`。

节日和购物季建议优先来自维护表或稳定数据源，例如新加坡、越南、泰国、印尼、马来、印度、巴西、墨西哥、美国等常见投放国家。时间窗口建议分为未来 7 天、30 天、60 天。

当地热门话题只有在接入可靠搜索/API 时才进入 `local_trend_notes`。如果没有可靠来源，模型提示中必须说明不要编造热点。

## 选题阶段

选题生成继续默认 3 个，但必须由 `topic_angle_plan` 控制差异化。

游戏类建议从这些角度中挑选不重复的 3 个：

- 挑战失败型：过不了关、倒计时、差一点成功。
- 猪队友型：错误选择、离谱操作、让用户想纠正。
- 逆袭型：从弱到强、装备成长、翻盘。
- 爽感展示：奖励爆发、连击、升级、爆装备。
- 对比型：普通玩家 vs 高手玩家。

电商类建议从这些角度中挑选不重复的 3 个：

- 痛点型：明确问题和焦虑，但不夸大。
- Before/After：只做合规表达，避免保证效果。
- 场景共鸣：工作、家庭、通勤、加班、天气等。
- 种草型：本地趋势或社交证明，必须有事实边界。
- 限时优惠型：折扣、包邮、组合价、试用等。

如果 3 个选题角度重复，视为生成质量不合格，应要求模型重试或在后处理阶段标记。

## 文案阶段

文案阶段继承所选 topic 和完整 `creative_strategy`：

- 根据 `market_context.language` 控制用户可见语言。
- 根据 `audience_lens` 调整语气和利益表达。
- 根据 `vertical` 控制文案节奏：游戏短促、刺激、行动驱动；电商清楚说明痛点、解决方案、价值和信任。
- 根据 `compliance_guardrails` 改写风险表达，避免保证效果、夸大收益、敏感属性直呼。

## 生图阶段

图片 brief 生成时使用同一份策略：

- 游戏类：失败瞬间、选择冲突、奖励弹出、高手对比、强反馈画面。
- 电商类：真实使用场景、产品质感、痛点场景、合规前后对照、可信布局。
- 国家和人群影响画面风格、模特/角色场景、色彩和使用环境，但避免刻板化或宗教文化冒犯。

图片模型最终 prompt 只接收压缩后的安全策略摘要，避免把过长工单或内部敏感判断直接塞入图像提示。

## 创意脚本/分镜阶段

分镜不写死为 30 秒结构。前端传入的 `duration_seconds` 是唯一时长依据。

建议按时长自适应：

- 6 秒左右：一个强 Hook，一个核心刺激点或痛点，一个 CTA。
- 10-12 秒：Hook，冲突或展示，结果或利益，CTA。
- 15 秒左右：Hook，失败/痛点，正确玩法/解决方案，奖励/利益，CTA。
- 30 秒左右：完整叙事，可展开多段失败、转折、证明和 CTA。

游戏类分镜围绕挑战、失败、正确玩法、奖励反馈组织。电商类分镜围绕痛点、产品出现、使用过程、利益展示和 CTA 组织。

## 生视频阶段

视频生成继续使用前端选择的素材、脚本、时长和比例。内部在视频 prompt 中追加压缩后的 `creative_strategy` 摘要：

- `vertical`
- 核心 Hook 类型
- 国家/语言/文化注意点
- 人群表达方式
- 视觉节奏
- 合规禁用项

视频 provider 的调用参数和外部接口保持不变。

## 数据流

1. 外部系统创建工单，现有接口不变。
2. 系统保存 `WorkOrder` 和 `Campaign`，现有字段不变。
3. 内部根据工单、落地页、国家、人群、当前日期构建 `creative_strategy`。
4. 选题生成读取 `creative_strategy.topic_angle_plan`，输出 3 个不同角度。
5. 文案生成读取 topic、campaign、landing page、`creative_strategy`。
6. 生图读取 draft 和 `creative_strategy.image_guidance`。
7. 分镜读取 campaign、draft、素材、`duration_seconds`、`creative_strategy.video_guidance`。
8. 生视频读取素材、prompt、storyboard 和压缩后的 `creative_strategy`。

## 错误和降级

- 落地页抓取失败：继续使用 URL、domain、工单文本和已有 metadata。
- vertical 判断低置信：使用 `unknown` 通用策略，并避免强行套游戏或电商模板。
- 国家不明确：语言按现有 target language 逻辑回退，不默认中文。
- 节日表缺失：不生成节日 hook。
- 热点不可用：不编造热门话题。
- 策略生成失败：沿用当前生成流程，只记录 metadata 中的缺失原因。

## 测试建议

后端单元测试：

- 不改变外部请求 schema 和 API 路由。
- `country=Singapore` 能生成市场语境和语言策略。
- `gender=Female, age=25-34` 能生成对应 audience lens。
- 游戏落地页识别为 `game`，电商落地页识别为 `ecommerce`。
- 3 个选题的 `angle_type` 不重复。
- 不同 `duration_seconds` 生成不同长度的分镜结构。
- 落地页抓取失败时仍能生成选题。

集成测试：

- 从工单创建到选题、文案、生图、分镜、生视频的 metadata 中都能追踪到同一份 `creative_strategy.schema_version`。
- 外部创建工单接口、鉴权和回跳行为保持兼容。

## 实施边界

推荐先实现内部策略构建和 prompt 接入，不改 UI 操作流程。前端仍控制生成 3 个选题、视频时长和后续生成按钮。后续如果需要展示策略解释，可以作为单独迭代处理。
