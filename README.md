# AI News Radar 🚀

AI 驱动的 A 股扫描推荐、技术指标筛选与飞书推送系统。

---

## 📋 功能总览

系统通过 **10 个 Windows 计划任务**自动运行，覆盖技术面选股、持仓分析、策略回测、信号跟踪四大场景。

| 时间 | 任务 | 功能 | 运行日 |
|---|---|---|---|
| **07:00** | `AI_News_Radar_WeChat` | 公众号股票推荐跟踪（已暂停） | 📅 每天 |
| **07:00** | `AI_News_Radar_Morning` | 早间预判 | 📅 周一\~周五 |
| **15:30** | `AI_News_Radar_Screener_PM` | 十全十美股票推荐 | 📅 周一\~周五 |
| **15:45** | `AI_News_Radar_ZLZY` | 主力捉妖股票推荐 | 📅 周一\~周五 |
| **16:00** | `AI_News_Radar_ZLZY_Track` | 主力捉妖跟踪池更新 | 📅 周一\~周五 |
| **19:00** | `AI_News_Radar_Evening` | 收盘复盘 | 📅 周一\~周五 |
| **19:05** | `AI_News_Radar_Strategy_Track` | 策略信号跟踪更新 | 📅 周一\~周五 |
| **周六 15:00** | `AI_News_Radar_Strategy_Report` | 策略回测周报 | 📅 周六 |
| **周六 15:15** | `AI_News_Radar_Cleanup` | 数据库清理 + VACUUM | 📅 周六 |

---

## 🔥 核心功能详解

### 1️⃣ 十全十美股票推荐（15:30 推送）

通达信「十全十美」多指标共振系统的 Python 实装。通过对 A 股全市场扫描，筛选出技术面首次共振的个股。

**筛选流水线：**

```
全市场股票 -> 排除ST/退市
  -> 成交额>=2亿 -> 取前200名
  -> 5日涨幅>=7%或涨停
  -> 流通市值100~1000亿
  -> SQSM评分>=9/10 且 昨日<9/10(首日共振)
  -> 推送飞书卡片
```

**10 个技术指标（通达信公式转 Python）：**

| # | 指标 | 判断逻辑 |
|---|---|---|
| 1 | **MACD** | DIFF > DEA（多头） |
| 2 | **KDJ** | K > D（金叉） |
| 3 | **RSI** | RSI5 > RSI13（短期强于中期） |
| 4 | **LWR** | LWR1 > LWR2（威廉指标金叉） |
| 5 | **BBI** | 收盘价 > 多空线（多头排列） |
| 6 | **ZLMM** | 短期动量 > 中期动量（动量向上） |
| 7 | **DBCD** | 异同离差 > 均线（底部背离） |
| 8 | **CGZ** | 持股线 > 下跌线（趋势向上） |
| 9 | **ZLGJ** | 主力资金线 > 均线（主力买入） |
| 10 | **ZJL** | 资金净流入 > 0（资金流入） |

9 分及以上（10 个指标中 ≥9 个满足）定义为 **共振**。系统只推送**今日首次共振**（昨日 < 9 分）的个股。

**公式推导源码：** `src/ai_news_radar/sqsm_indicator.py`

---

### 2️⃣ 主力捉妖股票推荐（15:45 推送）

通达信「主力捉妖」公式的 Python 实装。识别主力资金介入、量价齐升的短线爆发信号。

**筛选流水线：**

```
全市场股票 -> 排除ST/退市
  -> 成交额>=2亿 -> 取前1000名
  -> 5日涨幅>=7%或涨停
  -> 无市值限制
  -> ZLZY信号触发 且 昨日未触发(今日首次)
  -> 推送飞书卡片
```

**信号触发条件（通达信公式转 Python）：**

| 条件 | 含义 |
|---|---|
| **ABC1** | 光头阳线 + 涨幅 > 2.8% + 量能适中 |
| **量能基础** | 放量 / 短期爆量(换手65~500%) / 庄家吸筹 |
| **强势区域** | MACD > 0 多头排列 |
| **FILTER(28)** | 触发后 28 天屏蔽，防重复 |

