言海瞭望 - 青少年网络言论健康监测与干预系统
================================================

一、项目简介
------------------------------------------------
本项目是一个面向校园网络言论场景的心理健康风险识别、预警审核、归档管理和干预跟进系统。

系统采用前后端一体运行方式：
1. 后端使用 FastAPI 提供接口、托管静态页面，并管理 SQLite 数据库。
2. 前端使用原生 HTML / CSS / JavaScript，配合 Bootstrap、Font Awesome 和 ECharts。
3. 默认演示数据库位于 datasets/runtime/runtime_showcase_demo_v2.db。
4. 系统内置本地多标签风险识别模型，模型文件位于 backend/model。
5. 干预中心支持 DeepSeek 在线生成干预建议，也支持在配置缺失或网络异常时自动生成本地兜底草稿。

线上演示地址：
https://speechhealth.cn

二、目录结构
------------------------------------------------
项目根目录主要包含：

1. backend
   后端服务、API 路由、数据库模型、爬虫服务、风险分析、模型配置和启动脚本。

2. frontend
   前端页面和静态资源。
   - login.html：登录页
   - dashboard.html：总览页
   - posts.html：帖子总览
   - alerts.html：预警中心
   - archive.html：归档中心
   - intervention.html：干预跟进工作台
   - assets/css/style.css：全局样式
   - assets/js/*.js：页面脚本

3. datasets
   演示数据库、训练数据、评估数据、人工标注数据和指标文件。
   - runtime：默认演示数据库
   - training：模型训练数据
   - evaluation：模型评估数据
   - manual_labels：人工标注数据
   - metrics：模型评估指标和报告材料

三、运行环境
------------------------------------------------
推荐环境：
1. Windows 10 / Windows 11，或 Ubuntu 20.04 及以上。
2. Python 3.10 或 3.11。Python 3.12/3.13 在本机也可运行，但比赛复现建议优先使用 3.10/3.11。
3. 可以联网安装 Python 依赖。

依赖文件：
1. backend/requirements.txt
   包含 FastAPI、Uvicorn、SQLAlchemy、Playwright、openpyxl 等基础运行依赖。

2. backend/requirements-model.txt
   包含 torch、transformers 等本地模型推理依赖。

说明：
1. 如果只查看页面、数据列表、预警、归档和导出功能，安装 backend/requirements.txt 即可。
2. 如果需要启用本地模型推理能力，请继续安装 backend/requirements-model.txt。
3. 如果需要真实抓取贴吧数据，请安装 Playwright Chromium。

四、启动方式
------------------------------------------------
在 PowerShell 或终端进入项目根目录后执行：

Windows：

cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-model.txt
playwright install chromium
python run.py

Linux / Ubuntu：

cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-model.txt
playwright install chromium
python run.py

启动成功后访问：

http://127.0.0.1:8000

或：

http://localhost:8000

五、登录信息
------------------------------------------------
默认管理员账号：

用户名：admin
密码：123456

说明：
1. 当前登录逻辑用于比赛演示流程，登录成功后返回 demo-token。
2. 公网长期部署时建议更换默认密码，并增加后端真实鉴权。

六、主要功能
------------------------------------------------
1. 登录页
   管理员进入系统工作台。

2. 总览页
   展示帖子总数、预警数量、高风险数量、待处理数量、抓取任务和抓取健康状态。
   支持手动更新抓取。

3. 帖子总览
   查看已入库帖子，支持关键词检索、风险等级筛选和分页浏览。

4. 预警中心
   查看风险预警列表，支持审核预警、处理预警、归档预警和导出 Excel。

5. 归档中心
   查看历史归档记录，支持导出归档 Excel。

6. 干预跟进
   集中处理高风险帖子队列。
   支持查看帖子内容、风险依据、AI 生成干预建议、人工审核、保存草稿、驳回建议和记录已处理状态。

七、主要后端接口
------------------------------------------------
认证：
POST /api/auth/login

总览：
GET /api/dashboard/overview
GET /api/dashboard/risk-distribution
GET /api/dashboard/source-ranking
GET /api/dashboard/recent-alerts
GET /api/dashboard/crawl-health

帖子：
GET /api/posts
GET /api/posts/{post_id}

预警：
GET /api/alerts
GET /api/alerts/export
POST /api/alerts/{alert_id}/review
POST /api/alerts/{alert_id}/archive

归档：
GET /api/archive
GET /api/archive/export

抓取：
POST /api/crawler/manual-refresh
POST /api/crawler/tieba/preview
POST /api/crawler/tieba/run

干预：
GET /api/interventions
GET /api/interventions/{intervention_id}/reply
POST /api/interventions/{intervention_id}/reply/generate
PUT /api/interventions/{intervention_id}/reply/review
POST /api/interventions/{intervention_id}/reply/mark-sent
GET /api/interventions/{intervention_id}/reply/logs

八、数据库说明
------------------------------------------------
默认数据库路径：

datasets/runtime/runtime_showcase_demo_v2.db

如需指定其他数据库，可以设置环境变量 MONITORING_DB_PATH。

Windows 示例：

$env:MONITORING_DB_PATH="C:\path\to\your.db"
python run.py

Linux 示例：

export MONITORING_DB_PATH="/path/to/your.db"
python run.py

后端启动时会自动：
1. 创建缺失的数据表。
2. 初始化演示账号和抓取源配置。
3. 将上次异常中断的 running 抓取任务标记为 failed。

九、模型与数据说明
------------------------------------------------
本地模型文件位于：

backend/model

主要文件：
1. model.safetensors：模型权重。
2. config.json：模型配置。
3. labels.json：风险标签配置。
4. tokenizer.json / tokenizer_config.json：分词器配置。

已整合的数据文件位于 datasets，包含训练集、评估集、人工标注样本、指标文件和演示数据库。

十、DeepSeek 在线生成配置
------------------------------------------------
干预中心的“生成干预建议”按钮会调用：

backend/deepseek_config.json

配置格式：

{
  "api_key": "你的 DeepSeek API Key",
  "model": "deepseek-chat",
  "base_url": "https://api.deepseek.com/chat/completions"
}

说明：
1. 如果 deepseek_config.json 不存在，系统会自动使用本地兜底草稿。
2. 如果 API Key 无效、余额不足、网络异常或接口超时，系统也会自动使用本地兜底草稿。
3. 判断是否真正调用 DeepSeek 成功，可查看接口返回中的 generate_status。
   - success：在线模型生成成功。
   - fallback：使用了本地兜底草稿。

十一、Excel 导出说明
------------------------------------------------
预警中心和归档中心支持导出 Excel。

导出依赖：

openpyxl

该依赖已写入 backend/requirements.txt。如果导出时报错，请确认当前虚拟环境中已安装：

python -c "import openpyxl; print(openpyxl.__version__)"

十二、常见问题
------------------------------------------------
1. 打不开 http://127.0.0.1:8000

请确认后端终端中 Uvicorn 已启动成功，也可以检查 8000 端口是否被占用。

2. 登录失败

请确认默认账号为 admin / 123456。如果修改过数据库用户表，请使用数据库中的账号密码。

3. 页面能打开但接口没有数据

请确认默认数据库文件存在：

datasets/runtime/runtime_showcase_demo_v2.db

如果设置了 MONITORING_DB_PATH，请确认该路径有效。

4. Excel 导出失败

请确认已安装 backend/requirements.txt 中的 openpyxl。

5. 手动抓取失败

请检查：
1. 网络是否正常。
2. Playwright Chromium 是否已安装。
3. 目标网站是否出现验证码、访问限制或页面结构变化。

6. 干预建议生成显示“本地草稿”

说明 DeepSeek 未成功调用。请检查 backend/deepseek_config.json、API Key、网络和账号额度。


十三、当前版本特点
------------------------------------------------
1. 前后端一体运行，启动后端即可访问完整页面。
2. 集成 v2 风险识别模型和演示数据库。
3. 总览页集中展示抓取状态，并保留手动更新抓取入口。
4. 帖子总览页聚焦查询和浏览。
5. 预警中心和归档中心支持 Excel 导出。
6. 干预跟进页支持 DeepSeek 在线生成、人工审核和处理记录闭环。


十四、许可证
------------------------------------------------
本项目基于 MIT 许可证开源，完整条款见仓库根目录的 LICENSE 文件。

Copyright (c) 2026 袁嘉睿

说明：MIT 许可证覆盖本仓库的源代码。系统展示、模型评估和文档中涉及的公开网络文本数据，
仅用于研究与作品演示用途，不代表对原始文本内容享有任何权利。
