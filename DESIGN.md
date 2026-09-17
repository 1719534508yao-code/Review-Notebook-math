# 试卷错题整理软件 — 设计决策与分工文档（v1 数学先行版）

> 本文档是与用户（北京高三学生）逐轮澄清后的**完整需求决策记录 + 技术方案 + 模块分工说明**。
> 供参与开发的各 agent 分工使用。任何人动工前先读本文档；与本文件冲突的旧假设以本文件为准。
> 最后更新：2026-09-14

---

## 1. 背景与目标

用户手上有大量北京各区一模/二模数学试卷（做过、已核对答案）。要做一款本地软件：

**上传试卷 PDF → 按小题题号自动切片 → AI 识别知识点与题型 → 错题/对题分别归档 → 知识点气泡图 + 每周总结 + 下周题型推荐。**

第一版只覆盖**数学**一科，但架构必须预留科目维度（知识点树、题型选项都挂在科目下，未来加英语/物理等科目时不改表结构）。

### 三大核心功能（用户原始表述）
1. **知识点气泡图**：每条叶子知识点可见；每个小类别上显示「做过几道用此知识点的题」与错误率。
2. **切片-识别-归档**：整套卷按题号 1,2,3… 切片；用户标注的错题 → 识别知识点 + 题型（题型从用户预设选项中选）→ 归档到**错题本**；未标注的正确题 → 同样识别归档到**题库**。
3. **周总结**：过去一周做了哪些知识点的题、哪个知识点错得多；推荐下一周做什么题型。

---

## 2. 需求澄清结果（用户已拍板，勿再改动）

| # | 问题 | 用户决策 | 对实现的含义 |
|---|------|---------|-------------|
| 1 | 试卷 PDF 形式 | **文字版为主**（可选中文字） | 可用 PyMuPDF 文字块坐标 + 正则定位题号切片；无需 OCR 版面分析兜底（但保留人工修正入口） |
| 2 | 错题如何录入 | **在软件界面里点选题号**（放弃识别纸面红圈） | 复核页每题一个「标为错题」开关；is_wrong 由用户点击决定，100% 准确、零识别成本 |
| 3 | 软件形态 | 用户无偏好，「好用就行」→ **由我定为本地 Web 应用** | Mac 上一行命令启动（run.sh），浏览器打开；数据全存本地 SQLite；不部署上云 |
| 4 | 科目范围 | **先数学，跑通再加科目** | 表结构带 subject_id；UI 有科目切换器但第一版只有「数学」 |
| 5 | AI 识别引擎 | 用户明确要**单独办按量付费的识图 API**，选定 **通义 Qwen-VL-Max**（阿里云百炼） | OpenAI SDK 走 DashScope 兼容端点 `https://dashscope.aliyuncs.com/compatible-mode/v1`，模型 `qwen-vl-max`；key 存 `.env` 的 `DASHSCOPE_API_KEY` |
| 6 | 知识点库来源 | **预置 + AI 自动扩充** | 种子 JSON（约 10 大类 / 90 叶子）；AI 发现库里没有的考点时产出「新增建议」，UI 一键确认后写入树 |
| 7 | 复习类功能 | **第一版不做**（只要能看能统计） | 无错题重做、无组卷导出、无掌握度；题库/错题本仅浏览+筛选 |
| 8 | 一题多知识点 | **主 + 次，只有主标签计数** | `question_kps.is_primary`；气泡图/错误率/周报统计只算主标签；次标签仅在题目详情展示 |
| 9 | 源 PDF 去留 | **切片后不留源文件** | 上传的纯净版 PDF（无手写）只作临时输入，切片完成后删除；一切以切片 PNG 为存档对象 |
| 10 | 切片归档到文件夹 | **题库**：`数学/题库/{年份}/{城区}/{考试类型}/`（三层，题型写在文件名里）；**错题本**：`数学/错题本/{题型}/`（直接按题型一层） | 除 SQLite 外，文件系统里就有肉眼可见的归档文件夹（Finder 可直接浏览）；写库和写文件同时发生 |
| 11 | 每周总结载体 | **Markdown 文件存入 `数学/计划库/`** | `周报-{起}-{止}.md`，同时界面里也能看（界面读同一份 md 或同源数据渲染） |
| 12 | 试卷元信息来源 | **文件名不可靠，上传后在软件里手选** 区/年份/考试类型（软件记住上次选择，连续传同区卷子只点两下） | 不做文件名正则解析；表单三下拉：年份(2023-2027)/城区(北京 18 区)/类型(一模|二模|期末|其他) |
| 13 | 数学题型预设（用户给定，共 9 类） | **选择题、填空题、三角函数、立体几何、概率统计、数列、解析几何、函数导数、新定义题** | 这就是错题本文件夹的分类目录，也是 AI 必选的封闭枚举；注意它混合了卷面形式（选择/填空）与解答题板块（按内容分），AI 打标时：客观题按卷面归入选择题/填空题，解答题按内容板块归入其余六类，压轴新颖题归新定义题 |
| 14 | 数学卷题量 | **一律 21 题，不存在第 22 题**（2026-09-16 补，用户确认其手上所有数学卷均为 21 题） | 切片器默认最大题号 = **21**（`pdf_slicer.EXPECTED_MAX_DEFAULT`，原为 22）；**首尾的推测性缺口不再建成「未定位」幽灵题**，只报警告。原先按 22 找会凭空多出一个「第 22 题（无切片、无知识点）」，混进题库与气泡图的 `without_kp`。若真的锚定到第 22 题（说明后面确有内容/或误切），**保留不丢**并明确警告请人工复核 —— 宁可多留一道让人看一眼，也不静默丢题。非数学科目或特例卷传 `expected_max` 覆盖即可 |

