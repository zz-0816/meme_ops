# meme_ops — Web3 Meme 投研运营系统 · 项目框架

> ⚠️ 当前阶段：框架设计。暂不编写代码，仅定义架构和模块边界。

## 当前模型配置

复制 `.env.example` 为 `.env`，并至少配置文字分析 Key。修改 `.env` 后必须重启
FastAPI 进程，因为模型配置在后端模块加载时读取。

- `DEEPSEEK_API_KEY` + `DEEPSEEK_MODEL=deepseek-v4-pro`：生成文字分析。
- `IMAGE_PROVIDER`：`auto`、`openai`、`gemini` 或 `stability`。
- `OPENAI_API_KEY`：默认图片模型 `gpt-image-2`。
- `GEMINI_API_KEY`：默认图片模型 `gemini-3.1-flash-image`。
- `STABILITY_API_KEY`：默认使用 Stable Image Core。
- `PINATA_JWT`：AI 栅格海报铸造前必需；图片和元数据先固定到 IPFS，避免把
  大体积 base64 图片直接写入 tokenURI 造成异常高 Gas。

前端报告会显示 `DeepSeek` 或 `Rules-engine fallback`，海报预览也会显示实际图片
Provider。没有配置图片 Key 时只提供明确标注的语义模板预览，不会伪装成 AI 图片。

---

## 项目概述

一个 Web3 Meme 代币投研调查系统。整合链上数据、社交数据和社区内容，支持**单项目深度分析**和**多项目横向对比**，输出结构化海报/报告，关键信息持久化存储。

**产品定位**（来自 PRD v1.0）：帮助用户快速理解 Meme 项目的链上状态、社区状态、潜在风险与发展机会。

---

