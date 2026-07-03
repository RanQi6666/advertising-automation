# 首尾帧品牌锚点 + 中段 3A 博彩感视觉爆发方案

## 背景

当前视频生成使用首尾帧图片作为控制输入。视频质量不稳定时，最直接的控制点不是只改视频提示词，而是让首帧和尾帧在进入视频模型前就承担明确的导演功能：

- 首帧负责品牌识别、强钩子和视觉压迫感。
- 中段负责 3A 大作级视觉冲击、博彩感奖励反馈和节奏爆发。
- 尾帧负责品牌收口和行动引导。

本方案采用轻量版：不做图片后处理、不做 logo 叠加合成、不新增复杂配置页，只通过首尾帧图片 brief 和视频 prompt 规则强化控制。

## 目标

1. 让首帧一定出现品牌 logo 或品牌名，并出现符合目标国家偏好的强视觉角色或 Boss。
2. 让中段 3-9 秒具备 3A 游戏广告常见的强冲击感，而不是平淡转场。
3. 让尾帧一定回到品牌 logo 或品牌名，并出现安全 CTA。
4. 保持审核安全边界：不出现真钱金额、保证赢钱、提现、充值、余额等高风险文字。
5. 保持轻量改动，优先验证提示词控制是否足够改善视频效果。

## 视频结构

### 0-3 秒：首帧强钩子

首帧必须同时满足两个要求：

- 品牌锚点：出现品牌 logo 或品牌名，例如 GAJA。
- 强视觉角色：出现史诗英雄、王者、战士、鸟神风格 Boss、巨蛇 Boss、石像守卫 Boss，或其他符合目标国家审美的高压迫感角色。

画面方向：

- 3A 大作开场感。
- Boss 登场或英雄对峙。
- 神光、传送门、金色粒子、史诗场景深度。
- 品牌名不带数字后缀，例如用 GAJA，不用 GAJA777。

印度市场建议偏向：

- 金色神光。
- 史诗英雄。
- 鸟神风格 Boss。
- 巨蛇 Boss。
- 石像守卫 Boss。
- 宫殿、风暴云层、金色边缘光、神话感但不使用真实宗教人物或经文。

GAJA 类素材建议偏向：

- 传送门。
- Boss 登场。
- 英雄挑战。
- 金色奖励光效。
- 品牌 logo 或 GAJA wordmark。

### 3-9 秒：中段视觉爆发

中段重点不是品牌文字，而是视觉冲击、挑战、反转、奖励爆发。默认不出现大段文字和现金金额，最多保留一个小品牌 logo。

中段特效库按以下内容保留：

1. 金币爆炸特效

- 金币喷射。
- 金光爆闪。
- 屏幕震动。
- 粒子飞散。
- 适合 Jackpot 演绎、Boss 掉落、宝箱开启、奖励反馈。

2. 神光降临特效

- 金色神光。
- 光柱降落。
- 云层裂开。
- 金色粒子。
- 适合印度市场、开门、Boss 出现、稀有奖励。

3. 传送门特效

- 紫色能量门。
- 金色圣门。
- 雷电门。
- 适合进入挑战、开启关卡、幸运之门。
- 特别适合 GAJA 这类素材。

4. Jackpot 反馈特效，弱博彩版

- 大字弹出。
- 光效扩散。
- 粒子爆发。
- 慢镜头。
- 可使用 EPIC、LEGENDARY、BIG REWARD。

5. Boss 击败特效

- Boss 出现。
- 挑战。
- Boss 被击败。
- 掉落宝箱。
- 奖励爆发。
- 核心吸引力是挑战、反转、爆发奖励。

6. 数字翻滚特效

- 数字快速增长。
- 发光。
- 跳动。
- 只使用 Score、Points、Stars、Power 这类游戏数值。
- 不出现现金金额。

7. 慢镜头爆发

- 时间暂停。
- 子弹时间。
- 金币悬浮。
- 宝箱开启。
- 可结合史诗英雄、王者、战士、鸟神风格 Boss、巨蛇 Boss、石像守卫 Boss、石像守卫等角色。

### 9-12 秒：尾帧品牌收口

尾帧必须回到清晰的广告落版：

