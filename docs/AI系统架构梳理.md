# AI系统架构梳理

# 全过程数据流向

## 外部系统点击 AI 创建

外部系统跳转到：

```Plain Text
https://ai.ggcss.xyz/work-orders/new?ai_access_token=<AI_ADS_ACCESS_TOKEN>
```

可选还可以带：

```Plain Text
external_order_id
return_url
callback_url
```

参数解释：

**这一阶段真正带走的数据**

```Plain Text
{
  "ai_access_token": "访问令牌",
  "external_order_id": "外部订单ID",
  "return_url": "浏览器回跳地址",
  "callback_url": "后端回调地址"
}
```

**注意**

`ai_access_token` 只负责鉴权，不是广告业务字段。真正进入广告生产流程的数据，是后面的：

```Plain Text
external_order_id
raw_content
structured_fields
delivery_extraction
```

## 运营填写/粘贴工单内容，并让 AI 解析投放参数

这一步对应你的图里：

```Plain Text
AI 系统创建工单
-> 规则解析参数 + AI 确认参数
```

更准确应该是：

```Plain Text
运营填写 raw_content
-> AI/规则解析 delivery_extraction
-> 人工确认 structured_fields
```

**第 2 步：运营填写/粘贴工单**

运营进入 AI 创建页面后，需要把外部投放需求粘贴进来。这个原始内容叫：

```Plain Text
raw_content
```

它是整个后续流程的源头。

示例：

```Plain Text
产品：某游戏 App
投放国家：美国
投放人群：18-35 岁男性
优化事件：完成注册
落地页：https://example.com
素材类型：视频广告
需求：突出新手奖励和快速上手
```

**rawcontent 参数解释**

`raw_content` 里可能包含这些业务信息：

**第 21 步：AI/规则解析投放参数**

运营点创建后，前端会先调用：

```Plain Text
POST /api/v1/work-orders/extract-delivery-fields
```

传入：

```Plain Text
{
  "raw_content": "原始工单内容"
}
```

系统会从 `raw_content` 里提取一批字段，结果叫：

```Plain Text
delivery_extraction
```

你可以理解为：

```Plain Text
AI 初步识别出来的投放参数
```

**deliveryextraction 里的核心参数**

每个字段一般还会带这些辅助信息：

**举例**

如果工单写了：

```Plain Text
投放美国，18-35 岁男性，目标完成注册
```

AI 可能解析成：

```Plain Text
{
  "country": {
    "value": "美国",
    "normalized_value": "US",
    "status": "detected"
  },
  "age_min": {
    "value": "18",
    "normalized_value": 18,
    "status": "detected"
  },
  "age_max": {
    "value": "35",
    "normalized_value": 35,
    "status": "detected"
  },
  "gender": {
    "value": "男性",
    "normalized_value": "male",
    "status": "detected"
  },
  "event_name": {
    "value": "完成注册",
    "normalized_value": "COMPLETE_REGISTRATION",
    "status": "detected"
  }
}
```

**第 22 步：人工确认参数**

AI 解析后，运营要确认或修改这些字段。确认后的结果叫：

```Plain Text
structured_fields
```

或者在后端里也会叫：

```Plain Text
reviewed_delivery_fields
```

你可以理解为：

```Plain Text
人工确认后的正式投放字段
```

这一步非常关键，因为后面系统不应该直接相信 AI 识别结果，而是应该以人工确认后的字段为准。

**structuredfields 参数解释**

**这一阶段的数据流**

```Plain Text
raw_content
  ↓
AI/规则解析
  ↓
delivery_extraction
  ↓
人工确认
  ↓
structured_fields / reviewed_delivery_fields
```

**这一步结束后，带到下一步的数据**

下一步要真正创建 AI 生成任务，所以会把这些数据打包：

```Plain Text
{
  "raw_content": "原始工单内容",
  "structured_fields": {
    "landing_url": "人工确认后的落地页",
    "event_name": "人工确认后的优化事件",
    "country": "人工确认后的国家",
    "age_min": "人工确认后的最小年龄",
    "age_max": "人工确认后的最大年龄",
    "gender": "人工确认后的性别",
    "audience_description_raw": "人工确认后的人群描述",
    "work_order_category": "人工确认后的工单类型"
  },
  "delivery_extraction": {
    "AI 初始识别结果": "用于追溯和对比"
  }
}
```

**一句话总结第 2 步**

```Plain Text
第 2 步不是生成广告，而是把一段杂乱的工单文本，变成后续 AI 和外部投放系统都能理解的结构化投放字段。
```

下一步就是：

```Plain Text
第 3 步：创建 AdGenerationJob，也就是正式进入 AI 生产任务
```



## 正式创建 AI 生成任务

继续讲**第 3 步：正式创建 AI 生成任务 ****`AdGenerationJob`**。

前面第 2 步已经完成了：

```Plain Text
原始工单 raw_content
-> AI 识别 delivery_extraction
-> 人工确认 structured_fields
```

接下来，系统会把这些数据正式打包成一个 AI 生产任务。

**第 3 步：创建 AdGenerationJob**

前端调用接口：

```Plain Text
POST /api/v1/integrations/publishing/ad-generation/jobs
```

这个接口的作用是：

```Plain Text
创建一条 AI 广告生成任务
```

你可以把 `AdGenerationJob` 理解为：