### 环境事实（已探明）
- 机器：macOS（darwin 27.0），Apple Silicon 与否未测但不影响。
- Python 3.9.6 系统自带；Pillow、sqlite3 可用；**fastapi/uvicorn/pymupdf/openai/dashscope 均未装** → 需要 requirements.txt + 首次安装。
- Node v24.20 存在，但**不使用**（避免构建链；前端为原生 HTML/JS + vendored D3）。
- 项目目录 `/Users/yao/ai程序库/试卷集` 当前为空，全新项目，无 git。

### 环境风险与对策
- Python 3.9 偏老：语法避免 `match`、避免 `X | Y` 类型联合（用 `Optional[X]`），依赖版本钉死在 3.9 兼容范围。
- 无 key 也能开发：AI 识别做成可跳过（见 §6.4），开发初期全手动模式跑通流程。

---

## 3. 总体技术方案

**栈**：Python 3.9 + FastAPI（API + 托管静态前端）+ PyMuPDF（PDF 文字块提取/渲染切片）+ SQLite（单文件 `storage/app.db`）+ 原生 HTML/CSS/JS 单页前端，气泡图用 D3 v7 force-pack（vendored 本地引入，不用 CDN）。

**原则**：
- 零构建工具链：改文件刷新浏览器即生效；agent 间只靠本文件 + 代码注释对齐。
- **切片后不留源 PDF**（上传的纯净版仅作临时输入，切片成功即删）。
- **归档双轨**：数据库记录之外，切片 PNG 必须物理落入 `archive/` 文件夹树（题库三层、错题本按题型一层，见 §5.5），Finder 里肉眼可见；周报以 Markdown 落入 `archive/数学/计划库/`。
- AI 只做「识别打标」，统计与推荐全部用确定性 SQL/规则计算（省钱、可复现）。

## 4. 项目结构

