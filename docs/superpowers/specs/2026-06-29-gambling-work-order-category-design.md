# 博彩类工单类别与印度激进风格包设计

## 背景

当前工单确认流程已经会确认投放链接、国家、事件、人群等参数，并在后端生成统一的
`creative_strategy`，供选题、文案、图片、分镜和视频继续使用。现有策略主要区分游戏类
和电商类，但实际投放里存在一批被归到游戏类的博彩工单。它们按普通游戏策略生成时，
画面刺激不足、转化词不足、国家风格不够强，导致 CTR 表现不理想。

这次新增的方向是：在确认投放参数时增加“工单类别”下拉框，选项为“游戏类、 电商类、
博彩类”。当运营选择“博彩类”时，系统默认使用激进的博彩广告生成策略。第一版重点把
“博彩类 + 印度”做深，形成印度神话史诗、高刺激奖励反馈和尾页强转化的专用风格包。

## 目标

1. 让运营可以在确认投放参数时显式选择“博彩类”。
2. 让“博彩类”默认进入激进模式，不再额外要求运营切换保守或激进开关。
3. 当“博彩类 + 印度”同时成立时，触发印度博彩风格包。
4. 让该类别和风格包进入 `creative_strategy`，并贯穿选题、文案、图片、分镜和视频。
5. 支持首页出现 `VIP`，尾页优先出现 `GET FREE 200/500`、`BIG WIN`、`FORTUNE GEMS`、
   倍率等强转化文字。
6. 保留后续扩展其他国家博彩风格包的结构，而不是把博彩类写死为印度。

## 非目标

1. 不在本轮实现代码。
2. 不改变现有鉴权、外部入口、回跳、回传接口。
3. 不要求外部投放系统第一版新增字段；浏览器人工确认流程先承担类别确认。
4. 不把所有游戏工单自动当成博彩工单。
5. 不用模型自由猜测是否博彩；自动识别只用于预选，最终以运营确认值为准。

## 方案选择

推荐采用“人工确认优先，自动识别辅助”的方案。

### 方案 A：只靠自动识别

系统根据链接、工单文本、落地页内容自动判断游戏、电商或博彩。优点是运营少操作；缺点
是误判成本高。博彩和游戏边界很近，如果误把普通游戏当博彩，会让素材风格过激；如果误
把博彩当游戏，又回到当前问题。

### 方案 B：下拉框确认 + 自动预选

系统先用规则预选类别，运营在确认投放参数时可以修改。提交后以后端收到的确认值为准。
这是推荐方案。它符合当前“确认投放参数”的人工复核流程，也能把高风险类别的责任边界做
清楚。

### 方案 C：类别 + 风险模式双开关

下拉框选择“博彩类”后，再额外选择保守或激进。这个方案灵活，但会增加运营判断成本。已
确认产品决策为：选择“博彩类”即默认激进，所以第一版不做额外风险模式开关。

## 核心触发规则

触发印度博彩风格包必须同时满足：

```text
工单类别 = 博彩类
投放国家 = 印度 / India / IN
```

后端归一化后的策略字段建议如下：

```json
{
  "work_order_category": "gambling",
  "risk_mode": "aggressive",
  "country_style_pack_id": "IN_gambling_mythic_aggressive"
}
```

分类矩阵：

```text
博彩类 + 印度 -> 印度博彩激进风格包
博彩类 + 其他国家 -> 博彩基础激进包，后续按国家扩展
游戏类 + 印度 -> 印度游戏风格包，不使用博彩尾页词
电商类 + 印度 -> 印度电商风格包
```

## 自动预选规则

自动预选只影响前端默认值，不覆盖运营最终选择。

可预选为“博彩类”的信号包括：

- 投放链接包含 `.game`，并且项目名或落地页出现明显博彩品牌或奖励语义。
- 工单或落地页出现 `VIP`、`FORTUNE`、`GEMS`、`BIG WIN`、倍率、宝箱、首充、充值、
  奖励、tokens 等信号。
- 项目名、落地页或素材文案出现类似 GAJA、FORTUNE GEMS 这类博彩游戏化品牌表达。
- 优化事件为首充、充值、注册后奖励、首次充值等。

