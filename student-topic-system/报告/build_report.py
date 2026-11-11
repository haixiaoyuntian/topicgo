# -*- coding: utf-8 -*-
"""按《软件工程》项目报告模板生成 docx。

格式口径（与模板一致）：
- 章标题：黑体小2（18pt）加粗居中，章间分页
- 节标题：黑体4号（14pt）加粗
- 三级标题：黑体小4（12pt）加粗
- 正文：宋体小4（12pt），1.5 倍行距，首行缩进 2 字符
- 表标题：表格上方，黑体小4；表内容：宋体5号（10.5pt）
- 图标题：图下方，黑体小4
- 附录：宋体5号
"""
import os
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BASE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(BASE, "figs")
SHOT = os.path.join(os.path.dirname(BASE), "wiki", "screenshots")
OUT = os.path.join(BASE, "《软件工程》项目报告-学生选题系统.docx")

SONG, HEI, TNR = "宋体", "黑体", "Times New Roman"
TEXT_W = 16.0  # cm

doc = Document()

# ---------------- 基础样式 ----------------
normal = doc.styles["Normal"]
normal.font.name = TNR
normal.font.size = Pt(12)
normal.font.color.rgb = RGBColor(0, 0, 0)
normal._element.rPr.rFonts.set(qn("w:eastAsia"), SONG)
normal.paragraph_format.line_spacing = 1.5
normal.paragraph_format.space_before = Pt(0)
normal.paragraph_format.space_after = Pt(0)

for i, (size, align) in enumerate([(18, WD_ALIGN_PARAGRAPH.CENTER),
                                   (14, WD_ALIGN_PARAGRAPH.LEFT),
                                   (12, WD_ALIGN_PARAGRAPH.LEFT)], start=1):
    st = doc.styles[f"Heading {i}"]
    st.font.name = TNR
    st.font.size = Pt(size)
    st.font.bold = True
    st.font.color.rgb = RGBColor(0, 0, 0)
    st._element.rPr.rFonts.set(qn("w:eastAsia"), HEI)
    st.paragraph_format.alignment = align
    st.paragraph_format.space_before = Pt(12 if i > 1 else 0)
    st.paragraph_format.space_after = Pt(6)
    st.paragraph_format.line_spacing = 1.5
    st.paragraph_format.keep_with_next = True

for sec in doc.sections:
    sec.page_height = Cm(29.7)
    sec.page_width = Cm(21.0)
    sec.top_margin = Cm(2.54)
    sec.bottom_margin = Cm(2.54)
    sec.left_margin = Cm(2.5)
    sec.right_margin = Cm(2.5)
    sec.header_distance = Cm(1.5)
    sec.footer_distance = Cm(1.5)


def set_font(run, cn=SONG, en=TNR, size=12, bold=False, color=None):
    run.font.name = en
    run.font.size = Pt(size)
    run.font.bold = bold
    if color:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rpr.rFonts.set(qn("w:eastAsia"), cn)


def p(text="", indent=True, size=12, bold=False, align=WD_ALIGN_PARAGRAPH.JUSTIFY,
      cn=SONG, space_before=0, space_after=0, spacing=1.5):
    par = doc.add_paragraph()
    par.paragraph_format.alignment = align
    par.paragraph_format.line_spacing = spacing
    par.paragraph_format.space_before = Pt(space_before)
    par.paragraph_format.space_after = Pt(space_after)
    if indent:
        par.paragraph_format.first_line_indent = Pt(size * 2)
    if text:
        r = par.add_run(text.replace("`", ""))
        set_font(r, cn=cn, size=size, bold=bold)
    return par


def h1(text, page_break=True):
    par = doc.add_paragraph(style="Heading 1")
    par.paragraph_format.page_break_before = page_break
    par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    par.paragraph_format.space_before = Pt(6)
    par.paragraph_format.space_after = Pt(12)
    r = par.add_run(text)
    set_font(r, cn=HEI, size=18, bold=True)
    return par


def h2(text):
    par = doc.add_paragraph(style="Heading 2")
    r = par.add_run(text)
    set_font(r, cn=HEI, size=14, bold=True)
    return par


def h3(text):
    par = doc.add_paragraph(style="Heading 3")
    r = par.add_run(text)
    set_font(r, cn=HEI, size=12, bold=True)
    return par


def caption(text, before=6, after=3, keep_next=True):
    par = doc.add_paragraph()
    par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    par.paragraph_format.space_before = Pt(before)
    par.paragraph_format.space_after = Pt(after)
    par.paragraph_format.line_spacing = 1.0
    par.paragraph_format.keep_with_next = keep_next
    r = par.add_run(text.replace("`", ""))
    set_font(r, cn=HEI, size=12, bold=False)
    return par


def shade(cell, color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    tcPr.append(shd)


def table(headers, rows, widths=None, font_size=10.5, align_center=True, header_fill="DCE6F1"):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    tblPr = t._tbl.tblPr

    hdr = t.rows[0]
    trPr = hdr._tr.get_or_add_trPr()
    tblHeader = OxmlElement("w:tblHeader")
    trPr.append(tblHeader)
    for i, h in enumerate(headers):
        cell = hdr.cells[i]
        cell.text = ""
        par = cell.paragraphs[0]
        par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        par.paragraph_format.line_spacing = 1.0
        par.paragraph_format.space_before = Pt(2)
        par.paragraph_format.space_after = Pt(2)
        r = par.add_run(str(h))
        set_font(r, cn=HEI, size=font_size, bold=True)
        shade(cell, header_fill)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    for row in rows:
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cell = cells[i]
            cell.text = ""
            par = cell.paragraphs[0]
            par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER if align_center else WD_ALIGN_PARAGRAPH.LEFT
            par.paragraph_format.line_spacing = 1.15
            par.paragraph_format.space_before = Pt(2)
            par.paragraph_format.space_after = Pt(2)
            r = par.add_run(str(val).replace("`", ""))
            set_font(r, cn=SONG, size=font_size)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        trPr = t.rows[-1]._tr.get_or_add_trPr()
        cantSplit = OxmlElement("w:cantSplit")
        trPr.append(cantSplit)

    if widths:
        for el in tblPr.findall(qn("w:tblW")):
            tblPr.remove(el)
        tblW = OxmlElement("w:tblW")
        tblW.set(qn("w:type"), "dxa")
        tblW.set(qn("w:w"), str(int(sum(widths) / 2.54 * 1440)))
        tblPr.insert_element_before(tblW, "w:jc", "w:tblCellSpacing", "w:tblInd",
                                    "w:tblBorders", "w:shd", "w:tblLayout",
                                    "w:tblCellMar", "w:tblLook")
        for i, w in enumerate(widths):
            t.columns[i].width = Cm(w)
            for cell in t.columns[i].cells:
                cell.width = Cm(w)
    # 小表整体不跨页（长表仍允许分页并重复表头）
    if len(rows) <= 8:
        for row in t.rows[:-1]:
            for cell in row.cells:
                for par in cell.paragraphs:
                    par.paragraph_format.keep_with_next = True
    return t


def figure(path, caption_text, width=15.0):
    par = doc.add_paragraph()
    par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    par.paragraph_format.space_before = Pt(6)
    par.paragraph_format.space_after = Pt(2)
    par.paragraph_format.line_spacing = 1.0
    par.paragraph_format.keep_with_next = True
    par.add_run().add_picture(path, width=Cm(width))
    caption(caption_text, before=2, after=10, keep_next=False)


def code(text, size=10.5):
    for line in text.split("\n"):
        par = doc.add_paragraph()
        # 先写底纹，再写间距/缩进，保证 pPr 子元素顺序合法
        pPr = par._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), "F5F5F5")
        pPr.append(shd)
        par.paragraph_format.line_spacing = 1.0
        par.paragraph_format.space_before = Pt(0)
        par.paragraph_format.space_after = Pt(0)
        par.paragraph_format.left_indent = Cm(0.6)
        r = par.add_run(line if line else " ")
        set_font(r, cn=SONG, en="Consolas", size=size)


def bullets(items, indent=True, size=12):
    for item in items:
        par = doc.add_paragraph()
        par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        par.paragraph_format.line_spacing = 1.5
        par.paragraph_format.left_indent = Pt(size * 2)
        if indent:
            par.paragraph_format.first_line_indent = Pt(-size * 2 + size * 2)
        r = par.add_run(item.replace("`", ""))
        set_font(r, size=size)


def field_page_number(par):
    run = par.add_run()
    fldChar1 = OxmlElement("w:fldChar")
    fldChar1.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    fldChar2 = OxmlElement("w:fldChar")
    fldChar2.set(qn("w:fldCharType"), "separate")
    txt = OxmlElement("w:t")
    txt.text = "1"
    fldChar3 = OxmlElement("w:fldChar")
    fldChar3.set(qn("w:fldCharType"), "end")
    run._r.append(fldChar1)
    run._r.append(instr)
    run._r.append(fldChar2)
    run._r.append(txt)
    run._r.append(fldChar3)
    set_font(run, size=10.5)


