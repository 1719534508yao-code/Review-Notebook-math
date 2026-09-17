# -*- coding: utf-8 -*-
"""REST 路由：T1 冒烟 + T3 上传/复核全链路。

- T1：/api/health、/api/kp/tree、/api/meta/enums
- T3（§6-T3）：POST /api/exams（multipart，切片后保留源 PDF 待 confirm）、
  GET /api/exams、GET /api/exams/{id}/questions、PATCH /api/questions/{id}、
  POST /api/exams/{id}/confirm（删源 PDF + 正确题切片复制入 archive/题库 + 打标留桩）

约定：源 PDF 固定存放 storage/uploads/{exam_id}.pdf（约定路径，exams 表不加列，
§5「不存 pdf_path」保持成立）。图片 URL = "/" + questions.image_path
（/storage/crops、/archive 由 app.py 静态挂载）。
"""
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from backend.db import (
    DISTRICTS_ALL, EXAM_TYPES, PROJECT_ROOT, QTYPES_MATH, SUBJECT_MATH, YEARS,
    get_conn, q, scalar,
)
from backend.pdf_slicer import EXPECTED_MAX_DEFAULT, slice_pdf
from backend import bubbles, stats, tagger

router = APIRouter(prefix="/api", tags=["api"])

log = logging.getLogger("exam.api")

UPLOADS_DIR = PROJECT_ROOT / "storage" / "uploads"

# 目录段（区/类型）白名单：汉字、字母数字、_ - （） 等；杜绝 / .. 防路径穿越
_SAFE_SEG = re.compile(r"^[\w一-鿿（）()·\-]{1,20}$")


def _safe_seg(v: str, label: str) -> str:
    v = (v or "").strip()
    if not _SAFE_SEG.match(v):
        raise HTTPException(status_code=400,
                            detail="%s 含非法字符（仅允许中英文/数字/括号/短横，≤20字）: %r" % (label, v))
    return v


@router.get("/health")
def health():
    """存活检查：顺带验证 db 可用。"""
    conn = get_conn()
    try:
        n = scalar(conn, "SELECT COUNT(*) FROM knowledge_points")
    finally:
        conn.close()
    return {"ok": True, "kp_count": n}


def _build_node(conn, kp_id: int, subject_id: int) -> dict:
    """递归构建知识点子树节点。"""
    row = conn.execute(
        "SELECT id, name, origin, status FROM knowledge_points WHERE id=?", (kp_id,)
    ).fetchone()
    children = q(
        conn,
        "SELECT id FROM knowledge_points WHERE subject_id=? AND parent_id=? ORDER BY id",
        (subject_id, kp_id),
    )
    node = {
        "id": row["id"],
        "name": row["name"],
        "origin": row["origin"],
        "status": row["status"],
        "children": [_build_node(conn, c["id"], subject_id) for c in children],
    }
    return node


@router.get("/kp/tree")
def kp_tree(subject: str = Query(default=SUBJECT_MATH)):
    """完整知识点树。大类为根，children 递归。"""
    conn = get_conn()
    try:
        subj_id = scalar(conn, "SELECT id FROM subjects WHERE name=?", (subject,))
        if subj_id is None:
            raise HTTPException(status_code=404, detail="科目不存在: %s" % subject)
        roots = q(
            conn,
            "SELECT id FROM knowledge_points WHERE subject_id=? AND parent_id IS NULL ORDER BY id",
            (int(subj_id),),
        )
        return {
            "subject": subject,
            "tree": [_build_node(conn, r["id"], int(subj_id)) for r in roots],
        }
    finally:
        conn.close()


@router.get("/meta/enums")
def meta_enums():
    """前端表单/上传下拉所需的枚举与常量（§2-12、§5.5 规则3）。"""
    conn = get_conn()
    try:
        subj_id = int(scalar(conn, "SELECT id FROM subjects WHERE name=?", (SUBJECT_MATH,)))
        qtypes = [
            {"id": r["id"], "name": r["name"]}
            for r in q(
                conn,
                "SELECT id, name FROM qtype_options WHERE subject_id=? ORDER BY ord, id",
                (subj_id,),
            )
        ]
        subjects = [r["name"] for r in q(conn, "SELECT name FROM subjects ORDER BY id")]
    finally:
        conn.close()
    return {
        "subjects": subjects,
        "qtypes": qtypes,           # 数学 9 类，含 id，顺序即展示顺序
        "districts": DISTRICTS_ALL,  # 北京 18 区（下拉可编辑/手填）
        "exam_types": EXAM_TYPES,
        "years": YEARS,
    }


# ══════════════════════════ T3 上传 / 复核 ══════════════════════════

def _question_out(conn, row) -> Dict[str, Any]:
    """questions 行 → API 输出（补 image_url 与题型名）。
    row 若无 qtype_name 列（未 join），按 qtype_id 现查一次。"""
    d = dict(row)
    # crops 中间态与 archive 最终态都经 /storage、/archive 挂载暴露（§6-T3 双挂载）
    img = d.get("image_path")
    d["image_url"] = ("/" + img.replace("\\", "/")) if img else None
    if "qtype_name" not in d:
        d["qtype_name"] = (scalar(conn, "SELECT name FROM qtype_options WHERE id=?",
                                  (d["qtype_id"],)) if d.get("qtype_id") else None)
    d["qtype"] = d.pop("qtype_name")
    return d


def _load_exam_questions(conn, exam_id: int) -> List[Dict[str, Any]]:
    rows = q(
        conn,
        """SELECT qs.*, qt.name AS qtype_name
           FROM questions qs LEFT JOIN qtype_options qt ON qt.id = qs.qtype_id
           WHERE qs.exam_id=? ORDER BY qs.number""",
        (exam_id,),
    )
    return [_question_out(conn, r) for r in rows]


def _source_pdf_path(exam_id: int) -> Path:
    return UPLOADS_DIR / ("%d.pdf" % exam_id)