```Plain Text
一次完整广告生产流程的主任务单
```

后面所有选题、文案、图片、视频、最终回传，都会挂在这个任务下面。

**请求数据结构**

```Plain Text
{
  "external_order_id": "外部订单ID",
  "return_url": "浏览器回跳地址",
  "callback_url": "后端回调地址",
  "work_order": {
    "raw_content": "原始工单内容",
    "structured_fields": {},
    "delivery_extraction": {}
  },
  "preferences": {
    "creative_type": "image",
    "image_count": 1,
    "daily_budget": 5000
  }
}
```

**顶层参数解释**

**workorder 参数解释**

**structuredfields 常见字段**

**preferences 参数解释**

**后端创建任务后生成什么**

接口成功后，后端会创建：

```Plain Text
AdGenerationJob
```

并返回：

```Plain Text
{
  "job_id": "AI任务ID",
  "status": "queued 或 processing",
  "review_url": "审核页面地址"
}
```

参数解释：

**这一步的数据怎么流**

```Plain Text
external_order_id
return_url
callback_url
raw_content
structured_fields
delivery_extraction
preferences
        ↓
创建 AdGenerationJob
        ↓
生成 job_id
        ↓
进入 AI 生产流程
```

**这一步结束后，系统内部会准备两类数据**

第一类是任务控制数据：

```Plain Text
job_id
status
review_url
return_url
callback_url
external_order_id
```

用途：控制流程、回跳、回调、追踪外部订单。

第二类是广告生产数据：

```Plain Text
raw_content
structured_fields
delivery_extraction
creative_type
image_count
daily_budget
```

用途：生成投放字段、选题、文案、图片、视频。

**一句话总结第 3 步**

```Plain Text
第 3 步就是把“人工确认后的工单”正式登记成一个 AI 生产任务 AdGenerationJob，后面所有内容生产都围绕 job_id 继续推进。
```

下一步就是：

```Plain Text
第 4 步：后端根据 AdGenerationJob 创建内部 WorkOrder 和 Campaign，并生成第一版投放参数包
```



## 后端创建内部 WorkOrder 和 Campaign

**第 4 步：后端创建内部 WorkOrder 和 Campaign**

第 3 步创建了外部集成任务：

```Plain Text
AdGenerationJob
```

但它只是“总任务”。后端真正开始生产时，会把它拆成两个内部业务对象：

```Plain Text
WorkOrder：工单
Campaign：广告内容生产项目
```

你可以这样理解：

```Plain Text
AdGenerationJob = 对外任务壳
WorkOrder = 原始需求和字段识别
Campaign = 后续选题、文案、图片、视频都挂在这里
```

**第 4 步输入数据**

从上一阶段带过来的数据是：

**第 41 步：创建 WorkOrder**

后端会创建一条内部工单：

```Plain Text
WorkOrder
```

它保存的是“这个广告需求最原始、最可信的上下文”。

`WorkOrder` 里主要带这些数据：

这里有个优先级：

```Plain Text
人工确认字段 > AI 识别字段 > 原始文本规则解析
```

也就是说，如果 AI 识别错了，但人工改对了，后面应该以后者为准。

**第 42 步：创建 Campaign**

接着后端会基于 `WorkOrder` 创建：

```Plain Text
Campaign
```

这里的 `Campaign` 不是外部投放系统里的真实广告系列，而是 AI 系统内部的“内容生产项目”。

它会带这些数据：

后面这些东西都依赖 `campaign_id`：

```Plain Text
Topic
CopyDraft
CreativeAsset
VideoAsset
ReviewTask
```

所以从第 4 步开始，`campaign_id` 会成为内部生产主线 ID。

**第 43 步：生成第一版投放参数包**

创建完 `WorkOrder` 和 `Campaign` 后，系统会生成第一版：

```Plain Text
AdGenerationJob.result_payload
```

这时任务状态会进入：

```Plain Text
fields_review
```

意思是：

```Plain Text
字段已解析，需要运营确认投放参数
```

第一版 `result_payload` 里会包含：

```Plain Text
{
  "status": "fields_review",
  "external_order_id": "外部订单ID",
  "campaign_payload": {},
  "adset_payload": {},
  "creative_payload": null,
  "assets": {
    "images": [],
    "videos": []
  },
  "review": {},
  "metadata_json": {}
}
```

**campaignpayload 参数解释**

这是未来给外部系统创建“广告系列”的建议字段。

**adsetpayload 参数解释**

这是未来给外部系统创建“广告组”的建议字段。

**creativepayload 此时为空**

这一阶段还没有文案、图片、视频，所以：

```Plain Text
creative_payload = null
assets.images = []
assets.videos = []
```

这很正常。创意字段要等后面文案和素材生成后才会补齐。

**review 参数解释**

`review` 是给运营看的风险和缺失提示。

**metadatajson 参数解释**

这是内部追踪数据，不是外部投放主字段。

**第 4 步结束后的数据流**

```Plain Text
AdGenerationJob
  ↓
创建 WorkOrder
  ↓
创建 Campaign
  ↓
生成第一版 result_payload
  ↓
状态进入 fields_review
```

这一步之后，系统手里有三条关键 ID：

```Plain Text
job_id：对外任务主 ID
work_order_id：内部原始工单 ID
campaign_id：内部内容生产 ID
```