```
试卷集/
├── run.sh                  # 一键：venv+装依赖(首次) → 启动 uvicorn → 打开浏览器
├── app.py                  # FastAPI 入口：路由注册 + /static 托管 + 启动时建表/导种子
├── backend/
│   ├── __init__.py
│   ├── db.py               # 连接、建表、迁移、通用查询helper
│   ├── models.py           # 表结构 DDL（单一事实来源，db.py 引用执行）
│   ├── kp_seed.py          # 读 data/kp_math.json 幂等导入
│   ├── pdf_slicer.py       # 题号定位 + 切片渲染
│   ├── tagger.py           # Qwen-VL-Max 打标（图+文→严格JSON）+ 校验 + 新知识点建议
│   ├── stats.py            # 气泡图数据、周总结、题型推荐
│   └── api.py              # 所有 REST 路由（app.py include_router）
├── data/
│   ├── kp_math.json        # 预置数学知识点树
│   └── qtypes_math.json    # 预置题型选项（用户给定的 9 类，见 §2-13）
├── static/
│   ├── index.html          # 单页：上传 | 复核 | 错题本 | 题库 | 气泡图 | 周报 六个 view
│   ├── app.js
│   ├── style.css
│   └── vendor/d3.v7.min.js # vendored（开发时下载放置一次即可）
├── storage/                # 仅缓存：app.db、uploads 临时 PDF、切片中间态
├── archive/                # 用户的「软件可见」归档（Finder 直接可逛，详见 §5.5）
│   └── 数学/{题库,错题本,计划库}/
├── .env.example            # DASHSCOPE_API_KEY=
├── requirements.txt        # fastapi uvicorn pymupdf openai python-dotenv（钉 3.9 兼容版本）
└── README.md               # 给用户的使用说明（3 分钟上手）
```

## 5. 数据模型（SQLite，全部带科目维度）

```sql
subjects(id INTEGER PK, name TEXT UNIQUE);                    -- 预置: 数学
knowledge_points(id PK, subject_id FK, name TEXT, parent_id FK NULL,
                 origin TEXT CHECK(origin IN('preset','ai')),
                 status TEXT CHECK(status IN('confirmed','pending')),
                 UNIQUE(subject_id, parent_id, name));        -- 树: 大类→叶子(parent_id NULL=大类)
qtype_options(id PK, subject_id FK, name TEXT, UNIQUE(subject_id,name));
exams(id PK, subject_id FK, district TEXT, exam_type TEXT,   -- '一模'|'二模'|'期末'|'其他'(手选)
       year INTEGER, title TEXT, uploaded_at TEXT);          -- 不存 pdf_path：源文件切片后即删
questions(id PK, exam_id FK, number INTEGER,                 -- 题号 1..21（数学卷规则，见 §2-14）
       page_start INTEGER, page_end INTEGER,                 -- 切片来源页(0-based)
       image_path TEXT,                                      -- archive/ 下的最终归档路径(相对项目根)
       raw_text TEXT,
       is_wrong INTEGER NULL,                                -- NULL=未标 0=对 1=错(用户点选)
       qtype_id FK NULL, status TEXT CHECK(status IN('pending_slice_confirm','untagged','tagged')),
       confidence REAL NULL,
       UNIQUE(exam_id, number));
question_kps(question_id FK, kp_id FK, is_primary INTEGER, PRIMARY KEY(question_id,kp_id));
new_kp_suggestions(id PK, question_id FK, parent_hint TEXT, proposed_name TEXT,
       reason TEXT, status TEXT CHECK(status IN('pending','approved','rejected')));
```

视图：`v_wrong_bank` = questions WHERE is_wrong=1 AND status='tagged'；`v_pool` = is_wrong=0 AND tagged。

## 5.5 归档文件夹布局（用户的真实诉求：Finder 里能逛）

```
archive/数学/
├── 题库/
│   └── {年份}/{城区}/{考试类型}/
│       └── {题号}_{题型}.png            # 例: 2025/海淀/一模/7_选择题.png（正确题切片落这里）
├── 错题本/
│   └── {题型}/                          # 直接按 9 个题型分一层
│       └── {年份}{城区}{考试类型}_{题号}.png   # 例: 错题本/函数导数/2025海淀一模_19.png
└── 计划库/
    └── 周报-{起YYYYMMDD}-{止YYYYMMDD}.md        # 每周总结的持久载体（见 T7）
```