## 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                          前端 (Web UI)                           │
│                                                                  │
│  ┌──────────┐  ┌──────────────────────────────┐                  │
│  │ 左侧栏    │  │       中央主区域               │                  │
│  │          │  │                               │                  │
│  │ 📋 历史  │  │  ┌─────────────────────────┐  │                  │
│  │   数据    │  │  │  输入框                   │  │                  │
│  │   保存    │  │  │  (支持单/多代币输入)      │  │                  │
│  │          │  │  └─────────────────────────┘  │                  │
│  │ ⭐ 自选  │  │  ┌─────────────────────────┐  │                  │
│  │   Meme   │  │  │  海报①: 趋势分析图        │  │                  │
│  │   列表    │  │  │  海报②: Meme生命力图      │  │                  │
│  │          │  │  │  海报③: 资产配置建议图     │  │                  │
│  │          │  │  │  ... (历史海报瀑布流)     │  │                  │
│  │          │  │  └─────────────────────────┘  │                  │
│  └──────────┘  └──────────────────────────────┘                  │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP API
┌────────────────────────▼────────────────────────────────────────┐
│                      后端 (Python FastAPI)                        │
│                                                                  │
│  ┌─────────────────────────────────────────────┐                │
│  │  Agent 引擎                                   │                │
│  │  - 加载 MEMORY_PROMPT.md 作为共享层指令        │                │
│  │  - 叠加当前 Persona prompt (personas/*.md)     │                │
│  │  - 识别分析模式（单项目 / 多项目对比）          │                │
│  │  - 按权重优先级逐维度调研                       │                │
│  │  - 按 Persona 切换输出海报类型                  │                │
│  │  - 决策困难时重新加载 MEMORY_PROMPT            │                │
│  └─────────────────────────────────────────────┘                │
│                       │                                         │
│  ┌────────────────────▼────────────────────────┐                │
│  │  数据持久化层                                  │                │
│  │  - 本地：SQLite                               │                │
│  │  - 生产：PostgreSQL / MySQL（待迁移）          │                │
│  └─────────────────────────────────────────────┘                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 投研维度 & 权重

按优先级从高到低排列。当多维度信号矛盾时，以权重更高者为准。

| 优先级 | 维度 | 权重 | 数据来源（待接入） |
|--------|------|:----:|------|
| 1 | 🔗 链上流动性 | 5 | DexScreener / GeckoTerminal API |
| 2 | 👛 链上钱包持有地址数量 | 4 | Etherscan / Solscan API |
| 3 | 💰 链上钱包地址持有量分布 | 4 | 链上数据 + 鲸鱼监控 |
| 4 | 💬 社媒社区讨论量 | 3 | Twitter API / LunarCrush |
| 5 | 🔥 社媒热点流量 | 3 | Twitter Trends / KOL 监控 |
| N | 📊 （待补充） | — | 合约安全审计、团队背景、叙事契合度等 |

---

## 模块划分

### 1. `MEMORY_PROMPT.md` — 长期记忆提示词（核心）
│  - 角色定义、行为约束、决策铁律
│  - 五维权重体系（共享层，所有 persona 共用）
│  - Persona 分层架构说明
│  - 前端交互行为详细描述

### 1.5 Persona 系统（新增）

Agent 通过「共享层 + Persona 层」双 prompt 架构，支持同一数据引擎输出不同视角：

| Persona | 文件 | 核心问题 | 海报① | 海报② | 海报③ |
|------|------|------|------|------|------|
| 🔍 投研观察者 | `investor.md` | 这个币值不值得关注？ | 📈 趋势分析图 | 🧬 Meme生命力图 | 💼 资产配置建议图 |
| 📣 社区运营者 | `operator.md` | 这个社区能不能做起来？ | 🩺 社区健康度诊断 | 🎯 增长机会图 | 📋 7-Day Playbook |
| 🏗️ 项目方 | `builder.md` | 我的项目哪里需要改进？ | 🏥 项目体检报告 | 📉 竞品差距分析 | 🗺️ 改进路线图 |
| 📊 研究员 | `researcher.md` | 赛道整体情况如何？ | 🌍 赛道全景图 | 📊 深度对比矩阵 | ⚠️ 风险评估报告 |

**核心原则**：数据相同，视角不同。如同一个体检报告——医生看指标异常、教练看体能短板、营养师看代谢问题。

**Persona 层能力**：
- 输出视角和海报类型切换
- 评分权重临时微调（运营者社媒 +1/流动性 -1，项目方持仓分布 +1）
- 输出语气和术语风格切换
- 微调仅在 persona 输出侧生效，数据库原始评分保持统一标准

### 2. 前端模块

#### 2.1 页面布局：三栏式

```
┌────────────┬──────────────────────────────────────┐
│  左侧栏     │            中央主区域                   │
│  (240px)   │                                      │
│            │  ┌────────────────────────────────┐   │
│ ┌────────┐ │  │  👤 [🔍 投研观察者 ▾]            │   │  ← Persona 选择器
│ │📋 历史  │ │  │  ┌──────────────────────────┐  │   │
│ │  数据   │ │  │  │  输入框 (支持单/多代币)    │  │   │
│ │  保存   │ │  │  └──────────────────────────┘  │   │
│ │        │ │  ┌────────────────────────────────┐   │
│ │⭐ 自选  │ │  │  海报③ (最新, 最靠近输入框)      │   │
│ │  Meme  │ │  │  海报②                          │   │
│ │  列表   │ │  │  海报①                          │   │
│ └────────┘ │  │  ... (更早的海报向下排列)         │   │
│            │  └────────────────────────────────┘   │
└────────────┴──────────────────────────────────────┘
```

#### 2.2 左侧栏 Tab 1：历史数据保存

- **列表结构**：按代币名分组，标题格式 `{代币名} [{链名}]`
  - 示例：`PEPE [ETH]`、`DOGE [SOL]`、`WIF [SOL]`
- **分组行为**：同一代币的多次分析折叠在同一分组下，展开后显示按时间排序的分析记录
- **点击行为**：点击某条历史记录 → 主区域展示该次分析的完整结果（趋势图 + 生命力图 + 资产配置图），与输入框下方输出格式完全一致
- **搜索/筛选**：顶部支持关键词搜索历史记录
- **数据来源**：后端 API → 数据库 `analysis_records` 表

#### 2.3 左侧栏 Tab 2：自选 Meme 列表

- **列表来源**：用户手动收藏的代币，存储在数据库 `watchlist` 表
- **添加方式**：从分析结果页一键添加到自选，或在自选页直接输入添加

**默认模式（编辑开关 OFF）**：
| 交互 | 行为 |
|------|------|
| 拖拽排序 | 拖动列表项调整展示顺序（本地持久化排序） |
| 备注 | 每条右侧有 📝 图标，点击弹出备注编辑框，保存到数据库 |

**编辑模式（编辑开关 ON）**：
| 交互 | 行为 |
|------|------|
| 复选框 | 每条左侧出现 ☑️，支持全选/取消全选 |
| 批量操作栏 | 底部浮现操作栏，包含两个按钮 |
| ⚖️ 对比分析 | 将选中代币送入多项目对比分析流程，在主区域展示对比矩阵 |
| 🗑 删除选中 | 从自选列表中移除选中代币（需确认弹窗） |

#### 2.4 中央主区域 — 分析结果输出

**输入框**：
- 单行输入，支持逗号或换行分隔多个代币
- placeholder：`输入 Meme 代币名称或合约地址，多个用逗号分隔...`
- 支持回车快捷提交

**输出区排版规则**：
- 新分析结果出现在输入框**正下方**
- 若该区域已有历史海报，新海报插入到已有海报**上方**（更靠近输入框）
- 形成瀑布流：最新结果 → 上一次结果 → 更早结果
- 单项目分析输出 3 张卡片/海报；多项目对比额外输出对比矩阵表

#### 2.5 输出卡片/海报类型（格式待定）

| 序号 | 类型 | 内容 | 参考方向 |
|:---:|------|------|------|
| ① | 📈 **趋势分析图** | 各维度随时间变化趋势曲线；关键指标（价格/交易量/持有者数）叠加展示 | 可参考 TradingView 轻量图表、DexScreener 趋势 |
| ② | 🧬 **Meme 生命力图** | 生命周期阶段判定（萌芽🐣 / 爆发🚀 / 成熟📊 / 衰退📉）；社区活跃度雷达图；传播力指数 | 可参考链上分析平台（Dune/Nansen）生命周期模型 |
| ③ | 💼 **资产配置建议图** | 风险等级颜色标识（绿/黄/红）；建议仓位比例饼图；进出场参考区间 | 可参考 Token Metrics、Messari 的资产评级卡片 |

#### 2.6 技术选型（待定）

| 层级 | 候选方案 |
|------|------|
| 框架 | React / Vue / 原生 HTML+JS |
| 图表 | ECharts / Chart.js / D3.js |
| 拖拽 | react-beautiful-dnd / sortablejs |
| 海报生成 | html2canvas / Puppeteer 截图 / SVG 渲染 |

---

### 3. 后端模块

#### 3.1 API 路由规划

**核心分析 API**：
| 方法 | 路径 | 功能 |
|------|------|------|
| `POST` | `/api/analyze` | 提交分析（单项目或多项目），参数含 `persona` |
| `GET` | `/api/history` | 获取历史记录（支持搜索、分页、persona 筛选） |
| `GET` | `/api/analysis/:id` | 获取单次分析完整详情 |
| `POST` | `/api/memory/reload` | 手动重载 MEMORY_PROMPT |
| `POST` | `/api/compare` | 多项目对比分析 |

**Persona API**：
| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/api/personas` | 获取可用 persona 列表 |
| `POST` | `/api/persona/switch` | 切换当前 persona |

**自选列表 API**：
| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/api/watchlist` | 获取自选列表 |
| `POST` | `/api/watchlist` | 添加代币 |
| `DELETE` | `/api/watchlist/:id` | 移除 |
| `PATCH` | `/api/watchlist/:id` | 更新备注/排序 |
| `POST` | `/api/watchlist/reorder` | 批量排序 |
| `POST` | `/api/watchlist/batch-delete` | 批量删除 |

**运营向 API（新增）**：
| 方法 | 路径 | 功能 |
|------|------|------|
| `POST` | `/api/community/health` | 社区健康度诊断 |
| `POST` | `/api/community/opportunities` | 增长机会分析（含竞品对比） |
| `POST` | `/api/playbook/generate` | 生成 7-Day Growth Playbook |
| `GET` | `/api/playbook/:id` | 查看 Playbook 详情 |
| `PATCH` | `/api/playbook/:id/action/:aid` | 标记行动完成/未完成 |
| `GET` | `/api/community/benchmark` | 竞品社区对比数据 |

#### 3.2 Agent 引擎能力矩阵

| 能力 | 说明 | 输入 | 输出 |
|------|------|------|------|
| 单项目分析 | 对单一 Meme 进行五维深度分析 | 代币名 + persona | 评分 + persona 对应的 3 张海报 |
| 多项目对比 | 对 2+ 项目进行横向对比 | 代币列表 + persona | 对比矩阵 + 各项目独立评分 |
| Persona 切换 | 切换输出视角和海报类型 | persona 名称 | 激活对应的 persona prompt |
| 社区健康诊断 | (运营者) 成员分层 + 风险/积极信号 | 代币名 | 健康度评分 + 分层数据 |
| 增长机会分析 | (运营者) 竞品对比 + 内容缺口 | 代币名 | 雷达图 + 缺口矩阵 + 机会 Top 3 |
| Playbook 生成 | (运营者) 7 天可执行行动计划 | 代币名 | 每日行动项 + KPI 清单 |
| 项目体检 | (项目方) 全维度问题诊断 | 代币名 | 问题清单 + 严重程度排序 |
| 改进路线图 | (项目方) 分阶段改进计划 | 代币名 | 紧急/短期/中期行动项 |
| 赛道全景 | (研究员) 赛道宏观分析 | 赛道名 | 赛道总览 + Top 排名 + 趋势 |
| 风险评估 | (研究员) 多维度风险矩阵 | 代币/赛道 | 风险分类 + 等级 + 缓解建议 |

---

### 4. 数据库模块

#### 4.1 表结构规划

**analysis_records**（分析记录主表）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| token_name | TEXT | 代币名称 |
| token_symbol | TEXT | 代币符号 |
| contract_addr | TEXT | 合约地址 |
| chain | TEXT | 所在链（ETH/SOL/BSC/...） |
| prompt | TEXT | 用户原始输入 |
| persona | TEXT | 使用的 persona 名称 |
| analysis_type | TEXT | 分析类型：single / compare |
| compare_group_id | TEXT | 对比分析组 ID |
| report_summary | TEXT(JSON) | 完整报告 JSON |
| overall_score | REAL | 综合评分 |
| risk_level | TEXT | 风险等级 |
| data_sources | TEXT(JSON) | 数据来源列表 |
| created_at | TIMESTAMP | 创建时间 |

**dimension_scores**（维度评分明细）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| analysis_id | INTEGER FK | 关联分析记录 |
| dimension | TEXT | 维度名称 |
| score | REAL | 评分 0-10 |
| weight | REAL | 权重 |
| raw_data | TEXT(JSON) | 原始数据 |
| notes | TEXT | 备注 |

**metric_snapshots**（指标历史快照）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| analysis_id | INTEGER FK | 关联分析记录 |
| metric_name | TEXT | 指标名 |
| metric_value | REAL | 指标值 |
| metric_unit | TEXT | 单位 |

**watchlist**（自选列表）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| token_name | TEXT | 代币名称 |
| token_symbol | TEXT | 代币符号 |
| contract_addr | TEXT | 合约地址 |
| chain | TEXT | 所在链 |
| sort_order | INTEGER | 排序位置 |
| notes | TEXT | 用户备注 |
| added_at | TIMESTAMP | 添加时间 |

**community_member_snapshot**（运营向 — 社区成员分层快照）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| analysis_id | INTEGER FK | 关联分析记录 |
| layer | TEXT | 层级：core/active/occasional/lurker |
| member_count | INTEGER | 该层级人数 |
| percentage | REAL | 占比 |
| change_7d | REAL | 7 日变化率 |

**community_content_analysis**（运营向 — 社区内容分析）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| analysis_id | INTEGER FK | 关联分析记录 |
| content_type | TEXT | 类型：meme/ama/education/task/ugc |
| frequency | TEXT | 频率：daily/weekly/monthly/none |
| engagement | REAL | 互动率 |

**competitor_benchmark**（运营向 — 竞品社区对比）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| analysis_id | INTEGER FK | 关联分析记录 |
| competitor_name | TEXT | 竞品名称 |
| dimension | TEXT | 对比维度 |
| competitor_score | REAL | 竞品评分 |
| our_score | REAL | 本项目评分 |

**growth_playbook**（运营向 — Playbook）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| analysis_id | INTEGER FK | 关联分析记录 |
| week_start | TEXT | 周开始日期 |
| week_end | TEXT | 周结束日期 |
| goal_summary | TEXT | 本周目标摘要 |
| status | TEXT | 状态：active/completed |

**playbook_actions**（运营向 — Playbook 行动项）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| playbook_id | INTEGER FK | 关联 Playbook |
| day | INTEGER | 第几天 (1-7) |
| theme | TEXT | 当日主题 |
| preparation | TEXT | 准备工作 |
| promotion | TEXT | 推广渠道 |
| execution | TEXT | 执行步骤 |
| kpi | TEXT | 预期 KPI |
| completed | INTEGER | 是否完成 (0/1) |

#### 4.2 部署策略
- **本地**：SQLite，适合单机开发和 Hackathon MVP 阶段
- **生产**：PostgreSQL / MySQL，表结构无缝迁移
- **链上**：NFT 合约部署在测试链（Monad Testnet），稳定后迁移主网

---

### 5. DApp 平台层（新增）

#### 5.1 钱包登录

用户无需邮箱/密码，仅靠 Web3 钱包完成身份认证。

| 步骤 | 操作 | 说明 |
|:--:|------|------|
| 1 | 用户点击「连接钱包」 | 前端调用 `eth_requestAccounts` |
| 2 | MetaMask 弹窗确认 | 用户主动点击确认 |
| 3 | 后端生成随机 nonce | 返回给前端 |
| 4 | 用户用钱包签名 nonce | 前端调用 `personal_sign` |
| 5 | 后端验证签名 | 恢复地址 → 比对 → 签发 session JWT |
| 6 | 登录成功 | session 存储在客户端，断开后重新签名恢复 |

**安全原则**：
- 永不请求私钥
- 仅请求消息签名（登录）和交易签名（Mint NFT）
- 签名消息明文展示，用户清楚知道自己在签什么

#### 5.2 海报一键上链（NFT Minting）

每张分析海报右下角显示「🖼️ Mint 上链」按钮。

**MVP 阶段技术方案**：
```
用户点击 Mint
    │
    ▼
提取海报核心数据（代币名+评分+风险等级+建议摘要）→ JSON
    │
    ▼
    方案 A: JSON 存入 IPFS → TokenURI = ipfs://...
    方案 B: JSON 直接编码上链（低 Gas）
    │
    ▼
调用 NFT 合约 mint(tokenURI, recipient)
    │
    ▼
Metamask 弹窗 → 用户确认 → 交易上链
    │
    ▼
铸造成功 → 出现在「我的海报」收藏区
```

**NFT 合约规划**：
| 项目 | 说明 |
|------|------|
| 合约标准 | ERC-721（每张海报唯一 NFT） |
| 测试链 | Monad Testnet（ChainID 10143） |
| 合约功能 | `mint(tokenURI)` / `tokenURI(tokenId)` / `totalSupply()` |
| 元数据结构 | `{name, token_name, score, risk_level, persona, timestamp, poster_image_uri}` |

#### 5.3 社区平台（类 Twitter 社交）

**功能矩阵**：

| 功能 | 行为 | 数据存储 |
|------|------|------|
| 📝 发帖 | 发布文字（≤500字），可附带已生成的海报 | `posts` 表 |
| 🔁 转发 | 将他人帖子转发到自己的时间线 | `post_reposts` 表（关联原帖） |
| ❤️ 点赞 | 对帖子点赞 | `post_likes` 表 |
| 💬 引用 | 转发并附加自己的评论（Quote Post） | `post_reposts` 表 + `quote_text` |
| 👥 关注 | 关注/取消关注其他用户 | `user_follows` 表 |
| 🏠 个人主页 | 展示帖子、海报NFT、关注数/粉丝数 | 聚合查询 |

**时间线逻辑**：
```
用户 A 关注了 B、C
    │
    ▼
时间线 = B的帖子 + C的帖子 + B的转发 + C的转发
    │
    ▼
按时间倒序排列，每页 20 条
```

#### 5.4 用户中心

| 区域 | 内容 |
|------|------|
| 头像/昵称 | ENS 域名自动识别，支持自定义头像 URL |
| 统计数据 | 帖子数 / 关注中 / 粉丝数 / 海报 NFT 数 |
| 我的帖子 | 按时间倒序展示用户发布的所有帖子 |
| 我的海报 | 展示用户铸造的所有海报 NFT（TokenID + 缩略图） |
| 分析历史 | 展示用户进行过的所有分析记录 |
| 点赞/转发 | 用户点赞过的帖子列表 |

#### 5.5 社交 API（新增）

| 方法 | 路径 | 功能 |
|------|------|------|
| `POST` | `/api/auth/nonce` | 获取签名 nonce |
| `POST` | `/api/auth/login` | 签名验证登录 |
| `GET` | `/api/auth/me` | 获取当前用户信息 |
| `POST` | `/api/posts` | 发帖 |
| `GET` | `/api/posts` | 获取时间线（关注用户的帖子） |
| `GET` | `/api/posts/:id` | 帖子详情（含回复） |
| `DELETE` | `/api/posts/:id` | 删除自己的帖子 |
| `POST` | `/api/posts/:id/like` | 点赞/取消点赞 |
| `POST` | `/api/posts/:id/repost` | 转发/引用 |
| `GET` | `/api/users/:address` | 用户主页 |
| `GET` | `/api/users/:address/posts` | 用户帖子列表 |
| `GET` | `/api/users/:address/nfts` | 用户海报 NFT 列表 |
| `POST` | `/api/users/:address/follow` | 关注/取消关注 |
| `GET` | `/api/users/:address/followers` | 粉丝列表 |
| `GET` | `/api/users/:address/following` | 关注列表 |
| `POST` | `/api/nft/mint` | 铸造海报 NFT（触发链上 mint） |

#### 5.6 社交数据表（新增）

**users**（用户）
| 字段 | 类型 | 说明 |
|------|------|------|
| address | TEXT PK | 钱包地址 |
| nickname | TEXT | 昵称 |
| avatar | TEXT | 头像 URL |
| ens | TEXT | ENS 域名 |
| bio | TEXT | 个人简介 |
| created_at | TIMESTAMP | 注册时间 |

**posts**（帖子）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| author | TEXT FK | 作者钱包地址 |
| content | TEXT | 文字内容（≤500字） |
| attached_analysis_id | INTEGER FK | 关联分析记录（可选） |
| created_at | TIMESTAMP | 发布时间 |

**post_likes**（点赞）
| 字段 | 类型 | 说明 |
|------|------|------|
| post_id | INTEGER FK | 帖子 ID |
| user_address | TEXT FK | 点赞用户 |

**post_reposts**（转发）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| original_post_id | INTEGER FK | 原帖 ID |
| reposter | TEXT FK | 转发者地址 |
| quote_text | TEXT | 引用文字（可选，为空则为纯转发） |
| created_at | TIMESTAMP | 转发时间 |

**user_follows**（关注）
| 字段 | 类型 | 说明 |
|------|------|------|
| follower | TEXT | 关注者地址 |
| following | TEXT | 被关注者地址 |

**poster_nfts**（海报 NFT）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| token_id | TEXT | NFT Token ID |
| contract_address | TEXT | 合约地址 |
| chain | TEXT | 所在链 |
| minter | TEXT FK | 铸造者地址 |
| analysis_id | INTEGER FK | 关联分析记录 |
| token_uri | TEXT | NFT 元数据 URI |
| tx_hash | TEXT | 铸造交易哈希 |
| created_at | TIMESTAMP | 铸造时间 |

---

## 运行流程（完整版）

```
用户输入（单个/多个代币）
    │
    ▼
Agent 加载 MEMORY_PROMPT
    │
    ├── 单项目模式 ──────────────────┐
    │                                │
    │  解析代币信息                    │
    │    │                           │
    │    ├─▶ 维度1-5 独立评分         │
    │    │                           │
    │    ▼                           │
    │  加权综合评分 → 风险等级         │
    │    │                           │
    │    ├─▶ 生成 ① 趋势分析图数据    │
    │    ├─▶ 生成 ② Meme生命力评估    │
    │    └─▶ 生成 ③ 资产配置建议      │
    │                                │
    └── 多项目对比模式 ──────────────┤
         │                           │
         对每个项目独立评分             │
           │                         │
           ▼                         │
         横向对比矩阵生成               │
           │                         │
           各项目独立卡片 + 排名        │
                                     │
    ┌────────────────────────────────┘
    ▼
结构化报告 JSON + 数据库存储
    │
    ▼
前端渲染 → 海报插入输入框下方（已有海报上方）
```

---

## MEMORY_PROMPT 使用规则

1. **每次 Agent 启动** → 完整加载 `MEMORY_PROMPT.md`
2. **运行中遇到不确定** → 调用 `reload_memory()` 重新加载
3. **修改权限** → 仅在用户明确指示时修改
4. **加载失败时** → Agent 降级运行，使用硬编码的默认维度权重

---

## 文件结构（规划）

```
meme_ops/
├── MEMORY_PROMPT.md        # 🔴 共享层：长期记忆提示词（核心资产）
├── README.md               # 本文档：项目框架说明
├── personas/               # 🟡 Persona 层：用户群体 prompt
│   ├── investor.md         #   投研观察者
│   ├── operator.md         #   社区运营者
│   ├── builder.md          #   项目方
│   └── researcher.md       #   研究员
├── contracts/              # 🟣 链上合约（新增）
│   ├── PosterNFT.sol       #   ERC-721 海报 NFT 合约
│   └── deploy/             #   部署脚本
├── backend/                # 后端（待开发）
│   ├── main.py             # FastAPI 入口
│   ├── agent.py            # Agent 分析引擎（共享层+Persona 层合并加载）
│   ├── database.py         # 数据库操作
│   ├── auth.py             # 钱包签名验证（新增）
│   ├── nft.py              # NFT 铸造服务（新增）
│   └── requirements.txt
├── frontend/               # 前端（待开发）
│   ├── index.html          # 主页面（顶部导航: 社区/分析/我的）
│   ├── app.js              # 前端逻辑
│   │   ├── 钱包连接 + 签名登录
│   │   ├── 社区动态流（发帖/转发/点赞/引用）
│   │   ├── 用户中心（主页/关注/粉丝）
│   │   ├── 左侧栏切换 + Persona 切换
│   │   ├── 自选列表拖拽排序 + 编辑模式
│   │   ├── 海报瀑布流渲染 + Mint 上链按钮
│   │   └── 历史记录加载
│   └── style.css           # 暗色主题样式
├── sql/                    # 数据库（待开发）
│   └── schema.sql          # 含所有表
└── data/                   # 运行时数据（gitignore）
    └── meme_ops.db
```

---

## 参考项目 & 灵感来源

### 一、开源库（直接可用）

| 类别 | 项目 | ⭐ | 链接 | 用途 |
|------|------|:--:|------|------|
| 📈 图表 | **TradingView Lightweight Charts** | 10k+ | https://github.com/tradingview/lightweight-charts | 趋势分析图的 K 线/折线渲染 |
| 📈 图表 | **Apache ECharts** | 60k+ | https://github.com/apache/echarts | 雷达图、对比矩阵、多维可视化 |
| 🖼️ 海报生成 | **html2canvas** | 30k+ | https://github.com/niklasvh/html2canvas | 前端 DOM → 海报图片导出 |
| 🖼️ 海报生成 | **html-to-image** | 5k+ | https://github.com/bubkoo/html-to-image | 更轻量的 DOM → PNG/SVG |
| 🔄 拖拽排序 | **SortableJS** | 30k+ | https://github.com/SortableJS/Sortable | 自选列表拖拽排序 |
| 🔄 拖拽排序 | **react-beautiful-dnd** | 33k+ | https://github.com/atlassian/react-beautiful-dnd | React 版拖拽（若选 React） |
| 🔄 拖拽排序 | **vuedraggable** | 20k+ | https://github.com/SortableJS/vue.draggable.next | Vue 版拖拽（若选 Vue） |
| 📊 看板 UI | **Tremor** | 16k+ | https://github.com/tremorlabs/tremor | React 仪表盘组件库 |
| 🎨 图表封装 | **Recharts** | 24k+ | https://github.com/recharts/recharts | React 图表组件 |

### 二、同类开源项目（可参考架构/UI）

| 项目 | ⭐ | 链接 | 参考价值 |
|------|:--:|------|------|
| **TradingView Gratis** | 43 | https://github.com/outlinersclub-cpu/tradingview-gratis | Crypto 图表 Dashboard，Next.js + lightweight-charts，UI 布局参考 |
| **OnChain Sage** | 12 | https://github.com/degenspot/onchainsage | AI + 社媒情绪 + 链上数据融合分析，架构思路接近 |
| **SocialTradeBot** | 7 | https://github.com/plotJ/SocialTradeBot | Twitter 情绪 + Telegram + Token 综合分析 |
| **Crypto Crush** | 6 | https://github.com/jayden-n/crypto-crush | Mobile-first 自选列表 + 数据趋势，Watchlist UI 参考 |
| **Crypto Tracker** | 5 | https://github.com/sourav-357/crypto-tracker | React 自选列表 + 搜索 + 排序，前端交互参考 |
| **TAwarsAI Launchpad** | 3 | https://github.com/TAwarsAI/TAWarsAI-Launchpad | AI + Meme Coin 交易 Dashboard |
| **Meme Coin Signal Dashboard** | 1 | https://github.com/dantiezsaunderson/meme-coin-tracker | Meme Coin 信号追踪 Dashboard |
| **Memecoin Research** | 0 | https://github.com/nickyfin/memecoin-research | Twitter 数据 → Solana Meme 趋势分析 |
| **MemeCoin Tracker** | 0 | https://github.com/tirivashemutanho/MemeCoin-Tracker | Meme Coin 实时数据 Dashboard |
| **Meme Coin Tracker** | 0 | https://github.com/mesopotamia/meme-coin-tracker | Meme Coin 指标 Dashboard |
| **QuillCheck API** | 0 | https://github.com/ThrippleD/quillcheck-api-landing | Solana Token 风险/安全检查，风控模块参考 |

### 三、产品级 UI 参考（非开源，参考交互设计）

| 产品 | 链接 | 参考方向 |
|------|------|------|
| **DexScreener** | https://dexscreener.com | 链上数据实时展示、趋势图、代币详情页布局 |
| **GeckoTerminal** | https://www.geckoterminal.com | DEX 数据聚合、多链代币对比 |
| **Dune Analytics** | https://dune.com | 自定义链上数据仪表盘、图表布局 |
| **DefiLlama** | https://defillama.com | DeFi 数据 Dashboard、排版风格 |
| **CoinMarketCap** | https://coinmarketcap.com/watchlist | 自选列表交互、拖拽排序、多币对比 |
| **CoinGecko** | https://www.coingecko.com | 代币详情页、评分卡片 |
| **Token Metrics** | https://tokenmetrics.com | 代币评级卡片、资产评分展示 |
| **Messari** | https://messari.io | 结构化资产报告布局 |
| **Nansen** | https://www.nansen.ai | 链上行为标签、Smart Money 追踪 |
| **LunarCrush** | https://lunarcrush.com | 社媒情绪仪表盘、热度趋势 |

### 四、API / 数据源（待接入）

| 类别 | 产品 | 链接 | 用途 |
|------|------|------|------|
| DEX 数据 | DexScreener API | https://docs.dexscreener.com | 流动性、交易量、价格 |
| DEX 数据 | GeckoTerminal API | https://www.geckoterminal.com/dex-api | DEX 池数据聚合 |
| 链上数据 | Etherscan API | https://etherscan.io/apis | ETH 链 Holder、交易 |
| 链上数据 | Solscan API | https://solscan.io/apis | SOL 链 Holder、交易 |
| 链上数据 | Moralis | https://github.com/MoralisWeb3 | 多链数据 API |
| 社媒数据 | Twitter/X API | https://developer.x.com | 讨论量、情绪、KOL |
| 社媒数据 | LunarCrush API | https://lunarcrush.com/developers | 社媒情绪评分 |
| 综合数据 | CoinGecko API | https://www.coingecko.com/en/api | 代币基本信息、市场数据 |

### 五、DApp / Web3 开发（新增）

| 类别 | 项目 | ⭐ | 链接 | 用途 |
|------|------|:--:|------|------|
| 🔐 钱包连接 | **wagmi** | 6k+ | https://github.com/wevm/wagmi | React 钱包连接 hooks（首选） |
| 🔐 钱包连接 | **ethers.js** | 8k+ | https://github.com/ethers-io/ethers.js | 签名验证、合约交互 |
| 🔐 签名验证 | **siwe** | 2k+ | https://github.com/spruceid/siwe | Sign-In with Ethereum 标准 |
| 📜 合约开发 | **OpenZeppelin Contracts** | 25k+ | https://github.com/OpenZeppelin/openzeppelin-contracts | ERC-721 标准实现 |
| 📜 合约开发 | **Hardhat** | 7k+ | https://github.com/NomicFoundation/hardhat | Solidity 开发框架 |
| 📜 合约开发 | **Foundry** | 8k+ | https://github.com/foundry-rs/foundry | Solidity 测试/部署 |
| 🖼️ IPFS | **Pinata** | — | https://www.pinata.cloud | IPFS 托管 + 专用网关 |
| 🖼️ IPFS | **Web3.Storage** | — | https://web3.storage | 免费 IPFS 存储 |
| 🧪 测试链 | Monad Testnet | — | https://docs.monad.xyz | ChainID 10143 |
| 🧪 测试链 | Sepolia | — | https://sepolia.dev | Ethereum 测试网 |
| 🌐 前端 | **RainbowKit** | 3k+ | https://github.com/rainbow-me/rainbowkit | 钱包连接 UI 组件 |
| 📊 链上索引 | **The Graph** | 3k+ | https://github.com/graphprotocol | 链上事件索引查询 |
| 📱 社交协议 | **Lens Protocol** | 2k+ | https://github.com/lens-protocol | 去中心化社交图参考 |
| 📱 社交参考 | **Farcaster** | — | https://github.com/farcasterxyz | Web3 社交协议参考 |

---

## 免责声明

本产品仅提供链上数据整理、社区信息汇总和研究参考，**不构成任何投资建议**，不提供买入、卖出或价格预测建议。用户应自行判断风险。