后面最常用的是：

```Plain Text
job_id：审核和回传用
campaign_id：选题、文案、图片、视频生成用
```

**一句话总结第 4 步**

```Plain Text
第 4 步就是把外部传来的工单，拆成 AI 系统内部可生产的 WorkOrder 和 Campaign，并生成第一版可审核的投放参数包。
```

下一步就是：

```Plain Text
第 5 步：人工确认投放参数，然后进入生成选题
```



## 人工确认投放参数

继续讲 **第 5 步：人工确认投放参数**。

这一步的核心不是生成素材，而是把“投放方向”确认下来。也就是：广告要投哪个国家、什么年龄、什么性别、什么事件目标、预算多少、受众是谁。确认错了，后面的选题、文案、图片、视频都会跟着错。

**第 5 步流程**

```Plain Text
初始 result_payload
  ↓
人工检查投放参数
  ↓
必要时修改 campaign_payload / adset_payload / review
  ↓
PATCH 保存到 AI 任务
  ↓
进入下一步：生成选题
```

对应接口是：

```Plain Text
PATCH /api/v1/integrations/publishing/ad-generation/jobs/{job_id}/review
```

请求里主要带：

这里要注意：这一步不是最终回传，最终回传用的是后面的 `/confirm`。第 5 步只是“保存当前审核阶段的数据”。

**这一步主要确认的数据**

**这一阶段还会带着审核信息走**

这一关过了以后，真正传给下一步的关键数据是：

```Plain Text
campaign_id
+ 已确认的国家/年龄/性别/事件/受众
+ campaign_payload
+ adset_payload
+ reviewed_delivery_fields
```

下一步就是 **第 6 步：根据确认后的投放参数生成选题**。这里开始进入内容创意阶段，AI 会围绕产品、受众、投放目标生成多个广告选题，然后人工选择一个继续生成文案。

### metadatajsoncreativestrategy这个不传给选题吗？

传的，准确说是 **间接传给选题**。

`metadata_json.creative_strategy` 不是由前端在生成选题接口里手动带过去，而是在创建内部 `Campaign` 时，后端已经把同一份 `creative_strategy` 写进了 `campaign.metadata_json`。后面生成选题只传 `campaign_id`，选题服务根据 `campaign_id` 读取 `Campaign`，再把 `campaign.metadata_json.creative_strategy` 放进 `signals.creative_strategy` 传给 LLM。

数据流是这样：

```Plain Text
工单参数 + 人工确认字段
  ↓
build_creative_strategy()
  ↓
写入 Campaign.metadata_json.creative_strategy
同时也写入 result_payload.metadata_json.creative_strategy
  ↓
生成选题时通过 campaign_id 读取 Campaign
  ↓
_topic_creative_strategy() 取出 creative_strategy
  ↓
signals.creative_strategy
  ↓
LLM 生成选题
  ↓
Topic.source_data.creative_strategy / topic_angle
```

关键点：

需要注意一个细节：如果你说的是 `AdGenerationJob.result_payload.metadata_json.creative_strategy`，它更像是任务结果包里的备份和最终回传上下文；真正生成选题时读取的是 `Campaign.metadata_json.creative_strategy`。所以如果后面人工只改了 `result_payload.metadata_json.creative_strategy`，但没有同步改 `Campaign.metadata_json.creative_strategy`，那选题阶段可能不会吃到这个修改。

### topicangleplan是从什么角度来选择的

`topic_angle_plan` 是按 **业务类型 vertical** 来选角度的，不是 AI 临时随便想的。

它的逻辑是：

```Plain Text
工单内容 / 人工确认参数
  ↓
判断业务类型 vertical
  ↓
根据 vertical 套一组固定的 3 个选题角度
  ↓
生成 3 个候选选题
  ↓
人工选择其中一个
```

**先判断业务类型**

系统优先看 `work_order_category`：

如果没有明确分类，就用关键词判断，比如：

如果都不明显，默认按 `ecommerce` 电商处理。

**不同业务类型对应的选题角度**

每个角度里有 3 个参数：

所以它的本质是：**让 3 个选题分别测试不同创意方向**。

比如游戏类不会让 3 个选题都写“奖励很多”，而是拆成：

```Plain Text
1. 卡关失败，引发好奇
2. 从弱到强，制造成长感
3. 奖励爆发，制造爽感
```

这样人工审核时就不是在 3 个差不多的标题里选，而是在 3 个不同投放假设里选。

## 生成选题  人工选择选题

继续讲 **第 6 步：生成选题  人工选择选题**。

这一阶段的目标是：**不是马上写文案，而是先确定广告创意方向**。系统会根据前面确认好的投放参数、工单信息、落地页、`creative_strategy.topic_angle_plan`，生成 3 个候选选题，然后人工选择一个。

流程是：

```Plain Text
campaign_id
+ 工单上下文
+ 投放参数
+ creative_strategy
+ topic_angle_plan
+ 落地页信息
  ↓
生成 3 个选题
  ↓
人工选择 1 个选题
  ↓
把 selected topic_id 传给下一步生成文案
```

**生成选题接口**

```Plain Text
POST /api/v1/topics/generate/stream
```

也有普通接口：

```Plain Text
POST /api/v1/topics/generate
```

前端现在更常用流式接口，因为可以一个个显示生成结果。