原通达信公式见 `zlzy.txt`，Python 实现见 `src/ai_news_radar/zlzy_indicator.py`。

---

### 3️⃣ 主力捉妖跟踪池（16:00 推送）

每个交易日 16:00 自动更新，跟踪所有 ZLZY 信号的后续表现，**25 个交易日自动退出**。

**跟踪池内容：**
```
👹 主力捉妖跟踪池
跟踪25个交易日  |  当前11只  |  胜率5/11(45.5%)  |  均收益-0.38%

🔴 #1 上海合晶 (688584) 半导体  跟踪第2天
  起始日期: 2026-07-09
  跟踪价: 40.27  →  当前: 43.38  +7.72%（涨=红色）

🟢 #2 凯龙高科 (300912)  跟踪第5天
  起始日期: 2026-07-06
  跟踪价: 28.56  →  当前: 25.12  -12.04%（跌=绿色）
```

- 每只信号显示：所属行业、起始日期、跟踪价、当前价、涨跌幅
- 红涨绿跌，一目了然
- **核心代码：** `src/ai_news_radar/zlzy_tracker.py` → `run_zlzy_track.py`

---

### 4️⃣ 早间预判 & 收盘复盘（07:00 / 19:00 推送）

基于用户持仓的个性化股票 AI 分析日报，通过飞书私聊推送给每个用户。

**早间预判（07:00）：** 今日关注 + 短期趋势判断 + AI 操作建议 + 关键价位（入场/止损/目标）+ 风险评估

**收盘复盘（19:00）：** 今日行情总结 + 持仓盈亏统计 + 明日策略 + AI 技术分析

**信号分级体系（收盘复盘卡片内嵌）：**

| SQSM 评分 | 级别 | 标签 | 含义 |
|---|---|---|---|
| ≥ 9 且连续 ≥ 3 天 | **SS** | ★★★ 强烈关注 | 连续多日共振，趋势强劲 |
| ≥ 9 | **S** | ★★ 关注 | 首次共振或评分提升 |
| 7~8 分 | **A** | ★ 观察 | 接近共振区间 |
| 5~6 分 | **B** | 观望 | 中等评分 |
| < 5 分 | **C** | 不推荐 | 评分较低 |

**投资论文追踪（收盘复盘内嵌）：** 每日对比持仓股票的 SQSM 评分变化，自动判断：

- ✅ **信号维持** — 评分持续高分，原推荐逻辑依然有效
- ⚠️ **信号退化** — 评分明显下降（如"3日内从9/10降至5/10"），触发关注提醒
- 📈 **信号增强** — 评分显著上升，逻辑加强

**核心代码：** `cli.py check-stocks` → `stock_notifier.py`（含 `_grade_signal` + 论文追踪）→ `stock_analyzer.py`（DeepSeek API）

---

### 5️⃣ 微信公众号股票推荐跟踪（07:00 推送群聊，已暂停）

每天定时检查指定公众号的最新文章，提取股票推荐并持续跟踪 15 天。

**流程：** WeWeRSS 检查新文章 → 解析股票代码 → 首日推送卡片（含价格）→ 每日更新跟踪表格

**当前跟踪公众号：** 凡尘一灯、涨公主的后花园

**核心代码：** `scrapers/wechat_article.py` + `scrapers/stock_extractor.py`

---

### 6️⃣ 用户持仓管理

多用户股票持仓管理，通过飞书私聊 + CLI 交互。

```
ai-news stock 510300              # 添加自选
ai-news buy 510300 -p 3.95 -q 1000  # 记录买入
ai-news sell 510300               # 卖出平仓
ai-news portfolio                 # 查看持仓盈亏
ai-news watch 510300              # 开启每日监控
ai-news daily-on                  # 开启日报推送
```

---

## 📂 项目结构