def add_header(section, text):
    section.header.is_linked_to_previous = False
    par = section.header.paragraphs[0]
    par.text = ""
    pPr = par._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "4")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "BFBFBF")
    borders.append(bottom)
    pPr.insert_element_before(borders, "w:shd", "w:tabs", "w:spacing", "w:ind", "w:jc")
    par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r = par.add_run(text)
    set_font(r, cn=SONG, size=9, color=RGBColor(0x66, 0x66, 0x66))


def add_footer_page(section):
    section.footer.is_linked_to_previous = False
    par = section.footer.paragraphs[0]
    par.text = ""
    par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    field_page_number(par)


# ================= 封面 =================
for _ in range(3):
    doc.add_paragraph()

par = doc.add_paragraph()
par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = par.add_run("《软件工程》项目报告")
set_font(r, cn=HEI, size=36, bold=True)

for _ in range(4):
    doc.add_paragraph()

cover = [
    ("题　　目", "学生选题系统（TopicGo）的设计与实现"),
    ("课程名称", "软件工程"),
    ("专业班级", "创新 2401 班"),
    ("组　　号", "第 1 组"),
]
for label, value in cover:
    par = doc.add_paragraph()
    par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    par.paragraph_format.space_before = Pt(6)
    r = par.add_run(f"{label}：")
    set_font(r, cn=HEI, size=14, bold=True)
    r2 = par.add_run(value)
    set_font(r2, cn=SONG, size=14)

doc.add_paragraph()
for idx, (label, name, sid) in enumerate([
        ("同组成员：", "姬昕昊", "U202414904"),
        ("　　　　　", "王礼骐", "U202414918")]):
    par = doc.add_paragraph()
    par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if idx:
        par.paragraph_format.space_before = Pt(2)
    r = par.add_run(label)
    set_font(r, cn=HEI, size=14, bold=True)
    r2 = par.add_run(f"{name}（学号：{sid}）")
    set_font(r2, cn=SONG, size=14)

for label, value in [("指导教师", "瞿彬彬"), ("报告日期", "2026 年 12 月")]:
    par = doc.add_paragraph()
    par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    par.paragraph_format.space_before = Pt(6)
    r = par.add_run(f"{label}：")
    set_font(r, cn=HEI, size=14, bold=True)
    r2 = par.add_run(value)
    set_font(r2, cn=SONG, size=14)

for _ in range(5):
    doc.add_paragraph()
par = doc.add_paragraph()
par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = par.add_run("计算机科学与技术学院")
set_font(r, cn=HEI, size=16, bold=True)

# ================= 任务书 =================
sec2 = doc.add_section(WD_SECTION.NEW_PAGE)
par = doc.add_paragraph()
par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
par.paragraph_format.space_after = Pt(16)
r = par.add_run("任 务 书")
set_font(r, cn=HEI, size=18, bold=True)

h = doc.add_paragraph()
r = h.add_run("一 总体要求")
set_font(r, cn=HEI, size=14, bold=True)
for txt in [
    "1. 综合运用软件工程的思想，协同完成一个软件项目的开发，掌握软件工程相关的技术和方法；",
    "2. 组成小组进行选题，通过调研完成项目的需求分析，并详细说明小组成员的分工、项目的时间管理等方面；",
    "3. 根据需求分析进行总体设计、详细设计、编码与测试等。",
]:
    p(txt, indent=True)

h = doc.add_paragraph()
h.paragraph_format.space_before = Pt(6)
r = h.add_run("二 基本内容")
set_font(r, cn=HEI, size=14, bold=True)
p("根据给出的题目任选一题，自行组队，设计与开发中软件过程必须包括：", indent=True)
for txt in [
    "1. 问题概述、需求分析：正确使用相关工具和方法说明所开发软件的问题定义和需求分析，比如 NABCD 模型、Microsoft Visio、StarUML 等工具（20%）；",
    "2. 原型系统设计、概要设计、详细设计：主要说明所开发软件的架构、数据结构及主要算法设计，比如墨刀等工具（35%）；",
    "3. 编码与测试：编码规范，运用码云等平台进行版本管理，设计测试计划和测试用例（30%）；",
    "4. 功能创新：与众不同、特别吸引用户的创新（10%）；",
    "5. 用户反馈：包括用户的使用记录、照片、视频等（5%）。",
]:
    p(txt, indent=True)

# ================= 目录 =================
sec3 = doc.add_section(WD_SECTION.NEW_PAGE)
par = doc.add_paragraph()
par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
par.paragraph_format.space_after = Pt(12)
r = par.add_run("目　　录")
set_font(r, cn=HEI, size=18, bold=True)

import json as _json
toc_path = os.path.join(BASE, "toc.json")
if os.path.exists(toc_path) and not os.environ.get("NO_TOC"):
    from docx.enum.text import WD_TAB_ALIGNMENT, WD_TAB_LEADER
    for level, title, page in _json.load(open(toc_path, encoding="utf-8")):
        tp = doc.add_paragraph()
        tp.paragraph_format.line_spacing = 1.0
        tp.paragraph_format.space_before = Pt(3 if level == 1 else 0)
        tp.paragraph_format.space_after = Pt(0)
        tp.paragraph_format.left_indent = Cm([0, 0.74, 1.48][level - 1])
        tp.paragraph_format.tab_stops.add_tab_stop(Cm(16.0), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        tr = tp.add_run(title)
        set_font(tr, cn=HEI if level == 1 else SONG, size=11, bold=(level == 1))
        tp.add_run("\t")
        pr = tp.add_run(str(page))
        set_font(pr, cn=SONG, size=11)
else:
    par = doc.add_paragraph()
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), r'TOC \o "1-3" \h \z \u')
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "（目录为自动域：在 Word 中右键选择“更新域”或按 F9 生成/刷新页码）"
    run.append(t)
    fld.append(run)
    par._p.append(fld)

# ================= 正文 =================
sec4 = doc.add_section(WD_SECTION.NEW_PAGE)
add_header(sec4, "《软件工程》项目报告 · 学生选题系统 TopicGo")
add_footer_page(sec4)
sectPr = sec4._sectPr
pgNumType = OxmlElement("w:pgNumType")
pgNumType.set(qn("w:start"), "1")
sectPr.insert_element_before(pgNumType, "w:cols", "w:formProt", "w:vAlign",
                             "w:noEndnote", "w:titlePg", "w:textDirection",
                             "w:bidi", "w:rtlGutter", "w:docGrid",
                             "w:printerSettings", "w:sectPrChange")

# ---------- 1 ----------
h1("1 问题定义", page_break=False)

h2("1.1 项目背景与意义")
h3("1.1.1 项目背景")
p("课程设计选题是每学期初的必要环节。传统做法通常是在课堂上口头公布题目、由学生举手或事后在群聊中“抢题”，"
  "再由任课教师用 Excel 汇总。当学生人数达到百人级、题目数量达到数十个时，这种方式会暴露出四个问题：")
p("（1）信息分散且不对称。题目、人数上限、先修要求分散在课件、群文件和口头通知中，学生难以在一处比较，"
  "经常出现“选完才知道有先修要求”的情况。")
p("（2）顺序不透明。谁先选、谁后选、名额何时满，全凭教师记录；一旦出现争议，缺少可核查的凭据。")
p("（3）名额容易超卖。多人同时提交时，“查剩余名额—写入记录”之间存在竞态窗口，手工统计更容易重复计数。")
p("（4）退选与改选没有闭环。学生退选次数用尽、临近截止需要改选时，系统往往只提示“联系教师”，"
  "但教师端没有对应入口，流程断在系统之外，记录仍挂在原题目上。")
p("本小组在立项前访谈了 2 位任课教师和 5 名同学，上述问题均被提及，其中“名额超卖”和“退选后没人递补”"
  "是教师最关心的两点。因此，本项目将“规则显性化、过程留痕、结果可审计、异常有出口”作为设计主线。")

h3("1.1.2 项目意义")
p("从管理角度看，系统把选题规则（先到先得、绩点优先、多志愿、随机抽签）变成可配置、可复现的代码逻辑，"
  "减少人为解释空间；从学生角度看，所有名额、要求与结果实时可见，退选和改选有明确的系统入口；"
  "从审计角度看，选题、退选、申请、审批、递补、分配、抽签全部写入不可修改的操作日志，出现争议时可以逐条还原。")
p("此外，系统采用轻量技术栈（Python + Flask + SQLite），一份源码即可在普通笔记本上运行，"
  "不需要额外购买服务器或数据库服务，具备在学院内多个课程中复用的条件。")

p("下面用 NABCD 模型对项目价值做结构化说明，如表 1-1 所示。", space_before=6)
caption("表 1-1　项目 NABCD 分析")
table(
    ["维度", "分析内容"],
    [
        ["N（Need，需求）", "选题信息分散、顺序不透明、名额易超卖、退选靠口头、统计靠手工。访谈中 2 位教师均提到“每次都要花半天核对名单”，5 名学生均希望“随时能看到还剩多少名额”。"],
        ["A（Approach，做法）", "以“轮次 + 策略”为核心：教师按轮次配置规则与时间窗；学生按规则选题或填报志愿；系统用数据库事务与部分唯一索引保证不超卖；退选锁定后提供在线申请与教师代退闭环。"],
        ["B（Benefit，收益）", "学生端实时可见名额与前置要求，减少无效咨询；教师端自动统计、一键分配/抽签、导出 CSV，预计每次选题统计由半天缩短到十分钟内；全过程留痕，争议可追溯。"],
        ["C（Competitors，竞争）", "替代方案主要是群接龙、Excel 登记、问卷收集和通用教务选课系统。前三种缺少并发控制与审计；通用系统侧重学分选课，难以支持“课题—教师—班级—先修要求”这类课设场景。"],
        ["D（Delivery，推广）", "源码托管于 CNB 仓库，`python src/app.py` 一条命令启动；提供演示账号、项目主页、可交互原型与图文使用流程；可打包为压缩包在机房离线部署。"],
    ],
    widths=[3.6, 12.4], align_center=False,
)