请求参数：

示例：

```Plain Text
{
  "campaign_id": "内部广告项目ID",
  "limit": 3,
  "signals": {},
  "model_id": null
}
```

这里要注意：虽然前端传的 `signals` 可能是 `{}`，但后端不会只用空对象。后端会根据 `campaign_id` 自动补齐很多有效信息。

**后端自动补齐的 signals**

其中最关键的是：

```Plain Text
signals.creative_strategy.topic_angle_plan
```

它会要求 AI 尽量按照 3 个不同角度生成 3 个选题。

**生成出来的 Topic 参数**

每一个选题会存成一条 `ContentTopic`。

`source_data` 里面也会继续保存这些信息：

**人工选择选题**

选中某个选题时调用：

```Plain Text
POST /api/v1/topics/{topic_id}/select
```

参数：

系统会把这个选题状态改成：

```Plain Text
selected
```

如果之前已有其他选题被选中，会把旧的选中状态取消，避免一个 Campaign 同时有多个主选题。

也可以拒绝某个选题：

```Plain Text
POST /api/v1/topics/{topic_id}/reject
```

**这一步传给下一步的数据**

第 6 步结束后，真正传给下一步“生成文案”的核心数据是：

```Plain Text
selected topic_id
+ campaign_id
+ topic.title
+ topic.angle
+ topic.audience
+ topic.selling_points
+ topic.source_data.creative_strategy
+ campaign.metadata_json.work_order
+ campaign.metadata_json.landing_page
```

下一步就是 **第 7 步：根据人工选择的选题生成文案**。
从这里开始，AI 不再是泛泛写广告语，而是围绕你选中的那个 `topic_id` 来生成广告文案。

### 还有一个国家风格包，在哪里呢？

有的，它不在数据库表里，也不是外部系统传来的独立参数，而是在后端代码里根据国家和业务类型生成。

主要位置是：

当前主线里，更重要的是这两个：

```Plain Text
creative_strategy.market_game_style_pack
creative_strategy.gambling_style_pack
```

如果是印度博彩，会额外生成：

```Plain Text
{
  "country_style_pack_id": "IN_gambling_mythic_aggressive"
}
```

位置在 creativestrategybuilderpy line 867C:/Users/panda/Documents/Advertising Automation/backend/app/services/creativestrategybuilderpy:867。

它的数据流是：

```Plain Text
人工确认国家 country
  ↓
转成 country_code
  ↓
生成 market_context
  ↓
根据 vertical 判断游戏/博彩/电商
  ↓
游戏：生成 market_game_style_pack
博彩：生成 gambling_style_pack
  ↓
写入 creative_strategy
  ↓
传给选题、文案、图片、视频脚本、视频生成
```

几个关键参数解释：

一句话：**国家风格包现在是** `creative_strategy` **里面的一部分，不是单独的一步；它在生成选题之前就已经生成好了，后面所有创意阶段都会带着它走。**

### 旧版 GAJA 游戏专用国家风格包什么时候会触发

会触发的条件分两层：

**第一层：当前主流程会不会触发？**
目前我查到的结果是：**当前主线广告生成流程基本不会主动触发旧版 GAJA 国家风格包**。

原因是旧版逻辑在：

gamecreativestrategypy line 43C:/Users/panda/Documents/Advertising Automation/backend/app/services/gamecreativestrategypy:43

里面的：

```Plain Text
build_game_creative_strategy()
```

但现在主流程用的是新版：

```Plain Text
creative_strategy_builder.build_creative_strategy()
```

也就是现在主流程更常用：

```Plain Text
creative_strategy.market_game_style_pack
creative_strategy.gambling_style_pack
```

旧版的：

```Plain Text
creative_strategy.country_style_pack
```

更多是保留给旧 GAJA 逻辑和历史 metadata 兼容。

**第二层：如果旧版函数被调用，它什么时候触发？**

旧版 GAJA 策略必须先满足这个条件：

```Plain Text
工单/产品/落地页里出现 GAJA 信号
```

具体包括：

代码位置：

gamecreativestrategypy line 566C:/Users/panda/Documents/Advertising Automation/backend/app/services/gamecreativestrategypy:566

如果没有 GAJA 信号，它直接返回：

```Plain Text
None
```

也就是不生成旧版 GAJA 策略。

**触发 GAJA 后，再判断是哪种模板**

代码位置：

gamecreativestrategypy line 49C:/Users/panda/Documents/Advertising Automation/backend/app/services/gamecreativestrategypy:49

**然后才生成国家风格包**

旧版国家风格包字段是：

```Plain Text
country_style_pack
```

它会根据国家判断：

代码位置：

gamecreativestrategypy line 276C:/Users/panda/Documents/Advertising Automation/backend/app/services/gamecreativestrategypy:276

所以完整触发链是：

```Plain Text
必须有人调用 build_game_creative_strategy()
  ↓
检测到 GAJA 信号
  ↓
判断是否小游戏流量池
  ↓
生成 gaja_brand 或 mini_game_pool 策略
  ↓
根据国家生成 country_style_pack
  ↓
写入 creative_strategy.country_style_pack
```

一句话总结：**旧版 GAJA 国家风格包只有在旧 GAJA 策略函数被调用，并且工单/产品/落地页里识别到 GAJA 时才会触发；当前主流程默认走新版* `market_game_style_pack`*，所以普通新工单一般不会走旧版* `country_style_pack`*。**