@router.post("/exams")
async def create_exam(
    file: UploadFile = File(...),
    year: int = Form(...),
    district: str = Form(...),
    exam_type: str = Form(...),
    expected_max: int = Form(default=EXPECTED_MAX_DEFAULT),   # 数学卷一律 21 题
):
    """上传试卷 → 同步切片（§6-T3）。源 PDF 暂存 storage/uploads/{id}.pdf，
    confirm 成功后才删（防切片失败需重来）。"""
    if not (2000 <= year <= 2100):
        # §2-12 的下拉候选为 2023-2027（YEARS），但真实旧卷（如 2022）也得能传：
        # 年份字段与区同口径「候选+手填」，仅做宽泛合法性校验（见 PROGRESS 偏离）
        raise HTTPException(status_code=400, detail="年份须为 2000~2100 的整数")
    district = district.strip()
    if not district:
        raise HTTPException(status_code=400, detail="城区不能为空")
    _safe_seg(district, "城区")
    if exam_type not in EXAM_TYPES:
        raise HTTPException(status_code=400, detail="考试类型须为 %s 之一" % EXAM_TYPES)
    fname = file.filename or ""
    if not fname.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅接受 .pdf 文件")

    conn = get_conn()
    try:
        subj_id = int(scalar(conn, "SELECT id FROM subjects WHERE name=?", (SUBJECT_MATH,)))
        cur = conn.execute(
            "INSERT INTO exams(subject_id, district, exam_type, year, title) VALUES (?,?,?,?,?)",
            (subj_id, district, exam_type, year, fname),
        )
        exam_id = int(cur.lastrowid)
        conn.commit()

        try:
            # 先落盘源 PDF（约定路径），再切片
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            dst = _source_pdf_path(exam_id)
            with dst.open("wb") as f:
                shutil.copyfileobj(file.file, f)

            res = slice_pdf(dst, exam_id, expected_max=expected_max)
        except Exception:
            # 切片失败：回滚考试记录与临时 PDF，用户可修正后重传
            conn.execute("DELETE FROM exams WHERE id=?", (exam_id,))
            conn.commit()
            _source_pdf_path(exam_id).unlink(missing_ok=True)
            raise

        for qq in res["questions"]:
            conn.execute(
                """INSERT INTO questions(exam_id, number, page_start, page_end,
                                         image_path, raw_text, status)
                   VALUES (?,?,?,?,?,?, 'pending_slice_confirm')""",
                (exam_id, qq["number"], qq["page_start"], qq["page_end"],
                 qq["image_path"], qq["raw_text"]),
            )
        conn.commit()
        return {
            "exam_id": exam_id,
            "title": fname,
            "year": year, "district": district, "exam_type": exam_type,
            "ncol": res["ncol"],
            "questions": _load_exam_questions(conn, exam_id),
            "gaps": res["gaps"],
            "warnings": res["warnings"],
        }
    finally:
        conn.close()
        file.file.close()


@router.get("/exams")
def list_exams():
    """已上传试卷列表（含待复核的）。"""
    conn = get_conn()
    try:
        rows = q(conn, "SELECT * FROM exams ORDER BY id DESC")
        out = []
        for r in rows:
            d = dict(r)
            d["n_questions"] = int(scalar(
                conn, "SELECT COUNT(*) FROM questions WHERE exam_id=?", (r["id"],)))
            d["n_pending"] = int(scalar(
                conn, "SELECT COUNT(*) FROM questions WHERE exam_id=? "
                      "AND status='pending_slice_confirm'", (r["id"],)))
            out.append(d)
        return {"exams": out}
    finally:
        conn.close()


@router.get("/exams/summary")
def exams_summary(subject: str = Query(default=SUBJECT_MATH)):
    """首页汇总：已上传的卷子按 科目 → 年份 → 考试类型 → 城区 分组（§2-12 的手选维度）。

    只输出**真实存在**的分支（没传过的年份/区不会出现空行），每个节点带
    n_exams / n_questions / n_wrong，叶子上挂具体卷子。排序：
    年份↓，考试类型按 EXAM_TYPES 固定顺序（一模/二模/期末/其他），城区按名称。
    """
    conn = get_conn()
    try:
        subj_id = scalar(conn, "SELECT id FROM subjects WHERE name=?", (subject,))
        if subj_id is None:
            raise HTTPException(status_code=404, detail="科目不存在: %s" % subject)
        subj_id = int(subj_id)
        # 一次查询取全部卷子 + 题数/错题数/已打标数（避免逐卷 N+1）
        rows = q(
            conn,
            """SELECT e.id, e.year, e.exam_type, e.district, e.title, e.uploaded_at,
                      COUNT(qs.id) AS n_questions,
                      COALESCE(SUM(CASE WHEN qs.is_wrong = 1 THEN 1 ELSE 0 END), 0) AS n_wrong,
                      COALESCE(SUM(CASE WHEN qs.status = 'tagged' THEN 1 ELSE 0 END), 0) AS n_tagged
               FROM exams e LEFT JOIN questions qs ON qs.exam_id = e.id
               WHERE e.subject_id = ?
               GROUP BY e.id
               ORDER BY e.year DESC, e.id DESC""",
            (subj_id,),
        )
    finally:
        conn.close()

    # 考试类型的展示顺序：以 EXAM_TYPES 为准，非枚举值（用户手填过的旧值）排最后
    type_rank = {t: i for i, t in enumerate(EXAM_TYPES)}

    def _rollup(node, child_key):
        """把子节点汇总成父节点计数。构造期 child_key 可能还是 {key: node} 的字典
        （按 年份/类型/城区 去重用），收尾时才转成有序列表，故两种都要吃。"""
        kids = node[child_key]
        if isinstance(kids, dict):
            kids = list(kids.values())
        node["n_exams"] = sum(k["n_exams"] for k in kids)
        node["n_questions"] = sum(k["n_questions"] for k in kids)
        node["n_wrong"] = sum(k["n_wrong"] for k in kids)
        node["n_tagged"] = sum(k["n_tagged"] for k in kids)
        return node

    years = {}
    for r in rows:
        y = years.setdefault(int(r["year"]), {"year": int(r["year"]), "exam_types": {},
                                              "n_exams": 0, "n_questions": 0,
                                              "n_wrong": 0, "n_tagged": 0})
        et = y["exam_types"].setdefault(r["exam_type"],
                                        {"exam_type": r["exam_type"], "districts": {},
                                         "n_exams": 0, "n_questions": 0, "n_wrong": 0, "n_tagged": 0})
        d = et["districts"].setdefault(r["district"],
                                       {"district": r["district"], "exams": [],
                                        "n_exams": 0, "n_questions": 0, "n_wrong": 0, "n_tagged": 0})
        exam = {
            "id": int(r["id"]), "title": r["title"], "uploaded_at": r["uploaded_at"],
            "year": int(r["year"]), "exam_type": r["exam_type"], "district": r["district"],
            "n_exams": 1,
            "n_questions": int(r["n_questions"]), "n_wrong": int(r["n_wrong"]),
            "n_tagged": int(r["n_tagged"]),
        }
        d["exams"].append(exam)
        _rollup(d, "exams")
        _rollup(et, "districts")
        _rollup(y, "exam_types")

    year_list = []
    for y in sorted(years.values(), key=lambda x: -x["year"]):
        et_list = []
        for et in sorted(y["exam_types"].values(),
                         key=lambda x: (type_rank.get(x["exam_type"], len(EXAM_TYPES)),
                                        x["exam_type"])):
            et["districts"] = sorted(et["districts"].values(), key=lambda x: x["district"])
            et_list.append(et)
        y["exam_types"] = et_list
        year_list.append(y)

    total = {"n_exams": sum(y["n_exams"] for y in year_list),
             "n_questions": sum(y["n_questions"] for y in year_list),
             "n_wrong": sum(y["n_wrong"] for y in year_list),
             "n_tagged": sum(y["n_tagged"] for y in year_list)}
    return {"subject": subject, "years": year_list, "totals": total,
            "max_questions": max([y["n_questions"] for y in year_list] or [0])}