规则：
1. **写库与写文件同时发生**：T3 复核确认后（is_wrong 定档），若 T4 尚未打标则先放 `题库/{年}/{区}/{型}/` 暂用 `_未分类` 后缀；打标完成后**移动/重命名**为 `{题号}_{题型}.png`；错题在打标完成时**移入** `错题本/{题型}/`。即：文件位置永远反映最新归档态，无重复副本。
2. 元信息（区/年/型）**手选**：文件名不解析（§2-12）；上传表单三下拉，记住上次选择。
3. 城区枚举预置北京 18 区（东/西/朝/海/丰/石/通/昌/大/顺/房/门/延/怀/密/平/兴/延→用全称），可手填。
4. 路径写入 `questions.image_path`（相对项目根，UI/统计都读它）。
5. 计划库 md 是**主载体**：界面「周报」view 直接 fetch 并 marked 渲染最新 md（marked 也 vendored，或 T7 里用后端把 md 转 HTML）。

## 6. 模块规格与分工（每个模块 = 一个可独立交给 agent 的任务）

### T1 地基（agent A，最先做）
requirements.txt / app.py / db.py / models.py / kp_seed.py / .env.example / run.sh 骨架 / 空路由冒烟。
- **产出验收**：`./run.sh` 起服务，`GET /api/health` 返回 200；`GET /api/kp/tree?subject=数学` 返回完整种子树；重复启动不重复导入种子（幂等按 name+parent 判重）。
- 同时由本任务撰写 `data/kp_math.json`：10 大类（集合与逻辑、函数与导数、三角函数与解三角形、数列、不等式、立体几何、解析几何、平面向量、概率与统计、计数原理与二项式），每类 6~12 叶子、共约 90 条，叶子粒度到「导数与切线」「圆锥曲线·轨迹方程」这一级。`qtypes_math.json`：用户给定 9 类 = **选择题、填空题、三角函数、立体几何、概率统计、数列、解析几何、函数导数、新定义题**（顺序即展示顺序）。另预置北京 18 区列表常量（backend/db.py 或 data/districts.json）。