### 测试工单会触发吗？

会，但要分清 **“旧版函数内部会触发”** 和 **“当前主流程会不会用旧版”**。

如果单独看旧版 GAJA 逻辑，这个工单 **会触发旧版 GAJA 策略**：

所以如果调用旧函数：

```Plain Text
build_game_creative_strategy()
```

结果大概是：

```Plain Text
template_id = gaja_brand
country_style_pack.country_code = IN
country_style_pack.style_family = epic Indian-inspired CG game world
```

触发位置：

- GAJA 判断：gamecreativestrategypy line 566C:/Users/panda/Documents/Advertising Automation/backend/app/services/gamecreativestrategypy:566
- 国家风格包：gamecreativestrategypy line 276C:/Users/panda/Documents/Advertising Automation/backend/app/services/gamecreativestrategypy:276

但是，按 **当前主流程** 来说，正常广告工单现在走的是新版：

```Plain Text
creative_strategy_builder.build_creative_strategy()
```

不是旧版：

```Plain Text
game_creative_strategy.build_game_creative_strategy()
```

所以当前主流程大概率会得到：

```Plain Text
vertical = game
market_game_style_pack = 有
country_style_pack = 没有
gambling_style_pack = 没有，除非工单类别被确认成 gambling/博彩
```

这里还有一个关键点：你的工单里有 `投放事件：首充`。如果 AI 抽取或人工确认把：

```Plain Text
work_order_category = gambling / 博彩
```

那当前新版逻辑会走博彩分支，生成：

```Plain Text
gambling_style_pack
country_style_pack_id = IN_gambling_mythic_aggressive
```

如果没有确认成博彩，只靠 `GAJA777` 和 `.game` 域名，当前新版会把它判断成 `game`，走 `market_game_style_pack`。

一句话结论：**旧版 GAJA 国家风格包从条件上会命中这张工单，但当前主流程默认不会调用旧版；当前实际更可能触发新版印度游戏风格包，除非你把类别确认成博彩，才会触发印度博彩风格包。**

### 现在有那些风格包了？

1. **基础国家市场包 ****`COUNTRY_CONTEXT`**

位置：creativestrategybuilderpy line 57C:/Users/panda/Documents/Advertising Automation/backend/app/services/creativestrategybuilderpy:57

这是所有业务都会用的基础国家信息，不算强视觉风格包，但会影响语言、文化限制、市场语气。

目前支持：

1. **新版游戏风格包 ****`market_game_style_pack`**

位置：creativestrategybuilderpy line 725C:/Users/panda/Documents/Advertising Automation/backend/app/services/creativestrategybuilderpy:725

触发条件：

```Plain Text
vertical = game
```

它会根据国家、人群年龄、性别生成游戏广告方向。

目前主要分支：

它里面会带：

1. **新版博彩风格包 ****`gambling_style_pack`**

位置：creativestrategybuilderpy line 785C:/Users/panda/Documents/Advertising Automation/backend/app/services/creativestrategybuilderpy:785

触发条件：

```Plain Text
vertical = gambling
```

默认博彩包是：

```Plain Text
style_family = reward_stimulus
```

如果国家是印度，会升级成特殊包：

```Plain Text
country_style_pack_id = IN_gambling_mythic_aggressive
style_family = mythic_epic_reward
```

印度博彩包会带：

印度博彩还会生成 3 个视频变体：

1. **旧版 GAJA 专用风格包 ****`country_style_pack`**

位置：gamecreativestrategypy line 276C:/Users/panda/Documents/Advertising Automation/backend/app/services/gamecreativestrategypy:276

这是旧逻辑，当前主流程默认不走它，但代码还保留兼容。

触发条件：

```Plain Text
调用 build_game_creative_strategy()
并且识别到 GAJA / GAJA777 / gaja777.game
```

它有 3 个国家风格：

旧版 GAJA 还有两个模板：

所以总结一下：

```Plain Text
当前主线真正重要：
1. COUNTRY_CONTEXT
2. market_game_style_pack
3. gambling_style_pack

旧版兼容：
4. country_style_pack
```

你的 GAJA777 印度工单，如果没有明确标成博彩，当前主线更可能走：

```Plain Text
vertical = game
market_game_style_pack = 印度游戏风格
```

如果人工确认成：

```Plain Text
work_order_category = gambling / 博彩
```

就会走：

```Plain Text
gambling_style_pack
country_style_pack_id = IN_gambling_mythic_aggressive
```



## 根据已选选题生成文案  人工审核文案

继续讲 **第 7 步：根据已选选题生成文案  人工审核文案**。

流程是：

```Plain Text
人工已选择 topic_id
  ↓
POST /copywriting/generate
  ↓
读取 Topic + Campaign + 落地页 + creative_strategy
  ↓
AI 生成 CopyDraft 文案草稿
  ↓
人工审核：通过 / 拒绝 / 需修改
  ↓
通过后进入图片或视频脚本阶段
```

**生成文案接口**

```Plain Text
POST /api/v1/copywriting/generate
```

请求参数：

示例：

```Plain Text
{
  "topic_id": "选题ID",
  "constraints": {
    "cta": "Learn More"
  },
  "model_id": null
}
```