h2("1.2 项目基本目标")
p("项目目标分为功能目标与非功能目标两类，并尽量量化为可验证的指标。")
p("（1）功能目标：覆盖“管理员登记账号—教师建轮次与题库—学生选题/填报—教师监控与执行策略—退选与递补—结果公示与导出”的完整链路，"
  "并在退选次数用尽、截止前锁定、志愿/报名阶段等异常场景提供系统内出口。")
p("（2）非功能目标：保证并发选题不超卖；页面响应在课程规模下保持流畅；全站表单具备 CSRF 防护；"
  "所有关键动作可追溯；系统可在 Windows/macOS/Linux 上以同一套命令运行。")
caption("表 1-2　项目目标与量化指标")
table(
    ["类别", "目标", "量化指标", "验证方式"],
    [
        ["功能", "四种选题策略可用", "先到先得/绩点优先/多志愿/随机抽签均可配置并生效", "手工用例 + 回归断言"],
        ["功能", "退选与递补闭环", "退选/代退后按策略递补，学生收到站内通知", "回归第 20 组 31 项断言"],
        ["功能", "异常有出口", "次数用尽、截止锁定、志愿/报名阶段均可提交申请", "手工用例 B13、E08~E12"],
        ["性能", "页面响应流畅", "14 个页面/接口中位数 < 100 ms", "非功能脚本实测"],
        ["并发", "多人抢题不超卖", "6 线程抢 2 个名额，确认数 ≤ 2", "并发测试脚本"],
        ["安全", "越权与伪造请求被拦截", "未登录/越权/缺 CSRF 令牌均被拒绝", "安全用例 + 脚本"],
        ["质量", "回归可重复", "163 项断言全部通过", "`test_regression.py`"],
        ["规模", "代码规模可控", "主体功能代码约 4400 行（含 18 个页面）", "`wc -l` 复核"],
    ],
    widths=[1.8, 4.2, 6.5, 3.5], align_center=False,
)

h2("1.3 可行性分析")
p("（1）技术可行性。项目采用 Python 3.12 + Flask 3 + Jinja2 + SQLite。Flask 路由与模板机制成熟，"
  "SQLite 支持事务、WAL 与部分索引，足以支撑百人级选课的并发写入；前端使用原生 HTML/CSS/JS，无构建链，"
  "降低了环境配置与演示风险。小组两人均具备 Python 与 Web 开发基础，关键技术点（事务、索引、会话）均有官方文档与成熟案例可查。")
p("（2）经济可行性。全部技术栈开源免费，开发与演示均在个人电脑上完成，不需要云服务器、域名或商业数据库；"
  "部署成本仅为一份源码和一个 Python 环境。")
p("（3）操作可行性。系统为浏览器访问的响应式页面，学生、教师、管理员按角色进入各自工作台；"
  "关键页面内置“使用说明”折叠面板，新生无需培训即可完成选题；管理员采用登记制，不开放公开注册，符合课程管理习惯。")
p("（4）进度可行性。项目自 2026 年 9 月 8 日立项，按“立项—需求—设计—实现—测试—发布”六阶段推进，"
  "分解为 32 项任务（两人各 16 项），关键里程碑为需求确认（9 月 16 日）、设计报告（9 月 30 日）、"
  "测试报告（10 月 30 日）与验收（11 月 6 日），留有约两周缓冲。")
caption("表 1-3　主要风险与应对措施")
table(
    ["风险", "影响", "应对措施"],
    [
        ["并发写入导致超卖", "高：直接破坏公平性", "`BEGIN IMMEDIATE` 串行化 + `(ROUND_ID,USER_ID) WHERE confirmed` 部分唯一索引兜底，并用 6 线程脚本验证"],
        ["需求边界不清（如“退选找老师”）", "中：流程断在系统外", "以“用户完不成任务时怎么办”为检查清单，补充退选申请与教师代退闭环"],
        ["规则切换留下中间状态", "中：学生卡在待分配/待抽签", "切换前校验遗留记录，监控页提供“处理遗留志愿/报名”入口，抽签前作废关闭课题的报名"],
        ["两人协作冲突", "中：合并成本高", "按模块分工、每日提交、Issue 跟踪，数据库迁移脚本向后兼容"],
        ["演示环境网络不稳定", "低：影响现场展示", "本地一键启动，公网地址仅作备选；准备离线压缩包与截图"],
    ],
    widths=[4.2, 4.0, 7.8], align_center=False,
)

h2("1.4 人员管理和项目进度管理")
h3("1.4.1 人员分工")
p("小组共 2 人，采用“模块牵头、互相补位”的方式：一人负责架构、后端算法与测试，一人负责需求、原型、页面与文档；"
  "双方都参与代码评审与对方模块的联调，避免单点依赖。")
caption("表 1-4　小组成员与分工")
table(
    ["成员", "角色", "主要职责", "任务编号"],
    [
        ["姬昕昊", "组长 / 项目经理、后端开发", "总体进度、系统架构、数据库设计、选题/退选/递补/分配/抽签算法、权限与事务、自动化测试与验收材料", "T01、T04、T08、T11~T17、T19、T21、T25~T27、T29"],
        ["王礼骐", "组员 / 产品、前端、文档", "需求调研与分析、高保真原型、页面与样式、公告与导出页面、使用说明与项目主页、用户反馈", "T02、T03、T05~T07、T09、T10、T18、T20、T22~T24、T28、T30~T32"],
    ],
    widths=[2.2, 4.0, 7.4, 2.4], align_center=False,
)

h3("1.4.2 进度计划")
p("项目按阶段推进，每阶段有明确产出与检查点。表 1-5 给出阶段计划，详细 32 项任务与甘特图见 `wiki/03-进度计划.md`。")
caption("表 1-5　项目阶段计划")
table(
    ["阶段", "时间", "主要工作", "产出/里程碑"],
    [
        ["立项", "2026-09-08 ~ 09-13", "头脑风暴、候选方向对比、NABCD 分析", "立项 Wiki、思维导图"],
        ["需求", "2026-09-14 ~ 09-20", "访谈教师与学生、梳理角色与业务规则、需求评审", "需求分析（T08 里程碑）"],
        ["设计", "2026-09-21 ~ 09-30", "架构、数据库、原型、接口设计", "设计报告（T14 里程碑）"],
        ["实现", "2026-10-01 ~ 10-25", "骨架、认证、学生端、教师端、监控与导出", "可运行系统 v1.0"],
        ["测试", "2026-10-26 ~ 11-06", "用例设计、功能/并发/非功能测试、缺陷修复", "测试报告（T29 里程碑）"],
        ["发布与验收", "11-07 ~ 12-08", "使用说明、项目主页、验收材料、报告撰写", "验收材料、项目报告"],
    ],
    widths=[2.2, 4.2, 6.6, 3.0], align_center=False,
)
p("进度控制采用三条约定：每周五晚线上站会（20 分钟）同步进度与阻塞；每个任务在仓库建 Issue，关闭即完成；"
  "提交信息统一为 `feat/fix/docs/test: 简述`，便于回溯。关键里程碑逾期时，优先砍非核心功能（如原型动画），"
  "保证“选题—退选—递补—审计”主干不受影响。")

# ---------- 2 ----------
h1("2 需求分析")
h2("2.1 需求分析概述")
p("系统面向三类角色，权限与数据归属严格分离：管理员只负责账号；教师只管理自己的课题、轮次与学生；"
  "学生只能看到自己指导教师发布的、面向自己班级的、处于开放轮次内的课题。")
caption("表 2-1　角色与权限")
table(
    ["角色", "可见数据", "可执行操作", "明确禁止"],
    [
        ["学生", "本人指导教师名下、面向本人班级、状态开放的课题；本人选题与通知", "浏览/搜索、选题、候补、填报志愿、抽签报名、退选、提交退选申请、查看公示", "查看他师课题、替他人操作、访问教师/管理页"],
        ["教师", "本人发布的课题、本人的轮次、本人名下学生", "题库 CRUD、前置要求、轮次与策略、监控、分配/抽签、递补、撤销、退课/代退、审批申请、录成绩、导出、公告", "管理他人课题/轮次、查看他师学生、访问用户管理"],
        ["管理员", "全部账号", "登记、编辑、分配指导教师、停用/启用、重置密码、删除（有依赖时拒绝）", "访问题库/轮次/监控等业务页、修改成绩"],
    ],
    widths=[1.6, 5.2, 5.4, 3.8], align_center=False,
)