- 品牌 logo 或品牌名。
- 安全 CTA，例如 Start、Play Now、Explore。
- 奖励收口画面，例如宝箱开启后的光效、传送门后的品牌大厅、Boss 掉落后的奖励场景。
- 画面干净，CTA 区域清晰。
- 不出现数字后缀。

## 禁止内容

以下内容不要出现在首帧、尾帧或视频中段：

- $1000。
- Win Cash。
- Guaranteed Money。
- REAL CASH。
- GUARANTEED WIN。
- 现金金额。
- 真实货币符号。
- 提现。
- 充值。
- 余额。
- 保证赢钱。
- 真实赌博收益承诺。

允许出现但需控制表达：

- Jackpot 氛围。
- 金币爆炸。
- 宝箱奖励。
- Boss 掉落。
- EPIC。
- LEGENDARY。
- BIG REWARD。
- Score。
- Points。
- Stars。
- Power。

## 生成规则

### 生图 brief 规则

当生成 video_keyframe_variants 图片时，根据 keyframe_role 区分首帧和尾帧：

first_frame：

- 必须包含品牌 logo 或品牌名。
- 必须包含强视觉角色或 Boss。
- 必须是 3A 大作级开场构图。
- 可使用传送门、神光、Boss 登场、英雄对峙、金色粒子。
- 不出现品牌数字后缀。

last_frame：

- 必须包含品牌 logo 或品牌名。
- 必须包含安全 CTA。
- 必须有清晰收口画面。
- 可使用奖励爆发后的稳定构图、品牌大厅、宝箱光效、传送门落点。
- 不出现品牌数字后缀。

### 视频 prompt 规则

视频生成时，prompt 应明确告诉模型：

- 前 0-3 秒严格延续首帧：品牌 + 史诗英雄或 Boss 登场。
- 中间 3-9 秒使用中段特效库，制造 3A 大作级视觉冲击。
- 中间段不要新增现金金额、提现、充值、余额、保证赢钱等高风险文字。
- 中间段最多允许一个小品牌 logo。
- 后 9-12 秒严格回到尾帧：品牌 + CTA + 奖励收口。

## 推荐默认模板

首帧模板：

```text
Create a cinematic 3A mobile game opening frame for {brand_name}.
Show the visible {brand_name} logo or wordmark without numeric suffix.
Feature a country-market strong visual character: epic hero, king, warrior, bird-god-style boss, giant serpent boss, or stone guardian boss.
Use dramatic portal light, golden divine light, storm clouds, particles, and high-pressure boss confrontation.
No cash amount, no guaranteed win claim, no withdrawal or recharge UI.
```

中段视频模板：

```text
From 3s to 9s, turn the scene into a high-impact 3A game reward sequence.
Use coin explosion effects, golden light burst, portal effects, jackpot-style feedback, boss defeat, score or power rolling numbers, and slow-motion reward explosion.
Keep the visuals intense and cinematic.
Do not show cash amounts, real-money claims, withdrawal, recharge, balance UI, or guaranteed winning language.
At most keep a small brand logo during the middle sequence.
```

尾帧模板：

```text
End on a clean branded CTA frame.
Show the visible {brand_name} logo or wordmark without numeric suffix.
Use Start, Play Now, or Explore as the CTA.
Resolve the reward explosion into a clear brand end card with premium game lobby or portal background.
No cash amount, no guaranteed win claim, no withdrawal or recharge UI.
```

## 成功标准

一条 12 秒视频应满足：

- 0-3 秒能看到品牌名或 logo。
- 0-3 秒能看到史诗英雄、王者、战士或 Boss 型角色。
- 3-9 秒有明显 3A 视觉冲击，不是平淡转场。
- 3-9 秒可以有博彩感奖励反馈，但没有真钱收益承诺。
- 9-12 秒能看到品牌名或 logo。
- 9-12 秒有 Start、Play Now 或 Explore 这类安全 CTA。
- 整条视频不出现 GAJA777、现金金额、提现、充值、余额、Guaranteed Win 等高风险内容。

## 后续升级路径

如果轻量版仍不稳定，再升级到中等版：

- 先让 AI 生成无文字或低文字底图。
- 后端用确定性图片合成方式叠加 logo、品牌名和 CTA。
- 视频模型只使用合成后的首尾帧。

中等版更稳定，但本轮先不做，避免扩大范围。