@router.get("/exams/{exam_id}/questions")
def exam_questions(exam_id: int):
    """一套卷的全部小题（复核页数据源）。"""
    conn = get_conn()
    try:
        exam = q(conn, "SELECT * FROM exams WHERE id=?", (exam_id,))
        if not exam:
            raise HTTPException(status_code=404, detail="试卷不存在: %d" % exam_id)
        return {"exam": dict(exam[0]), "questions": _load_exam_questions(conn, exam_id)}
    finally:
        conn.close()


class QuestionPatch(BaseModel):
    is_wrong: Optional[int] = None       # 0|1|None
    qtype_id: Optional[int] = None
    raw_text: Optional[str] = None
    # T4 手动模式：无 key 时手填知识点（§6-T4 硬性要求：PATCH 可手填 kp/qtype）
    primary_kp_id: Optional[int] = None      # 主知识点 → question_kps is_primary=1
    secondary_kp_ids: Optional[List[int]] = None


@router.patch("/questions/{question_id}")
def patch_question(question_id: int, body: QuestionPatch):
    """复核页保存单题标注（§6-T3：is_wrong / 题型 / 题号文本）。"""
    if body.is_wrong not in (None, 0, 1):
        raise HTTPException(status_code=400, detail="is_wrong 须为 0/1/null")
    conn = get_conn()
    try:
        row = q(conn, "SELECT * FROM questions WHERE id=?", (question_id,))
        if not row:
            raise HTTPException(status_code=404, detail="题目不存在: %d" % question_id)
        if body.qtype_id is not None:
            ok = scalar(conn, "SELECT 1 FROM qtype_options WHERE id=?", (body.qtype_id,))
            if not ok:
                raise HTTPException(status_code=400, detail="题型不存在: %d" % body.qtype_id)
        has_kp = body.primary_kp_id is not None or bool(body.secondary_kp_ids)
        sets, args = [], []
        for col in ("is_wrong", "qtype_id", "raw_text"):
            v = getattr(body, col)
            if v is not None:
                sets.append("%s=?" % col)
                args.append(v)
        if sets:
            args.append(question_id)
            conn.execute("UPDATE questions SET %s WHERE id=?" % ",".join(sets), args)
        if body.qtype_id is not None or has_kp:
            # T4 手动模式：手填题型 + 知识点 → 走共享归档落地（无 key 时主路径，§6-T4）
            tagger.manual_tag(conn, question_id, body.qtype_id,
                              body.primary_kp_id, body.secondary_kp_ids)
        conn.commit()
        new = q(conn, "SELECT * FROM questions WHERE id=?", (question_id,))[0]
        return _question_out(conn, new)
    finally:
        conn.close()


def _remove_slice_file(img_rel: Optional[str]) -> bool:
    """删掉一张切片文件，返回是否真的删了。

    只删**项目目录内**的文件：image_path 正常都是相对路径，但 T2 的 CLI 允许传
    项目外 out_dir（见 pdf_slicer 说明），那种情况一律不碰（防误删用户别处的文件）。
    """
    if not img_rel:
        return False
    fp = Path(img_rel)
    if not fp.is_absolute():
        fp = PROJECT_ROOT / img_rel
    try:
        fp.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        log.warning("跳过项目外的切片路径（不删）: %s", fp)
        return False
    if not fp.is_file():
        return False
    try:
        fp.unlink()
        return True
    except OSError as e:
        log.warning("删除切片失败 %s: %s", fp, e)
        return False


def _purge_questions(conn, question_ids: List[int]) -> int:
    """批量清掉题目及其全部从属数据（标签 / 新知识点建议 / 切片文件）。返回删掉的题数。"""
    if not question_ids:
        return 0
    n_img = 0
    for qid in question_ids:
        r = q(conn, "SELECT image_path FROM questions WHERE id=?", (qid,))
        if r and _remove_slice_file(r[0]["image_path"]):
            n_img += 1
    ph = ",".join("?" * len(question_ids))
    conn.execute("DELETE FROM question_kps WHERE question_id IN (%s)" % ph, question_ids)
    conn.execute("DELETE FROM new_kp_suggestions WHERE question_id IN (%s)" % ph, question_ids)
    conn.execute("DELETE FROM questions WHERE id IN (%s)" % ph, question_ids)
    return n_img