p("需求获取采用三种方式：一是访谈 2 位任课教师与 5 名学生，记录真实流程与痛点；二是对比群接龙、Excel、通用教务系统；"
  "三是按“正常路径 + 异常路径”走查场景。最终形成 12 条功能需求，如表 2-2。")
caption("表 2-2　功能需求列表")
table(
    ["编号", "需求名称", "需求说明", "优先级"],
    [
        ["FR-01", "登记制登录", "不开放注册，仅管理员登记的账号可登录；停用账号拒绝登录", "高"],
        ["FR-02", "课题浏览与筛选", "按关键词、标签、难度筛选；显示名额、前置要求与本人匹配情况", "高"],
        ["FR-03", "选题与候补", "每人限选 1 题；满员可入候补并显示序号", "高"],
        ["FR-04", "四种选题策略", "先到先得、绩点优先、多志愿分配、随机抽签（种子可复现）", "高"],
        ["FR-05", "退选与递补", "退选需选原因；按自动/手动/不递补处理候补", "高"],
        ["FR-06", "退选锁定与申请", "次数上限、截止前锁定；无法自助退选时可提交申请，教师同意后代退", "高"],
        ["FR-07", "教师监控与策略执行", "实时名额、候补、待分配名单；执行分配/抽签/递补/撤销", "高"],
        ["FR-08", "前置要求核验", "教师维护成绩与绩点；课题可设课程分数门槛；不满足可退课并通知", "中"],
        ["FR-09", "站内通知", "退课、代退、递补、撤销、申请结果均通知学生并显示未读红点", "中"],
        ["FR-10", "全程留痕", "选题、退选、申请、审批、递补、分配、抽签写入操作日志", "高"],
        ["FR-11", "多班级与多轮次", "课题按班级发布；教师可建多个轮次，最新轮次生效", "中"],
        ["FR-12", "导出与公示", "教师导出本轮 CSV；学生查看本轮结果公示", "低"],
    ],
    widths=[1.6, 3.8, 8.9, 1.7], align_center=False,
)

p("主要业务规则如下（也是后续测试用例的设计依据）：")
bullets([
    "① 同一轮次内，每名学生最多一条“已确认”记录；",
    "② 选题必须落在轮次时间窗内，且班级在允许范围内、课题面向本班；",
    "③ 退选受“次数上限 + 截止前锁定 + 原因白名单”三重约束；教师撤销/代退不计入学生次数；",
    "④ 候补按队列序号递补；递补结果必须通知本人；",
    "⑤ 记录只能软取消（`cancelled`），不物理删除，保留取消时间。",
])

h2("2.2 UML相关需求分析图")
p("图 2-1 为系统用例图。学生侧的核心用例是“选题/加入候补”和“提交退选申请”；教师侧的核心用例是"
  "“监控/分配/抽签/递补”和“审批申请/代为退选”。后者是本次需求分析中补充的关键用例——它把原本发生在系统外的"
  "“找老师退课”变成了系统内的标准动作。")
figure(os.path.join(FIG, "2-1-usecase.png"), "图 2-1　学生选题系统用例图", width=16.0)

p("图 2-2 给出先到先得模式下的核心业务流程。需要特别说明的是右侧的三条失败分支：轮次未开放、班级不符、已有选题，"
  "它们都在写入数据库之前完成校验，保证事务内的逻辑足够短，减少锁竞争。")
figure(os.path.join(FIG, "2-2-flow.png"), "图 2-2　选题核心业务流程图（先到先得模式）", width=14.0)

p("选题记录的状态迁移是需求分析的另一条主线：`confirmed`（已确认）、`waiting`（候补中）、`wish`（志愿待分配）、"
  "`entry`（待抽签）与 `cancelled`（已取消）。任何有效状态都可以通过退选、撤销、代退或申请审批进入 `cancelled`，"
  "且所有入口最终调用同一个 `close_selection` 函数，保证“取消—日志—递补—通知”四件事不会被漏做。")

h2("2.3 原型系统设计")
p("原型采用手写 HTML/CSS/JS 制作高保真可交互原型（`prototype/prototype.html`），无需登录即可在浏览器打开，"
  "用于需求评审与交叉评估。原型覆盖登录、学生首页、题库、志愿填报、我的选题、教师工作台、题库维护、轮次设置、"
  "监控与管理员用户管理 10 个界面，交互与最终系统基本一致。")
caption("表 2-3　原型页面清单")
table(
    ["端", "页面", "关键交互"],
    [
        ["公共", "登录 / 修改密码", "账号密码登录、原密码校验、错误提示"],
        ["学生", "首页", "待办卡片、截止倒计时、公告、未读通知"],
        ["学生", "浏览题库", "搜索/标签/难度筛选、名额进度、前置要求匹配提示"],
        ["学生", "志愿填报 / 抽签报名", "勾选题目、设置志愿序号、一次提交"],
        ["学生", "我的选题", "状态、退选（选原因）、退选申请、申请进度、历史记录"],
        ["学生", "结果公示 / 通知", "本轮结果、站内通知列表与未读标记"],
        ["教师", "工作台", "待办（志愿/抽签/申请）、班级卡片、学生进度"],
        ["教师", "题库维护 / 选题轮次", "课题 CRUD、前置要求、四种策略与递补配置"],
        ["教师", "选题监控 / 我的学生", "实时统计、分配/抽签、申请审批、代退、成绩录入、导出"],
        ["管理员", "用户管理", "登记、分配指导教师、停用、重置密码"],
    ],
    widths=[1.6, 5.0, 9.4], align_center=False,
)
figure(os.path.join(SHOT, "09-prototype-student.png"), "图 2-3　高保真原型（学生端选题流程）", width=14.5)
p("原型评审阶段发现两处需要调整：一是“我的选题”表格中直接嵌入退选原因下拉框会挤压列宽，最终改为“行内下拉 + 折叠申请表单”；"
  "二是教师监控页信息密度过高，最终把“退选申请”独立成卡片并放在页面顶部，保证教师登录后第一眼能看到待办。"
  "这两处在实现阶段均已落实。")

# ---------- 3 ----------
h1("3 概要设计和详细设计")
h2("3.1 系统结构")
h3("3.1.1 功能说明")
p("系统采用 B/S 架构与服务端渲染，整体分为表示层、应用层与数据层，如图 3-1 所示。")
figure(os.path.join(FIG, "3-1-architecture.png"), "图 3-1　系统体系结构图", width=15.5)
p("应用层是一个 Flask 进程，包含 35 个路由、若干业务函数与两个上下文处理器（CSRF 令牌注入、未读通知/待办注入）。"
  "数据层为单个 SQLite 文件，开启 WAL 模式，所有写操作使用显式事务。")
figure(os.path.join(FIG, "3-2-modules.png"), "图 3-2　系统功能模块结构图", width=16.0)
caption("表 3-1　模块职责说明")
table(
    ["模块", "职责", "关键实现"],
    [
        ["认证与权限", "登录/登出、会话、角色校验、CSRF、自助改密", "`login_required`、`csrf_protect`、`change_password`"],
        ["学生选题", "浏览、选题、候补、退选、退选申请、我的选题、公示、通知", "`student_browse/select/cancel/cancel_request/mine`"],
        ["教师题库与轮次", "课题 CRUD、前置要求、面向班级、轮次与四种策略", "`teacher_topics`、`teacher_round`"],
        ["策略执行", "多志愿分配、公开抽签、手动递补、撤销、退课/代退、申请审批", "`teacher_allocate/lottery/promote/revoke/kick/cancel_request`"],
        ["学业数据", "成绩与绩点维护、前置核验", "`teacher_students`、`prereq_check`"],
        ["审计与通知", "操作日志、站内通知、CSV 导出", "`log_event`、`notify`、`teacher_logs`、`teacher_export`"],
        ["用户管理", "登记、编辑、分配导师、停用、重置密码、删除", "`admin_users`"],
    ],
    widths=[3.0, 7.4, 5.6], align_center=False,
)

h3("3.1.2 接口设计")
p("系统以“页面路由 + 表单提交”为主，不单独提供 REST API；所有写操作均为 POST 并携带 CSRF 令牌。主要接口如表 3-2。")
caption("表 3-2　主要接口清单（节选）")
table(
    ["方法", "路径", "说明", "权限"],
    [
        ["GET/POST", "/login、/logout", "登录（校验状态）、退出", "匿名"],
        ["GET", "/student/browse", "题库浏览与筛选（四重过滤）", "学生"],
        ["POST", "/student/select/<topic_id>", "选题/加入候补（事务内判容量）", "学生"],
        ["GET/POST", "/student/wish", "多志愿填报 / 抽签报名", "学生"],
        ["POST", "/student/cancel/<selection_id>", "退选（次数/锁定/原因校验）", "学生"],
        ["POST", "/student/cancel-request/<selection_id>", "提交退选/改选申请", "学生"],
        ["GET", "/student/mine", "我的选题、申请进度、跨轮次历史", "学生"],
        ["GET", "/teacher/monitor", "监控、候补、待分配、退选申请", "教师"],
        ["POST", "/teacher/allocate", "执行多志愿分配", "教师"],
        ["POST", "/teacher/lottery", "执行公开抽签", "教师"],
        ["POST", "/teacher/promote/<id>", "手动递补", "教师"],
        ["POST", "/teacher/revoke/<id>", "撤销误提交记录（通知学生）", "教师"],
        ["GET/POST", "/teacher/kick/<id>", "前置退课 / 教师代退（原因+建议必填）", "教师"],
        ["POST", "/teacher/cancel-request/<req_id>", "同意/驳回退选申请", "教师"],
        ["GET", "/teacher/export", "导出本轮 CSV", "教师"],
        ["GET/POST", "/admin/users", "账号登记、编辑、停用、重置、删除", "管理员"],
    ],
    widths=[2.2, 6.3, 5.9, 1.6], align_center=False,
)

