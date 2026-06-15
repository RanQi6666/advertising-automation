# 发布投流系统分离方案分析

## 1. 新思路评价

**这个思路非常好**，完全符合软件工程的**关注点分离**原则。从您提供的 `publishing_system_integration_notes.md` 文档可以看出，之前已经考虑过类似的集成方式。

**优势：**
- **职责清晰**：您的项目专注于"素材生成"，外部发布系统专注于"广告投放"
- **模块化**：可以独立开发、测试和维护
- **可扩展性**：可以支持多个发布系统（如果未来需要）
- **复用性**：素材生成能力可以被其他系统复用

## 2. 当前项目需要的变化

### 2.1 核心职责转变
**从**：完整的广告投放流程（工单 → 素材 → 审核 → 发布）
**到**：专业的素材生成服务（工单 → LLM处理 → 素材生成 → 返回素材包）

### 2.2 代码层面变化

#### 需要**移除或简化**的部分：
1. `publish_service.py` 中的 Facebook 发布逻辑
2. `review_service.py` 中的审核逻辑（审核可能转移到外部系统）
3. 与 Facebook API 直接交互的部分

#### 需要**保留或增强**的部分：
1. `work_order_service.py` 的工单处理能力
2. LLM 集成（选题、文案生成）
3. 图片/视频生成能力
4. 落地页分析能力

#### 需要**新增**的部分：
1. 专门的 API 端点用于接收外部工单
2. 素材包（ad generation package）的数据结构
3. 异步任务处理机制
4. 回调通知机制（可选）

### 2.3 数据模型变化
- `WorkOrder` 模型：增加外部系统关联字段
- 新增 `AdGenerationJob` 模型：跟踪素材生成任务状态
- 简化 `PublishJob` 模型：不再需要 Facebook 相关字段

## 3. 架构建议

### 3.1 系统边界
```
外部发布系统 ← API → 您的项目（素材生成服务）
    ↓                       ↓
Facebook API          LLM + 图片/视频生成
```

### 3.2 工作流程
1. **接收工单**：外部系统通过 API 发送工单信息
2. **LLM 处理**：提取投放人群、国家、年龄、链接等
3. **素材生成**：生成图片、视频、标题、宣传文案
4. **返回素材包**：结构化数据返回给外部系统
5. **外部系统处理**：保存素材、提取数据、投放广告

### 3.3 API 设计建议
```python
# 接收工单并生成素材
POST /api/v1/ad-generation/jobs
{
    "external_order_id": "外部系统工单ID",
    "work_order": {
        "raw_content": "工单原文",
        "structured_fields": {  # 可选
            "product_name": "...",
            "country": "...",
            "age_min": 25,
            "age_max": 45,
            "landing_url": "...",
            "event_name": "purchase"
        }
    },
    "preferences": {
        "creative_type": "image",  # image/video/both
        "image_count": 1,
        "video_required": false
    }
}

# 响应（立即返回任务ID）
{
    "job_id": "...",
    "status": "queued"
}

# 查询任务状态
GET /api/v1/ad-generation/jobs/{job_id}

# 完成时的响应
{
    "job_id": "...",
    "external_order_id": "...",
    "status": "completed",
    "campaign_payload": {
        "name": "AI生成的广告系列名称",
        "objective": "OUTCOME_SALES",
        "status": "PAUSED"
    },
    "adset_payload": {
        "name": "AI生成的广告组名称",
        "daily_budget": 5000,
        "billing_event": "IMPRESSIONS",
        "optimization_goal": "LINK_CLICKS",
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "pixel_id": null,
        "custom_event_type": null,
        "countries": "US",
        "age_min": 18,
        "age_max": 65,
        "status": "PAUSED"
    },
    "creative_payload": {
        "name": "AI生成的创意名称",
        "type": "image",
        "message": "主要文案",
        "link": "https://example.com",
        "ads_name": "标题",
        "description": "描述",
        "asset_url": "https://ai-service.example/assets/image.png",
        "asset_id": null
    },
    "assets": {
        "images": [
            {
                "filename": "creative_1.png",
                "url": "https://ai-service.example/assets/image.png",
                "size": "1080x1080",
                "prompt": "...",
                "alt_text": "..."
            }
        ],
        "videos": []
    },
    "review": {
        "missing_fields": [],
        "warnings": [],
        "low_confidence_fields": []
    }
}
```

## 4. 具体实现步骤

### 阶段 1：设计接口（1-2天）
1. 定义 API 请求/响应格式
2. 设计数据模型
3. 编写接口文档

### 阶段 2：重构现有代码（3-5天）
1. 创建新的 API 端点
2. 移除/简化发布相关代码
3. 调整数据模型

### 阶段 3：增强素材生成（5-7天）
1. 接入真实 LLM API
2. 接入真实图片生成模型
3. 接入真实视频生成模型（可选）

### 阶段 4：测试和集成（2-3天）
1. 单元测试
2. 集成测试
3. 与外部系统联调

## 5. 注意事项

### 5.1 接口设计原则
- **幂等性**：相同工单多次提交应返回相同结果
- **异步处理**：素材生成耗时，采用异步任务模式
- **错误处理**：清晰的错误码和错误信息
- **版本控制**：API 版本管理（如 `/api/v1/`）

### 5.2 数据一致性
- 保留工单原文和解析结果
- 记录 LLM 处理过程和结果
- 保存生成的素材和元数据

### 5.3 性能考虑
- LLM 调用可能较慢，需要超时处理
- 图片/视频生成可能需要队列处理
- 考虑缓存机制（相同工单不重复生成）

### 5.4 安全性
- API 认证（API Key 或 JWT）
- 输入验证和清理
- 速率限制

## 6. 总结

您的新思路是**完全可行且推荐**的。通过将发布投流系统分离，您的项目可以专注于核心的素材生成能力，同时保持架构的清晰和可维护性。

**关键建议**：
1. 先设计清晰的 API 接口
2. 逐步重构，不要一次性重写
3. 保留现有的工单处理和素材生成逻辑
4. 移除或简化发布相关逻辑
5. 考虑异步处理机制

这种架构不仅解决了当前的需求，也为未来的扩展（如支持多个发布系统、更复杂的素材生成需求等）打下了良好基础。