如果自动识别到博彩信号，但国家不是印度，则预选“博彩类”，但不触发印度专属包，只进入
博彩基础激进包。

## `creative_strategy` 扩展

建议在 `creative_strategy.v2` 中增加：

```json
{
  "vertical": "gambling",
  "work_order_category": "gambling",
  "risk_mode": "aggressive",
  "country_style_pack_id": "IN_gambling_mythic_aggressive",
  "gambling_style_pack": {
    "country_code": "IN",
    "style_family": "mythic_epic_reward",
    "homepage_text_allowed": ["project_name", "VIP"],
    "end_page_text_allowed": ["GET FREE 200", "GET FREE 500", "BIG WIN", "FORTUNE GEMS", "500X"],
    "opening_visuals": [],
    "reward_feedback": [],
    "ending_cta_patterns": []
  }
}
```

现有链路里，`ad_generation_service._generate_result()` 是外部工单进入内部 `WorkOrder` 和
`Campaign` 的桥，后续 `topic_service`、`copywriting_service`、`creative_service` 和
`video_service` 会继续复用 `campaign.metadata_json`、`work_order`、`landing_page` 和
`creative_strategy`。因此类别和风格包应在工单创建或活动创建时写入 `creative_strategy`，
后面阶段只消费它，不重复判断。

## 印度博彩激进风格包

第一版风格包名称：

```text
IN_gambling_mythic_aggressive
```

视觉方向：

- 印度神话史诗感。
- 金色神殿、宫殿拱门、雷暴天空、宝石、光门、锁链、宝箱。
- 神祇拟像、史诗英雄拟像、神话 BOSS、翅膀守护者、蓝金或紫金怪物。
- 强 VFX、强背光、强透视、强镜头推进。
- 允许印度旗、印度建筑、节庆金色光效，但不要让画面只剩国旗或静态海报。

首页规则：

- 首页必须有东西，不做空白首屏。
- 首页固定优先出现项目名、`VIP`、神话 BOSS 或守护者、强光效。
- 首页可以有宝箱、光门、奖励暗示，但不建议把所有强转化词堆在首秒。

中段规则：

- 开宝箱、光门开启、倍率滚动、宝石爆发、金币或 token 反馈、BOSS 压迫、角色冲突。
- 重点是期待感和反馈感，而不是完整剧情。

尾页规则：

- 尾页优先放强转化词：`GET FREE 200`、`GET FREE 500`、`BIG WIN`、`FORTUNE GEMS`、
  `500X`、`VIP`。
- 尾页可配合按钮式 CTA、项目 logo、奖励爆发背景。

## 视频结构

参考素材以 7 到 10 秒为主，15 秒为次要形态。第一版默认使用短爆发结构。

7 到 10 秒模板：

```text
0-1s：项目名 + VIP + 神话 BOSS/守护者登场，雷电、金光、宫殿、强压迫感。
1-4s：宝箱、光门、BOSS 冲突、倍率滚动或奖励机制出现。
4-6s：奖励爆发，宝石、金币、500X、BIG WIN 等反馈增强。
最后 1-2s：尾页大字 GET FREE 200/500、FORTUNE GEMS、VIP、CTA。
```

15 秒模板：

```text
0-2s：神话 BOSS 和 VIP 首屏钩子。
2-6s：角色进入神殿或战场，面对 BOSS 或宝箱。
6-10s：奖励触发，倍率、光门、宝石、爆炸反馈。
10-13s：BIG WIN 或 FORTUNE GEMS 强化。
13-15s：GET FREE 200/500 尾页 CTA。
```

## 图片结构

博彩类图片建议服务于视频首帧、关键帧或尾页，而不是只生成普通静态海报。

三类图片 brief：

1. 首页首帧：项目名、`VIP`、神话 BOSS、金色宫殿、雷电、强压迫感。
2. 中段关键帧：宝箱开启、BOSS 冲突、光门、倍率或宝石爆发。
3. 尾页转化帧：`GET FREE 200/500`、`BIG WIN`、`FORTUNE GEMS`、`VIP`、CTA。

## 宗教和史诗表达边界