后端会自动补齐这些上下文：

生成结果是 `CopyDraft`：

**人工审核文案**

审核接口：

```Plain Text
POST /api/v1/reviews
```

请求参数：

审核结果影响：

**重写文案接口**

```Plain Text
POST /api/v1/copywriting/{draft_id}/revise
```

这一步结束后，传给下一步的核心数据是：

```Plain Text
approved draft_id
+ primary_text
+ headline
+ description
+ cta
+ topic_id
+ campaign_id
+ creative_strategy
+ landing_page
+ target_language
```

需要注意一个点：当前初版生成文案会把 `creative_strategy` 写进 `draft.metadata_json`；但我看到“按意见重写”的新版本文案，代码里没有显式把原来的 `creative_strategy` 复制到新草稿。后面如果用重写版文案直接生图，要确认策略有没有继承，否则图片阶段可能少吃一部分风格包信息。

代码位置：

- 文案接口字段：copywritingpy line 6C:/Users/panda/Documents/Advertising Automation/backend/app/schemas/copywritingpy:6
- 文案生成逻辑：copywritingservicepy line 27C:/Users/panda/Documents/Advertising Automation/backend/app/services/copywritingservicepy:27
- 人工审核状态更新：reviewservicepy line 66C:/Users/panda/Documents/Advertising Automation/backend/app/services/reviewservicepy:66

下一步就是：**文案通过后，分两条路：直接根据文案生成图片，或者先根据文案生成视频脚本，再生成视频关键帧图片。**

## 文案通过后生成素材

继续讲 **第 8 步：文案通过后生成素材**。

这一步有两条路：

```Plain Text
路径 A：文案 → 图片
路径 B：文案 → 视频脚本 → 关键帧图片 → 后面再生成视频
```

注意：**视频脚本不是替代文案**。真正的源头还是已审核通过的 `CopyDraft`，视频脚本只是给关键帧和视频生成提供画面结构。

**路径 A：直接根据文案生成图片**

接口：

```Plain Text
POST /api/v1/creatives/generate/stream
```

主要请求参数：

后端实际逻辑：

```Plain Text
draft_id
  ↓
读取 CopyDraft
  ↓
读取 draft.metadata_json.creative_strategy
  ↓
LLM 先生成图片 brief
  ↓
图片模型生成图片
  ↓
保存 CreativeAsset
```

生成结果 `CreativeAsset`：

图片生成后要人工审核：

```Plain Text
POST /api/v1/reviews
```

图片通过后，如果是图片广告，可以进入最终预审；如果还要做视频，也可以作为视频参考图。

**路径 B：先生成视频脚本，再生成关键帧图片**

第一步生成视频脚本：

```Plain Text
POST /api/v1/videos/storyboard
```

也有流式接口：

```Plain Text
POST /api/v1/videos/storyboard/stream
```

请求参数：

返回结果：

如果人工觉得脚本不好，可以重写：

```Plain Text
POST /api/v1/videos/storyboard/rewrite
```

关键参数多一个：

**根据视频脚本生成关键帧图片**

还是用图片生成接口：

```Plain Text
POST /api/v1/creatives/generate/stream
```

但参数变了：

关键帧会这样分组：

```Plain Text
方案 1：第 1 张 first_frame + 第 2 张 last_frame
方案 2：第 3 张 first_frame + 第 4 张 last_frame
方案 3：第 5 张 first_frame + 第 6 张 last_frame
```

每张关键帧图片会带这些元数据：

第 8 步结束后，传给下一步的数据是：

```Plain Text
approved creative_asset_ids
+ 图片 URL
+ keyframe_group
+ first_frame / last_frame
+ storyboard_text
+ storyboard
+ draft_id
+ campaign_id
```

下一步就是 **第 9 步：用审核通过的关键帧图片生成视频**。

## **用审核通过的图片/关键帧生成视频**

这一阶段分 3 个动作：

```Plain Text
已审核通过的关键帧图片
  ↓
创建 VideoAsset 视频任务
  ↓
启动视频模型生成
  ↓
刷新视频结果，拿到 video_url
  ↓
人工审核视频
```

**第一步：创建视频任务**

接口：

```Plain Text
POST /api/v1/videos/from-images
```

请求参数：

示例：

```Plain Text
{
  "campaign_id": "广告项目ID",
  "creative_asset_ids": ["首帧图片ID", "尾帧图片ID"],
  "draft_id": "文案ID",
  "prompt": "视频脚本文本",
  "duration_seconds": 12,
  "aspect_ratio": "9:16",
  "storyboard": [],
  "metadata_json": {}
}
```

这里最关键的是：

```Plain Text
creative_asset_ids
```

视频不是空手生成，它要带着审核通过的图片走。对于关键帧视频，通常是一组：

```Plain Text
first_frame + last_frame
```

也就是：

```Plain Text
方案 1：首帧图 + 尾帧图
```

如果只传一张，视频的稳定性和方向会弱很多；如果用火山/Seedance 这类 first/last frame 模式，通常最多只能传配置允许的参考图数量。

**创建后得到 VideoAsset**

返回参数：

注意：`POST /videos/from-images` 只是 **创建视频任务**，不一定马上生成完视频。

**第二步：启动视频生成**

接口：

```Plain Text
POST /api/v1/videos/{video_id}/generate
```