@router.delete("/questions/{question_id}")
def delete_question(question_id: int):
    """删除一道题（错题本/题库详情页的「删除此题」）。

    一并清掉：它的切片图片文件、主/次知识点关联、新知识点建议。
    **不可恢复**，前端有二次确认。删除后错题本/题库/气泡图/周报的数字都会随之更新
    （统计全部读库，无缓存）。
    """
    conn = get_conn()
    try:
        row = q(conn, "SELECT * FROM questions WHERE id=?", (question_id,))
        if not row:
            raise HTTPException(status_code=404, detail="题目不存在: %d" % question_id)
        r = row[0]

        img_rel = r["image_path"]
        image_removed = False
        if img_rel:
            image_removed = _remove_slice_file(img_rel)

        conn.execute("DELETE FROM question_kps WHERE question_id=?", (question_id,))
        conn.execute("DELETE FROM new_kp_suggestions WHERE question_id=?", (question_id,))
        conn.execute("DELETE FROM questions WHERE id=?", (question_id,))
        conn.commit()
        return {
            "ok": True, "question_id": question_id, "exam_id": int(r["exam_id"]),
            "number": int(r["number"]), "image_path": img_rel,
            "image_removed": image_removed,
        }
    finally:
        conn.close()


@router.delete("/exams/{exam_id}")
def delete_exam(exam_id: int):
    """删除**整套卷**（首页汇总里每行卷子右侧的 🗑）。

    一并清掉：该卷全部小题（含切片图片文件、标签、新知识点建议）、
    中间态目录 storage/crops/{exam_id}/、以及可能残留的源 PDF。
    **不可恢复**，前端有二次确认（弹窗写明是哪套卷、多少题、多少错题）。

    注意：`new_kp_suggestions` 里已 approved 的知识点叶子**不会**被删 ——
    它已进知识点树，可能被别的题的标签引用（§2-6「预置 + AI 自动扩充」），
    删卷不该改动知识点树。
    """
    conn = get_conn()
    try:
        row = q(conn, "SELECT * FROM exams WHERE id=?", (exam_id,))
        if not row:
            raise HTTPException(status_code=404, detail="试卷不存在: %d" % exam_id)
        exam = row[0]
        qids = [int(r["id"]) for r in
                q(conn, "SELECT id FROM questions WHERE exam_id=?", (exam_id,))]
        n_img = _purge_questions(conn, qids)
        conn.execute("DELETE FROM exams WHERE id=?", (exam_id,))
        conn.commit()
    finally:
        conn.close()

    # 中间态目录（整卷删除后一定没用了）
    crops_dir = PROJECT_ROOT / "storage" / "crops" / str(exam_id)
    crops_removed = False
    if crops_dir.is_dir():
        try:
            shutil.rmtree(crops_dir)
            crops_removed = True
        except OSError as e:
            log.warning("删除 crops 目录失败 %s: %s", crops_dir, e)

    # 残留源 PDF（正常 confirm 后就删了；未确认就删卷时会留在这里）
    pdf_removed = False
    src = _source_pdf_path(exam_id)
    if src.is_file():
        try:
            src.unlink()
            pdf_removed = True
        except OSError as e:
            log.warning("删除源 PDF 失败 %s: %s", src, e)

    # 顺手清掉归档里因此变空的目录（题库/{年}/{区}/{型} 全空则逐级删）
    for base in (PROJECT_ROOT / "archive" / SUBJECT_MATH / "题库",
                 PROJECT_ROOT / "archive" / SUBJECT_MATH / "错题本"):
        if not base.is_dir():
            continue
        for p in sorted((x for x in base.rglob("*") if x.is_dir()),
                        key=lambda x: -len(x.parts)):
            try:
                if not any(p.iterdir()):
                    p.rmdir()
            except OSError:
                pass

    return {
        "ok": True, "exam_id": exam_id, "title": exam["title"],
        "year": exam["year"], "district": exam["district"],
        "exam_type": exam["exam_type"],
        "n_questions_deleted": len(qids), "n_images_removed": n_img,
        "crops_removed": crops_removed, "source_pdf_removed": pdf_removed,
    }


def _queue_tagging(exam_id: int) -> None:
    """确认后触发 T4 后台打标（§6-T4）。无 key 时自动降级手动模式（不崩，见 tagger）。"""
    tagger.start_tagging(exam_id)