h2("3.2 类图等")
p("系统采用“领域对象 + 服务函数”的实现方式，未引入 ORM。类图（图 3-3）用于表达领域对象及其职责："
  "`User` 负责身份与可见范围，`Topic` 负责容量与前置要求，`Round` 负责规则与时间窗，"
  "`Selection` 负责状态迁移，`CancelRequest` 负责退选申请，`SelectionEvent` 是不可变的审计记录。")
figure(os.path.join(FIG, "3-3-class.png"), "图 3-3　核心领域类图", width=16.0)
p("对象之间的关键约束：一个 `User` 可以发布多个 `Topic`、管理多个 `Round`，但在同一 `Round` 中最多有一条"
  "`confirmed` 的 `Selection`；一条 `Selection` 最多有一条待处理的 `CancelRequest`；任何状态变化都会追加一条"
  "`SelectionEvent`。这些约束分别由部分唯一索引与业务函数保证。")

h2("3.3 关键数据结构定义")
p("系统共 10 张表，所有表统一包含 `CREATED_BY / CREATED_AT / UPDATED_BY / UPDATED_AT / FLAG` 五个公共字段，"
  "用于记录创建人、修改人和软删除标记。实体关系如图 3-4 所示。")
figure(os.path.join(FIG, "3-3-er.png"), "图 3-4　数据库 E-R 图", width=16.0)
caption("表 3-3　数据表清单")
table(
    ["表名", "作用", "关键字段"],
    [
        ["USERS", "用户（学生/教师/管理员）", "USER_NO、USER_NAME、ROLE、ADVISOR_ID、GPA、STATUS"],
        ["TOPICS", "课题", "TOPIC_CODE、MAX_NUM、TEACHER_ID、TARGET_CLASSES、PREREQ_RULES"],
        ["ROUNDS", "选题轮次与策略", "RULE、START_AT、END_AT、PROMOTE_MODE、MAX_CANCEL、LOTTERY_SEED"],
        ["SELECTIONS", "选题/志愿/候补记录", "ROUND_ID、TOPIC_ID、USER_ID、STATUS、WISH_NO、QUEUE_NO"],
        ["SELECTION_EVENTS", "操作日志（只追加）", "ACTION、FROM_STATUS、TO_STATUS、OPERATOR、REMARK"],
        ["CANCEL_REQUESTS", "退选/改选申请", "SELECTION_ID、REASON、STATUS、HANDLE_REMARK、HANDLED_AT"],
        ["COURSES", "课程字典", "COURSE_NAME、CREDIT"],
        ["SCORES", "学生课程成绩", "USER_ID、COURSE_ID、SCORE、TERM"],
        ["NOTICES", "站内通知", "USER_ID、TITLE、CONTENT、KIND、READ_FLAG"],
        ["ANNOUNCEMENTS", "教师公告", "TEACHER_ID、TITLE、CONTENT、PUBLISHED_AT"],
    ],
    widths=[4.3, 4.5, 7.2], align_center=False,
)
p("核心表 `SELECTIONS` 与 `CANCEL_REQUESTS` 的字段定义如下。")
caption("表 3-4　SELECTIONS 选题记录表")
table(
    ["字段", "类型", "说明"],
    [
        ["ID", "INTEGER PK", "主键"],
        ["ROUND_ID / TOPIC_ID / USER_ID", "INTEGER", "所属轮次、课题、学生"],
        ["STATUS", "TEXT", "confirmed / waiting / wish / entry / cancelled"],
        ["WISH_NO / QUEUE_NO", "INTEGER", "志愿序号 / 候补序号"],
        ["SELECTED_AT / CANCELLED_AT", "TEXT", "选题时间 / 取消时间（保留历史）"],
    ],
    widths=[5.0, 3.0, 8.0], align_center=False,
)
caption("表 3-5　CANCEL_REQUESTS 退选申请表")
table(
    ["字段", "类型", "说明"],
    [
        ["ID", "INTEGER PK", "主键"],
        ["ROUND_ID / TOPIC_ID / USER_ID / SELECTION_ID", "INTEGER", "关联轮次、课题、学生与选题记录"],
        ["REASON", "TEXT", "学生说明（4~300 字）"],
        ["STATUS", "TEXT", "pending / approved / rejected"],
        ["HANDLE_REMARK / HANDLED_BY / HANDLED_AT", "TEXT", "教师意见、处理人、处理时间"],
    ],
    widths=[6.0, 3.0, 7.0], align_center=False,
)
p("索引与约束共 8 个，其中两个部分唯一索引承担关键的一致性职责，如表 3-6 所示。")
caption("表 3-6　索引与约束")
table(
    ["名称", "类型", "作用"],
    [
        ["UK_SELECTION_USER", "部分唯一索引 (ROUND_ID, USER_ID) WHERE STATUS='confirmed'", "同一轮一人最多一条已确认记录（并发兜底）"],
        ["UK_SCORE_USER_COURSE", "部分唯一索引 (USER_ID, COURSE_ID) WHERE FLAG=1", "同一学生同一课程只保留一条成绩"],
        ["UK_CANCEL_REQ_PENDING", "部分唯一索引 (SELECTION_ID) WHERE STATUS='pending'", "同一记录只允许一条待处理申请"],
        ["IDX_SELECTION_TOPIC", "普通索引 (TOPIC_ID, STATUS)", "加速按题统计"],
        ["IDX_EVENTS_ROUND", "普通索引 (ROUND_ID, OPERATED_AT)", "加速按轮次查日志"],
        ["IDX_SCORES_USER / IDX_NOTICES_USER / IDX_CANCEL_REQ_USER", "普通索引", "加速成绩、未读通知、申请进度查询"],
    ],
    widths=[5.4, 6.4, 4.2], align_center=False,
)

h2("3.4 关键算法设计")
h3("3.4.1 并发选题防超卖")
p("朴素实现是“先查剩余名额，再插入记录”，两个请求可能同时读到相同余量，导致超卖。系统把“查—判—写—记日志”放进"
  "`BEGIN IMMEDIATE` 事务：SQLite 在事务开始时立即获取写锁，后到的请求排队等待，从而把并发写串行化。")
figure(os.path.join(FIG, "3-4-concurrency.png"), "图 3-5　并发选题事务流程图", width=11.0)
code("""BEGIN IMMEDIATE;
    n = SELECT COUNT(*) FROM SELECTIONS
        WHERE ROUND_ID=? AND TOPIC_ID=? AND STATUS='confirmed';
    IF n < MAX_NUM:
        INSERT INTO SELECTIONS(..., STATUS='confirmed');
        log_event('select', 'confirmed');
    ELSE IF WAITLIST_ON:
        INSERT INTO SELECTIONS(..., STATUS='waiting', QUEUE_NO=n+1);
        log_event('select', 'waiting');
    ELSE:
        ROLLBACK; 返回“已满员”;
    COMMIT;""")
p("数据库层再用 `(ROUND_ID, USER_ID) WHERE STATUS='confirmed'` 部分唯一索引兜底：即使业务判断被绕过，"
  "重复确认也会触发 `IntegrityError`，被捕获后回滚并给出中文提示。")

h3("3.4.2 四种策略与多志愿分配")
p("四种策略由 `ROUNDS.RULE` 驱动，切换策略不需要改代码。先到先得按提交时间录取；绩点优先在放行前统计"
  "“同样能看到该题、尚无有效记录且绩点更高”的同学数量；多志愿按志愿序号依次处理；抽签用公开种子洗牌。")
caption("表 3-7　四种选题策略对比")
table(
    ["策略", "学生动作", "录取规则", "适用场景"],
    [
        ["先到先得", "直接选题", "按提交时间，满员转候补", "拼手速、流程简单"],
        ["绩点优先", "直接选题", "高绩点未选时，低绩点等待", "成绩导向"],
        ["多志愿分配", "填 1~N 个志愿", "按志愿序 + 绩点，未命中最小志愿转候补", "兼顾意愿与公平"],
        ["随机抽签", "报名（可多题）", "公开种子洗牌，每人最多中 1 题", "完全随机、可审计"],
    ],
    widths=[3.0, 3.6, 6.8, 2.6], align_center=False,
)
p("多志愿分配分两轮：第一轮按 `WISH_NO` 升序、`GPA` 降序遍历，命中即确认；第二轮处理剩余记录——"
  "已命中其他志愿的作废，未命中的取“志愿序号最小的一条”转候补，其余作废。这里有一个容易被忽略的边界："
  "学生可能只填第二志愿而不填第一志愿，若只判断 `WISH_NO == 1`，该记录会永远停留在“志愿待分配”。"
  "实现中改为按学生分组、取最小志愿序号，修复了该问题（回归第 21 组有专门断言）。")