参数：

后端会做这些检查：

启动后会写入：

**第三步：刷新视频结果**

接口：

```Plain Text
POST /api/v1/videos/{video_id}/refresh
```

参数：

刷新时系统会向视频供应商查询状态。如果成功拿到视频 URL，会做两件事：

```Plain Text
供应商 video_url
  ↓
转存到系统配置的存储
  ↓
写入 VideoAsset.url 和 storage_key
```

刷新后关键字段：

**第四步：人工审核视频**

视频生成完成后，人工审核：

```Plain Text
POST /api/v1/reviews
```

参数：

审核通过后：

```Plain Text
VideoAsset.status = approved
workflow_stage = final_review
```

然后进入下一步：

```Plain Text
最终预审 + 组装最终回传包
```

第 9 步结束后，传给第 10 步的数据是：

```Plain Text
approved video_id
+ video.url
+ source_asset_ids
+ draft_id
+ campaign_id
+ prompt
+ storyboard
+ duration_seconds
+ aspect_ratio
+ creative_strategy
```

最重要的是：

```Plain Text
video.url
```

因为最终回传给外部系统创建视频广告时，需要用它作为视频素材地址。

## 最终预审包生成

继续讲 **第 10 步：最终预审包生成，也就是 ****`final_review`**。

这一阶段的作用是：把前面人工确认过的投放参数、选题、文案、图片/视频，合成一份外部投放系统能读取的“待投放草稿数据包”。代码入口在 Apptsx line 6374C:UserspandaDocumentsAdvertising AutomationfrontendwebadminsrcApptsx:6374 的 `buildFinalPayload()`。

**这一步的数据流**

```Plain Text
已确认投放参数
+ 已选中选题
+ 已审核通过文案
+ 已审核通过图片
+ 如果是视频广告，还要有已审核视频
        ↓
buildFinalPayload()
        ↓
生成 final_review 数据包
        ↓
运营检查 JSON
        ↓
下一步确认并回传 returned
```

**进入这一步必须满足的条件**

**最终包顶层字段**

`campaign_payload`** 广告系列数据**

`adset_payload`** 广告组数据**

`creative_payload`** 最终创意数据**

这里的核心规则是：**如果有视频，最终主素材优先用视频；如果没有视频，最终主素材就是图片。**
但是即使是视频广告，系统也会继续带着图片 URL，方便外部系统做封面、素材库入库或追溯。

**需要注意**

当前品牌安全有一个实现差异：文档说 blocked 时不应回传，但我核对代码后，前端只是展示“品牌安全未通过”的状态，`brandSafetyAllowsReturn()` 目前始终返回 true；后端确认接口也会扫描并写入 `review.brand_safety`，但仍然把任务状态改为 `returned`。这个点后面如果你想做严格拦截，需要单独修一下。

下一步就是 **第 11 步：运营点“确认并回传”，AI 系统把* `final_review` *变成* `returned`*，然后通知外部投放系统**。

## 确认并回传

继续讲 **第 11 步：确认并回传，也就是 ****`returned`**。

这一步的核心作用是：运营确认最终预审包没问题后，AI 系统把任务从 `final_review` 改成 `returned`，然后让外部投放系统知道“素材和广告草稿数据已经准备好了”。

代码主要在 Apptsx line 2512C:UserspandaDocumentsAdvertising AutomationfrontendwebadminsrcApptsx:2512 和 adgenerationservicepy line 415C:UserspandaDocumentsAdvertising Automationbackendappservicesadgenerationservicepy:415。

**流程怎么走**

```Plain Text
运营点击“确认并回传”
        ↓
前端调用 confirm 接口
        ↓
后端合并最终 result_payload
        ↓
后端执行品牌安全扫描
        ↓
任务状态改为 returned
        ↓
如果有 callback_url，POST 通知外部系统
        ↓
如果有 return_url，浏览器跳回外部系统页面
        ↓
外部系统 GET result_url 拉最终投放包
```

**前端调用的接口**

```Plain Text
POST /api/v1/integrations/publishing/ad-generation/jobs/{job_id}/confirm
```

**后端确认时会做什么**

**callback 回调给外部系统的数据**

如果创建任务时外部系统传了 `callback_url`，AI 后端会 POST 这个数据：

**returnurl 是另一条线**

`callback_url` 是后端通知外部系统。
`return_url` 是浏览器页面跳回外部系统。

确认成功后，如果有 `return_url`，前端会跳转，并带这些参数：

也就是说：

```Plain Text
callback_url = 系统对系统通知
return_url = 页面跳回外部系统
result_url = 外部系统主动拉最终结果
```

**外部系统拉最终结果**

接口是：

```Plain Text
GET /api/v1/integrations/publishing/ad-generation/jobs/{job_id}/result
```

注意：只有任务状态是 `returned` 时才能拉成功。
如果还停在 `final_review`、`copy_review`、`image_review` 等状态，会返回 409，意思是最终结果还没准备好。

**这一阶段传到下一步的数据**

最重要的是这几个：

**这里的业务边界**

AI 系统到 `returned` 就结束了。
它只是把“可投放的数据包”交给外部投放系统，不负责真正创建 Facebook 广告、不负责 BM 授权、不负责 Pixel 绑定、不负责广告上线。