@router.post("/exams/{exam_id}/confirm")
def confirm_exam(exam_id: int):
    """复核确认（§6-T3）：
    1) 全部题 status → untagged（进入待打标），is_wrong 定档（未点=正确 0，§2-2）；
    2) 正确题切片复制入 archive/数学/题库/{年}/{区}/{型}/{题号}.png
       （暂用无题型后缀名，T4 打标后改名补 _{题型}，§5.5 规则1）；
    3) 错题切片留在 crops（T4 打标后移入错题本/{题型}/）；
    4) 删除源 PDF（confirm 成功之后才删）；
    5) 触发打标任务（T4 桩）。
    """
    conn = get_conn()
    try:
        exam = q(conn, "SELECT * FROM exams WHERE id=?", (exam_id,))
        if not exam:
            raise HTTPException(status_code=404, detail="试卷不存在: %d" % exam_id)
        exam_row = exam[0]
        rows = q(conn, "SELECT * FROM questions WHERE exam_id=? ORDER BY number", (exam_id,))
        if not rows:
            raise HTTPException(status_code=400, detail="该试卷没有题目，无法确认")
        if any(r["status"] != "pending_slice_confirm" for r in rows):
            raise HTTPException(status_code=409, detail="该试卷已确认过，勿重复提交")

        pool_dir = (PROJECT_ROOT / "archive" / SUBJECT_MATH / "题库" /
                    str(exam_row["year"]) / exam_row["district"] / exam_row["exam_type"])
        pool_dir.mkdir(parents=True, exist_ok=True)

        n_pool = n_wrong = n_noimg = 0
        for r in rows:
            # §2-2：is_wrong 由用户点选决定；未点 = 正确，确认时定档为 0
            is_wrong = 1 if r["is_wrong"] == 1 else 0
            img_rel = r["image_path"]
            if img_rel and Path(PROJECT_ROOT / img_rel).exists():
                if is_wrong == 0:
                    # 正确题：移入题库暂存位（§5.5 规则1；T4 打标后补 _{题型} 后缀）
                    dest = pool_dir / ("%d.png" % r["number"])
                    src = PROJECT_ROOT / img_rel
                    shutil.copyfile(src, dest)
                    try:
                        new_rel = str(dest.relative_to(PROJECT_ROOT))
                    except ValueError:
                        new_rel = str(dest)
                    conn.execute("UPDATE questions SET image_path=? WHERE id=?",
                                 (new_rel, r["id"]))
                    # crops 是「中间态暂存区」（§6-T2：确认后**移入** archive），移完即删源，
                    # 否则每题都会在 storage/crops/ 留一份永久副本（与 §5.5「无重复副本」相悖，
                    # 且逐卷累积）。错题仍留在 crops —— 它们要等打标知道题型后才搬进错题本。
                    try:
                        src.unlink()
                    except OSError as e:
                        log.warning("清理 crops 中间态失败 %s: %s", src, e)
                    n_pool += 1
                else:
                    n_wrong += 1
            else:
                n_noimg += 1   # unlocated 题：无图，留在 DB 待复核页手动设范围后重切（不阻塞确认）
            conn.execute(
                "UPDATE questions SET is_wrong=?, status='untagged' WHERE id=?",
                (is_wrong, r["id"]))
        conn.commit()

        # 删源 PDF（confirm 成功之后才删，§6-T3）
        src_pdf = _source_pdf_path(exam_id)
        pdf_removed = src_pdf.exists()
        if pdf_removed:
            src_pdf.unlink()

        _queue_tagging(exam_id)

        return {
            "ok": True, "exam_id": exam_id,
            "pool_copied": n_pool,        # 已复制入 题库/{年}/{区}/{型}/ 的正确题数
            "wrong_kept": n_wrong,        # 待 T4 移入错题本的错题数
            "no_image": n_noimg,          # 未定位无图题数
            "source_pdf_removed": pdf_removed,
            "pool_dir": "archive/%s/题库/%s/%s/%s" % (
                SUBJECT_MATH, exam_row["year"], exam_row["district"], exam_row["exam_type"]),
            # T4：是否降级手动模式（无 DASHSCOPE_API_KEY 时，§6-T4 硬性要求）
            "manual_mode": tagger.manual_mode(),
            "api_key_present": tagger.has_api_key(),
        }
    finally:
        conn.close()


# ══════════════════════════ T4 打标状态 / 新知识点建议 ══════════════════════════

@router.get("/exams/{exam_id}/tagging")
def exam_tagging_status(exam_id: int):
    """打标进度（前端轮询；manual_mode=True 时提示手动补录）。"""
    return tagger.tagging_status(exam_id)


@router.post("/exams/{exam_id}/retag")
def exam_retag(exam_id: int):
    """重跑打标（用于：key 修好后重试 / 之前系统性失败中止的卷）。
    只处理 status='untagged' 的题，已 tagged 的题不受影响（幂等、不会重复烧钱）。"""
    conn = get_conn()
    try:
        if not q(conn, "SELECT id FROM exams WHERE id=?", (exam_id,)):
            raise HTTPException(status_code=404, detail="试卷不存在: %d" % exam_id)
        n = int(scalar(conn, "SELECT COUNT(*) FROM questions WHERE exam_id=? "
                             "AND status='untagged'", (exam_id,)))
    finally:
        conn.close()
    if not n:
        raise HTTPException(status_code=409, detail="没有待打标的题（全部已完成或尚未确认）")
    # 清掉上次的中止错误，重新开始
    tagger.TAGGING_STATE.pop(exam_id, None)
    tagger.start_tagging(exam_id)
    return {"ok": True, "exam_id": exam_id, "queued": n,
            "manual_mode": tagger.manual_mode()}


@router.get("/exams/{exam_id}/kp_suggestions")
def exam_kp_suggestions(exam_id: int):
    """该卷的新知识点建议（pending 优先）。"""
    conn = get_conn()
    try:
        rows = q(
            conn,
            """SELECT s.id, s.question_id, qs.number, s.parent_hint, s.proposed_name,
                      s.reason, s.status
               FROM new_kp_suggestions s JOIN questions qs ON qs.id = s.question_id
               WHERE qs.exam_id=? ORDER BY s.status='pending' DESC, s.id""",
            (exam_id,),
        )
        out = [dict(r) for r in rows]
        return {"suggestions": out, "manual_mode": tagger.manual_mode()}
    finally:
        conn.close()


@router.post("/kp_suggestions/{suggestion_id}/approve")
def kp_suggestion_approve(suggestion_id: int):
    """确认新知识点：建叶子 + 回填该题主标签（§6-T4）。"""
    res = tagger.approve_suggestion(suggestion_id)
    if not res["ok"]:
        raise HTTPException(status_code=409, detail=res["error"])
    return res


@router.post("/kp_suggestions/{suggestion_id}/reject")
def kp_suggestion_reject(suggestion_id: int):
    """忽略新知识点建议（§6-T4）。"""
    res = tagger.reject_suggestion(suggestion_id)
    if not res["ok"]:
        raise HTTPException(status_code=404, detail=res["error"])
    return res


# ══════════════════════════ T5 错题本 / 题库浏览 ══════════════════════════
#
# 两个 view 共用同一实现（§6-T5）：错题本 = v_wrong_bank，题库 = v_pool。
# 两视图口径见 models.py：is_wrong=1/0 **且 status='tagged'** —— 因此
# 「切完片还没打标的题」两个库都不出现（T4 手动模式补录完 qtype 后即进入题库）。
#
# 知识点筛选口径（§2-8）：默认只匹配**主标签**（kp_mode=primary），与气泡图/周报
# 的统计口径一致；kp_mode=any 可把次标签也算上（用于「这题还考了什么」的翻查）。