code("""stats = 各题已有 confirmed 数（防止跨阶段叠加超配）
第一轮：for w in 按(WISH_NO, GPA DESC)排序的志愿:
    题已关闭 → cancelled（留痕）
    该生未分配 且 stats[t] < MAX_NUM → confirmed, stats[t] += 1
第二轮：rest = 未命中且未作废的记录
    for 已分配学生: 其余志愿 → cancelled
    for 未分配学生: 取 WISH_NO 最小的一条 → waiting(QUEUE_NO+1)
                    其余 → cancelled""")

h3("3.4.3 随机抽签可复现")
p("抽签的公平性来自“种子公开 + 输入顺序固定 + 算法确定”。种子由教师配置或按轮次自动生成，写入每条抽签日志的备注；"
  "任何同学拿到种子与报名名单，都可用同一 Python 版本复现结果。")
code("""seed = rnd['LOTTERY_SEED'] or f"{rnd['ID']}-{rnd['ROUND_NAME']}"
rng = random.Random(seed)
entries.sort(key=lambda e: (-e['GPA'], e['ID']))   # 固定输入顺序
rng.shuffle(entries)                               # 洗牌
for i, e in enumerate(entries):
    if i < MAX_NUM and 该生未在其他题中签: confirmed
    elif 该生已中签: cancelled（每人限 1 题）
    else: waiting（候补）""")

h3("3.4.4 退选锁定与教师代退闭环")
p("这是本次设计中最重要的补全。原实现只做了“次数上限 + 截止锁定”，学生被挡住后系统提示“联系教师”，"
  "但教师端没有入口，流程断在系统外。现在增加申请与审批两条路径，并让它们与直接退选共用同一个出口函数。")
figure(os.path.join(FIG, "3-5-cancel-loop.png"), "图 3-6　退选锁定闭环流程图", width=13.0)
p("`close_selection` 在同一个事务内完成四件事：把记录置为 `cancelled` 并记录取消时间；按 `PROMOTE_MODE` 递补候补；"
  "写入操作日志；给学生发站内通知。教师直接“代退”与学生申请被同意都走这个函数，因此不会出现“取消了但没递补”"
  "或“递补了但没通知”的半完成状态。")

h3("3.4.5 前置要求核验与递补")
p("课题的前置要求以 JSON 保存（如 `{\"数据结构\": 75}`），教师录入成绩后生效。学生在浏览时逐题核验，"
  "不满足会显示缺失课程与实际分数，并在确认框中二次警告；教师监控页用同一函数核验，可一键退课。"
  "递补按 `QUEUE_NO` 取候补首位，支持自动、手动与不递补三种模式。")

h2("3.5 数据管理说明")
p("（1）存储方式。系统使用 SQLite 单文件数据库，开启 WAL（写前日志）模式，读写可以并行；"
  "数据库文件、WAL 文件与共享内存文件均位于 `src/` 目录，备份时只需复制 `topicgo.db` 或执行 `VACUUM INTO`。")
p("（2）访问方式。`db.py` 统一提供连接：每次请求新建连接，设置 `timeout=30`、`foreign_keys=ON`、"
  "`isolation_level=None`，由业务代码显式 `BEGIN IMMEDIATE / COMMIT / ROLLBACK`；所有 SQL 使用 `?` 参数化，"
  "避免注入。")
p("（3）数据完整性。除主键与外键约束外，用两个部分唯一索引保证“一人一轮一题”“一人一课一成绩”“一记录一待办申请”；"
  "所有删除均为软删除（`FLAG=0`），历史记录与日志永不物理删除。")
p("（4）迁移与兼容。`migrate()` 在启动时检查列与表是否存在，缺失则 `ALTER TABLE`/`CREATE TABLE` 补齐，"
  "老版本数据库可以直接升级，不需要手工改表。")
p("（5）容量估算。按 1 位教师、100 名学生、30 个课题、4 个轮次估算，`SELECTIONS` 约 400 行、"
  "`SELECTION_EVENTS` 约 2000 行、`NOTICES` 约 1000 行，数据库大小在 1 MB 量级，SQLite 完全够用。")

# ---------- 4 ----------
h1("4 实现与测试")
h2("4.1 实现环境和代码管理")
caption("表 4-1　实现环境")
table(
    ["项", "配置"],
    [
        ["操作系统", "macOS 14 / Windows 11（开发），Linux（测试与演示）"],
        ["语言与运行时", "Python 3.12.16"],
        ["Web 框架", "Flask 3.1.3 + Jinja2 3.1 + Werkzeug 3.1"],
        ["数据库", "SQLite 3.53.1（WAL 模式）"],
        ["前端", "HTML5 + CSS3 + 原生 JavaScript（无构建链）"],
        ["测试", "纯 Python 脚本 + urllib（163 + 22 项断言、并发脚本、非功能 14 项）"],
        ["版本管理", "Git，托管于 CNB（https://cnb.cool/jxh-U202414904/software-engineering）"],
    ],
    widths=[4.0, 12.0], align_center=False,
)
figure(os.path.join(FIG, "4-1-git-log.png"), "图 4-1　版本库提交记录（git log --oneline）", width=16.0)
p("代码管理遵循三条约定：主分支保持可运行，功能在本地完成后一次性提交；提交信息使用"
  "feat/fix/docs/test/chore: 简述；每次提交只做一件事，便于回滚。图 4-1 为仓库最近 14 条提交记录。")
p("编码规范方面：后端遵循 PEP 8，函数职责单一，路由只做参数校验与流程编排，复杂逻辑下沉到工具函数；"
  "所有 SQL 参数化；所有写操作显式事务；模板中不写业务判断之外的 Python 逻辑；中文文案统一使用“退选/代退/撤销”三个词，"
  "避免同义混用。")

h2("4.2 关键函数说明")
p("系统共有 60 余个函数，表 4-2 列出承担核心职责的 12 个。函数之间是“路由 → 工具函数 → 数据访问”的单向调用关系，"
  "工具函数不依赖请求上下文（除 `session` 相关），便于单独测试。")
caption("表 4-2　关键函数说明")
table(
    ["函数", "职责", "关键点"],
    [
        ["`login_required(*roles)`", "登录与角色校验装饰器", "未登录跳登录页；角色不符提示“无权限访问”"],
        ["`active_round(conn, teacher_id)`", "取教师当前开放轮次", "状态 open 且在时间窗内，按 ID 倒序取最新"],
        ["`log_event(...)`", "写操作日志", "记录动作、前后状态、志愿/候补号、操作人、备注"],
        ["`notify(...)`", "写站内通知", "退课/代退/递补/撤销/申请结果统一入口"],
        ["`prereq_check(...)`", "前置要求核验", "返回是否满足与缺失明细，浏览/选题/监控共用"],
        ["`promote_next(...)`", "候补自动递补", "取队列首位置 confirmed，写日志并通知学生"],
        ["`close_selection(...)`", "关闭选题记录（唯一出口）", "取消 + 日志 + 递补 + 通知 + 关闭僵尸申请，同一事务"],
        ["`student_select(topic_id)`", "直接选题", "四重过滤 + 绩点放行 + 事务内判容量 + 满员转候补"],
        ["`student_cancel(selection_id)`", "学生自助退选", "次数/锁定/原因三重校验，调用 `close_selection`"],
        ["`teacher_cancel_request(req_id)`", "审批退选申请", "事务内二次校验 pending，同意/驳回均通知"],
        ["`teacher_allocate(round_id)`", "多志愿统一分配", "两轮处理，按最小志愿号转候补，防跨阶段超配"],
        ["`teacher_lottery(round_id)`", "公开抽签", "固定输入顺序 + 公开种子洗牌，每人最多中 1 题"],
    ],
    widths=[5.4, 5.2, 5.4], align_center=False,
)

_h = h3("4.2.1 主要界面运行效果")
_h.paragraph_format.page_break_before = True
p("系统已在本地与临时公网地址实际运行，下面给出主要界面截图。截图数据来自演示库（2 位教师、12 名学生、3 个班级），"
  "账号与验收材料一致，可直接对照复现。")
figure(os.path.join(SHOT, "01-login.png"), "图 4-2　系统登录页", width=12.5)
figure(os.path.join(SHOT, "03-student-browse.png"), "图 4-3　学生浏览题库：名额、面向班级与前置要求", width=14.5)
p("图 4-4 是本次补全的退选锁定场景：学生本轮已退选 2 次，页面提示“次数已用完”并展开申请表单；"
  "提交后，“我的退选申请”区域显示待处理状态，历史记录仍保留两次退选痕迹。")
figure(os.path.join(SHOT, "11-student-cancel-locked.png"), "图 4-4　退选次数用尽后的在线申请入口", width=14.5)
p("图 4-5 是教师端监控页：左侧导航“选题监控”带红点，页面顶部独立展示待处理申请，教师可填写意见后同意或驳回；"
  "同意后系统自动取消记录、按策略递补并通知学生，整个过程不需要离开页面。")
