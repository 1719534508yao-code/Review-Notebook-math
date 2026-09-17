# PROGRESS —— 开发进度与决策记录

> 接力棒文件：每个窗口开工前先读 DESIGN.md + 本文件。
> 与 DESIGN.md 冲突的改动，先在此记录理由再动。

## T1 地基 —— ✅ 完成（2026-09-14，窗口 1）

### 交付清单（实际文件）

| 文件 | 内容 |
|------|------|
| `requirements.txt` | 钉死 3.9 兼容版本：fastapi 0.104.1 / uvicorn 0.24.0 / pymupdf 1.24.11 / openai 1.35.13 / python-dotenv 1.0.1（+2 个补充，见偏离 ③） |
| `run.sh` | venv（优先 python3.11/3.10，回退 3.9）→ 首跑装依赖 → uvicorn 127.0.0.1:8000 → 3 秒后自动 `open` 浏览器；Ctrl+C 停止；已 chmod +x |
| `.env.example` | `DASHSCOPE_API_KEY=` |
| `README.md` | 骨架：3 分钟上手 + key 配置 + 目录说明（T8 再补全） |
| `backend/models.py` | §5 全部表（subjects / knowledge_points / qtype_options / exams / questions / question_kps / new_kp_suggestions）+ v_wrong_bank / v_pool 视图 + 4 个常用索引；DDL 全幂等 |
| `backend/db.py` | 连接/通用查询 helper、`init_db()`（建表 + 预置科目「数学」+ 9 题型）；常量 `DISTRICTS_ALL`(18) / `EXAM_TYPES` / `YEARS`(2023-27) |
| `backend/kp_seed.py` | `seed_kp_tree()` 幂等导入 data/kp_math.json（parent+name 判重） |
| `backend/api.py` | 三个冒烟路由：`/api/health`、`/api/kp/tree?subject=`、`/api/meta/enums`（全局 router 挂载点，T3+ 在此续加） |
| `app.py` | FastAPI 入口：load_dotenv → startup 里 init_db + seed_kp_tree → include api → `/` 挂 static |
| `data/kp_math.json` | **10 大类 / 96 叶子**（8,12,11,8,7,11,11,9,12,7），含「导数与切线」「圆锥曲线·轨迹方程」级粒度 |
| `data/qtypes_math.json` | §2-13 的 9 类题型（顺序即展示顺序；§4 指定此文件，db.py 读取） |
| `static/index.html` | 占位页（自动 fetch /api/health 显示 ✅ 后端在线 + kp 条数） |
| `archive/数学/{题库,错题本,计划库}/` | 目录骨架已建（.gitkeep 未放——项目无 git） |

### 验收实测（窗口 1 回复中已贴原始输出）

- `./run.sh` 首跑建 venv+装依赖成功，服务 UP；`GET /api/health` → **200** `{"ok":true,"kp_count":106}`（106 = 10 大类 + 96 叶子）。
- `GET /api/kp/tree?subject=数学` → 200，10 大类 96 叶子完整返回；`GET /api/meta/enums` → 9 题型/18 区/4 类型/5 年份。
- **重启 ×3**：DB 计数恒为 106，零重复导入（幂等成立）。视图查询 `v_wrong_bank` / `v_pool` 正常返回 0 行。
- 未知科目 `/api/kp/tree?subject=xx` → 404（分支可用）。

### 偏离 DESIGN.md / 补充决定

1. **questions 表加了 `created_at TEXT DEFAULT (datetime('now','localtime'))`**（§5 未列）。
   理由：§6-T2 切片与 §6-T7 周报的时间窗聚合必须知道「题是什么时候做的」，而 exams 只有
   uploaded_at 一列，无法区分「今天新传上周的卷子」；T7 验收明确要「改 created_at 模拟上周数据」，
   故把列建在 questions 上，T7 直接可用，不动即无副作用。
2. **北京 18 区**：§5.5 缩写表去重后实际是 16 个行政区（「延」出现两次应为笔误）。
   决定：16 区全称 + 「燕山地区」「北京经济技术开发区」（北京高考/期末卷区级单位口径）凑成
   18 个下拉候选，写在 `db.DISTRICTS_ALL`，且表单允许手填任意值（§5.5 规则 3），不影响正确性。
   （§4 提到 districts 也可放 data/districts.json，本次未单拆文件，常量在 db.py 即 §6-T1 允许的位置。）
3. **requirements.txt 多了 2 行**：`python-multipart`（T3 UploadFile 的硬前提）、`pillow`
   （§2-1/§6-T2 跨页切片纵向拼接、§6-T4 base64 缩图）。二者是已定方案确定要用的库，提前钉版
   避免下游 agent 各装各的版本漂移；§4 括号里只列了前 5 个。
4. **kp 大类行的判重不能依赖 UNIQUE(subject_id,parent_id,name)**：parent_id 为 NULL 时 SQLite
   唯一约束不去重，所以 kp_seed.py 对大类用「先 SELECT 后 INSERT」，叶子用 INSERT OR IGNORE。
   （models.py 仍保留该 UNIQUE，对叶子与后续 AI 新增建议同样生效。）
5. **run.sh 的 python 选择**：优先找 python3.11/3.10（若用户日后 brew 装了更快），找不到用
   系统 python3（3.9.6，实测通过）。依赖钉版全部验证兼容 3.9。
6. 代码遵守 §2 环境约束：无 match 语句、无 `X | Y` 联合类型（用 Optional/List），全部 utf-8。

### 给下游窗口的备忘（T1 立的规矩）

- **路由只加在 `backend/api.py` 的全局 `router` 上**（前缀 `/api` 已挂好）；不要在别处新建 FastAPI 实例。
- 读库用 `db.get_conn()`（每请求一连接，dict 式取行 `row["col"]`）+ helper `db.q/db.scalar`。
- 静态挂载在 `/`（static 目录），图片双挂载 `/storage`、`/archive` **由 T3 补**（T1 占位页用不到）。
- `seed_kp_tree()` 返回值 = 本次新增数（0 = 全命中旧数据），打日志/排查幂等时可直接看它。
- 端口默认 8000，可 `PORT=8001 ./run.sh` 覆盖（调试并行时用）。

## T2 PDF 切片 —— ✅ 完成（2026-09-15，窗口 2）

### 交付清单（实际文件）

