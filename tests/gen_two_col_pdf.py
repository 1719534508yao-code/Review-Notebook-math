# -*- coding: utf-8 -*-
"""生成双栏 + 跨页测试卷（T2 开发/回归用例，DESIGN §6-T2）。

版式：A4；页眉「第 x 页 共 N 页」（应被过滤）、页脚「x / N」（应被过滤）；
正文双栏（左栏写满→右栏→下一页），先左后右、栏内从上到下（北京卷口径）。
内容：考生须知 1．～4．（应触发「1．再现重置」而不是误锚）、22 道题、
题内含「0.05；」等干扰行（应被递增校验拒绝、记入 candidates）、
跨栏题（左栏底→同页右栏顶）与跨页题（右栏底→下一页左栏顶）自然出现。

输出 storage/test_papers/双栏跨页测试卷.pdf；同时把每题的行→(page,col)
布局真值以 JSON 输出，供 test_pdf_slicer.py 比对。

用法：.venv/bin/python tests/gen_two_col_pdf.py
"""
import json
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
PDF_PATH = ROOT / "storage" / "test_papers" / "双栏跨页测试卷.pdf"
TRUTH_PATH = ROOT / "storage" / "test_papers" / "双栏跨页测试卷.truth.json"

PAGE_W, PAGE_H = 595, 842
COL_X = [40, 315]          # 左右栏起点（栏宽 240，中间空隙 35pt）
COL_W = 240
TOP = 70                   # 内容顶（页眉下方）
BOTTOM = 782               # 内容底（页脚上方）
LH = 17                    # 行高
FS = 10.5

NOTICE = [
    "考生须知：",
    "1．答题前请填写姓名、考号。",
    "2．本试卷共 3 页，满分 150 分。",
    "3．请将答案填写在答题卡相应位置。",
    "4．考试结束后交回答题卡与试卷。",
]


def question_lines(num):
    """每题 5~8 行，让全卷约 130+ 行铺满两页：
    既有同页跨栏题，也有跨页题（第 2 页放不下）。"""
    n = 5 + (num % 4)  # 5..8 行
    lines = ["%d．已知函数 f(x)=x^3-3ax+1，其中 a∈R，讨论其单调性。" % num]
    for i in range(2, n + 1):
        if num == 14 and i == 2:
            lines.append("0.05；请根据附表判断结论是否成立。（干扰行）")
        elif num == 8 and i == n:
            lines.append("2、3、写出 a1，a2，a3 的大小关系。（干扰行）")
        else:
            lines.append("第%d题第%d行：求曲线 y=f(x) 在点(1,f(1))处的切线方程。" % (num, i))
    return lines


def build():
    """排版并返回 {num: {"lines":[txt..], "chunks":[[page,col]..]}} 真值。"""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    state = {"page": 0, "col": 0, "y": TOP}
    footers = []

    def new_page():
        state["page"] += 1
        state["col"] = 0
        state["y"] = TOP
        return doc.new_page(width=PAGE_W, height=PAGE_H)

    def ensure_room(h=0):
        p = doc[state["page"]]
        if state["y"] + LH + h > BOTTOM:
            if state["col"] == 0:
                state["col"] = 1
                state["y"] = TOP
            else:
                p = new_page()
        return doc[state["page"]]

    def put(text, page, col, y):
        """按栏宽预折行写入；返回占用的行数（真值以逻辑行为单位）。"""
        rows = 1
        w = fitz.get_text_length(text, fontname="china-s", fontsize=FS)
        if w > COL_W - 4:
            rows = 2
            text = text[:16] + "\n" + text[16:]
        r = fitz.Rect(COL_X[col], y, COL_X[col] + COL_W, y + rows * LH + 4)
        page.insert_textbox(r, text, fontsize=FS, fontname="china-s")
        return rows

    truth = {}
    # 考生须知（普通正文流，制造「1．再现」场景）
    for t in NOTICE:
        p = ensure_room()
        rows = put(t, p, state["col"], state["y"])
        state["y"] += rows * LH
    # 试卷标题
    p = ensure_room()
    rows = put("2026 届某区高三模拟 数学试卷（双栏测试卷）", p, 0, state["y"])
    state["y"] += rows * LH + 6
    state["col"] = 0

    for num in range(1, 23):
        chunks = []
        for i, line in enumerate(question_lines(num)):
            p = ensure_room()
            key = (state["page"], state["col"])
            if not chunks or chunks[-1] != list(key):
                chunks.append(list(key))
            rows = put(line, p, state["col"], state["y"])
            state["y"] += rows * LH
        truth[str(num)] = {"chunks": chunks}

    npages = state["page"] + 1
    for i in range(npages):
        pg = doc[i]
        # 页眉
        pg.insert_textbox(fitz.Rect(0, 28, PAGE_W, 46), "第 %d 页 共 %d 页" % (i + 1, npages),
                          fontsize=9, fontname="china-s")
        # 页脚
        pg.insert_textbox(fitz.Rect(260, 796, 335, 812), "%d / %d" % (i + 1, npages), fontsize=9)
    PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(PDF_PATH))
    n = len(doc)
    doc.close()
    TRUTH_PATH.write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已生成 %s（%d 页）+ 真值 %s" % (PDF_PATH.name, n, TRUTH_PATH.name))
    return truth


if __name__ == "__main__":
    build()