```
ai_news_radar/
├── daily_runner.py              # 定时任务分发入口
├── run_screener.py              # 十全十美筛选脚本
├── run_zlzy.py                  # 主力捉妖筛选脚本
├── run_zlzy_track.py            # 主力捉妖跟踪池推送
├── run_strategy_track.py        # 策略信号每日更新
├── run_strategy_report.py       # SQSM策略周报推送
├── create_tasks2.bat            # 一键安装计划任务
├── zlzy.txt                     # 通达信原公式参考
├── .env                         # 飞书API密钥配置
│
├── src/ai_news_radar/
│   ├── cli.py                   # 命令行入口（所有命令）
│   ├── config.py                # 配置加载
│   ├── database.py              # SQLite数据库
│   ├── feishu_client.py         # 飞书卡片推送
│   ├── sqsm_indicator.py        # 十全十美10指标计算
│   ├── _sqsm_helper.py          # 十全十美子进程
│   ├── zlzy_indicator.py        # 主力捉妖指标计算
│   ├── _zlzy_helper.py          # 主力捉妖子进程
│   ├── _spot_helper.py          # 行情数据子进程
│   ├── _enricher_helper.py      # PE/PB/行业/财务数据子进程
│   ├── strategy_tracker.py      # 策略信号记录+跟踪+周报
│   ├── zlzy_tracker.py          # 主力捉妖跟踪池
│   ├── stock_notifier.py        # 股票日报推送
│   ├── stock_analyzer.py        # AI技术分析
│   ├── stock_scheduler.py       # 公众号跟踪+计算调度
│   └── scrapers/
│       ├── wechat_article.py    # 公众号抓取
│       └── stock_extractor.py   # 股票代码提取
│
├── data/                        # SQLite数据库文件
└── logs/                        # 运行日志
```

---

## 🔧 技术架构

### 子进程隔离模式

所有行情数据获取都在独立子进程中执行，主进程不直接调用 akshare，避免其内部的 py_mini_racer（JavaScript 引擎）崩溃影响主流程。

```
_spot_helper.py  子进程  -> 加载全市场5500+只股票行情
_sqsm_helper.py  子进程  -> 下载500天K线 + 计算十全十美
_zlzy_helper.py  子进程  -> 下载500天K线 + 计算主力捉妖
```

### 腾讯行情 API

使用 `http://ifzq.gtimg.cn/appstock/app/fqkline/get` 纯 JSON 接口获取 K 线数据。

使用 `https://qt.gtimg.cn/q={code}` 接口获取 PE/PB/52周等估值数据：

| 字段 | 含义 |
|---|---|
| qt[72] → arr[72] | 流通A股（股），流通市值 = 股价 x 流通股 / 1亿 |
| qt[38] → arr[38] | 换手率（%） |
| qt[44] → arr[44] | 总市值（亿） |
| **PE[39]** | 动态市盈率 |
| **PB[46]** | 市净率 |
| **52w高[47]** | 52 周最高价 |
| **52w低[48]** | 52 周最低价 |

### 东方财富财务数据

通过 `datacenter.eastmoney.com` 获取近 5 年核心财务指标（营收/净利润/增速/EPS/ROE/BPS），数据自动呈现在飞书推荐卡片上。

### Decimal 精度计算

所有市值计算使用 Python `decimal.Decimal` 替代 `float`，杜绝浮点数精度漂移问题。关键计算（市值 = 股价 × 流通股 / 1亿）精确到 28 位十进制。

---

## 🛠️ 运维管理

### 日志轮转

| 文件 | 轮转规则 | 保留 |
|---|---|---|
| `logs/app.log` | 每 1MB 自动轮转 | 保留最近 3 个备份 |
| `logs/task_runner.log` | 每 512KB 自动轮转 | 保留最近 3 个备份 |

日志不会无限膨胀，最新日志始终在 `.log`，旧日志自动归档为 `.log.1` / `.log.2` / `.log.3`。

### 数据库自动清理（`cleanup_old_data`）

每周六 15:15 自动执行，清理规则：