| 文件 | 内容 |
|------|------|
| `backend/pdf_slicer.py` | 题号定位 + 双栏分栏 + 跨页/跨栏 segment 渲染 + Pillow 纵向拼接 + raw_text 合并；CLI `python -m backend.pdf_slicer <pdf> [--exam-id] [--out] [--expected-max] [--scale]`；主入口 `slice_pdf(pdf_path, exam_id, ...)` 返回 T3 契约 dict（questions[].number/page_start/page_end/image_path/raw_text/n_segments/unlocated/rects + gaps/candidates/warnings） |
| `tests/gen_two_col_pdf.py` | 生成双栏+跨页测试卷（A4、页眉「第x页」、页脚「x/N」、考生须知 1-4 干扰、「0.05；」「2、3、」干扰行；22 题 4 页，含 6 道跨栏/跨页题），同时输出逐题 (page,col) 真值 JSON 到 `storage/test_papers/` |
| `tests/test_pdf_slicer.py` | 验收回归（无 pytest，直接跑）：ncol/全锚定/段序列==真值/拼接图高==各段和/干扰行拒绝/考生须知重置/raw_text 顺序 + 真实卷冒烟（tests/*.pdf 存在即跑） |

切片输出 `storage/crops/{exam_id}/{num}.png`（未动 archive）；`storage/test_papers/`、`storage/uploads_demo/` 目录已建。

### 验收实测

- 测试卷（4 页双栏）：22/22 全锚定、gaps=[]、6 道跨栏/跨页题（3/6/10/13/16/19）的 segment (页,栏) 序列与生成器真值逐一相符，拼接 PNG 高度==各 rect 段高之和×2，考生须知与 2 处干扰行被递增校验拦截记入 candidates。**13 项断言全部 PASS**。
- 真实卷 `tests/2022北京朝阳高二（下）期末数学.pdf`（13 页，试题 4 页+答案 9 页）：自动判单栏（x0 分布证实），21/21 题定位无中间缺口；跨页题 8、16、19 双 rect 拼接完整（19 题含直方图插图未被切断）；答案段「1．【分析】」被「再现 1．且已锚 >8 题 → 答案段终止」规则正确拦截，切片无答案内容；题号 22 标 unlocated（该卷实际 21 题，属预期）。
- 目测切片：7（含函数图像插图）、8/19/22（跨页）边界与内容正确。

### 偏离 DESIGN.md / 补充决定

1. **行级提取而非 blocks 级**（§6-T2 写 `get_text("blocks")`）：MuPDF 会把相邻行合并进同一 block，且数学卷上下标 span（如 x⁵ 的 5、展开式 (x²+1)⁵）被拆成独立小条目且略高于基线，blocks 级会串题/漏锚。改为 `get_text("dict")` 行级提取 + 「基线 y 差<8pt 且横向间隔<20pt 聚为视觉行」（`_cluster_rows`），锚定与 raw_text 质量都明显改善。真实卷拦截候选从 11 条增至 18 条（数据表数值行也被正确拒绝）。
2. **答案段检测规则**：§6-T2 未给「如何不切答案」的方案。实现双保险：a) 行首「参考答案/试题答案」标记截断；b)「1．」再现时若已锚 ≤8 题判考生须知→就地重置重锚，否则判答案段→停止。真实卷两规则均验证有效。
3. **递增校验分隔符集扩为 `．.、，` + 全角数字 + 数字间空格合并**：PyMuPDF 抽中文会在数字间插空格（「1 2．」），不处理会漏两位数题号。
4. **边界中点按「同栏」**而非全局阅读顺序（§6-T2 原文「相邻题号块中点」）：双栏跨栏时全局中点会越栏误切；同栏取中点、跨栏/页取栏缘/页缘（内容底=页脚上方，保住题内插图）。
5. **expected_max 默认 22**（§1 验收口径），CLI 可覆盖；期末卷题数不一（本卷 21 题），超出最大锚定题号的尾部一律标 unlocated+warning，不误报。
6. 复用 T1 骨架未动其他模块：路径常量取自 `backend.db.PROJECT_ROOT`；未改 db/models/api；未装新依赖（pymupdf、pillow 均已在 requirements.txt）。`out_dir` 传项目外路径时 image_path 回落绝对路径（T3 正常用法不受影响）。
7. 失败兜底（复核页「与上一题合并/拆分」「手动设页范围」）属 T3 UI 范围，本模块已备好 rects/candidates/warnings 数据。

### 给下游窗口的备忘（T2 立的规矩）

- T3 直接 `from backend.pdf_slicer import slice_pdf`；返回 dict 结构见上；unlocated=True 的题不出图（image_path=None）。
- 切片固定 2x PNG；rects 为 (page 0-based, col, rect=(x0,y0,x1,y1)pt)，复核页可复用 rects 做「手动设范围」。
- 新真实卷调参入口：`backend/pdf_slicer.py` 顶部常量区（SCALE/META_TEXT_RE/STOP_RE/RESET_MAX_ANCHORS/COL_GAP_MIN…）。
- 回归：`.venv/bin/python tests/test_pdf_slicer.py`（自动重建用例卷；tests/ 放新 PDF 即被真实卷冒烟覆盖）。

## T3 上传/复核全链路 —— ✅ 完成（2026-09-15，窗口 3）

### 交付清单（实际文件）

| 文件 | 内容 |
|------|------|
| `backend/api.py`（续写） | `POST /api/exams`（multipart+手选年/区/型 → 同步调 T2 slice_pdf；切片失败自动回滚 exam 行与临时 PDF）；`GET /api/exams`（列表含 n_pending）；`GET /api/exams/{id}/questions`；`PATCH /api/questions/{id}`（is_wrong/qtype_id/raw_text）；`POST /api/exams/{id}/confirm`（全卷 status→untagged、is_wrong 定档；正确题切片**复制**入 `archive/数学/题库/{年}/{区}/{型}/{题号}.png` 并改写 image_path，错题留 crops 待 T4；删源 PDF；`_queue_tagging()` 打标留桩=日志占位不报错）。源 PDF 约定路径 `storage/uploads/{exam_id}.pdf`（exams 表不加列，§5「不存 pdf_path」成立）；区名过 `_SAFE_SEG` 白名单防目录穿越 |
| `app.py`（小改） | `/storage`、`/archive` 图片双挂载（先于 `/` 兜底，否则被 static 吃掉）；startup 前建好 uploads/crops 目录 |
| `static/index.html` `style.css` `app.js` | 原生单页：tab 框架（上传/复核可用，T5~T7 占位禁用）。**上传 view**：文件+年/区/型表单，localStorage 记住上次选择（键 `exam-upload-memo-v1`），成功后自动跳复核；**复核 view**：试卷下拉（待复核优先）、切片网格卡片（缩略图+题号+raw_text 可编辑折叠区）、**点击缩略图=红框标错题/再点取消**（PATCH 即时落库、卡片闪「已保存」）、9 类题型下拉、顶部未定位题警告横幅、底部计数+「确认无误，开始识别」（confirm() 二次确认后 POST confirm，成功转只读） |

### 验收实测（真实卷 `tests/2022北京朝阳高二（下）期末数学.pdf`，API+无头 Chrome 双通道）

- 上传 → `POST /api/exams` 200：22 题入库（题 22 未定位不出图，复核页横幅提示）、源 PDF 落 `storage/uploads/1.pdf`。
- 点错 3 题（8 跨页题/16/19，其中 8、16 顺手选题型「函数导数」「数列」）→ PATCH 全部 200 回显正确。
- `POST /api/exams/1/confirm` → `{pool_copied:18, wrong_kept:3, no_image:1, source_pdf_removed:true}`。
- **9 项断言全 PASS**：源 PDF 已删 ✅；题库目录恰 18 张未打标切片（不含 8/16/19）✅；错题 is_wrong=1 且 image_path 仍指 crops ✅；全卷 status=untagged ✅；正确题 image_path 指向 archive ✅；未定位题不阻塞确认 ✅；archive/crops 中文路径图片经 URL 可访问(200) ✅；重复 confirm → 409 ✅。
- 无头 Chrome `--dump-dom`：待复核卷渲染 22 张卡片、每卡 9 题型选项、确认按钮可用、计数条正确；已确认卷转只读（按钮 disabled、卡片 locked）。
- 演示数据保留：exam #1（朝阳 2022 期末，已确认，错题 8/16/19 待 T4）、exam #2（海淀 2024 一模，待复核，供用户练 UI）。

### 偏离 DESIGN.md / 补充决定

1. **年份改为「候选+手填」**（§2-12 写下拉 2023-2027）：真实验收卷是 2022 年，严格枚举会挡住旧卷。前端年份=input+datalist（候选仍 2023-2027），后端放宽为 2000~2100 整数。区本就允许手填（§5.5 规则3），口径一致。
2. **源 PDF 存储用约定路径** `storage/uploads/{exam_id}.pdf` 而非表列：遵守 §5 exams 不存 pdf_path；confirm 删文件即可，重启后也不产生孤儿（切片已持久化）。
3. **confirm 对未定位题（无图）**：不算错、不复制文件、status 照常 →untagged，返回体报 no_image 计数。T2 的 rects 手动设范围重切 UI 本窗口未做（§6-T3 的「合并/拆分」失败兜底入口数据已备好，留待后续）。
4. **PATCH 对 is_wrong 无法清空回 NULL**（Pydantic Optional 语义下 null 与未传不分）；复核 UI 点错/取消只用 1/0，不影响流程。raw_text 同理空串可传。
5. **切片失败自动回滚**：删除刚建的 exam 行与临时 PDF 后抛错，用户可改参重传，不留半成品。
6. 打标触发为 `_queue_tagging()` 桩（仅 INFO 日志），T4 替换其函数体即可。

### 给下游窗口的备忘（T3 立的规矩）

- **T4 接手点**：`api.py::_queue_tagging(exam_id)` 替换为真任务；打标成功后按 §5.5/§6-T4 做文件搬迁——正确题 `archive/数学/题库/{年}/{区}/{型}/{题号}.png` → 重命名补 `_{题型}.png`；错题从 `storage/crops/{exam_id}/{题号}.png` **移入** `archive/数学/错题本/{题型}/{年}{区}{型}_{题号}.png`，同步 image_path。
- `questions.image_path` 在 confirm 后即「最终归档态路径」（正确题在 archive、错题暂在 crops），前端 `_question_out` 统一给 `image_url = "/"+path`；T5 浏览 view 直接复用 `/api/exams/{id}/questions` 模式或新写查询均可，注意 join `qtype_options.name`。
- 新路由继续加在 `backend/api.py` 全局 `router`；`_safe_seg` 可复用于其它拼路径的用户输入。
- 服务起法不变 `./run.sh`（8000）；上传/确认是同步接口，多页卷几秒，前端按钮已有 pending 态。

## T4 AI 打标 —— ✅ 完成（2026-09-16，窗口 4）

### 交付清单（实际文件）

| 文件 | 内容 |
|------|------|
| `backend/tagger.py`（新） | Qwen-VL-Max 打标：`compact_kp_tree()`（紧凑树「大类/叶子」+ 路径→kp_id 映射，仅 confirmed）、`_image_base64()`（PIL 缩图至 ≤1280px 再 base64，省 token）、`_call_qwen()`（OpenAI SDK → DashScope 兼容端点 + `response_format=json_object`）、`_validate_result()`（qtype∈9型 / primary_kp∈树 / secondary∈树 / new_kp 结构）、`tag_question()`（校验失败 retry 1 次 → 仍失败则 status=tagged 但 qtype/kp 留空待手动补）、`_archive_after_tag()`（归档落地）、`_create_suggestion()`、`run_tagging()/start_tagging()/tagging_status()`（后台线程 + 全局 `threading.Lock` 串行防并发烧钱）、`approve_suggestion()/reject_suggestion()`、`manual_tag()`（手动模式与 AI 共用同一归档落地）、CLI `python -m backend.tagger --exam-id N [--approve ID]` |
| `backend/api.py`（续写） | `_queue_tagging()` 桩 → 真任务 `tagger.start_tagging()`；新增 `GET /api/exams/{id}/tagging`（进度轮询）、`GET /api/exams/{id}/kp_suggestions`、`POST /api/kp_suggestions/{id}/approve`（409 幂等）、`POST /api/kp_suggestions/{id}/reject`；confirm 返回体补 `manual_mode` / `api_key_present`；**PATCH 扩 `primary_kp_id` / `secondary_kp_ids`**（手动模式硬性要求）→ 走 `tagger.manual_tag` |
| `static/index.html` | 复核 view 增 3 个容器：`#rv-sugg`（新知识点一键确认横幅）、`#rv-manual`（无 key 手动模式提示）、`#rv-progress`（识别进度） |
| `static/app.js` | `flattenKp()` 拍平树供下拉；卡片改**按题状态（per-question）判可编辑**（`pending_slice_confirm`/`untagged` 可编辑，`tagged` 才锁卡——T3 的按卷锁定会在手动模式下锁死待补录的题）；每题加**主知识点下拉**；`refreshT4/renderSuggestions/decideSuggestion/pollTagging`（1.2s 轮询进度，完成后自动刷卡片） |
| `static/style.css` | `.suggbox` / `.warnbox` / `.progbox` 三组样式 |

### 验收实测（无 key → 手动模式 + `TAGGER_MOCK=1` mock 打标，双通道）

**A. mock 打标全流程（exam #1 真实卷 2022 朝阳期末，22 题）**
- `TAGGER_MOCK=1 python -m backend.tagger --exam-id 1` → **22/22 全部 ok**，`primary_tagged 22/22`。
- **归档落地实测**（DESIGN §5.5 规则1 / §6-T4）：
  - 错题 8/16 → `archive/数学/错题本/函数导数/2022朝阳期末_{8,16}.png`（**crops 移出**，crops/1 由 21→18 张）；
  - 错题 19 → `archive/数学/错题本/新定义题/2022朝阳期末_19.png`；
  - 正确题 18 张 → `题库/2022/朝阳区/期末/{题号}.png` **原地改名为 `{题号}_{题型}.png`**；
  - 未定位题 22（无图）→ `image_path=null`，不阻塞；
  - DB `image_path` 全部随之更新为新归档相对路径。
- **新知识点建议闭环**：q19 产出 `new_kp_suggestions #1`（函数与导数/新定义函数的性质探究，pending）→ `--approve 1` → 建叶子 **kp_id=107**（`origin='ai'`, `status='confirmed'`, parent=函数与导数）+ **回填 q19 主标签**（旧主标签作废）+ 建议转 approved；kp 总数 106→107。

**B. 手动模式（无 key）——** 服务在**不设** `TAGGER_MOCK`、无 `DASHSCOPE_API_KEY` 下实跑：
- `POST /api/exams/2/confirm` → `{manual_mode:true, api_key_present:false, pool_copied:22}`，**不崩**；
- `GET /api/exams/2/tagging` → `{running:false, done:22/22, manual_mode:true}`，全程**零 API 调用**；
- 22 题保持 `status='untagged'`（留给手动补录），卡片在 UI 中**仍可编辑**（前端按题状态判可编辑）；
- `PATCH /api/questions/23 {qtype_id:4, primary_kp_id:55}` → `status=tagged`，文件改名 `1_立体几何.png`，`image_url` 同步；
- `PATCH /api/questions/24 {is_wrong:1}` + `{qtype_id:7}` → **移入** `archive/数学/错题本/解析几何/2024海淀一模_2.png`。

**C. 路由/前端冒烟**（live HTTP + headless Chrome `--dump-dom`）
- `GET /api/exams/1/tagging`、`/kp_suggestions` 均 200；重复 approve → **409**（幂等）；reject → `{ok:true}` 且库中转 `rejected`。
- 无头 Chrome 渲染复核页：22 张卡片、每卡**题型下拉 + 主知识点下拉**、**手动模式提示横幅**文案正确；新知识点横幅「💡 AI 建议新增知识点 1 条…」带**确认/忽略**按钮。

### D. 真实 key 实测（智谱 GLM-4V-Plus）—— 2026-09-16 追加

用户提供了可用 key，遂**真跑**了 exam #1 全 22 题（不是 mock）。这一轮暴露并修掉了 4 个
只有在真实调用下才会出现的问题，也拿到了真实 token 数。

**接真实 key 时暴露并已修的 bug：**

| # | 问题 | 现象 | 修法 |
|---|------|------|------|
| 1 | **依赖冲突（会直接崩）** | `openai 1.35.13` + `httpx 0.28.1` → `OpenAI()` 构造即 `TypeError: __init__() got an unexpected keyword argument 'proxies'` | `requirements.txt` 钉 `httpx==0.27.2`（上游 openai≥1.55 才修）。**此前无 key 走手动模式，永不触达该代码路径，属潜伏 bug** |
| 2 | **key 无效时整卷被误锁** | 旧逻辑逐题吞掉调用异常 → 22 题全标 `tagged` 但题型/知识点空白 → **卡片锁死，用户看到的是「识别完成」而不是「你的 key 是坏的」** | `_classify_error()` 区分 auth/network；**系统性失败立即中止整卷**（不再逐题烧钱），题保持 `untagged`，错误进 `TAGGING_STATE.error` + 前端红框 + 一键重试（`POST /api/exams/{id}/retag`） |
| 3 | **9 类题型从未真正给过模型** | system prompt 只在「判定规则」里**隐含**提到 9 类，从没作为**显式清单**下发 → 模型答出「概率与统计」（那是知识点大类名，题型里叫「概率统计」） | prompt 显式列出 9 类 + 明确「题型 ≠ 知识点大类」；`_build_user` 再复述一次 |
| 4 | **模型「改写」中文路径导致解析失败** | 要求精确到叶子后，模型开始**编造**叶子名（答「集合与交集」，树里是「交集、并集、补集运算」） | **给知识点树每行加编号，让模型报编号**（`primary_kp_no`）。数字不会抄错。加了编号后**失败率从 8/22 → 0/22** |

**解析容错（`resolve_qtype` / `resolve_kp`）**：题型走「精确 → 包含 → difflib 近似」三级，
知识点走「编号 → 精确路径 → 唯一叶子名 → 大类兜底 → 近似」五级。两者都是**宽容解析**，
只有真解析不出来才判失败。

**最终实测结果（22 题全绿）：**

| 指标 | 结果 |
|------|------|
| 打标成功 | **22/22**（前两轮分别是 14/22、21/22） |
| 主标签粒度 | **叶子级 22 / 大类级 0**（中间一版曾 20/21 落在大类上，靠「大类不进 paths」+ 编号修掉） |
| 归档落地 | 正确题 18 张全部改为 `{题号}_{题型}.png`；错题 8/16 → `错题本/选择题/2022朝阳期末_{8,16}.png`、19 → `错题本/概率统计/2022朝阳期末_19.png`；题 22 无图不阻塞 |
| 新知识点建议 | **0 条**（未加闸门前同一套卷提出 16 条，绝大多数是树里已有的重名） |
| 耗时 | 60~75 秒/套（串行 22 题） |

**真实 token 用量（`glm-4v-plus`，22 题一套）**：
```
prompt = 98,291   completion = 1,142   total = 99,433   (cached = 0)
```
≈ **4,520 tokens/题**、**99k tokens/套**。按 ¥0.01/千token 量级算约 **¥1.0/套**；
若换回 DESIGN 原定的 DashScope `qwen-vl-max`（约 ¥0.02/千token）约 **¥2.0/套**。
（智谱各模型单价请以其控制台当期价目为准；`glm-4v-flash` 更便宜但识图更弱。）

**⚠️ 必须如实记录的准确性问题：模型分类质量不达标。**
数字解析已做到 22/22，但**模型判的知识点有明确错判**，人工比对：
- 题 10（分段函数 + 整数解，已核对切片图与 raw_text 一致）→ 判成 `数列/倒序相加与错位相减求和` ❌
- 题 8（2×2 列联表/独立性检验）→ 判成 `概率与统计/随机抽样与分层抽样` ❌
- 题 13（平均数与方差）→ 判成 `数列/等差数列的基本量与性质` ❌
- 题 19 → `概率与统计/频率分布直方图与百分位数` ✅；题 21（集合新定义）✅；题 9（sin2x 最值）✅
- 且同一题在三次运行中答案**不稳定**（题 10 先后被判成 集合与逻辑 → 集合与逻辑 → 数列）

即 DESIGN §6-T4 的验收线「5 题人工比对合理」**未达到**（约 3/5 合理）。
**但已排除是我们这边的问题**：切片图与 raw_text 一一对应（已逐图核对题 10），
解析/归档/建议/降级链路全部正确。结论是**模型+提示词的判别力有限**，不是管线 bug。
- 对产品的影响可控：复核页本就可手动改题型/知识点（PATCH 已支持），失败题会留空待补；
  但若指望「AI 自动打标准确」直接进气泡图，**当前质量不足以无人值守**。
- 下一步建议（留给后续窗口）：① 把题号区间的**板块先验**喂给模型（北京卷通常 1-10 选择、
  11-15 填空、16-21 解答且按板块递进）；② 换 `qwen-vl-max` 对比一次；③ 把「同一套卷
  重复率过高」当异常信号做二次校验。

### E. 评测基建 + 准确率基线（2026-09-16，同一窗口）

**动因**：没有金标准，任何 prompt/模型改动都无法证明是变好还是变坏，只能靠感觉。
故先建评测，再谈优化。

**交付：**

| 文件 | 用途 |
|------|------|
| `tests/eval_tagger.py` | 三个子命令：`template`（导出人工标注模板）/ `dump`（从 DB 导出当前模型预测）/ `score`（打分）。指标：题型准确率、主知识点**叶子级**（最严）、**大类级**（宽松）、弃权率、按 `_conf` 把握度分层 |
| `static/eval.html` | 标注页（开发用，不影响产品 UI；访问 `http://127.0.0.1:8000/eval.html`）。左边切片图、右边真值下拉（9 题型 + 97 条**带编号**知识点），AI 判定单列只读，实时算分，localStorage 续标，导出 JSON 与 CLI 同格式可往返 |
| `data/gold/gold_exam1.json` | 金标准集（**Claude 据 raw_text 草拟，待用户复核**）：21 题，把握度 high=13 / mid=7 / low=1（题 22 未定位无文本，不标，评测自动跳过空真值） |
| `data/gold/pred_exam1.json` | glm-4v-plus 的预测（`dump` 产出） |

**基线（exam #1，21 题）：**

| 指标 | 结果 |
|------|------|
| 题型准确率 | **18/21 = 85.7%** |
| 主知识点 叶子级 | **11/21 = 52.4%**（全量） |
| 主知识点 叶子级（仅 high 把握标注） | **8/13 = 61.5%** ← **最可辩护的数字** |
| 主知识点 大类级 | **17/21 = 81.0%** |
| 弃权率 | **0/21 = 0%** |

⚠️ **金标准是 Claude 草拟的，不是用户确认的**。mid/low 那 8 题本身有歧义（如题 10
「分段函数+分式不等式整数解」，可算 42 也可算 44），**模型与人的分歧未必是模型错**。
所以只把 `high` 那 13 题（8/13=61.5%）当可辩护结论；全量 52.4% 偏低，部分是被歧义题拉低的。
→ **待办：请用户复核 `data/gold/gold_exam1.json`，重点看 `_conf` 为 mid/low 的题。**

**最重要的诊断（决定下一步做什么）：**

把 10 道判错题拆开看，**6 道是「大类判对、叶子判错」**：
题 8（概率与统计→误判「随机抽样」而非「回归分析与独立性检验」）、题 14（→误判随机抽样）、
题 15（函数与导数→误判「函数定义域与值域」而非「周期性与对称性」）、题 16（→误判「单调性奇偶性」）、
题 17（→误判「古典概型」而非「相互独立性」）、题 20（→误判「用导数研究单调性」而非「极值与最值」）。

即模型 **81% 能判对板块，但进了板块之后分不清是哪片叶子**——这正好说明：
**它缺的不是「看懂题」，而是「在 97 个近义叶子里的分辨力」。**
另 4 道（题 4/10/12/13）是**大类也判错**，且都塌缩到同一个叶子「倒序相加与错位相减求和」，
属"不会却硬答"。
3 道题型错（题 14/16 该是填空题却判成选择题/概率统计、题 20 该是解答题却判成选择题）
全是**卷面形式**混淆，与知识点无关。

**据此排出的优化优先级（有数据支撑，不再是猜）：**

1. **喂结构先验（零成本，先做）**：把「第 N 题」和北京卷固定结构告诉模型
   （1-10 选择、11-16 填空、17-21 解答且板块有序）。**直接消灭题 14/16/20 那类题型错**，
   同时对板块判断有正向锚定。代价：prompt 多一句话。
2. **两级分类（大类 → 叶子）**：先让模型在 10 个大类里选（它已 81% 对），
   **再只把那一个类别的 8~12 条叶子喂给它选**。97 选 1 变成 10 选 1 + ~10 选 1。
   针对性命中上面那 6 道「大类对叶子错」。代价：2 次调用（第二次列表极小）。
3. **允许弃权**：`primary_kp_no` 可为 null + 低置信留空。针对题 4/10/12/13 那种
   "不会却硬答"。**错标签比空标签危害大**（气泡图只数主标签，错标签永久污染统计）。
   代价：几乎为零。配套要把 confidence 从「0-1 浮点」改成**离散三级**——实测现在
   22 题**全部**返回 0.9，这个字段目前不含任何信息。
4. **自洽投票**：同题跑 2~3 次取多数，无多数→弃权。把「答案不稳定」从 bug 变成置信信号。
   代价 ×2~3，可只对低置信题启用。
5. **模型 A/B**：换 `qwen-vl-max` 跑同一套卷同一套金标准，数字直接可比（约 ¥2）。

**用法备忘**（改完任何东西都这样量一遍）：
```bash
.venv/bin/python tests/eval_tagger.py dump  --exam-id 1 --out data/gold/pred_v2.json --model qwen-vl-max
.venv/bin/python tests/eval_tagger.py score --gold data/gold/gold_exam1.json --pred data/gold/pred_v2.json
```

### F. 准确率优化 A/B —— 同一套卷同一份金标准（2026-09-16，同窗口）

按 E 节诊断出的优先级，实现了 **①结构先验 ②两级分类 ③允许弃权 ④题型确定性校正**，
每次改动都用同一份 `gold_exam1.json` 打分，数字直接可比。

**结果（exam #1，21 题）：**

| 指标 | v1 单级(基线) | **v5 最终** | Δ |
|------|------|------|------|
| **题型准确率** | 18/21 = 85.7% | **21/21 = 100%** | **+14.3%** |
| **主知识点 叶子级** | 11/21 = 52.4% | **13/21 = 61.9%** | **+9.5%** |
| **主知识点 大类级** | 17/21 = 81.0% | **19/21 = 90.5%** | **+9.5%** |
| **叶子级（仅 high 把握标注）** | 8/13 = 61.5% | **11/13 = 84.6%** | **+23.1%** |
| 弃权 | 0%（题22 无内容仍瞎猜） | 正确弃权 1 题（题22） | — |
| token/套 | 99,433 | 121,305（+22%） |  |

**改了什么：**

1. **两级分类（默认开启）**：`_two_stage()` —— 第一步只给 10 个板块选 1，第二步**只给该板块的
   8~13 个叶子**选 1（带编号）。97 选 1 → 10 选 1 + ~10 选 1。这是**针对「6/10 错题是大类对叶子错」
   那一条诊断**下的药，也是本轮涨得最多的一处。单级路径保留（`TAGGER_ONE_STAGE=1`）供 A/B。
2. **结构先验**：`section_hint()` 按北京卷惯例把「第 N 题 → 卷面形式」写进 prompt。
   ⚠️ **实测教训：作为软提示写进 prompt 模型并不遵守**（题14/16 仍被判成概率统计/选择题）。
3. **题型确定性校正**（软提示无效后的补救，`CAT_TO_QTYPE`）：
   - 客观题段（1-10 选择 / 11-16 填空）：题型**硬校正**为该段形式；
   - 解答题段（17+）：题型**由主知识点所属大类推出**（§2-13「解答题按内容板块归入」）。
   收益：既修掉题14/16/20，也让「错题本文件夹」与「气泡图板块」**构造上不可能矛盾**——
   实测修正前模型会自相矛盾（题18 知识点判 `函数与导数/…切线` 却把题型写成 `解析几何`）。
   校验：解答题段「题型==板块」矛盾数 **0**。
4. **离散信心 `high|mid|low` → `CONF_MAP` 落 REAL**：旧的「0~1 浮点」实测 22 题**全返回 0.9**，
   零信息量；换成离散三级后才有区分度。
5. **允许弃权**：两步里任何一步给出 null / 编号越界 / 板块对不上，都判**弃权**并留空待手动补，
   不再硬塞一个错标签。实测题 22（无切片无文本）从"自信瞎猜"变为**正确弃权**。
   `TAGGING_STATE` 里带 `abstained` 标记，前端报「模型主动弃权」而非「识别失败」。

**仍存在的问题（如实记录）：**

- **主知识点叶子级 61.9%（高把握子集 84.6%）仍不够无人值守**。剩余 8 道错题：
  题5(计数原理→误判二项分布)、题10(不等式→误判函数定义域)、题14(条件概率→误判互斥对立)、
  题15(周期性对称性→误判单调性奇偶性)、题16(新定义函数→误判幂指对图象)、
  题17(相互独立→误判分布列期望)、题20(极值最值→误判单调性)、题21(集合新定义→误判集合概念)。
  **其中 5 道（题14/15/16/17/20）是「大类对、叶子错」** —— 说明两级分类方向对，但
  第二步在近义叶子间（如「极值与最值」vs「用导数研究单调性」）分辨力仍不足。
- 我的金标准集是**草拟的**（high=13/mid=7/low=1），mid/low 有歧义，这 8 道里 5 道落在 mid/low。
  故 **61.9% 是下限而不是定论**；等用户复核完金标准再定。
- **下一步性价比最高的两件事**（留给后续窗口）：
  a) **自洽投票**：同题跑 2~3 次取多数，无多数→弃权。把"答案不稳定"变成置信信号，
     预计能同时抬高叶子级准确率与弃权率（弃权率现在是 0%，偏高置信度值得质疑）。
  b) **叶子级 prompt 加判别性线索**：第二步把每个叶子的**典型题目特征/区别点**附在编号后
     （如「导数的极值与最值 = 求 f'(x)=0 后比较端点与驻点」），而不是只给一个名字。
  另：`data/gold/pred_exam1_final.json` 是当前最优版本的预测，可作后续对比基准。

### G. 模型 A/B + 「API 额度不足」中止与弹窗（2026-09-16，同窗口）

**① 模型可用性实测**（`GET /models` + 逐个实打实调用；注意 `/models` **只列文本模型**，
`glm-4v-*` 全都不在列表里但实际可用，所以只能试不能信列表）：

| 模型 | 纯文本 | **能否识图** | 结论 |
|------|--------|------|------|
| `glm-4v-plus`（现用） | OK | ✅ | **保留** |
| `glm-4v-flash` | OK | ✅ | 可用（更便宜、更弱） |
| `glm-4.6v` | OK | ✅ | 可用但更差、更慢，见下表 |
| `glm-4.5v` | OK | ✅ | 可用，未 A/B |
| `glm-4.6v-flash` | HTTP 429 | ❌ | 存在但常年限流（已被本窗口当 429 测试替身） |
| **`glm-4.5-air`** | OK | ❌ **不能识图** | 报 `messages.content.type 参数非法，取值范围 ['text']`。**对本项目无用** |
| `glm-4.6` | OK | ❌ 不能识图 | 同上 |
| `glm-4.5v-air` | HTTP 400 模型不存在 | — | 不存在 |

**`glm-4.6v` A/B（同一套卷同一份金标准）：**

| 指标 | glm-4v-plus | glm-4.6v |
|------|------|------|
| 主知识点 叶子级 | **61.9%** | 57.1% |
| 主知识点 大类级 | **90.5%** | 76.2% |
| 叶子级（high 把握子集） | **84.6%** | 76.9% |
| 题型 | **100%** | 95.2% |
| 弃权率 | 0% | 4.8% |
| token/套 | 121,305 | **60,103（省 48%）** |
| 耗时 | **2:09** | 8:44（慢 4×，completion 达 23.6k，是推理模型） |

**结论：不换。** 省一半钱但四项质量指标全降、还慢 4 倍。`.env` 保持 `glm-4v-plus`。
（`glm-4.6v` 的 prompt_tokens 只有 ~125/题 vs glm-4v-plus 的 ~2374，图像按新方式计费；
但质量证明它没把图用足。）

**② 余额查询：做不了。** 实测 8 个候选路径
（`/account/balance`、`/balance`、`/user/balance`、`/billing/balance`、`/account`、`/usage`
以及 v3/v2 版本）**全部 404** —— 智谱没有公开的余额 API，只能在控制台看。
故改为**「额度不足时中止 + 弹窗」**（用户明确要求）：

- `_classify_error()` 新增 **`quota`** 类别（`429/402/quota/insufficient/arrears/欠费/余额/
  额度/限额/访问量过大/rate limit/too many requests/exceeded`），且**排在 auth 之前**判
  （429 既可能是限流也可能是余额，两者都该中止）。
- `FATAL_KINDS = ("quota","auth","network")` → 任一中招**立即中止整卷**。
  修掉的真实缺陷：**429 原先被归为 `other`，会被逐题重试 22 遍**（既烧请求又拖时间）；
  且失败会被吞成"tagged 空标签"→ **锁卡**，用户看到的是"识别完成"而不是"你欠费了"。
- 中止后 `TAGGING_STATE` 带 `fatal_kind`，前端据此弹**分场景弹窗** + 红框 + 一键重试
  （`POST /api/exams/{id}/retag`，幂等，只处理 untagged 题）。弹窗用指纹
  `lastFatalSig` 去重，防轮询变连环弹窗；点重试会清空指纹以便再次失败能再弹。

**实测（用真的会 429 的 `glm-4.6v-flash` 当替身）：**
- `_classify_error` 单测 **7/7 通过**（429/欠费/401/连接/json/400 各归各类）。
- 整卷中止行为：`done=1/20` 即停（不是 22 次徒劳重试），
  **22/22 题全部保持 `untagged`、被误标的 0 题** —— 没有锁卡。
- 前端弹窗：Node 直接跑 `static/app.js` 里**真实的 `pollTagging` 源码**（stub DOM/fetch），
  **9/9 断言通过**，弹窗文案：
  ```
  ⚠️ 识别已中止（API 问题）
  API 额度/余额不足，或触发限流
  请到模型厂商控制台充值（或稍后再试），然后点「重试识别」继续。
  已完成 1/20 题，其余保持未打标。
  ```
- 注：无头 Chrome `--dump-dom` 会**卡在 `alert()` 上**（弹窗阻塞、页面 load 不完成），
  所以前端这块改用「提取真实源码在 Node 里跑」的方式验证，比截图更可靠。

### H. 降本专项：发现「图片按张固定收费」+ 整卷长图方案实测（2026-09-16，同窗口）

用户提出「PDF 先拼成一张长图、一次发出去、按题号输出全部标签」的想法。实测下来
**成本上极优、但知识点不可用**，过程中反而挖到一条关键计费事实和一处真 bug。

**① 最关键的事实：图片按张固定收费，完全不看尺寸。**

拿同一张真实切片缩到 6 种尺寸、外加纯色图实测：

| 送进去的图 | prompt_tokens |
|---|---|
| 1058×1128 真实题目 | 2366 |
| 480×512 | 2366 |
| 64×64 **纯白图** | 2366 |
| 1024×1024 纯白 | 2366 |

→ **缩图、裁空白、压缩字重，全都白费**。唯一有效的是**少发图**。
而各模型的「每张图」价格差 13 倍：`glm-4v-plus` ≈2366 / `glm-4v-flash` ≈178 / `glm-4.6v` ≈125。
单题 token 构成：**两张图 4,732 + 全部文字约 800 → 图占 85%**。

**② 整卷长图方案实测（用户的想法）**

把 21 张切片纵向拼成 1066×5296 的长图一次发出（`glm-4v-plus` max_tokens 上限 2048 装不下
21 题输出，只能用 `glm-4.6v`）：

| 做法 | tokens/套 | 省 | 题型 | 板块级 | **叶子级** |
|------|------|------|------|------|------|
| 现在（两级 × 22 题） | 121,338 | — | 100% | 90.5% | **61.9%** |
| **整卷一次调用** | **12,065** | **90%** | **100%** | 57.1% | **0%** |
| 分 3 批（每批 7 题） | 18,102 | 85% | 66.7% | 58.3% | 0% |

- **模型确实看得清长图**（单问时第13题答对「平均数和方差」、第21题答对「集合性质P」），
  但**一次让它吐 21 题输出就退化**：出现重号、漏题、把无关题判成同一个叶子
  （第4/13/19 题都被答成「平面向量/向量的投影」）。分批更差 → **根因是长条图里
  「内容↔题号」的对应关系不稳**，而不是图看不清。
- 结论：**整卷长图是个极好的「题型判定器」（100%），但不能用来定知识点。**
  单题切片 + 窄列表的准确率优势目前无法用长图替代。

**③ 捞到的可用优化：阶段1 换便宜模型**

阶段1 只是「10 个板块选 1」的粗活，不值得用最贵的模型。实测阶段1 单独准确率：

| 阶段1 模型 | 板块准确率 | prompt tokens(21题) |
|---|---|---|
| `glm-4v-flash` | 76.2% | 17,194 |
| **`glm-4.6v`** | **90.5%** | **17,215** |
| `glm-4v-plus` | 85.7% | 58,963 |

已加 `VLM_MODEL_STAGE1` 支持（阶段1 与阶段2 可用不同模型）。整卷实测：

| 配置 | tokens/套 | 叶子级 | 大类级 | 耗时 |
|------|------|------|------|------|
| plus + plus（**默认，保留**） | 121,338 | **61.9%** | **90.5%** | **2:09** |
| 46v(阶段1) + plus(阶段2) | 83,621（**省31%**） | 57.1% | 81.0% | 4:31 |
| flash 全程 | 37,233（省69%） | 47.6% | 76.2% | 0:55 |

⚠️ 注意 n=21，**1 道题就是 4.8 个百分点**，上表 1~2 题的差异**在噪声范围内**，
不能据此断言哪个模型「更准」。故默认仍取**质量最好且最快**的 `glm-4v-plus + glm-4v-plus`；
要省钱改 `.env` 一行即可（见 §4 备忘）。

**④ 顺带修掉的真 bug（接 46v 才暴露）**

- **客户端超时写死 60s**：`glm-4.6v` 是推理模型、单次调用要 100s+，60s 会把整卷打断
  （实测触发「网络连接失败：Request timed out」→ 整卷中止）。已改为可配 `VLM_TIMEOUT`（默认 180s）。
- **没设 `max_tokens`**：推理模型的**思考过程也吃输出预算**，给太小会出现
  「推理把预算吃光、正文返回空串」（实测 `max_tokens=120` 时回答为空）。已加
  `VLM_MAX_TOKENS`（默认 2000；注意 `glm-4v-plus` 上限是 2048）。
- 附注：`glm-4v-plus` 的 `max_tokens` 被限制在 `[1,2048]`，所以**整卷一次输出 21 题它做不到**
  （报 400 参数非法），这是整卷方案只能用 `glm-4.6v` 的原因。

### 单套卷 token 消耗估算（基于真实切片实测，非拍脑袋）

| 项 | 实测 |
|----|------|
| system prompt | 579 字 |
| 紧凑知识点树 | 107 行 / 1816 字（**每题都随 prompt 发一次**） |
| raw_text 平均 | 161 字（最大 612） |
| 切片缩图（≤1280px） | 320×22 ~ 1058×1128；qwen-vl 图像 token **中位 255 / 均 304**（43 张样本实测） |
| **单题 input tokens** | **≈ 2.1k**（文本 ≈1.79k + 图 ≈0.30k） |
| **单套 22 题** | **≈ 46k tokens ≈ ¥0.92**（按 ¥0.02/千token） |

> 注：上表是**接入真实 key 之前**按字符/图像 token 做的估算（当时算得 ¥0.92/套）。
> 接入后**真实计费实测为 99,433 tokens/套**（见上面 D 节），比估算高约 1.5 倍——
> 差异主要来自知识点树加编号后的长度、以及真实图片的 token 化比 `w*h/784` 粗估更高。
> **以 D 节的真实数字为准**，下表保留作估算方法留痕。

⚠️ **与 DESIGN §6-T4「单套卷成本 < ¥0.1 量级」不符**：按 §6-T4 自己给的「一题约 2-3k token × ¥0.02/千」算，22 题本就是 ¥1.1 上下，**「<¥0.1」与同段数字自相矛盾**（差约 10 倍）。实测落在 ¥0.9 量级，属预期内、量级仍很低（一套卷一块钱）。
- **最大可压缩项 = 那 1816 字的紧凑树**（占单题文本 token 的 71%）。它整卷逐题重复且字节完全相同 → 落在 prompt 前缀上，**Qwen 上下文缓存（context cache）可命中并降价**；命中后趋近「仅图 token」≈ 6.7k/套 ≈ **¥0.13/套**。故当前实现把**恒定部分（system+树）放在最前、可变部分（raw_text+图）放最后**即是为缓存命中做的排版。
- 若日后仍要压成本：可只发「命中大类」的子树（本题只有 1 个大类有用），代码改动点在 `compact_kp_tree()` 与 `_build_user()`。

### 偏离 DESIGN.md / 补充决定

1. **「可编辑」判定从「按卷」改为「按题」**：T3 的卡片在 confirm 后整卷锁死（`pending` 为卷级标志）。但手动模式的补录发生在 confirm **之后**（此时全卷 `untagged`），按卷锁定会把待补录的题全部锁死、手动模式无法使用。改为 `status ∈ {pending_slice_confirm, untagged}` 可编辑、仅 `tagged` 锁卡——这是让 §6-T4「无 key 降级」真正可用的必要前提。
2. **mock 通道 `TAGGER_MOCK=1`（新增，本窗口验收用）**：`has_api_key()` 在 mock 下返回 True 但 `get_client()` 仍返回 None，故 `tag_question` 显式分流到 `_mock_tag(题号)` 的**确定性**打标（固定题型/知识点，q19 附带一条 new_kp 建议），可在**不花一分钱**的前提下验收「归档落地 + 建议 + approve 回填」全链路。生产不受影响（不设该变量即走真实 API 或无 key 手动模式）。
3. **`manual_tag()` 只在「给了 qtype_id」时才把 `untagged→tagged`**：否则用户先选知识点、后选题型时会提前锁卡（`tagged`）而题型还空着。知识点可单独保存、不触发状态跃迁。
4. **`_archive_after_tag()` 的错题文件名**：按 §5.5 例「2025海淀一模_19」的**风格**（区名去尾「区」）生成 `{年}{区}{型}_{题号}.png`；「北京经济技术开发区」特殊压成「经开区」，否则会出现「开发区期末」这种怪串。文件名只认题型（§6-T4 明确新知识点确认**不**重命名文件）。
5. **`threading.Lock` 而非 `asyncio.Lock`**：§6-T4 写 `asyncio.Lock`，但打标用的是**同步** openai SDK 且跑在后台线程（阻塞调用放进事件循环会卡死整个 FastAPI）。用模块级 `threading.Lock` 达成的效果完全一致——**整卷串行、跨卷也串行**（一个锁护住全部 `run_tagging`），防并发烧钱的目标不变。
6. **`qtype_id` 落库时二次解析**：模型可能给出合法但**大小写/空白**有差异的题型名，`_resolve_qtype_id()` 按**去空白后的精确名**匹配 9 类；匹配不到则视为校验失败（进 retry）。知识点同理，靠 `大类/叶子` 精确路径串。
7. **打标失败（校验两次不过）仍置 `tagged`**（§6-T4 明写「tagged 但 qtype/kp 留空待手动补」）；此时**不动文件**（无题型可归），`image_path` 保持 confirm 时的位置，用户手动补 qtype 时由 `manual_tag` 一次性完成改名/搬迁。

### 给下游窗口的备忘（T4 立的规矩）

- **T5/T6 读标签**：主标签一律 `question_kps.is_primary=1`（§2-8，统计只数主标签）；`v_wrong_bank`/`v_pool` 已按 `status='tagged'` 过滤，T4 之后即可直接用。
- **图片路径两类都在 `questions.image_path`**（错题 → `错题本/{题型}/…`，正确题 → `题库/{年}/{区}/{型}/{题号}_{题型}.png`），前端统一 `"/"+path`；`/storage`、`/archive` 双挂载已由 T3 备好。
- **手动模式是常态路径不是异常**：无 key 时 `GET /api/exams/{id}/tagging` 的 `manual_mode=true`，前端须提示用户逐题手填（本窗口已实现）；`PATCH /api/questions/{id}` 接 `qtype_id` + `primary_kp_id`（+ 可选 `secondary_kp_ids`）即完成打标与归档。
- **后台打标是无状态的**：`TAGGING_STATE` 在内存，**进程重启即丢**（进度归零但 DB 已落库的部分不丢）。若 T5+ 需要更稳的进度，可把状态落到 `questions.status`（现已够用）。
- **真实 key 到手后的验收**：把 `DASHSCOPE_API_KEY` 写进 `.env` → 重启 → 传新卷 → 复核点错几题 → 确认，前端会显示「⏳ 识别中 n/N」并在完成后自动刷新卡片；`python -m backend.tagger --exam-id N` 可直接在 CLI 看逐题结果。
- 回归/自测入口：`TAGGER_MOCK=1 .venv/bin/python -m backend.tagger --exam-id N [--approve ID]`。

## T5 错题本 / 题库浏览 —— ✅ 完成（2026-09-16，窗口 5）

### 交付清单（实际文件）

| 文件 | 内容 |
|------|------|
| `backend/api.py`（续写 T5 段） | `GET /api/wrong_bank`、`GET /api/pool`（**两个路由同一实现** `_browse_bank`，只差视图名）：筛选 subject/kp_id/kp_mode/qtype_id/district/exam_type/year/date_from/date_to + 分页 page/page_size；返回 `image_path`/`image_url`/主标签/次标签/出处标签；辅助函数 `_kp_filter_ids`（大类展开叶子）、`_kps_by_question`（**一次查询批量取主次标签，无 N+1**）、`_bank_facets`（该库实际出现过的筛选候选 + `bank_total`）、`_check_date`（日期格式校验） |
| `static/index.html` | 点亮「错题本」「题库」两个 tab（不再是占位）；两个 `<section id="view-wrong/pool">` 各含一个 `.browse` 宿主容器（**共用组件，HTML 只声明两个挂载点**）；新增共用大图弹层 `#lightbox` |
| `static/app.js` | `makeBrowser(viewName, host)` **一个组件渲染两个 view**（筛选条/卡片网格/分页全部生成，实例各自独立 state）；`makeBrowseCard`（切片图+题号+出处+题型+主标签）；`openLightbox/closeLightbox`（大图 + 主/次知识点 + 归档路径，Esc/✕/点背景关闭）；`applyHash` 深链接 + `showView` 泛化 + `VIEW_LOADERS` 注册表；`initBrowsers()` 接在 kp 树加载之后 |
| `static/style.css` | `.filters` / `.bcard`（含 hover）/ `.pager` / `#lightbox` 弹层 等 T5 样式 |

### 验收实测（真实 tagged 数据，**未造 mock**）

> 本窗口接手时 DB 里**已有 T4 打标的真实数据**（exam#1 2022 朝阳期末 22 题、exam#2 2024 海淀一模 2 题），
> 故按要求**直接用真数据验收**，无需 SQL 造 mock。若当时无数据，本窗口的验收脚本会改为造 mock 并标注。

**A. 两库互斥且无遗漏（错题只进错题本、其余只进题库）**

```
错题本 4 题 = v_wrong_bank 4 条 ✅   题库 20 题 = v_pool 20 条 ✅
交集 = []                            ✅
并集 == 全部 status='tagged' 题 id    ✅ [1..24]
未打标题(id 25~44) 两库一个都不出现    ✅
  错题本: 2024海淀区一模 第2题(解析几何) / 2022朝阳区期末 第8题(选择题) /
          第16题(选择题) / 第19题(概率统计)
```

**B. 按知识点筛选（核心验收项：与 DB count 比对）**

- **全量穷举比对**：`214 个知识点 × 2 个库 = 428 项`，API `total` 与 DB `COUNT(*)` **逐项一致，0 处不符**。
- 命中题号逐题比对（不只比总数）：叶子「函数的单调性与奇偶性」错题本 → `[16]`、题库 → `[3]`；
  大类「函数与导数」→ 错题本 `[16]`、题库 `[3,7,11,15,18,20]`（= 其 11 个叶子的并集）。
- 次标签口径（§2-8）：q19 的次标签 `样本数字特征…`、`古典概型` 在默认 `kp_mode=primary` 下**命中 0 条**，
  切 `kp_mode=any` 后各命中 1 条 ✅（主标签计数、次标签只展示，统计口径不串）。

**C. 题型/区/类型/年份/日期筛选 + 分页**

- 6 组筛选（qtype_id=1 / district=朝阳区 / exam_type=期末 / year=2022 / date_from=2000-01-01 /
  date_to=2000-01-01）× 2 库 = 12 项，与 DB 直查**全部一致**（含 date_to=2000-01-01 → 0 条的边界）。
- 分页：题库 20 条按 `page_size=7` → 3 页（7/7/6），三页 id 并集**无重无漏 == 全量**；越界页返回空。

**D. 图片静态服务（含 `/archive/` 覆盖）**

- 两库 23 张切片图经 `image_url`（`/archive/…`，URL 编码中文路径）**全部 HTTP 200 且非空** ✅
- 未打标题仍留在 `storage/crops` 的中间态切片经 `/storage/…` 同样可访问（200）✅
- 不存在的路径 404 ✅。`/archive`、`/storage` 双挂载是 T3 已做的，本窗口**只验证未改动**。

**E. 前端（无头 Chrome 实跑，`--dump-dom` + 真实点击探针）**

- `#wrong` / `#pool` 深链接直开：标题、计数条（「共 4 / 20 题」）、筛选控件
  （知识点下拉 **108 项** = 10 大类 + 96 叶子 + 1 条 T4 新增叶子 + 「全部」）、分页条全部正确渲染。
- 卡片内容逐张核对（4 + 20 张）：题号 / 出处 / 题型 / 主标签 与 DB 完全对应；
  未定位的 q22 显示「未定位 · 无切片」占位而不报错。
- **交互探针**（真点真跑）：点卡片 → 弹层打开（`hidden=false`）且大图 = 该题切片、
  8 行元数据正确（出处/题号/题型/主知识点/次知识点/入库时间/归档路径/原卷文件）→ 点 ✕ 关闭 ✅。
- **筛选**：选「函数与导数（整个大类）」→ 题库计数条变「共 20 题（当前筛选命中 6 题）」、
  卡片 6 张且**每张的主标签大类都是「函数与导数」**；错题本同一操作（独立实例 state）命中 1 题 ✅。
- **分页**：每页 12 → 「第 1/2 页 · 本页 12 题」→ 下一页「第 2/2 页 · 本页 8 题」，两端按钮禁用态正确，
  「上一页」回得去；「重置筛选」回到 20 题且知识点下拉回「全部」✅。

### 偏离 DESIGN.md / 补充决定

1. **新增 `kp_mode` 参数（primary 默认 / any）**：§6-T5 只说「按知识点筛选」。但 §2-8 规定统计只数主标签，
   而浏览时「这题还考了什么」也有价值，故把口径做成显式参数：**默认 primary 与气泡图/周报同口径**
   （保证「筛选数 = 统计数」，这是验收项要的），`any` 供翻查次标签。
2. **`kp_id` 点大类时展开为其全部叶子**：§6-T5 未规定，§6-T6 只提叶子。实现为「大类 = 自身 + 全部子节点」，
   于是一个下拉既能选大类也能选叶子，且**大类命中数 == 其叶子并集**（已逐大类 × 2 库验证）。
3. **日期筛选口径 = 入库时间，另加 `year`**：`date_from`/`date_to` 作用于 `questions.created_at`
   （即 T1 补的那列，T7 周报同源），用于「最近一周/一月入库」；试卷年份用独立的 `year` 参数（试卷集更常用）。
   T6 写的是 `from`/`to`（Python 关键字需 `alias`），其实现已同时接受 `date_from` 别名并注释「与 T5 参数名一致」。
4. **两个 view 共用一个组件、两个 section 各挂一个实例**：`makeBrowser()` 全部 DOM 由 JS 生成
   （筛选条/网格/分页），HTML 里只有两个 `.browse` 宿主。实例 state 互相独立（题库筛到 6 题时错题本不受影响，已验证）。
5. **每页候选加了 12**（`[12,24,48,96]`，默认 24）：原 24/48/96 在只有 20 条数据的库上**页码器永远只有 1 页、
   点不动也无法自测**；12 = 3 列网格整屏一版，让分页在真实小数据量下就能用/能验。
6. **次标签不进卡片**（§2-8「次标签仅在题目详情展示」）：卡片只出主标签，次标签放大图弹层里。
7. **view 路由泛化（为避免与并行窗口撞车）**：本窗口把 `showView` 从「写死 5 个 section 的 hidden」
   改为「view 名 == tab 的 `data-view` == section id 的 `view-` 后缀，只切 `#view-<name>`」，
   并新增 `VIEW_LOADERS` 注册表（review/wrong/pool 各注册自己的重拉钩子）。
   起因：开发期间 T6 窗口并行加了 `view-bubbles` + `bubbles.js`，旧的写死版本会导致点「气泡图」时
   **上传页和气泡图同时显示**。泛化后 T6/T7 只要照约定加 section+button 即可，无需改本窗口的代码。
8. **深链接 `#upload/#review/#wrong/#pool/#bubbles`**：tab 点击改为「改 hash → hashchange → applyHash」，
   可收藏、可刷新、前进/后退可用；也让无头浏览器能直接打开某个 view 做验收（本窗口的验收即靠它）。
9. **`facets` 由数据反推**：筛选下拉的区/类型/年份/题型候选 = **该库实际出现过**的值（不是 18 区全量枚举），
   避免下拉里全是选不出东西的空选项；同时返回 `bank_total` 供页面显示「共 N 题（当前筛选命中 M 题）」。

### 给下游窗口的备忘（T5 立的规矩）

- **两库口径不可改**：`v_wrong_bank` / `v_pool`（`is_wrong=1/0` **且** `status='tagged'`）——**未打标题两库都不出现**，
  这是与 T6 气泡图数字对得上的前提（气泡图 `done` 也应等于两库之和）。
- **主标签一律 `question_kps.is_primary=1`**；前端/统计要用次标签请显式 `kp_mode=any` 或直接查表。
- **列表接口返回体**：`{bank,subject,total,count,page,page_size,pages,items[],facets{},filters{}}`；
  `items[]` 每条含 `id/number/image_path/image_url/qtype/qtype_id/primary_kp/secondary_kps/exam_label/year/district/exam_type/exam_title/created_at`。
  `primary_kp` 可能是 `null`（T4 手动模式只选了题型没选知识点的题，如 q24），前端已用「无主知识点（待补录）」兜底。
- **`_kp_filter_ids(conn, kp_id)`** 可复用于 T6 气泡图点击叶子→抽屉列错题：它就是「大类→全部叶子」的展开逻辑。
- **`_kps_by_question(conn, qids)`** 是批量取标签的通用 helper（一次 IN 查询），新列表别逐题查。
- **前端加 view 的约定**：section id = `view-<name>`、tab button `data-view="<name>"`、需要重拉数据就往
  `VIEW_LOADERS` 注册一个函数（`bubbles.js` 加载在 `app.js` 之后，可直接 `VIEW_LOADERS.bubbles = load`）。
  ⚠️ 目前 `bubbles.js` 是自己用 capture 监听 `#tabs` 处理显隐 + 只在页面加载时读一次 `#bubbles`：
  **从别的 view 手工改 hash 到 `#bubbles` 时它不会自动加载**（点 tab 正常）；T6 窗口若看到此条可顺手注册进 `VIEW_LOADERS`。
- 回归入口：`PORT=8099 .venv/bin/python -m uvicorn app:app --port 8099` 起服务后，
  `GET /api/pool?kp_mode=any&kp_id=<id>` 可与 `SELECT COUNT(*) FROM v_pool …` 对拍。

## T6 气泡图 / T7 周报 —— 并行期间的状态说明（两个窗口均已补写完成，见下两节）

> 本文档由 T5 窗口记录到 T5 为止。开发期间观察到 T6 已在同一工作区并行推进
> （`backend/stats.py`、`/api/stats/bubbles`、`static/bubbles.js|bubbles.css`、`view-bubbles` 已落地），
> 其章节与验收由该窗口自行补写；本窗口**未改动 T6/T7 的任何代码**（只在 `app.js` 里把 view 路由泛化以免互相踩踏，见偏离 7）。

## T6 知识点气泡图 —— ✅ 完成（2026-09-16，窗口 6）

### 交付清单（实际文件）

| 文件 | 内容 |
|------|------|
| `backend/bubbles.py`（新） | `bubble_stats(conn, subject, district, exam_type, year, date_from, date_to)`：叶子 `{id,name,path,parent_id,done,wrong,error_rate}` + 大类 `{id,name,done,wrong,error_rate,direct_done,direct_wrong,children[]}` + `facets`（候选区/型/年 + 数据时间跨度）+ `meta`（tagged_total / scored_total / wrong_total / without_kp / untracked / leaf_count / practiced_leaf_count / max_done）。**一题一票**（`GROUP BY qs.id` + `MIN(kp_id)`），只数 primary 标签 |
| `backend/api.py`（续写） | `GET /api/stats/bubbles`：参数 `subject/district/exam_type/year/from/to`（`from` 是 Python 关键字，用 `from_` + `alias="from"`），另收 `date_from/date_to` 别名（与 T5 参数名一致）；日期非法→400、科目不存在→404 |
| `static/vendor/d3.v7.min.js` | **d3 v7.9.0 已下载入库（279 KB，本地引用，无运行时 CDN）**；`file://`/离线都能用 |
| `static/bubbles.js`（新，~520 行） | D3 force-pack：`d3.hierarchy().sum().sort()` → `d3.pack()`；大小=题数、颜色=错误率绿黄红连续色带、大类成簇外圈；hover tooltip；点叶子/大类→右侧抽屉列错题缩略图（点缩略图出大图）；筛选条；数据表；接 `VIEW_LOADERS.bubbles` 复用 app.js 的 hash 路由 |
| `static/bubbles.css`（新） | 筛选条 / 图例色带+刻度 / tooltip / 抽屉 / 数据表 / 窄屏（<900px 抽屉转下置） |
| `static/index.html`（3 处小改） | ① 气泡图 tab 由 disabled 改为可点 `data-view="bubbles"`；② 加 `bubbles.css`、`vendor/d3.v7.min.js`、`bubbles.js` 三个引用；③ 加 `#view-bubbles` 骨架（内容全由 JS 生成） |
| `tests/test_bubbles.py`（新） | 19 项断言回归：内部自洽 / 与 v_pool+v_wrong_bank 对拍 / **次标签不计数** / **一题一票** / 筛选口径。C、D 两组在**事务内构造脏数据后 rollback**，不动真实库 |

### 验收实测（构造/复用 tagged 数据，双通道：HTTP 对拍 + 无头 Chrome）

**A. 气泡数字 vs `/api/pool` + `/api/wrong_bank`（§6-T6 硬验收）**

对 9 组筛选逐一实跑（数据取自当时库内的 tagged 题）：

| 筛选 | 气泡合计 | /api/pool | /api/wrong_bank | 池+错 | meta.tagged_total | 判定 |
|------|---------|-----------|-----------------|-------|-------------------|------|
| 全部 | 6 | 6 | 1 | 7 | 7 | OK（差 1 = 未设知识点的题） |
| 朝阳区 | 5 | 5 | 0 | 5 | 5 | OK（完全相等） |
| 期末 | 5 | 5 | 0 | 5 | 5 | OK（完全相等） |
| 2022 | 5 | 5 | 0 | 5 | 5 | OK（完全相等） |
| 海淀区 | 1 | 1 | 1 | 2 | 2 | OK（差 1） |
| 一模 | 1 | 1 | 1 | 2 | 2 | OK（差 1） |
| 2020 年（空窗口） | 0 | 0 | 0 | 0 | 0 | OK（完全相等） |
| 区+类型+时间 | 5 | 5 | 0 | 5 | 5 | OK（完全相等） |

> 唯一「不相等」的三行差值恒等于 `meta.without_kp`（=1）：那是**已打标但主知识点为空**的题
> （T4 手动模式只选了题型没选知识点，如 q24），它既不属于任何叶子、也不属于任何大类，
> 故不进气泡。**这一条不是静默丢数**：接口把它单列成 `without_kp`，前端顶部有黄色横幅
> 明写「另有 N 题已打标但没有主知识点，不进气泡，去复核页补一个即可计入」。
> 补上知识点后差值归零（上表「完全相等」的行即为此形态）。

**B. 次标签不计数（§2-8）** —— 两条独立证据：
1. `tests/test_bubbles.py`：事务内给一道已打标题挂一条 `is_primary=0` 的次标签到某叶子
   → 该叶子 `done` 仍为 0、气泡合计与大类合计都不变；SQL 复核次标签确实写进去了 → **PASS**。
2. 真实数据（本轮早期）：q19 的次标签 kp89（概率与统计/样本数字特征·平均数方差标准差）
   在气泡里 `done=0`，而 `/api/wrong_bank?kp_id=89&kp_mode=any` = 1 题 / `kp_mode=primary` = 0 题。

**C. 无头 Chrome（CDP 真事件驱动，非只 dump DOM）**——`node /tmp/bb_cdp{,2,3}.mjs`
- 渲染：97 个叶子气泡、10 个大类圈（10 个都带大类名标签）、17 个「有题」气泡带数字，
  **屏上数字之和 = 23 = `meta.scored_total`**（与当轮接口值一致）。
- hover：`mouseenter` 一个错题叶子 → tooltip 显示「概率与统计/频率分布直方图与百分位数／题数 1 题／
  错题 1 题／错误率 100%」，被 hover 的气泡描边加重。
- 点叶子 → 抽屉标题「…·错题」、`本筛选下共 1 道错题`、1 张缩略图，
  `src=/archive/数学/错题本/概率统计/2022朝阳期末_19.png`（**与错题本里的真实归档路径一致**）、
  图注「2022 朝阳区 期末 第19题 · 概率统计」。
- 点大类圈 → 抽屉文案变「共 1 道错题（含该大类全部叶子）」。
- 切筛选「海淀区」→ 概览条与屏上气泡同步变成 1 题；数据表同步。
- 数据表：17 行（= 有题叶子数），按题数排序，页脚「共 17 个知识点有做过题；合计 23 题、错 3 题」，
  与概览条一致。
- 路由（接 T5 泛化后的 hash 路由）：点 tab → `#bubbles` 正常渲染；切走再切回图仍在；
  浏览器后退可用；**冷启动深链接 `http://127.0.0.1:8011/#bubbles` 直接出图**；期间 `/api/stats/bubbles` 请求数 = 2（不重复拉）。

**D. 回归入口**：`.venv/bin/python tests/test_bubbles.py` → 本轮末次 **19 PASS / 0 FAIL**。

### 偏离 DESIGN.md / 补充决定

1. **统计代码落在 `backend/bubbles.py`，没有进 `backend/stats.py`**（§4 把两者都写在 stats.py）。
   **原因**：本窗口开工时 `stats.py` 正被 T7 窗口**整文件覆盖**（我写进去的 bubble_stats 被覆盖掉过一次），
   两个窗口并行写同一文件必然互相丢代码。故 T6 独立成模块，对外只暴露 `bubble_stats()`；
   日后若由单一窗口统一整理，把它并回 `stats.py` 即可（调用点只有 `api.py` 一处）。
2. **前端三件套独立成 `bubbles.js` / `bubbles.css`**，同理：`app.js` / `style.css` 当时正被 T5、
   T7 窗口高频改写，写进去就是互相覆盖。`index.html` 只做了 3 处最小改动（tab、三个 `<link>/<script>`、section 骨架）。
3. **颜色口径**：DESIGN 要「绿→黄→红连续色带」，实现取状态色三锚点
   （good `#0ca30c` / warning `#fab219` / critical `#d03b3b`，出自 dataviz 参考色板的 status 组），
   域 `[0, 0.5, 1]`（50% 错误率=正黄）线性插值。
   黄色在白底上对比度仅 1.83:1（低于 3:1），故按 dataviz 的 relief 规则配了**三重兜底**：
   气泡上的可见数字 + hover tooltip + 「数据表」视图（不依赖颜色也能读全部数值）。
4. **没做过的叶子画中性灰，不画绿色**：`error_rate` 在 `done=0` 时「无定义」，画绿色等于宣称「全对」，
   是错的信号；灰点 + tooltip「还没做过这个知识点」+ 图例说明「灰 = 还没做过」。
   气泡大小仍按 `done`（pack 权重取 `done + 0.3`，保证 0 题叶子留一个可见小点，不改变相对大小读数）。
5. **大类的 `direct_done/direct_wrong`**：AI 偶尔把主标签直接给到大类层级（§6-T4 的「大类兜底」路径），
   这类题不属于任何叶子。若不单列，大类数字会 ≠ 其叶子之和，用户会以为统计错了。接口单列、tooltip 里显示。
6. **`from`/`to` 同时接受 `date_from`/`date_to`**：§6-T6 写 `from`/`to`（Python 关键字，需 `alias`），
   §6-T5 写 `date_from`/`date_to`。两个都收，前端统一发 T5 名字，这样**同一串筛选参数能直接喂给
   `/api/wrong_bank`**（抽屉里的错题列表就是这么用的——最初写 `from`/`to` 时抽屉的时间筛选被静默忽略，实测发现后修掉）。
7. **时间筛选按 `questions.created_at`**，与 T5 一致（该列见 T1 偏离 ①，T7 验收也要改它模拟历史数据）。
8. **大类圈内不塞标签**：97 个叶子时任何「圈内顶部」位置都会被自己的孩子压住，
   改为**画在圈外上方**（白色描边光晕 + 最上层），并在画布顶部预留 34px 让最上面一排标签不出框。
9. **不动 `app.js` 的 view 路由**：T5 窗口已把 `showView` 泛化成「view 名 = tab 的 data-view = section 的
   `view-<name>`」，并留了 `VIEW_LOADERS` 注册表。本窗口按约定注册 `VIEW_LOADERS.bubbles`
   （切回时强制重拉，复核页刚补录的标签会改变气泡数字），删掉了自己原先的 capture 监听，
   只在 `init` 里补一次 `#bubbles` 冷启动（兜住「applyHash 早于本文件注册」的时序）。

### 给下游窗口的备忘（T6 立的规矩）

- **读统计一律走 `backend/bubbles.py::bubble_stats`**（不要在别处重写聚合 SQL）：它已经保证
  「一题一票 / 只数主标签 / 与 v_pool+v_wrong_bank 同源」三条口径。改口径就改这一个函数。
- **`meta` 里三个数字必须一起看**：`tagged_total`（= 池+错题本条数）、`scored_total`（进了气泡的）、
  `without_kp`（打标成功但没知识点的）。**三者关系恒为 `tagged_total = scored_total + without_kp`**，
  且 `untracked` 恒为 0（非 0 说明有主标签指向已删除的知识点，要修数据）。任何新页面做「合计」
  都应当把 `without_kp` 显示出来，否则用户会以为漏数。
- **前端加 view 的约定**（承 T5）：section id `view-<name>` + tab `data-view="<name>"` + 往 `VIEW_LOADERS`
  注册一个「重拉」函数即可；`bubbles.js` 是现成范例（含独立 CSS 的做法）。
- **d3 已 vendored**：`static/vendor/d3.v7.min.js`（v7.9.0）；`marked.min.js` 也在同目录（T7 用）。
  新页面直接用，**不要再引 CDN**。
- 回归：`.venv/bin/python tests/test_bubbles.py`（只读库；C/D 组用事务 + rollback 构造脏数据）。

## T7 周总结与推荐 —— ✅ 完成（2026-09-16，窗口 7）

> 本窗口**只做 T7**（每周总结）。接手时 T5/T6 由并行窗口同时在改同一工作区，
> 故本窗口刻意把自己的改动限制在**新文件 + 少量追加**（见偏离 1）。

### 交付清单（实际文件）

| 文件 | 内容 |
|------|------|
| `backend/stats.py`（新，~600 行） | ① `week_window(days, as_of)` 时间窗（含端点，days=1~365）；② `collect()` 近 N 天按**主标签**聚合（做题数/错题/错误率 + 按知识点 + 各题型正确率 + 明细），**同源一次查询**保证概览/知识点表/题型表/明细表四个数不打架；③ `recommend()` 推荐规则 A/B；④ `render_markdown()` 六节 md；⑤ `write_report()/read_report()/list_reports()` 落盘与读回；⑥ `weekly_report()` 主入口。规则参数集中在文件头（`MIN_SAMPLE=3 / REC_MIN_RATE=0.5 / REC_IDLE_DAYS=14 / REC_MAX=3`） |
| `backend/api.py`（续写 T7 段） | `GET /api/stats/weekly`（days/subject/as_of/write）、`GET /api/stats/weekly/report`（读盘 + 同源结构化数据，供「建议卡片」）、`GET /api/stats/weekly/reports`（计划库已有周报列表）。`StatsError`→400、科目不存在→404、未生成的文件→404 |
| `static/vendor/marked.min.js` | **marked v12.0.2 已 vendored（35 KB，无运行时 CDN）** |
| `static/weekly.js`（新） | 「周报」view：建议卡片 + `marked.parse()` 渲染 md 正文 + 文件路径/更新时间/大小信息条；时间窗下拉（7/14/30 天）、截止日 `as_of`、重新生成、历史周报回看（`regenerate=0` 只读原文）；按 T5/T6 约定注册 `VIEW_LOADERS.weekly`（切 tab / `#weekly` 深链 / 前进后退都自动重拉） |
| `static/weekly.css`（新） | 工具条 / 建议卡片（规则 A 橙、规则 B 蓝）/ 结论条 / markdown 排版（表格、引用块） |
| `static/index.html`（3 处小改） | ① 「周报」tab 由 disabled 改为可点 `data-view="weekly"`；② 加 `weekly.css`、`vendor/marked.min.js`、`weekly.js` 三个引用；③ 加 `#view-weekly` 骨架 |
| `tests/test_weekly.py`（新） | **69 项断言**回归：时间窗边界 / 样本门槛 / 排序（含并列样本多者优先）/ 题型正确率 / 规则 A 与 B / md 落盘与覆盖 / 空库与空窗口。合成数据在独立临时库上跑；真实库部分**期望值全部现算**，不写死数字 |
| `tests/serve_weekly_demo.py`（新） | 验收辅助：用「另一份 DB + 另一个计划库目录」起服务，验证界面时**不碰用户真实库与 archive/** |

### 验收实测（双通道：HTTP + 无头 Chrome 渲染对拍）

**A. 时间窗（改 created_at 造上周数据）**

- 真实库副本把 exam#1 整卷 `created_at` 改到 20 天前（2026-08-27）后：
  `days=7&as_of=今天` → 只剩 exam#2 的 2 题；`as_of=2026-09-02`（上周窗口 08-27~09-02）→ 该卷 22 题全部可见；
  `days=30` → 全库 24 题。**边界**：起-1 天（09-09 23:59:59）与止+1 天（09-17 00:00:00）的题均不计入，
  起/止当天的 00:00:00 与 23:59:59 均计入。
- 夹具库上换截止日：`as_of=09-16` → 23 题；`09-09` → 1 题；`09-02` → 0 题（窗口滑动正确）。

**B. 门槛与排序（样本≥3 才进榜）**

```
清单表: 函数与导数/函数的最值      题 2 错 2  100.0%  进榜=False   ← 样本不足被挡住
清单表: 三角函数/正弦定理与余弦定理  题 4 错 3   75.0%  进榜=True
清单表: 数列/等比数列求和          题 6 错 4   66.7%  进榜=True
清单表: 数列/错位相减求和          题 3 错 2   66.7%  进榜=True    ← 与上一条并列，样本少者靠后
清单表: 函数与导数/导数与切线      题 4 错 2   50.0%  进榜=False
```
低于门槛的知识点写进 md 的「未进榜（样本不足 3 题）」说明，而不是混进榜单。

**C. 推荐规则（纯 SQL/Python，无 LLM）**

- 规则 A「错误率≥50% × 近 14 天未练 × 样本≥3」→ 命中 3 条并按 错误率↓→样本↓ 排序：
  正弦定理与余弦定理(75%,4 题,24 天未练) → 等比数列求和(66.7%,6 题,37 天) → 错位相减求和(66.7%,3 题,44 天)，
  题型取该知识点全库最高频者（三角函数 / 数列 / 数列）。
- **本周刚练过的高错误率知识点不会被推荐**（导数与切线全库错误率 0.833 最高，但 last_date=09-17 → 被「近 14 天未练」挡下）。
- 规则 B（兜底，补足 2 条）：规则 A 无命中时，用本周窗口错误率榜里**真有错题**的知识点补足；
  全对的知识点不会被推荐。
- 零数据/无命中时**如实输出「数据不足」说明性建议**（kp=None），不硬凑假推荐——真实库当前就是这种状态，已实测。

**D. 落盘与「界面 == 文件」**

- `archive/数学/计划库/周报-20260910-20260916.md` **真实落盘**（6596 字节，六节齐全）；
  同一时间窗重生成**覆盖同名文件**（计划库里始终只有一份，不产生副本）。
- `GET /api/stats/weekly/report` 返回的 `markdown` 与磁盘文件**逐字节相同**（`== ` 断言 True）。
- 无头 Chrome 打开 `#weekly`：**4 个表格 cell-by-cell 与磁盘 md 完全一致**、
  7 个标题一致、引用块一致、建议卡片文案与接口 JSON 逐条一致；
  用**同一个 marked 版本**渲染磁盘 md 得到的 HTML，与浏览器 DOM 里的渲染结果**解码实体后完全相同**。

### 偏离 DESIGN.md / 补充决定

1. **改动面刻意收窄**：动工时 T5/T6 两个窗口在并行改 `app.js`/`index.html`/`api.py`。
   本窗口的做法是——T7 的一切**新逻辑放新文件**（`backend/stats.py`、`static/weekly.js|weekly.css`），
   对公共文件只做**追加式**小改（api.py 末尾追加 T7 路由段；index.html 三处；零改 `app.js`）。
   `weekly.js` 则按 T5 立的约定注册 `VIEW_LOADERS.weekly`，与 `bubbles.js` 同构。
   （T6 窗口因同样的原因把气泡统计单独放进 `bubbles.py`；`stats.py` 自始至终只有周报代码，
   两个文件将来若合并，把 `bubble_stats()` 并进 `stats.py` 即可。）
2. **周报 md 是界面显示的同一份文件，不是「同源数据各渲染一遍」**：§6-T7 写「读该 md 渲染」，
   故 `/api/stats/weekly/report` 的做法是「先按当前数据重算并**覆盖**同名 md → 再把**磁盘上的原文**返回」，
   前端只 marked 渲染这份原文。这样「界面与文件一致」是**构造上成立**的，不靠人工比对。
   `regenerate=0` 时只读不写（历史回看用）。代价：每次打开周报页会重写一次同名文件（<10ms，符合「重生成覆盖」）。
3. **时间窗 = 滚动 7 天（含今天），不做自然周对齐**：§6-T7 写「过去一周/近 7 天」，
   §5.5 的文件名也是 `周报-{起}-{止}`。选滚动窗口后，`as_of` 参数可复现任意历史窗口（验收也靠它）。
   时间口径用 `questions.created_at`（T1 就为此加了这列，见 T1 偏离 1）；`exams.uploaded_at` 无法区分
   「今天补传上周的卷子」。
4. **统计口径**（与 T5/T6 一致）：`status='tagged'` 且 `is_wrong` 已定档（0/1），
   知识点**只数主标签** `question_kps.is_primary=1`（§2-8）。
   例外一条：**概览的「做题总数/各题型正确率」包含「打了题型但没打知识点」的题**
   （否则用户会看到总数比题型表之和小），这类题单列 `no_kp` 计数并在 md 与结论里提示补录。
5. **错误率榜不过滤 0 错误项**：榜就是「样本≥3 的知识点按错误率降序」（这样门槛/排序可验证、可留痕），
   但当榜里没有错题时，md 会显式写「本周样本≥3 的知识点全对，没有可点名的错点」，
   且「一句话」与规则 B 只认 `wrong>0` 的条目——不会出现「拿 0% 当最薄弱」或「推荐全对的知识点」。
6. **推荐规则 A 用全库历史、规则 B 用本周窗口**：§6-T7 的「近 14 天未练」本身必是全库口径
   （本周窗口内不可能算出「未练」）。A 命中不足 2 条时用 B 补足（§6-T7 要求「输出 2-3 条」），
   理由里标注规则来源；A+B 都空则如实说数据不足。
7. **`as_of` 参数（新）**：§6-T7 只写了 `days=7`，但验收要求「改 created_at 造上周数据，验证时间窗」，
   没有 `as_of` 就只能真去改库。加了这个参数后，时间窗/门槛/排序都能在**不改库**的前提下复现与自测，
   也不影响前端默认行为（不传即今天）。
8. **`tests/serve_weekly_demo.py`**：验收界面时需要一份「数据恰好触发规则 A」的库，
   若直接用真实库就得改用户数据。该脚本用「另一份 DB + 另一个计划库目录」起服务，
   验证完即停，**用户的 `storage/app.db` 与 `archive/` 全程只读**（唯一写入是当前窗口那份真实周报）。

### 给下游窗口的备忘（T7 立的规矩）

- **周报唯一入口是 `backend/stats.py::weekly_report`**；要改口径（时间窗/门槛/推荐规则）就改这一个文件，
  参数常量都在文件头。界面永远显示 md 原文，别在前端二次加工统计。
- **加了新数据后不必手动刷新周报**：打开「周报」页就会按当前数据重生成并覆盖同名 md。
- **历史周报文件会累积**（每周一个文件，同名覆盖）：计划库 = `archive/数学/计划库/周报-YYYYMMDD-YYYYMMDD.md`，
  列表接口 `GET /api/stats/weekly/reports`。
- **`marked.min.js` 已 vendored**（v12.0.2）。渲染 md 用 `window.marked.parse(md)`；正文来自本机 DB，
  当前按可信内容处理（未做 HTML 消毒），若将来引入外部数据源需要补消毒。
- 回归：`.venv/bin/python tests/test_weekly.py`（合成库 + 真实库副本，全部只读；
  真实库部分的期望值现算，数据被别的窗口改动也不会假失败）。

## 窗口 10（规则修正 + 删整卷）—— ✅ 完成（2026-09-16，窗口 10）

### A. 新规则：数学卷一律 21 题，不存在第 22 题

用户确认「所有数学的卷子都只有 21 题，不会出现第 22 题」，要求写进规则；
多出来的题「我自己看着办」。

**改动：**

| 位置 | 改动 |
|------|------|
| `backend/pdf_slicer.py` | 新增常量 **`EXPECTED_MAX_DEFAULT = 21`**（原默认 22），附规则依据注释；`slice_pdf()` 与 CLI `--expected-max` 都用它 |
| `backend/pdf_slicer.py::_plan` | **尾部缺口（最大锚定题号+1 .. expected_max）不再建成「未定位」题**，改为只出警告。理由见下 |
| `backend/api.py` | `POST /api/exams` 的 `expected_max` 默认改取 `EXPECTED_MAX_DEFAULT` |
| `DESIGN.md` | §2 决策表新增第 14 条「数学卷题量 = 21」；§5 schema 注释 `题号 1..22` → `1..21`；§6-T2 验收口径同步 |
| `tests/test_pdf_slicer.py` | 新增 5 条断言覆盖新规则；真实卷改用**默认** expected_max |
| `README.md` | 「复核」页说明改写：数学卷 21 题、多出来的会保留并提醒复核 |

**「多出来的题我自己看着办」——分两种情形分别处理（都不静默丢数据）：**

1. **推测出来的空位（尾部缺口）→ 直接不建题。**
   原来是 `range(nums[-1]+1, expected_max+1)` 一律建成 `unlocated` 空题。但那些位置
   **根本没有内容可切**，纯属「这卷子也许还有题」的推测 —— 建出来就是幽灵题
   （真实卷实测：多出一个「第 22 题」，无切片、无知识点，还会以 `tagged` 空标签
   混进题库和气泡图的 `without_kp`，首页/统计全被它污染）。现在改为只写一条警告。
2. **真的锚到了超出惯例的题号 → 保留 + 警告，不删。**
   如果模型真在第 4 页锚出「22.」，那说明后面**确有内容**（或是把答案段编号误当题号）。
   两种可能都不该由程序静默删掉：留下并提示「检测到第 22 题，超出数学卷惯例的 21 题；
   已保留，请复核是否切错」，由人看一眼决定。**宁可多留一道让人扫一眼，也不误删一道题。**

**实测：**

| 场景 | 结果 |
|------|------|
| 真实卷 `2022北京朝阳高二（下）期末数学.pdf`，默认 21 | **located=21 total=21 gaps=[] warnings=无** —— 完全干净，幽灵题消失 |
| 合成 22 题双栏卷，expected_max=21（超惯例） | 保留 22 题 + 警告「检测到第 22 题，超出数学卷惯例的 21 题；已保留，请复核是否切错」 |
| 合成 22 题双栏卷，expected_max=30（欠惯例） | 题号止于 22、**不生成 23~30 的幽灵题** + 警告「本卷只锚定到第 22 题（预期到第 30 题）…请复核」 |
| 库里已存在的那个幽灵题（exam#1 题 22） | 已按新规则**删除**（`DELETE /api/questions/22`）。删后：21 题、`v_pool` 19→**18**、`without_kp` 1→**0**（气泡图顶部黄条随之消失）、`scored_total == tagged_total == 21` |

### B. 新功能：删除整套卷（首页汇总里）

| 位置 | 改动 |
|------|------|
| `backend/api.py` | ① 抽出公共 helper **`_remove_slice_file()`**（只删项目目录内的文件，项目外路径一律跳过）与 **`_purge_questions()`**（批量清题目 + 标签 + 建议 + 切片），`DELETE /api/questions/{id}` 改为复用它；② 新增 **`DELETE /api/exams/{exam_id}`**：删该卷全部小题 + `storage/crops/{exam_id}/` + 残留源 PDF，并**逐级清理归档里因此变空的目录**（`题库/{年}/{区}/{型}` 空则往上删）。返回删除计数 |
| `static/home.js` | 首页每行卷子右侧加 **🗑**（默认透明，hover 该行才显形；触屏 media query 下常显）；`deleteExam()` 二次确认（弹窗写明 年份/区/类型/文件名/题数/错题数）→ `DELETE` → 刷新首页 + 错题本 + 题库 + 复核页试卷下拉。按钮点击 `stopPropagation()`，不会误触发「点行去复核页」 |
| `static/home.css` | 行网格加第 6 列给操作按钮；`.hs-del` 样式与 hover 行为 |
| `README.md` | 首页说明补 🗑；新增 FAQ「整卷传错了怎么办」 |

**实测（真实 UI，无头 Chrome 真点击）：**
- 上传一套测试卷（2023 丰台区 二模，21 题，2 错）→ 首页出现两行卷子、两个 🗑；
- 点 2023 那行的 🗑 → 确认弹窗文案正确（含「2023 年 · 丰台区 · 二模」「共 21 题（其中错题 2 道）」）
  → 首页统计从「2 套卷 / 42 题 / 5 错题」变为 **「1 套卷 / 21 题 / 3 错题」**、
  该行消失、「复核」页试卷下拉同步只剩 `#1`；
- 文件系统：`archive/数学/题库/2023/…` 与 `错题本/{选择题,……}` 里该卷的文件全部消失，
  **空目录被逐级清掉**（`archive/数学/题库/` 下只剩 `2022`）；`storage/crops/2/` 整个消失；
- **exam#1 的文件与数据分毫未动**（21 题、3 错题、错题本 3 张、题库 18 张）；
- 边界：`DELETE /api/exams/999` → **404**；`question_kps` / `new_kp_suggestions` 一并清干净。

### C. 回归

| 套件 | 结果 |
|------|------|
| `tests/test_pdf_slicer.py` | **全部通过 ✅**（含新增 5 条 21 题规则断言） |
| `tests/test_bubbles.py` | **19 PASS / 0 FAIL** ✅ |
| `tests/test_weekly.py` | **全部 PASS** ✅ |

> 注：为验证删整卷，本轮又上传了一套测试卷并**真实跑了 AI 打标**（约 ¥1、120k token）。
> 验证完已整卷删除，库里现在**只有 exam#1**（真实卷 + 真实 AI 打标，21 题、错 3）。

## 窗口 9（验收后新增功能）—— ✅ 完成（2026-09-16，窗口 9）

用户（非技术）在验收后提了两个需求。两件都做完并实测。

### 交付清单

| 文件 | 内容 |
|------|------|
| `backend/api.py` | ① `GET /api/exams/summary?subject=`：已上传卷子按 **科目→年份→考试类型→城区** 分组的树，每节点带 `n_exams/n_questions/n_wrong/n_tagged`，叶子挂具体卷子；**只输出真实存在过的分支**（没传过的年份/区不出现空行）；年份↓、考试类型按 `EXAM_TYPES` 固定序、城区按名。② `DELETE /api/questions/{id}`：删题（行 + 主次标签 + 新知识点建议 + **切片图片文件**）。 |
| `static/home.js`（新） | 首页「📊 已上传的卷子」面板：概览数字（套数/题数/错题数/已归档数）+ 缩进目录树 + 每行一条**单色**量级横条；点卷子行 → 跳「复核」页并选中该卷。按 T5/T6/T7 的约定注册 `VIEW_LOADERS.upload`，并暴露 `window.loadHomeSummary` 供删题后刷新。 |
| `static/home.css`（新） | 树/横条/层级样式。横条 = 单系列单色（主色 `#1456b0`），**透明度只随层级变**（恒定编码"第几层"），不随数值变 —— 数值由条长 + 右侧文字列双重给出，不依赖颜色也能读全。 |
| `static/app.js` | 大图弹层加 `🗑 删除此题`：`deleteCurrentQuestion()` → 二次 `confirm`（弹窗写明哪套卷哪一题、会删文件、不可恢复）→ `DELETE` → 关弹层 + 刷新两个浏览 view + 首页汇总。`LB_ITEM` 记住当前题。 |
| `static/index.html` / `style.css` | 上传页加 `#home-summary` 容器；弹层加 `#lb-actions`；引 `home.css`/`home.js`；危险按钮样式。 |
| `README.md` | 补「已上传的卷子」汇总说明、删题说明与「想删一道题怎么办」FAQ；上一轮已整篇重写。 |

### 实测（真实数据 + 无头 Chrome 真点击）

- **首页汇总**（4 套卷时）：16 行、4 个可点卷子行、横条宽度按题量成比例（`max_questions` 归一）；
  层级与顺序正确（2026→2025→2022；2022 内「一模」在「期末」前）。
- **点行跳转**：点卷子行 → `#review` 且该卷被选中（实测 `exam_sel=4`、`#rv-meta` 显示对应卷）。
- **空状态**：把 summary 请求注入成「0 套卷」→ 渲染成「还没有上传过卷子…」提示，**不报错**。
- **删题**（题库，走真实 UI）：点卡片 → 弹层出现「🗑 删除此题」→ 点它 → 确认弹窗文案正确
  （含「2025 朝阳区 二模　第 1 题」）→ 确定后弹层关闭、题库「共 39 题」→「共 38 题」、
  首页汇总同步从「86 道题 / 46 已归档」→「85 道题 / 45 已归档」。
- **取消删除安全**（错题本）：弹窗选「取消」→ **弹层保持打开、卡片数不变**（无副作用）。
- **接口边界**：`DELETE` 不存在的题 → 404；重复删除 → 404；题目行/`question_kps`/
  `new_kp_suggestions` 三处都清干净；切片文件确实从 `archive/` 消失且**只删该题那一张**。
- **不误删项目外文件**：`image_path` 若不在项目目录内（T2 CLI 允许传项目外 `out_dir`）则**跳过不删**。
- **六 view 冒烟**：全部可见、互不重叠、控制台**零报错**。

### 偏离 / 设计取舍

1. **前端拆 `home.js` + `home.css` 独立文件**，不改 `app.js` 的 view 路由：
   沿用 T5/T6/T7 立下的约定（section id `view-<name>` + `VIEW_LOADERS` 注册），
   这样后续窗口再改 `app.js` 也不会与本功能互相覆盖。
2. **图表形式选「缩进树 + 单色横条」而非 treemap/饼图**（按 dataviz 方法选型）：
   用户要的是「按 科目/年份/类型/城区 的目录排列」+ 看总量，不是占比或趋势；
   树天然容纳四层且可容纳卷子名。**单系列 = 单色**，故不需要图例，也不需要跑分类色板
   CVD 校验（没有分类色）。数值一律以文字直接写在右侧列，颜色/条长都不是唯一读数途径。
3. **删除是硬删除**（无回收站）：用户明确要「删除此题的选项」，且做法上再简单不过。
   已用「弹窗写明是哪套卷哪一题 + 不可恢复」+ 取消无效化来兜住误操作。
   若日后要后悔药，最小改动是改成移动到一个 `archive/_已删除/` 目录而不是 `unlink`。
4. **没做「整套卷删除」**：用户没要求。若要加，可复用 `DELETE /api/questions/{id}` 的逻辑
   加一个 `DELETE /api/exams/{id}`（本窗口的清库工作就是用前者逐题删的，顺手验证了这条路）。

### 验收测试数据的清理（用户授权「你看着办」后执行）

按上节「§6 验收后的数据现状」把 #2/#3/#4 三套**我自己造的**验收测试卷删掉了
（走 `DELETE /api/questions/{id}` 逐题删 → 再删 exam 行 → 清空目录 → 重生成周报），
**只保留 #1：真实卷 + 真实 AI 打标**（22 题、错 3，错题本 3 张 / 题库 18 张有切片）。
清完实测：`exams` 只剩 1 行、`v_wrong_bank=3`、`v_pool=19`、
首页汇总「1 套卷 / 22 题 / 3 错题 / 22 已归档」、周报重算为「22 题、错 3」。

> 注：`v_pool=19` 里的那道「未定位题」（题 22，原卷只有 21 题）目前仍在库里，
> 是一道**无切片、无知识点**的空题（复核页显示「未定位」，气泡图顶部有黄色横幅说明它）。
> 现在用户可以用新的「🗑 删除此题」在题库里把它删掉 —— 留着也完全不影响使用。

## 窗口 8（总验收）—— ✅ 完成（2026-09-16，窗口 8）

> 按 DESIGN §7 的端到端清单**逐项实跑**，不是读代码判断。全程两条通道：
> 真实 HTTP API + 无头 Chrome（CDP 真事件、真点击、真渲染读数）。
> 验收用的 API key 是用户提供的智谱 `glm-4v-plus`，exam#1 全 22 题**真跑**（花的是真钱，约 ¥1）。

### 0. 验收前的状态处理

接手时库/归档里是第 1~7 窗口的开发残留（exam#1 22 题全 untagged 但切片已进
`题库/2022/朝阳区/期末/*.png`、exam#2 20 题 untagged + 2 题 tagged、错题本只有 1 张）。
新传同一套真实卷会往同名目录写 `1.png~21.png` **覆盖**这些残留，故验收前**已征得用户同意**
清空开发残留（`storage/app.db`、`storage/crops/*`、`archive/数学/{题库,错题本,计划库}`）。
清空前把 `app.db` + `archive/` + `crops/` 完整备份在 **`/tmp/acceptance_backup/`**（尚未删除）。

### 1. 逐项验收结果（DESIGN §7）

| # | 清单项 | 结果 | 实测证据 |
|---|--------|------|---------|
| 1 | `./run.sh` 冷启动 | ✅ | 杀掉旧实例后冷启，**端口 8000**、`/api/health` 200 `{"ok":true,"kp_count":106}`、**浏览器自动打开**（服务端日志可见浏览器真实的 GET 请求） |
| 2 | 上传真实卷 + 手选元信息 | ✅ | `POST /api/exams` 200；年份候选 2023-2027 / 城区 18 / 类型 4 项；选 2022+朝阳区+期末 → 切片 21/21 定位（该卷实际 21 题，题 22 正确置 unlocated） |
| 3 | 复核页点错 3 题 | ✅ | 点缩略图 → 卡片变红框「✗ 错题已保存」，计数条「共 22 题 · 已标错题 3 题」；再点可取消 |
| 4 | confirm → 源 PDF 删、正确题入库 | ✅ | `{pool_copied:18, wrong_kept:3, no_image:1, source_pdf_removed:true}`；`storage/uploads/` 事后为空 |
| 5 | AI 识别（真实 key） | ⚠️ 见偏差 ① | 21/22 打标成功、1 题**正确弃权**（题 22 无切片）；耗时 ~2 分钟；**121,264 token/套**。**但「含 1 条新知识点建议」未出现**（模型这轮 0 条建议） |
| 6 | 新知识点建议 → 点确认 | ✅（用 mock 通道验收） | 见下方「偏差 ①」；横幅渲染「💡 AI 建议新增知识点 1 条…[确认][忽略]」→ 点确认 → 叶子 96→97、`origin='ai'/confirmed`、该题主标签回填（旧主标签降为次）、**文件名不变**、重复确认 409 |
| 7 | archive 归档（Finder 层核对） | ✅ | 错题 3 张在 `错题本/{选择题,填空题,概率统计}/2022朝阳期末_{8,16,19}.png`；正确题 18 张在 `题库/2022/朝阳区/期末/{题号}_{题型}.png`；源 PDF 已消失；未定位题 22 无文件 |
| 8 | 错题本 view | ✅ | 3 张卡片，题型/出处/主标签与 DB 逐条对应；卡片图 `src` 指向真实归档路径 |
| 9 | 题库 view | ✅ | 19 题 = 18 张有切片 + 1 张未定位；分页「第 1/2 页 · 本页 12 题 · 命中 19 题」 |
| 10 | 气泡图 view | ✅ | 96 叶子 + 10 大类 = 106 圆；**屏上数字之和 21 == `meta.scored_total`**；数据表 18 行、题数合计 21、错题合计 3；hover tooltip「函数与导数 题数 8 题 错题 1 题 错误率 13%」；点大类 → 抽屉列出该大类错题且图为真实归档路径 |
| 11 | 周报 view | ✅ | 4 张表格、7 个标题与磁盘 md 一致；概览「做题总数 22 / 错题 3 / 正确率 86.4% / 覆盖知识点 18」；文件信息条显示 `archive/数学/计划库/周报-20260910-20260916.md`；md **真实落盘 6685 字节** |
| 12 | **四 view 数字一致** | ✅ | 见下表「一致性对拍」 |
| 13 | 全程无 key 手动模式 | ✅ | 见下方 B 节 |

### 2. 一致性对拍（§7「四个 view 逐一检查数字一致」）

验收后库里共 4 套卷（46 题 tagged），四个 view 与 DB 直查**逐项相符**：

| 口径 | 错题本 | 题库 | 气泡图 | 周报 |
|------|--------|------|--------|------|
| 题数 | **7** | **39** | wrong_total=**7** / scored=**45** | 做题总数 **46**、错题 **7** |
| 与 DB | `v_wrong_bank`=7 ✅ | `v_pool`=39 ✅ | 7+39=46=tagged ✅ | ✅ |
| 其它 | — | — | `tagged_total 46 = scored 45 + without_kp 1` ✅；大类 done 合计 45 = scored ✅ | 「覆盖知识点 21」= `practiced_leaf_count` ✅；「缺知识点未计入 1」= `without_kp` ✅ |

### 3. 无 key 手动模式（§7 最后一项）

**用 `VLM_API_KEY=` 覆盖环境变量模拟无 key**（`load_dotenv` 不覆盖已存在的变量，故无需改动 `.env`；
已验证 `has_api_key()=False / manual_mode()=True`）。全程走**真实 UI**：

- 上传 → 表单手选 2022/海淀区/一模 → 切片 22 题 → **localStorage 确实记住选择**（刷新后仍是这套值）✅
- 复核页顶部出现手动模式提示横幅，文案明确指向「逐题选 题型 + 主知识点」✅
- 点 3 张缩略图标错 → 计数「已标错题 3 题」✅
- 点确认 → 弹确认框（文案含「错题 3 题 / 正确题 19 题 / 1 题无切片 / 将删源 PDF」）→ 确认后
  `#rv-progress` 显示「✅ 识别完成 22/22 题（22 题需手动补录） · 手动模式」，**零 API 调用** ✅
- 手动补录：题型下拉 10 项（含占位）、主知识点下拉 **97 项**；选「立体几何 + 线面垂直的判定与性质」
  → 该题 `status=tagged`、文件改名 `2_立体几何.png` ✅
- 错题补录：给错题选题型「数列」→ **移入** `archive/数学/错题本/数列/2022海淀一模_1.png`，crops 里同步消失 ✅
- **确认后未打标的题仍可编辑**（按题状态判锁：20 张可编辑 / 2 张已 tagged 锁卡），手动模式前提成立 ✅

### 4. 本轮发现并修复的问题

| # | 问题 | 现象 / 影响 | 修法 |
|---|------|------------|------|
| 1 | **`run.sh` 重启时误判端口被占用** | 刚 Ctrl+C 关掉服务、立刻再启动时，8000 处于 TIME_WAIT，探针 `bind` 失败 → 顺延到 8001。**用户看到地址变了会以为启动失败**（本轮实测命中） | 端口探针加 `SO_REUSEADDR`。注意它不会让「真有服务在监听」被误判为空闲 |
| 2 | **确认后 crops 残留永久副本** | §6-T2 写「切片**移入** archive/」，但正确题是 `copyfile` 进题库、crops 原件没删 → 每题在 `storage/crops/{exam_id}/` 留一份永久副本（与 §5.5 规则1「无重复副本」相悖），逐卷累积 | 复制成功后 `unlink` 源文件；错题仍留 crops（要等题型才知道往哪搬）。修后实测：confirm 一套卷，`crops/4` 只剩 2 张错题、20 张正确题已清走 |
| 3 | **`tests/test_weekly.py` 真实库断言写死了「只剩 exam#2」** | 该断言假设库里只有 exam#1/#2 两套卷；本轮新传 exam#3/#4 后**假失败**（期望 2、实得 24）。与 PROGRESS T7 自称的「真实库期望值全部现算」不符 | 改为按窗口用 SQL 现算「非 exam#1 的已打标题数」。不再随用户传卷而假失败 |
| 4 | **README.md 严重过期** | 仍写着「当前进度：T1 地基」「界面与切片由后续版本提供」，且 API key 只提 `DASHSCOPE_API_KEY`/百炼，与实际 `.env`（`VLM_*` + 智谱）不符 —— 用户照它做会配不上 | 重写为完整使用说明（启动 / 上传流程 / key 配置 / 归档目录 / 四个页面 / 常见问题 / 开发者入口） |
| 5 | **`.env.example` 与实现不一致** | 只有 `DASHSCOPE_API_KEY` 一行，未提 `VLM_BASE_URL/VLM_MODEL/VLM_API_KEY`、也未提兼容的另 3 个 key 变量名与超时/模型选项 | 重写：两家 provider 的可复制模板 + 完整变量表 + 费用与实测备注 |

### 5. 未达标项 / 如实记录的偏差

① **DESIGN §7「AI 识别含 1 条新知识点建议」这一轮的模型行为对不上。**
真实 `glm-4v-plus` 跑完整卷 **22 题产出 0 条建议**（与 PROGRESS D 节加的「重名闸门」后的观察一致：
未加闸门时同一套卷曾报 16 条、绝大多数是树里已有的重名）。
**故建议的「生成」环节无法用真实模型复现；「确认」环节改用确定性 mock 通道
（`TAGGER_MOCK=1`，PROGRESS T4 已备案的验收通道）跑通了全链路**：
mock 在题 19 产出建议 → 前端横幅显示「💡 AI 建议新增知识点 1 条（确认后写入知识点树并回填该题主标签）」
+ [确认][忽略] → 点「确认」→ 新叶子 96→97（`origin='ai'`, `status='confirmed'`, parent=函数与导数）、
题 19 主标签回填为新叶子（旧主标签降为次）、**文件名不变**（§6-T4：文件名只认题型）、
重复 approve → **409**（幂等）。
→ **这不是管线 bug**，是模型在当前提示词下的输出偏好；建议「生成」侧要真正可用，得回到
PROGRESS F 节列的下一步（叶子级判别性线索）。**本项记为「部分达标」而非通过。**

② **知识点识别准确率仍不足以无人值守**（沿用 PROGRESS F/G 的结论，本轮未做新的评测）。
本轮 exam#1 的 22 题里，题型经确定性校正后基本可用，但主知识点仍有明显错判
（题 14 判成「互斥事件与对立事件」、题 20 判成「用导数研究单调性」等，
与 F 节金标准里记的偏差同型）。**对产品无阻塞**：复核页每张卡片都能手动改，
README 已明确写出「AI 建议 + 人工扫一遍改几处」是正确用法。

### 6. 验收后的数据现状（供用户决定去留）

库里现有 4 套卷（**全部是验收产生的**，开发残留已按用户同意清空）：

| exam | 标签 | 来源 | 性质 |
|------|------|------|------|
| #1 | 2022 朝阳区 期末 | `tests/2022北京朝阳高二（下）期末数学.pdf` | **真实卷 + 真实 AI 打标**（21 tagged，3 错），有保留价值 |
| #2 | 2022 海淀区 一模 | 同上真实卷 | 无 key 手动模式验收产物，标签是**测试标签**（卷子本身是朝阳期末） |
| #3 | 2025 朝阳区 二模 | `storage/test_papers/双栏跨页测试卷.pdf` | 合成双栏测试卷 + mock 打标 |
| #4 | 2026 西城区 一模 | 同合成卷 | crops 清理修复的验证产物 |

`archive/数学/计划库/` 里有本轮生成的周报 md。**#2/#3/#4 与计划库 md 都是验收垃圾，
用户可随时删**（删后重开「周报」页会自动按剩余数据重生成）。

### 7. 回归

| 套件 | 结果 |
|------|------|
| `tests/test_pdf_slicer.py` | 全部通过 ✅（测试卷 22/22 全锚定、6 道跨栏/跨页题段序列与真值一致；真实卷 21/21 无中间缺口） |
| `tests/test_bubbles.py` | **19 PASS / 0 FAIL** ✅ |
| `tests/test_weekly.py` | **全部 PASS** ✅（含本轮修掉的那条假失败） |
