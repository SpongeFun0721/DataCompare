# 数据比对工具 (DataCompare)

Excel × PDF 年度报告数值核对工具。

## 功能

上传 Excel 指标表和 PDF 报告文件，系统自动从 PDF 提取数值并与 Excel 目标值比对，生成核对报告。

- **自动匹配** — 根据 AI 数据来源行中的 `+P页码` 标注，在指定 PDF 页码中搜索数值
- **多来源** — 支持年鉴（按年份匹配）、司局报告（按机构名+年份+文本相似度匹配）、URL/AI 来源
- **一键核对** — 数值匹配自动确认，无需手动逐项核对
- **PDF 内联预览** — 直接在浏览器中查看 PDF，自动定位到匹配页码和高亮数值
- **AI 分析** — 对 URL 来源使用 DeepSeek API 智能分析网页文本中的指标数据
- **报告导出** — 导出核对结果 Excel、标色原表、PDF 提取纯文本

## 技术栈

- **后端**: Python / FastAPI / PyMuPDF / openpyxl / pandas / Redis
- **前端**: React 19 / Vite / Tailwind CSS 4 / react-pdf
- **AI**: DeepSeek API

## 快速开始

```bash
# 后端
pip install -r requirements.txt
uvicorn backend.app:app --reload

# 前端
cd frontend
npm install
npm run dev
```

## 目录结构

```
├── backend/           # FastAPI 后端
│   ├── app.py              # 应用入口与 API 路由
│   ├── config.py           # 全局配置
│   ├── models.py           # Pydantic 数据模型
│   ├── excel_reader.py     # Excel 读取
│   ├── comparator.py       # 核心比对引擎
│   ├── pdf_extractor.py    # PDF 文本提取
│   ├── number_finder.py    # 数值提取
│   ├── fuzzy_matcher.py    # 模糊匹配
│   ├── ai_analyzer.py      # DeepSeek AI 分析
│   ├── request_controller.py # 请求调度与限流
│   ├── scheduler.py        # 任务调度器
│   └── report_generator.py # 报告生成
├── frontend/          # React 前端
│   └── src/
│       ├── api.js              # API 调用封装
│       ├── hooks/              # 自定义 Hooks
│       └── components/         # UI 组件
├── cache/             # Redis 与内存缓存
└── data/shared_pdfs/  # PDF 数据目录
```

详细文档见 [项目参数文档.md](项目参数文档.md)。