### T2 PDF 切片（agent B）
`backend/pdf_slicer.py`：
- 输入 PDF → PyMuPDF `page.get_text("blocks")`；**双栏**：按页宽中点分栏，栏内按 y 排序，页间接续。
- 题号定位：栏内块首正则 `^(\d{1,2})[．.、\s]`，仅当数字 = 上一题号+1 时接受（防「例1」「2x+1」误判）；边界取相邻题号块中点。
- **单题跨页（必做，常见场景）**：若第 N 题直到页底仍未遇到第 N+1 题号 → 该题 = 前页 rect₁（题号起点→页底）+ 后页 rect₂（页顶→下一题号起点，双栏时取后页左栏顶部起）。两个 rect 各渲染 2x PNG 后用 Pillow **纵向拼接成一张完整切片**（两栏版式时按 栏内顺序 先右栏余下部分再左栏，注意北京卷实际为左右两栏从上到下、先左后右——以真实卷子调通为准）。raw_text 同样跨页合并。
- 渲染：每题 → `storage/crops/{exam_id}/{num}.png`（**中间态暂存区，确认后才移入 archive/**，见 §5.5）。
- 失败兜底：接续/边界判断错误时（页眉、装饰线干扰），复核页提供「与上一题合并 / 拆分」手动修正；缺口题标「未定位」+「手动设置页范围」。
- **产出验收**：用用户真实卷子：21 题边界目测正确（题量见 §2-14）、**故意含一道跨页题验证拼接完整**、含图大题不被截断、双栏卷不乱序。
- **依赖**：需要用户提供 1~2 套真实 PDF（见 §8）。可先自造测试 PDF（生成一份题目跨页的双栏文字版 PDF 作回归用例）。

### T3 上传/复核 API + 前端（agent B 或 C）
- 路由：`POST /api/exams`(multipart 上传 + 手选 年份/城区/考试类型)→同步切片、status=pending_slice_confirm、**保留源 PDF 到 confirm 成功为止**（防切片失败需要重来）；`GET /api/exams/{id}/questions`；`PATCH /api/questions/{id}`（改 is_wrong/qtype/题号文本）；`POST /api/exams/{id}/confirm`（点「开始 AI 识别」→ 触发 T4 异步任务 → **confirm 成功后删除源 PDF**）；题目图片静态路径 `/storage/crops/...` 与 `/archive/...` 双挂载。
- 正确题在 confirm 后、打标前：先把切片从 storage/crops 复制到 `archive/数学/题库/{年}/{区}/{型}/{题号}.png`（打标后 T4 改名为 `{题号}_{题型}.png`）。
- 前端 view「上传」：选文件 + 三个下拉（记住上次选择）→ 进度。view「复核」：网格卡片=切片缩略图+题号，**点击红框=标错题**，题型下拉（9 类，AI 填完前可手选）；顶部横幅显示「AI 建议新增知识点 ×× [确认][忽略]」；底部「开始识别」按钮。
- **验收**：上传→复核→点错 3 题→确认，全链路 UI 可用；确认后源 PDF 消失、题库目录出现未打标切片。

### T4 AI 打标（agent D）
`backend/tagger.py`：
- OpenAI SDK 指向 DashScope 兼容端点；无 `DASHSCOPE_API_KEY` 时 `识别` 按钮降级为「手动模式」提示（不崩、给明确文案），且 PATCH 接口允许手填 kp/qtype —— 此降级路径是硬性要求。
- Prompt 输入：题目 PNG(base64) + raw_text + 紧凑树文本（`大类/小类` 每行）+ 题型选项；输出**严格 JSON**（用 response_format json_object 并在代码里再校验）：
  `{"qtype": "...", "primary_kp": "...", "secondary_kps": ["..."], "new_kp": {"parent": "...", "name": "...", "reason": "..."} | null, "confidence": 0.0-1.0}`
- 题型判定规则（写进 system prompt，对应 §2-13 的混合枚举）：客观题按卷面形式归入「选择题」（单选/多选都算）或「填空题」；解答题按内容板块归入 三角函数/立体几何/概率统计/数列/解析几何/函数导数；形式新颖、依赖题面新概念的压轴类归「新定义题」。
- 校验：qtype ∈ 选项、kp ∈ 树（含路径解析）；不合法→retry 1 次→仍失败则 tagged 但 qtype/kp 留空待手动补。
- 逐题串行 + `asyncio.Lock` 防并发烧钱；每题完成后写 question_kps（主 1 条）+ 落 new_kp_suggestions(status=pending)。
- **归档落地**（每题打标成功的瞬间）：错题 → 切片从 `storage/crops/` 移入 `archive/数学/错题本/{题型}/{年}{区}{型}_{题号}.png`；正确题 → 把 §5.5 的暂存切片重命名补上 `_{题型}` 后缀。`image_path` 随之更新。新知识点被确认时不重命名文件（文件名只认题型，与知识点无关）。
- 确认新知识点路由：`POST /api/kp_suggestions/{id}/approve`（建叶子+回填该题主标签）/ `reject`。
- **验收**：5 题人工比对合理；断 key 时清晰报错；单套卷成本 < ¥0.1 量级（qwen-vl-max 约 0.02元/千token，一题约 2-3k token）。

### T5 错题本/题库浏览（agent E）
`GET /api/wrong_bank`、`GET /api/pool`：筛选参数 subject/kp_id/qtype_id/district/exam_type/日期；分页；返回含图片路径与主次标签。前端两 view 共用组件。
**验收**：T3 点错的 3 题出现在错题本、其余出现在题库；按知识点筛选正确。

### T6 气泡图（agent E 或 F）
`GET /api/stats/bubbles?subject&district&exam_type&from&to`：每个叶子 `{id,name,path,done,wrong,error_rate}` + 小类聚合 `{name,done,wrong,error_rate,children}`（**统计只数 primary kp**）。
前端 D3 force-pack：大小=done，颜色=error_rate 连续色带（绿→黄→红），大类成簇；hover tooltip（题数/错误率）；点击叶子→抽屉列该知识点错题缩略图；筛选条复用 T5 参数。
**验收**：气泡数字与列表条数一致；次标签不计数。

### T7 周总结与推荐（agent F）
`GET /api/stats/weekly?days=7`：
- 本周做题数按知识点聚合表；错误率 top3（样本≥3 才进榜）；各题型正确率。
- 推荐规则：对「错误率高 × 近 14 天未练 × 样本≥3」的 primary kp，取其出现最多的题型，输出 2-3 条 `{kp, qtype, reason}` + 一句人话总结。纯 SQL/Python 规则，不用 LLM。
- **落盘为主（§2-11）**：生成/刷新时同时写 `archive/数学/计划库/周报-{起}-{止}.md`（含：本周做题清单表、错点分析、下周题型建议；重生成覆盖同名文件）。前端「周报」view 通过 `GET /api/stats/weekly/report` 读取该 md 渲染（marked vendored 进 static/vendor/）。
**验收**：改 created_at 模拟上周数据，窗口/门槛/排序正确；md 文件真实出现在计划库且内容完整；界面显示与文件一致。

### 任务依赖
```
T1 ──┬─→ T2 ─→ T3 ─→ T4 ─→ T5 ─→ T6 ─→ T7
     └─→ (data种子文件随T1交付，T4/T6引用)
可并行：T2 与 T1；T5/T6/T7 互不依赖可并行（都依赖 T4 产出的 tagged 数据）
```
接口契约以 §5 表结构 + 上述路由名为准；先合 T1 再开工下游，避免 schema 漂移。

## 7. 端到端验收（全部完成后）
`./run.sh` → 浏览器自动打开 → 上传真实卷（手选 年/区/型）→ 复核点错 3 题 → AI 识别（含 1 条新知识点建议，点确认）→ 错题本=3、题库=其余 → **Finder 打开 archive/ 看到：源 PDF 已消失、错题 PNG 在 `错题本/{题型}/`、正确题 PNG 在 `题库/{年}/{区}/{型}/{题号}_{题型}.png`** → 气泡图颜色随错误率变化 → 计划库目录出现周报 md 且界面渲染一致 → 全程无 key 时同样可完整走「手动模式」。

## 8. 需要用户提供（阻塞项）
1. **真实数学一模/二模 PDF 1~2 套** —— T2 切片调参必需（放到 `storage/uploads_demo/` 或直接对话提供路径）。
2. **DASHSCOPE_API_KEY** —— 在 https://bailian.console.aliyun.com 开通并创建，写入项目 `.env`（照 `.env.example`）。开发初期可缺省，走手动模式。

## 9. 已否决/暂缓的方案（防止后续 agent 重新发明）
- 识别纸面红笔标注来自动判错题 —— 用户选择界面点选（准确率 100%）。
- 桌面打包 App / 手机端 —— 形态定为本地 Web；手机暂不需要。
- OCR 版面分析切片 —— 用户卷子为文字版 PDF，坐标正则足够。
- LLM 生成周报文案 —— 用规则计算，零额外成本、可复现。
- 错题重做、掌握度、组卷导出 PDF —— 用户明确第一版不做，后续迭代再加。
- 一题多主知识点 / 全标签计数 —— 定为主标签计数、次标签仅展示。
- 复用 Claude Code 订阅做识别 —— 用户明确要单独办按量付费 API（Qwen-VL-Max）。
- 从文件名正则解析 区/年/类型 —— 用户确认文件名不可靠，改为上传后手选（记住上次选择）。
- 长期保留源试卷 PDF —— 用户只要切片；确认切片成功后即删（此前临时保留以便重切）。