figure(os.path.join(SHOT, "13-teacher-cancel-request.png"), "图 4-5　教师端退选申请审批与代退入口", width=14.0)
p("图 4-6 是学生收到的处理结果通知，包含教师意见与“可重新选题”的指引，学生据此完成改选。")
figure(os.path.join(SHOT, "15-student-notice.png"), "图 4-6　学生收到退选申请处理结果", width=14.0)

h2("4.3 测试计划和测试用例")
p("测试采用“黑盒为主、白盒为辅”的策略：功能测试用等价类划分与边界值法设计用例；并发与事务相关逻辑结合代码走查；"
  "每个缺陷修复后补充回归断言，防止再次退化。测试分为四层：手工用例、自动化回归、多用户协同、非功能检查。")
caption("表 4-3　测试计划")
table(
    ["阶段", "对象", "方法", "通过标准"],
    [
        ["单元/接口", "35 个路由与工具函数", "构造合法/非法参数，检查状态码与提示", "无 500，提示可读，数据一致"],
        ["功能", "三角色主干流程", "69 条手工用例（等价类 + 边界值 + 场景法）", "预期结果全部符合"],
        ["回归", "全部功能点", "163 项断言，覆盖 20 个测试组", "全部 PASS"],
        ["协同", "2 教师 + 12 学生 + 3 班级", "并发/隔离/一致性场景", "22 项断言全部 PASS"],
        ["并发", "6 线程抢 2 名额", "多线程同时 POST", "确认数 ≤ 上限，候补队列正常"],
        ["非功能", "效率/安全/报错/健壮性", "计时、越权、畸形输入、超长参数", "14 项检查全部 PASS"],
    ],
    widths=[2.6, 5.2, 5.4, 2.8], align_center=False,
)
p("表 4-4 摘录 10 条代表性用例，完整 69 条见 `验收/01-功能展示-测试用例.md`。")
caption("表 4-4　核心测试用例（节选）")
table(
    ["编号", "用例", "输入/步骤", "预期结果", "结果"],
    [
        ["TC-01", "正确登录", "zhangsan / 123456", "进入学生首页，显示姓名与角色", "通过"],
        ["TC-02", "未登记账号", "nobody / 123456", "提示“账号不存在”，停留登录页", "通过"],
        ["TC-03", "重复选题", "已有选题后再选 T02", "提示“每人限选 1 题”", "通过"],
        ["TC-04", "满员候补", "3 人选满 T01 后第 4 人选题", "提示已加入候补第 1 位", "通过"],
        ["TC-05", "退选自动递补", "T01 满员且有候补，已选学生退选", "候补首位变已确认，收到递补通知", "通过"],
        ["TC-06", "退选次数用尽", "上限 2 次，第 3 次退选", "拒绝并提示提交申请", "通过"],
        ["TC-07", "提交退选申请", "次数用尽后填写原因提交", "申请待处理，记录保持已确认", "通过"],
        ["TC-08", "教师同意申请", "监控页点“同意退选”", "记录取消、按策略递补、学生收通知", "通过"],
        ["TC-09", "并发抢题", "6 线程抢 2 名额", "恰好 2 人确认、4 人候补", "通过"],
        ["TC-10", "越权访问", "学生访问 /teacher/index", "提示无权限，回到学生首页", "通过"],
    ],
    widths=[1.6, 3.4, 5.4, 4.4, 1.2], align_center=False,
)
p("自动化测试覆盖 20 个测试组、163 项断言，执行结果如图 4-7。多用户协同测试模拟 2 位教师、12 名学生、3 个班级，"
  "22 项断言全部通过；并发测试 6 线程抢 2 个名额，确认数恰好为 2；非功能脚本 14 项检查全部通过。")
figure(os.path.join(FIG, "4-3-test-output.png"), "图 4-7　自动化回归测试执行输出（163 项通过）", width=16.0)

h2("4.4 结果分析")
p("（1）功能完整性。69 条手工用例全部通过，覆盖三角色的正常路径与异常路径；163 项回归断言覆盖 20 个测试组；"
  "22 项协同断言验证了多教师、多班级的数据隔离与最终一致性。主干流程“选题 → 退选/锁定 → 申请 → 审批/代退 → 递补 → 通知”已闭环。")
p("（2）性能。在本机（Flask 开发服务器 + SQLite WAL）对 14 个页面/接口各执行 30 次，中位数 0.91~3.49 ms，"
  "P95 均低于 10 ms；登录因 scrypt 哈希约 68~84 ms，仍远低于 500 ms 的目标。")
p("（3）并发。6 线程同时抢 2 个名额，结果为“已确认 2 人、候补 4 人”，未出现超卖或重复确认；"
  "多用户 E2E 中两名学生并发抢同一题也保持最终一致。")
p("（4）安全与健壮性。未登录、越权、缺少 CSRF 令牌的请求均被拦截；SQL 注入字符串被当作普通文本，"
  "搜索词中的脚本被 HTML 转义；非法路径参数返回 404、GET 调用 POST 接口返回 405；"
  "绩点、人数上限、轮次时间等非法输入均有中文提示且不产生 500。")
p("（5）缺陷情况。测试与验收阶段共发现 16 个缺陷，其中高严重度 3 个（新建轮次缺失、抽签重复中签、退选锁定流程不闭环），"
  "均已修复并纳入回归；仅保留 1 个低风险优化项（绩点并列时按学号先后放行），不影响正确性。")
caption("表 4-5　主要缺陷与修复")
table(
    ["编号", "缺陷", "严重度", "修复方式"],
    [
        ["BUG-03", "新登记教师无法新建轮次", "高", "新增 `action=create` 与轮次列表"],
        ["BUG-04", "抽签时一人多题可同时中签，触发唯一索引整批回滚", "高", "全局已中签集合，每人最多 1 题"],
        ["BUG-12", "退选锁定后教师无代退入口，流程断在系统外", "高", "申请表 + 审批 + 直接代退 + 通知，统一 `close_selection`"],
        ["BUG-13", "只填第二志愿会卡在待分配；关闭课题的报名卡在待抽签", "中", "按最小志愿号转候补；抽签前作废并通知"],
        ["BUG-14", "非法数字输入导致 500 或脏数据", "中", "`to_int` + 范围/时间校验，中文提示"],
        ["BUG-16", "撤销/递补/代退学生无感知", "中", "统一站内通知与未读红点"],
    ],
    widths=[1.8, 8.0, 1.8, 4.4], align_center=False,
)
p("综上，系统满足需求规格与验收标准，可以交付使用。")

# ---------- 5 ----------
h1("5 总结")
h2("5.1 用户反馈")
p("系统在开发完成后进行了小范围试用：一方面用 12 个学生账号 + 2 个教师账号做了多用户协同测试，"
  "模拟 3 个班级同时选题；另一方面在课堂上用演示账号向同组同学展示完整动线，收集了 6 条反馈。"
  "试用过程中的操作记录、日志与页面截图均保存在仓库 `wiki/screenshots/` 与 `验收/` 目录。")
caption("表 5-1　试用反馈与处理")
table(
    ["序号", "反馈/问题", "来源", "处理"],
    [
        ["1", "“退选两次后想换题，只能找老师，老师那边也没有按钮”", "学生试用", "新增退选申请与教师代退闭环（本次核心改动）"],
        ["2", "“不知道自己是候补第几，也不知道什么时候能轮到我”", "学生试用", "我的选题显示候补序号，递补成功发送站内通知"],
        ["3", "“志愿填错了想改，但页面只让联系老师”", "学生试用", "我的选题新增申请入口，教师同意后可重新提交"],
        ["4", "“教师监控页信息太多，找不到要处理的事”", "教师试用", "待办独立成卡片，导航加红点，工作台显示待办数量"],
        ["5", "“换轮次后上一轮的记录看不到了”", "教师试用", "我的选题改为跨轮次展示，历史退选记录保留"],
        ["6", "“导出的名单里怎么有两轮的人”", "教师试用", "CSV 导出限定本轮，避免重复统计"],
    ],
    widths=[1.2, 8.2, 2.4, 4.2], align_center=False,
)
p("试用反馈中，教师最认可的是“监控页能直接看到谁没选、谁需要处理”，学生最认可的是“名额和前置要求一眼可见”。"
  "受课程时间限制，试用以课堂演示和同学互测为主，尚未开展跨学院的大规模使用；后续计划在下一轮课设中继续收集真实反馈。")
figure(os.path.join(SHOT, "13a-teacher-todo.png"), "图 5-1　教师工作台：待办、班级与选题进度一览", width=13.0)