| 清理内容 | 保留周期 | 说明 |
|---|---|---|
| 旧新闻管线的文章（`articles`） | 30 天 | 旧爬虫数据 |
| 旧报告（`reports`） | 60 天 | 历史报告 |
| 旧公众号文章 + 引用（`wechat_articles` / `article_stock_refs`） | 60 天 | 公众号历史 |
| 策略信号跟踪明细（`strategy_signal_tracking`） | 随信号过期清除 | 60 天窗口 |
| VACUUM 回收磁盘空间 | 删除 > 100 条时触发 | 自动压缩数据库文件 |

### 任务失败告警

所有定时任务（`daily_runner.py`）通过 `_run_and_check()` 执行：

- 返回码非 0 → 自动向飞书群推送 **红色告警卡片**
- 告警内容：任务名称 / 返回码 / 失败时间
- 覆盖：十全十美 / 主力捉妖 / 公众号跟踪 / 策略跟踪 / 周报 / 数据库清理

---

## 💡 数据来源

| 数据类型 | 接口 |
|---|---|
| A股实时行情 | akShare + 腾讯API |
| 历史日K线 | ifzq.gtimg.cn（纯JSON） |
| 机构龙虎榜 | akShare |
| 行业分类（申万） | datacenter.eastmoney.com（`RPT_LICO_FN_CPD` 接口） |
| 公众号文章 | WeWeRSS |
| AI分析 | DeepSeek API |

---

## 🚀 部署指南

### 环境要求

- Python >= 3.10
- Windows 10/11
- 飞书企业自建应用（App ID / App Secret）

### 安装

```bash
pip install -e .
pip install akshare    # 用于机构净买入数据
```

配置 `.env`，填入 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`。

### 安装计划任务

```bash
# 一键安装
create_tasks2.bat

# 或手动逐个创建
schtasks /create /tn "AI_News_Radar_Morning" /tr "python -m ai_news_radar.cli check-stocks --mode morning" /sc weekly /d MON,TUE,WED,THU,FRI /st 07:00 /f
schtasks /create /tn "AI_News_Radar_Evening" /tr "python -m ai_news_radar.cli check-stocks --mode evening" /sc weekly /d MON,TUE,WED,THU,FRI /st 19:00 /f
schtasks /create /tn "AI_News_Radar_Screener_PM" /tr "python daily_runner.py screener_evening" /sc weekly /d MON,TUE,WED,THU,FRI /st 15:30 /f
schtasks /create /tn "AI_News_Radar_ZLZY" /tr "python daily_runner.py zlzy_evening" /sc weekly /d MON,TUE,WED,THU,FRI /st 15:45 /f
schtasks /create /tn "AI_News_Radar_ZLZY_Track" /tr "python daily_runner.py zlzy_track" /sc weekly /d MON,TUE,WED,THU,FRI /st 16:00 /f
schtasks /create /tn "AI_News_Radar_Strategy_Track" /tr "python daily_runner.py strategy_track" /sc weekly /d MON,TUE,WED,THU,FRI /st 19:05 /f
schtasks /create /tn "AI_News_Radar_Strategy_Report" /tr "python daily_runner.py strategy_report" /sc weekly /d SAT /st 15:00 /f
schtasks /create /tn "AI_News_Radar_Cleanup" /tr "python daily_runner.py strategy_cleanup" /sc weekly /d SAT /st 15:15 /f
```

> ⚠️ `python` 需替换为实际 Python 解释器绝对路径

---

## 📖 CLI 命令参考

```bash
ai-news --help                    # 查看所有命令

# 持仓管理
ai-news stock 000001              # 添加自选
ai-news buy 000001 -p 10.5        # 记录买入
ai-news sell 000001               # 卖出
ai-news portfolio                 # 查看持仓

# 运行任务
ai-news check-stocks              # 手动执行日报推送
ai-news wechat-track              # 手动执行公众号跟踪

# 查询
ai-news wechat-list               # 近期公众号文章
ai-news wechat-recommend          # 近期推荐股票汇总
ai-news sqsm 000001               # 查询十全十美指标
```