下一步就是：**外部投放系统拿** `returned` **的最终包，创建真实广告系列、广告组、广告创意。**

## 外部投放系统创建真实广告

继续讲 **第 12 步：外部投放系统创建真实广告**。

这一步已经不在 AI 系统里完成了，而是在外部投放系统：

```Plain Text
https://newpixel.messrocts.com
```

AI 系统只提供“广告草稿数据包”，外部系统负责真正创建广告系列、广告组、广告创意，并决定是否上线。

**整体流程**

```Plain Text
AI 任务状态 returned
        ↓
外部系统拿 job_id / result_url
        ↓
GET 最终结果包
        ↓
读取 campaign_payload 创建广告系列
        ↓
读取 adset_payload 创建广告组
        ↓
读取 creative_payload 创建广告创意
        ↓
下载 image_url / video_url 入外部素材库
        ↓
生成真实广告草稿或暂停态广告
        ↓
运营在外部系统最终确认上线
```

**外部系统先拉结果**

```Plain Text
GET https://ai.ggcss.xyz/api/v1/integrations/publishing/ad-generation/jobs/{job_id}/result
```

只有 `job.status = returned` 才能拉到最终结果。
如果还不是 `returned`，说明 AI 流程还没完成，外部系统不应该创建真实广告。

**创建广告系列：读 ****`campaign_payload`**

例子：

```Plain Text
{
  "name": "GAJA777",
  "objective": "OUTCOME_SALES",
  "status": "PAUSED",
  "draft": 1
}
```

**创建广告组：读 ****`adset_payload`**

这里最需要注意的是：
**外部系统创建转化广告时，优先读* `adset_payload.customEventType`*，不要再依赖旧的* `optimization_event`*。**

**创建广告创意：读 ****`creative_payload`**

**图片广告怎么走**

```Plain Text
creative_payload.type = image
        ↓
外部系统读取 message / ads_name / description / link
        ↓
下载 creative_payload.image_url
        ↓
入外部素材库
        ↓
创建图片广告创意
```

**视频广告怎么走**

```Plain Text
creative_payload.type = video
        ↓
外部系统读取 message / ads_name / description / link
        ↓
下载 creative_payload.video_url
        ↓
可同时读取 image_url 作为封面或参考图
        ↓
入外部素材库
        ↓
创建视频广告创意
```

`assets`** 在这一步怎么用**

**这一步外部系统还必须自己补的数据**

AI 系统通常不负责这些：

所以这一步的边界很清楚：

```Plain Text
AI 系统负责：
选题、文案、图片、视频、最终草稿包

外部投放系统负责：
账户、BM、Pixel、素材入库、真实广告创建、上线、投放状态
```

下一步就是 **第 13 步：广告跑起来以后，外部系统把真实投放数据回传给 AI，AI 做效果分析和复盘。**

## **外部投放数据回传 AI，做效果分析和复盘**

继续讲 **第 13 步：外部投放数据回传 AI，做效果分析和复盘**。

这一步发生在真实广告已经创建、投放，并产生数据之后。外部投放系统把真实广告表现回传给 AI 系统，AI 系统分析：素材好不好、文案好不好、人群是否合适、转化链路是否有问题，以及下一轮应该改哪里。

接口在 adperformancepy line 18C:UserspandaDocumentsAdvertising Automationbackendappapiv1endpointsadperformancepy:18：

```Plain Text
POST https://ai.ggcss.xyz/api/v1/integrations/ad-performance/analyses
```

**整体流程**

```Plain Text
外部系统广告已投放
        ↓
产生真实表现数据：曝光、点击、花费、转化、视频播放等
        ↓
外部系统 POST 投放数据给 AI
        ↓
AI 提取 campaign / adset / creative / insight
        ↓
AI 计算 metrics
        ↓
AI 判断数据完整度
        ↓
AI 生成问题诊断、优化建议、素材分析
        ↓
前端“投放分析”页面展示结果
        ↓
下一轮可根据建议重新生成文案、图片或视频
```

**请求顶层参数**

`campaign`** 建议带的数据**

`adset`** 建议带的数据**

`creative`** 建议带的数据**

`insight`** 建议带的数据**

**AI 系统内部会生成什么**

**这一阶段最关键的判断**

AI 会先看数据完整度：

```Plain Text
有曝光/点击/花费 → 能判断基础投放表现
有目标和广告组设置 → 能判断投放配置
有文案/标题/素材 URL → 能判断创意问题
有购买/加购/注册/线索 → 能判断真实业务效果
有 siblings → 能横向比较同组素材
```

如果只传曝光、点击、花费，AI 只能判断“点击层面表现”。
如果还传 `purchase`、`add_to_cart`、`complete_registration`，AI 才能判断“真实业务转化”。
如果还传 `image_url`、`video_url`、`video_keyframes`，AI 才能判断“素材画面哪里有问题”。

**下一步数据流向**

```Plain Text
投放分析结果
        ↓
optimization_work_order
        ↓
判断要改哪里：
- 改文案
- 改标题
- 改图片
- 改视频前三秒
- 改人群
- 检查落地页
        ↓
进入下一轮 AI 生成
```

所以第 13 步是整个系统的闭环：

前面 AI 负责生产广告素材，外部系统负责真实投放，投放后再把真实效果数据回给 AI，AI 再指导下一轮怎么优化。