产品决策允许博彩类激进模式使用：

- 神祇原型拟像。
- 史诗英雄拟像。
- 宗教仪式感光效。
- 经文感纹样。
- 祈祷感构图。
- 《摩诃婆罗多》《罗摩衍那》的风格灵感。

为了保持模型可控，提示词层建议默认写成“deity-like boss”、“mythic guardian”、
“scripture-like glowing patterns”、“Indian epic-inspired fantasy scene”等表达，而不是要求
模型逐字输出真实神名、真实经文或真实宗教文本。画面可以像真实神话，但生成指令应尽量
使用“拟像”和“灵感”语言。

## 前端行为

在“确认投放参数”弹窗中增加“工单类别”下拉框：

```text
游戏类
电商类
博彩类
```

默认值：

- 有明显博彩信号时预选“博彩类”。
- 有电商信号时预选“电商类”。
- 有游戏信号但无博彩信号时预选“游戏类”。
- 判断不清时默认“游戏类”或沿用现有默认策略，但必须允许运营修改。

提交时，把确认后的类别放入 `structured_fields` 或 `metadata_json`，后端归一化为
`work_order_category`。

## 后端数据流

建议数据流：

1. 前端确认参数，提交 `work_order_category`。
2. 后端创建 `WorkOrder`，保存原始工单、LLM 识别字段、人工确认字段和类别。
3. 后端创建 `Campaign` 时构建 `creative_strategy`。
4. 如果 `work_order_category=gambling` 且 `country=IN`，写入印度博彩激进风格包。
5. 选题读取 `creative_strategy.topic_angle_plan` 和 `gambling_style_pack`。
6. 文案读取 `copy_guidance`，避免按普通游戏文案生成。
7. 图片 brief 读取首页、中段、尾页三类画面结构。
8. 分镜和视频读取同一份策略，按 7-10 秒或 15 秒模板组织。

## 错误和降级

- 类别缺失：按现有自动分类逻辑降级到游戏或电商，但在 review warnings 中提示“未确认工单类别”。
- 类别为博彩但国家缺失：进入博彩基础激进包，同时提示“国家缺失，未启用国家风格包”。
- 类别为博彩但国家不是印度：进入博彩基础激进包，不套用印度元素。
- 国家为印度但类别不是博彩：不触发博彩尾页词，只走对应类别的印度本地化策略。
- 生成策略构建失败：沿用当前生成流程，并在 metadata 中记录策略构建失败原因。

## 测试建议

后端单元测试：

- `work_order_category=gambling` 且 `country=India` 时，生成
  `country_style_pack_id=IN_gambling_mythic_aggressive`。
- `work_order_category=gambling` 且 `country=Brazil` 时，不生成印度风格包。
- `work_order_category=game` 且 `country=India` 时，不出现 `GET FREE`、`BIG WIN`、
  `FORTUNE GEMS` 的博彩尾页策略。
- 自动预选识别到 `VIP`、`FORTUNE GEMS`、`BIG WIN`、首充、充值等信号时，默认类别为博彩。
- 运营手动选择类别后，后端以人工确认类别为准。

前端测试：

- 确认投放参数弹窗显示工单类别下拉框。
- 博彩信号工单默认预选“博彩类”。
- 用户改成“游戏类”后提交，后端收到游戏类。

集成测试：

- 从创建工单到选题、文案、图片、分镜、视频，metadata 中能追踪到同一份
  `creative_strategy` 和 `country_style_pack_id`。
- 博彩类印度工单的视频分镜包含首页 `VIP`、中段奖励反馈、尾页 `GET FREE 200/500` 或
  `BIG WIN` 类结构。

## 实施边界

第一版建议只实现：

1. 工单类别下拉框。
2. 类别字段保存和向 `creative_strategy` 传递。
3. 博彩类默认激进。
4. 印度博彩激进风格包。
5. 视频和图片 prompt 对首页、中段、尾页结构的消费。

后续再扩展：

- 巴西、墨西哥、印尼、马来等国家博彩风格包。
- 按投放平台或素材审核结果动态调整风险强度。
- 把参考素材管理成可配置的素材风格库。