h2("5.2 全文总结")
p("本项目完成了一个可运行、可审计的学生选题系统，主要工作如下：")
bullets([
    "（1）完成需求分析：访谈 2 位教师与 5 名学生，用 NABCD 模型明确价值，形成 12 条功能需求与 5 条业务规则；",
    "（2）完成系统设计：给出分层架构、10 张数据表、8 个索引、四种选题策略与并发/退选/分配/抽签算法；",
    "（3）完成编码实现：35 个路由、18 个页面、4424 行主体功能代码，覆盖三角色完整链路；",
    "（4）完成测试与验收：69 条手工用例、163 项回归断言、22 项协同断言、并发与非功能检查全部通过；",
    "（5）完成流程补全：针对“退选锁定后无出口”的问题，新增退选申请、教师审批/代退与递补通知，使异常场景闭环；",
    "（6）完成过程管理：32 项任务、两人各 16 项，Git 提交规范、Issue 跟踪、阶段里程碑齐全。",
])
p("创新点主要体现在三处：一是把“四种公平策略”做成轮次级配置，教师无需改代码即可切换；"
  "二是用“事务 + 部分唯一索引”把并发超卖的概率降为零，并用公开种子让抽签结果可复现；"
  "三是把“退选锁定”从一条提示变成一条完整的申请—审批—递补—通知链路，体现了对真实业务异常的关注。")
p("不足与展望：当前为单机 SQLite，适合百人级课程，若扩展到全院选课需迁移到 PostgreSQL 并引入缓存；"
  "通知仅限站内，后续可接入邮件/企业微信；移动端为响应式页面，尚未做原生 App；"
  "绩点并列时按学号先后放行，可进一步引入随机或志愿优先级。")

# ---------- 6 ----------
h1("6 体会")
p("姬昕昊（组长，分工：项目管理、架构与数据库、选题/退选/分配/抽签算法、测试与验收材料）：", indent=True, bold=True)
p("这次项目让我印象最深的是“并发超卖”的修复。最初我写的是先 `SELECT COUNT(*)` 再 `INSERT`，自测时怎么都复现不了问题；"
  "直到用 6 个线程同时请求，才发现两个请求会读到相同的余量。改成 `BEGIN IMMEDIATE` 之后，我又担心性能，"
  "于是用脚本测了 30 次页面响应，确认中位数仍在 3 ms 以内。这个过程让我理解了“数据库事务不是理论，而是必须落到代码里的顺序”。")
p("第二个教训来自“退选锁定”。我原本认为“次数用尽就提示联系老师”已经足够，直到老师问了一句“老师怎么帮他退？”"
  "我才意识到流程断在了系统之外。补申请与审批时，我没有再加一套取消逻辑，而是把取消、日志、递补、通知收敛到一个"
  "`close_selection` 函数，避免四条路径各写一遍、漏掉其中一步。这件事让我明白：需求分析不仅要问“用户要做什么”，"
  "还要问“用户做不成时怎么办”。")
p("第三个收获是测试。只填第二志愿会卡在“志愿待分配”，是写回归用例时才发现的；如果只靠手工点页面，很难覆盖到。"
  "现在每修一个缺陷，我都会补一条断言，163 项断言里有 37 项是最后两周补的，它们让我敢在验收前继续改代码。")
p("王礼骐（组员，分工：需求调研、原型与页面、公告与导出、文档与发布）：", indent=True, bold=True)
p("我负责原型和页面，最大的体会是“原型不是画得好看就行”。第一版我把退选原因下拉框直接塞进表格，"
  "在 1440 像素下还能看，到了 1280 像素就挤成两行；后来改成“行内下拉 + 折叠申请表单”，才兼顾了信息密度和可操作性。"
  "教师监控页也是同样的问题：信息全放在一张表里，老师第一眼找不到待办，最后把“退选申请”单独做成卡片，"
  "并在导航上加了红点。")
p("第二个体会是文案一致性。系统里同时存在“退选”“撤销”“退课”“代退”四个动作，最初我在页面上混用，"
  "同学试用时问“撤销和退选有什么区别”。后来我们统一定义：学生自己操作叫“退选”，教师撤销误提交叫“撤销”，"
  "因前置要求退掉叫“退课”，教师替学生退叫“代退”，并在按钮和日志里保持一致。这个细节让我意识到，"
  "界面文案也是需求的一部分。")
p("第三个体会来自文档。验收材料里的数字前后改了三次：断言从 126 到 163、代码从 3917 到 4424、表从 9 到 10。"
  "每次改完代码都要回头核对文档，否则老师一问就会对不上。我后来把统计命令写进文档，验收时现场跑 `wc -l`，"
  "这比背数字更可靠。")

# ---------- 附录 ----------
h1("附录 A　关键源代码（节选）")
p("完整源码见仓库 `src/app.py`，以下仅列出与并发、退选闭环、分配与抽签相关的关键片段。", size=10.5)
code("""def close_selection(conn, sel, rnd, operator, remark, action="cancel", notice=None,
                    keep_request_id=None):
    \"\"\"关闭一条有效选题记录：取消 + 日志 + 递补 + 通知（同一事务）。\"\"\"
    ts = now_str()
    conn.execute(
        "UPDATE SELECTIONS SET STATUS='cancelled', CANCELLED_AT=?, QUEUE_NO=NULL,"
        " UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
        (ts, operator, ts, sel["ID"]))
    log_event(conn, sel["ROUND_ID"], sel["TOPIC_ID"], sel["USER_ID"], action,
              sel["STATUS"], "cancelled", wish_no=sel["WISH_NO"],
              queue_no=sel["QUEUE_NO"], operator=operator, remark=remark)
    # 同一记录上的其它待处理申请自动关闭，避免僵尸待办
    conn.execute(
        "UPDATE CANCEL_REQUESTS SET STATUS='rejected', HANDLE_REMARK=?, HANDLED_BY=?,"
        " HANDLED_AT=?, UPDATED_BY=?, UPDATED_AT=?"
        " WHERE SELECTION_ID=? AND STATUS='pending' AND ID<>?",
        ("选题记录已由其他操作取消，申请自动关闭", operator, ts, operator, ts,
         sel["ID"], keep_request_id or 0))
    promoted = None
    if sel["STATUS"] == "confirmed" and rnd:
        if rnd["PROMOTE_MODE"] == "auto":
            promoted = promote_next(conn, rnd, sel["TOPIC_ID"], operator="system")
        elif rnd["PROMOTE_MODE"] == "manual":
            log_event(conn, sel["ROUND_ID"], sel["TOPIC_ID"], None, "promote",
                      remark="名额待教师手动递补", operator="system")
    if notice:
        title, content, kind = notice
        notify(conn, sel["USER_ID"], title, content, kind=kind,
               topic_id=sel["TOPIC_ID"], operator=operator)
    return promoted""", size=9)

code("""@app.route("/teacher/cancel-request/<int:req_id>", methods=["POST"])
@login_required("teacher")
def teacher_cancel_request(req_id):
    ...
    conn.execute("BEGIN IMMEDIATE")
    fresh = conn.execute("SELECT STATUS FROM CANCEL_REQUESTS WHERE ID=?",
                         (req_id,)).fetchone()
    if not fresh or fresh["STATUS"] != "pending":
        conn.execute("ROLLBACK")
        flash("该申请已被处理，请刷新页面查看最新状态", "error")
    elif action == "approve":
        promoted = close_selection(
            conn, sel, rnd, session["user_name"],
            f"学生申请退选，教师同意｜学生说明：{req['REASON']}｜教师意见：{remark or '无'}",
            action="cancel", notice=notice, keep_request_id=req_id)
        conn.execute("UPDATE CANCEL_REQUESTS SET STATUS='approved', ... WHERE ID=?", ...)
        log_event(conn, req["ROUND_ID"], req["TOPIC_ID"], req["USER_ID"], "approve", ...)
        conn.execute("COMMIT")""", size=9)

code("""# 学生选题：事务内判容量，满员转候补
conn.execute("BEGIN IMMEDIATE")
confirmed = conn.execute(
    "SELECT COUNT(*) c FROM SELECTIONS WHERE ROUND_ID=? AND TOPIC_ID=? AND STATUS='confirmed'",
    (rnd["ID"], topic_id)).fetchone()["c"]
if confirmed < topic["MAX_NUM"]:
    conn.execute("INSERT INTO SELECTIONS(... STATUS='confirmed' ...)")
    log_event(conn, rnd["ID"], topic_id, user["ID"], "select", None, "confirmed",
              operator=user["USER_NAME"])
    conn.execute("COMMIT")""", size=9)

h1("附录 B　部署与运行说明")
p("（1）安装依赖：在项目根目录执行 `uv venv .venv` 与 `uv pip install --python .venv/bin/python -r src/requirements.txt`；"
  "（2）启动服务：`.venv/bin/python src/app.py`，浏览器访问 `http://localhost:5000`；"
  "（3）演示账号（初始密码均为 `123456`）：学生 `zhangsan`、教师 `teacher`、管理员 `admin`；"
  "（4）重置演示数据：停止服务后删除 `src/topicgo.db*`，重启即自动建表并写入种子数据；"
  "（5）临时公网演示：启动服务后执行 `cloudflared tunnel --url http://127.0.0.1:5000`，"
  "当前地址为 `https://toe-computed-handy-jazz.trycloudflare.com`（隧道进程停止后失效）。"
  "（6）固定地址部署：仓库提供 Dockerfile 与 render.yaml，按 DEPLOY.md 在 Render 创建免费 Web Service，"
  "可获得 `https://<service>.onrender.com` 的固定地址；免费实例闲置 15 分钟后休眠，首次访问约需 1 分钟唤醒。", indent=True, align=WD_ALIGN_PARAGRAPH.LEFT)

doc.save(OUT)
print("saved:", OUT)