_BANK_VIEWS = {"wrong_bank": "v_wrong_bank", "pool": "v_pool"}
_KP_MODES = ("primary", "any")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PAGE_SIZE_DEFAULT = 24
_PAGE_SIZE_MAX = 200


def _check_date(v: Optional[str], label: str) -> Optional[str]:
    if v in (None, ""):
        return None
    v = v.strip()
    if not _DATE_RE.match(v):
        raise HTTPException(status_code=400, detail="%s 须为 YYYY-MM-DD 格式: %r" % (label, v))
    return v


def _kp_filter_ids(conn, kp_id: int) -> List[int]:
    """知识点筛选的命中集合：点大类（parent_id IS NULL）→ 大类自身 + 其全部叶子；
    点叶子 → 仅它自己（叶子无子节点）。这样 UI 上一个下拉既能选大类也能选叶子。"""
    row = q(conn, "SELECT id FROM knowledge_points WHERE id=?", (kp_id,))
    if not row:
        raise HTTPException(status_code=404, detail="知识点不存在: %d" % kp_id)
    kids = q(conn, "SELECT id FROM knowledge_points WHERE parent_id=?", (kp_id,))
    return [int(kp_id)] + [int(k["id"]) for k in kids]


def _kps_by_question(conn, qids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """批量取主/次标签（一次查询，避免逐题 N+1）。返回 {question_id: [kp...]}，
    主标签（is_primary=1）排在最前。"""
    if not qids:
        return {}
    ph = ",".join("?" * len(qids))
    rows = q(
        conn,
        """SELECT qk.question_id, qk.is_primary, kp.id, kp.name, kp.parent_id,
                  par.name AS parent_name
           FROM question_kps qk
           JOIN knowledge_points kp ON kp.id = qk.kp_id
           LEFT JOIN knowledge_points par ON par.id = kp.parent_id
           WHERE qk.question_id IN (%s)
           ORDER BY qk.is_primary DESC, kp.id""" % ph,
        qids,
    )
    out: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(int(r["question_id"]), []).append({
            "id": int(r["id"]),
            "name": r["name"],
            # path = 「大类/叶子」，大类自身无父节点时即其 name（与前端 kpFlat 同格式）
            "path": (r["parent_name"] + "/" + r["name"]) if r["parent_name"] else r["name"],
            "parent_id": r["parent_id"],
            "is_primary": int(r["is_primary"]),
        })
    return out


def _bank_facets(conn, view: str, subj_id: int) -> Dict[str, Any]:
    """筛选下拉的候选值：该库**实际出现过**的区/类型/年份/题型（不受当前筛选影响），
    外加「全库条数」bank_total 供页面显示「筛选出 X / 共 Y 题」。"""
    base = ("FROM %s qs JOIN exams e ON e.id = qs.exam_id "
            "WHERE e.subject_id = ?" % view)
    districts = [r["district"] for r in q(
        conn, "SELECT DISTINCT e.district %s ORDER BY e.district" % base, (subj_id,))]
    exam_types = [r["exam_type"] for r in q(
        conn, "SELECT DISTINCT e.exam_type %s ORDER BY e.exam_type" % base, (subj_id,))]
    years = [int(r["year"]) for r in q(
        conn, "SELECT DISTINCT e.year %s ORDER BY e.year DESC" % base, (subj_id,))]
    qtypes = [{"id": int(r["id"]), "name": r["name"]} for r in q(
        conn,
        """SELECT DISTINCT qt.id, qt.name, qt.ord
           FROM %s qs
           JOIN exams e ON e.id = qs.exam_id
           JOIN qtype_options qt ON qt.id = qs.qtype_id
           WHERE e.subject_id = ?
           ORDER BY qt.ord, qt.id""" % view,
        (subj_id,))]
    return {
        "districts": districts,
        "exam_types": exam_types,
        "years": years,
        "qtypes": qtypes,
        "bank_total": int(scalar(conn, "SELECT COUNT(*) " + base, (subj_id,)) or 0),
    }


def _browse_bank(conn, bank: str, subject: str, kp_id: Optional[int], kp_mode: str,
                 qtype_id: Optional[int], district: Optional[str],
                 exam_type: Optional[str], year: Optional[int],
                 date_from: Optional[str], date_to: Optional[str],
                 page: int, page_size: int) -> Dict[str, Any]:
    """错题本/题库的公共查询：筛选 + 分页 + 主次标签 + 图片路径（§6-T5）。"""
    view = _BANK_VIEWS[bank]
    if kp_mode not in _KP_MODES:
        raise HTTPException(status_code=400, detail="kp_mode 须为 %s 之一" % (_KP_MODES,))
    subj_id = scalar(conn, "SELECT id FROM subjects WHERE name=?", (subject,))
    if subj_id is None:
        raise HTTPException(status_code=404, detail="科目不存在: %s" % subject)
    subj_id = int(subj_id)
    date_from = _check_date(date_from, "date_from")
    date_to = _check_date(date_to, "date_to")

    where = ["e.subject_id = ?"]
    args: List[Any] = [subj_id]
    if district:
        where.append("e.district = ?")
        args.append(district.strip())
    if exam_type:
        where.append("e.exam_type = ?")
        args.append(exam_type.strip())
    if year is not None:
        where.append("e.year = ?")
        args.append(int(year))
    if qtype_id is not None:
        where.append("qs.qtype_id = ?")
        args.append(int(qtype_id))
    if date_from:
        where.append("date(qs.created_at) >= date(?)")
        args.append(date_from)
    if date_to:
        where.append("date(qs.created_at) <= date(?)")
        args.append(date_to)
    kp_ids: List[int] = []
    if kp_id is not None:
        kp_ids = _kp_filter_ids(conn, int(kp_id))
        prim = " AND qk.is_primary = 1" if kp_mode == "primary" else ""
        where.append(
            "EXISTS (SELECT 1 FROM question_kps qk WHERE qk.question_id = qs.id "
            "AND qk.kp_id IN (%s)%s)" % (",".join("?" * len(kp_ids)), prim))
        args.extend(kp_ids)
    where_sql = " AND ".join(where)

    total = int(scalar(
        conn,
        "SELECT COUNT(*) FROM %s qs JOIN exams e ON e.id = qs.exam_id WHERE %s"
        % (view, where_sql),
        args,
    ) or 0)

    rows = q(
        conn,
        """SELECT qs.id, qs.exam_id, qs.number, qs.image_path, qs.raw_text,
                  qs.is_wrong, qs.qtype_id, qs.status, qs.confidence, qs.created_at,
                  qt.name AS qtype_name,
                  e.district, e.exam_type, e.year, e.title AS exam_title
           FROM %s qs
           JOIN exams e ON e.id = qs.exam_id
           LEFT JOIN qtype_options qt ON qt.id = qs.qtype_id
           WHERE %s
           ORDER BY e.year DESC, e.id DESC, qs.number ASC
           LIMIT ? OFFSET ?""" % (view, where_sql),
        args + [page_size, (page - 1) * page_size],
    )

    kps = _kps_by_question(conn, [int(r["id"]) for r in rows])
    items = []
    for r in rows:
        own = kps.get(int(r["id"]), [])
        img = r["image_path"]
        items.append({
            "id": int(r["id"]),
            "exam_id": int(r["exam_id"]),
            "number": int(r["number"]),
            "is_wrong": r["is_wrong"],
            "status": r["status"],
            "image_path": img,                                     # 相对项目根（§5.5 规则4）
            "image_url": ("/" + img.replace("\\", "/")) if img else None,
            "qtype_id": r["qtype_id"],
            "qtype": r["qtype_name"],
            "raw_text": r["raw_text"],
            "created_at": r["created_at"],
            "year": r["year"], "district": r["district"], "exam_type": r["exam_type"],
            "exam_title": r["exam_title"],
            "exam_label": "%s %s %s" % (r["year"], r["district"], r["exam_type"]),
            "primary_kp": next((k for k in own if k["is_primary"] == 1), None),
            "secondary_kps": [k for k in own if k["is_primary"] != 1],
        })

    pages = (total + page_size - 1) // page_size
    return {
        "bank": bank,
        "subject": subject,
        "total": total,               # 命中筛选的总题数（分页前）
        "count": len(items),          # 本页条数
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "items": items,
        "facets": _bank_facets(conn, view, subj_id),
        "filters": {                  # 回显（前端据此同步控件）
            "kp_id": kp_id, "kp_mode": kp_mode, "kp_scope_ids": kp_ids,
            "qtype_id": qtype_id, "district": district, "exam_type": exam_type,
            "year": year, "date_from": date_from, "date_to": date_to,
        },
    }


# 两个路由的参数签名完全一致，故用一个内部函数承接，避免复制两份长签名。
def _browse_endpoint(bank: str, subject: str, kp_id: Optional[int], kp_mode: str,
                     qtype_id: Optional[int], district: Optional[str],
                     exam_type: Optional[str], year: Optional[int],
                     date_from: Optional[str], date_to: Optional[str],
                     page: int, page_size: int) -> Dict[str, Any]:
    conn = get_conn()
    try:
        return _browse_bank(conn, bank, subject, kp_id, kp_mode, qtype_id, district,
                            exam_type, year, date_from, date_to, page, page_size)
    finally:
        conn.close()


@router.get("/wrong_bank")
def wrong_bank(
    subject: str = Query(default=SUBJECT_MATH, description="科目（第一版仅「数学」）"),
    kp_id: Optional[int] = Query(default=None, description="知识点 id：叶子=该叶子；大类=该大类全部叶子"),
    kp_mode: str = Query(default="primary", description="primary=只匹配主标签（§2-8，与统计同口径）/ any=主次都算"),
    qtype_id: Optional[int] = Query(default=None, description="题型 id（9 类之一）"),
    district: Optional[str] = Query(default=None, description="城区，如「朝阳区」"),
    exam_type: Optional[str] = Query(default=None, description="一模/二模/期末/其他"),
    year: Optional[int] = Query(default=None, description="试卷年份"),
    date_from: Optional[str] = Query(default=None, description="入库日期起 YYYY-MM-DD（按 questions.created_at）"),
    date_to: Optional[str] = Query(default=None, description="入库日期止 YYYY-MM-DD"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=_PAGE_SIZE_DEFAULT, ge=1, le=_PAGE_SIZE_MAX),
):
    """错题本：is_wrong=1 且 status='tagged' 的题（§6-T5）。返回 image_path 与主/次标签。"""
    return _browse_endpoint("wrong_bank", subject, kp_id, kp_mode, qtype_id, district,
                            exam_type, year, date_from, date_to, page, page_size)


@router.get("/pool")
def pool(
    subject: str = Query(default=SUBJECT_MATH, description="科目（第一版仅「数学」）"),
    kp_id: Optional[int] = Query(default=None, description="知识点 id：叶子=该叶子；大类=该大类全部叶子"),
    kp_mode: str = Query(default="primary", description="primary=只匹配主标签（§2-8，与统计同口径）/ any=主次都算"),
    qtype_id: Optional[int] = Query(default=None, description="题型 id（9 类之一）"),
    district: Optional[str] = Query(default=None, description="城区，如「朝阳区」"),
    exam_type: Optional[str] = Query(default=None, description="一模/二模/期末/其他"),
    year: Optional[int] = Query(default=None, description="试卷年份"),
    date_from: Optional[str] = Query(default=None, description="入库日期起 YYYY-MM-DD（按 questions.created_at）"),
    date_to: Optional[str] = Query(default=None, description="入库日期止 YYYY-MM-DD"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=_PAGE_SIZE_DEFAULT, ge=1, le=_PAGE_SIZE_MAX),
):
    """题库：is_wrong=0 且 status='tagged' 的题（§6-T5）。返回 image_path 与主/次标签。"""
    return _browse_endpoint("pool", subject, kp_id, kp_mode, qtype_id, district,
                            exam_type, year, date_from, date_to, page, page_size)


# ══════════════════════════ T7 周总结与推荐 ══════════════════════════
#
# §6-T7：GET /api/stats/weekly 取数（近 N 天按知识点聚合、错误率 Top3、各题型正确率、
# 规则化下周题型推荐，纯 SQL/Python 无 LLM）；§2-11：**生成的同时**把周报 md 写进
# archive/数学/计划库/，前端「周报」view 通过 GET /api/stats/weekly/report 读它渲染。
#
# 一致性保证：/report 是「按同一窗口先重新生成（覆盖同名文件）再读盘」，
# 所以界面渲染的内容与磁盘上的 md 逐字节相同（验收项「界面与文件一致」）。
# 想要「只看不写」时用 /api/stats/weekly?write=0。


def _stats_call(fn, *a, **kw):
    """stats 的参数/科目错误 → HTTP 400/404。"""
    try:
        return fn(*a, **kw)
    except stats.StatsError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/stats/weekly")
def stats_weekly(
    days: int = Query(default=stats.DAYS_DEFAULT, ge=1, le=365,
                      description="时间窗：近 N 天（默认 7，含今天）"),
    subject: str = Query(default=SUBJECT_MATH, description="科目（第一版仅「数学」）"),
    as_of: Optional[str] = Query(default=None,
                                 description="窗口截止日 YYYY-MM-DD（默认今天；传它可复现/验收任意窗口）"),
    write: int = Query(default=1, ge=0, le=1,
                       description="1=同时落盘 archive/数学/计划库/周报-{起}-{止}.md（§2-11）；0=只算不写"),
):
    """周总结与下周题型推荐（§6-T7）。返回统计 + 推荐 + markdown 全文 + md 落盘路径。"""
    conn = get_conn()
    try:
        return _stats_call(stats.weekly_report, conn, days=days, subject=subject,
                           as_of=as_of, write=bool(write))
    finally:
        conn.close()


@router.get("/stats/weekly/report")
def stats_weekly_report(
    days: int = Query(default=stats.DAYS_DEFAULT, ge=1, le=365),
    subject: str = Query(default=SUBJECT_MATH),
    as_of: Optional[str] = Query(default=None, description="窗口截止日 YYYY-MM-DD（默认今天）"),
    regenerate: int = Query(default=1, ge=0, le=1,
                            description="1=先按当前数据重算并覆盖同名 md 再读盘（保证与界面一致）"),
):
    """读计划库里的周报 md（前端 marked 渲染）+ 同源结构化数据（建议卡片用）。

    返回 markdown 一定来自**磁盘上的那份文件**；regenerate=1 时先重新生成以保证
    文件反映最新数据（同一时间窗覆盖同名文件，§2-11）。"""
    conn = get_conn()
    try:
        if regenerate:
            _stats_call(stats.weekly_report, conn, days=days, subject=subject,
                        as_of=as_of, write=True)
        meta = _stats_call(stats.read_report, days=days, as_of=as_of)
        if not meta["found"]:
            raise HTTPException(status_code=404, detail="周报文件不存在：%s" % meta["md_path"])
        data = _stats_call(stats.weekly_report, conn, days=days, subject=subject,
                           as_of=as_of, write=False)
        out = dict(data)
        out["report"] = meta          # md_path / md_url / updated_at / size / from / to
        out["markdown"] = meta["markdown"]   # ← 以磁盘文件为准（覆盖 data 里重算的那份）
        out["md_path"] = meta["md_path"]
        out["md_url"] = meta["md_url"]
        out["md_written"] = bool(regenerate)
        return out
    finally:
        conn.close()


@router.get("/stats/weekly/reports")
def stats_weekly_reports():
    """计划库已有的周报文件列表（按时间倒序；UI 里可回看历史周报）。"""
    return {"reports": stats.list_reports(), "plan_dir": "archive/%s/计划库" % SUBJECT_MATH}


# ══════════════════════════ T6 知识点气泡图 ══════════════════════════
#
# 参数按 §6-T6 的签名：subject & district & exam_type & from & to（`from` 是 Python
# 关键字，故形参用 from_ + alias="from"）。另加 year（与 T5 参数集一致，卷级筛选常用）。
# 统计口径见 backend/bubbles.py 模块头：只有 primary 标签计数，与 v_pool/v_wrong_bank 同源。

@router.get("/stats/bubbles")
def stats_bubbles(
    subject: str = Query(default=SUBJECT_MATH, description="科目（第一版仅「数学」）"),
    district: Optional[str] = Query(default=None, description="城区，如「朝阳区」"),
    exam_type: Optional[str] = Query(default=None, description="一模/二模/期末/其他"),
    year: Optional[int] = Query(default=None, description="试卷年份"),
    from_: Optional[str] = Query(default=None, alias="from",
                                 description="入库日期起 YYYY-MM-DD（按 questions.created_at）"),
    to: Optional[str] = Query(default=None, description="入库日期止 YYYY-MM-DD"),
    date_from: Optional[str] = Query(default=None, description="`from` 的等价别名（与 T5 参数名一致）"),
    date_to: Optional[str] = Query(default=None, description="`to` 的等价别名"),
):
    """气泡图数据：每个叶子 {done,wrong,error_rate} + 大类聚合（§6-T6，只数主标签 §2-8）。

    返回体里的 meta.tagged_total = /api/pool 条数 + /api/wrong_bank 条数（同筛选下），
    meta.without_kp 是「打标成功但知识点为空」的题数 —— 它们进不了气泡，
    浏览器里会显式提示，避免用户以为统计漏数。
    """
    d_from = _check_date(from_ or date_from, "from")
    d_to = _check_date(to or date_to, "to")
    conn = get_conn()
    try:
        return bubbles.bubble_stats(conn, subject=subject, district=district,
                                  exam_type=exam_type, year=year,
                                  date_from=d_from, date_to=d_to)
    except LookupError as e:          # 科目不存在
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:           # 参数不合法
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()
