"""学生选题系统 TopicGo - 应用入口与路由

角色与职责：
  admin   系统管理员：仅负责平台账户管理（登记/停用/重置密码/删除），不涉及课程业务
  teacher 教师：仅管理自己发布的课题、自己的选题轮次、自己学生的选题过程
  student 学生：仅能看到自己指导教师发布且已开放的课题

策略模式：first_come 先到先得 / gpa_priority 绩点优先 /
          multi_wish 多志愿分配 / lottery 随机抽签（种子公开可复现）
过程留痕：选题、退选、递补、分配、抽签全部写入 SELECTION_EVENTS 操作日志
"""
import csv
import hmac
import io
import json
import os
import random
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import quote, urlparse

from flask import (Flask, flash, redirect, render_template, request,
                   Response, send_file, session, url_for)
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

from db import BASE_DIR, get_conn, init_db, now_str

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "topicgo-secret-key")

init_db()


# ---------------- CSRF 防护 ----------------
# 每个会话生成一次性令牌，写入所有表单隐藏域；POST 时校验，防止跨站请求伪造

@app.before_request
def csrf_protect():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(16)
    if request.method != "POST":
        return None
    token = session.get("_csrf_token", "")
    form_token = request.form.get("_csrf_token", "")
    if not form_token or not hmac.compare_digest(token, form_token):
        flash("表单校验失败（CSRF），请返回页面重试", "error")
        ref = request.referrer
        if ref and urlparse(ref).netloc in ("", request.host):
            return redirect(ref)
        return redirect(url_for("index"))
    return None


@app.context_processor
def inject_csrf():
    def csrf_token():
        return session.get("_csrf_token", "")

    def csrf_input():
        # Markup 避免被 Jinja 自动转义，确保渲染为真实的隐藏表单域
        return Markup('<input type="hidden" name="_csrf_token" value="{}">'
                      ).format(session.get("_csrf_token", ""))

    return dict(csrf_token=csrf_token, csrf_input=csrf_input)

RULE_NAMES = {
    "first_come": "先到先得",
    "gpa_priority": "绩点优先",
    "multi_wish": "多志愿分配",
    "lottery": "随机抽签",
}
ACTION_NAMES = {
    "select": "选题/报名",
    "cancel": "退选",
    "kick": "教师代退",
    "promote": "递补",
    "allocate": "志愿分配",
    "lottery": "抽签",
    "request": "退选申请",
    "approve": "同意代退",
    "reject": "驳回申请",
}
# 有效选题状态（cancelled 以外的状态均占用/待处理名额）
ACTIVE_STATUSES = ("confirmed", "waiting", "wish", "entry")
REQUEST_STATUS_NAMES = {"pending": "待处理", "approved": "已同意", "rejected": "已驳回"}
# 退选原因（必选，写入操作日志，便于教师审计）
CANCEL_REASONS = [
    "选错了，想换题",
    "题目难度或方向不适合",
    "与其它课程/安排冲突",
    "经与指导教师沟通后退选",
]


def parse_dt(s):
    """解析 'YYYY-MM-DD HH:MM:SS' 时间串，失败返回 None"""
    try:
        return datetime.strptime(s or "", "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def student_cancel_used(conn, round_id, user_id, user_name):
    """该生在本轮已使用的退选次数（只统计学生本人发起的退选，教师撤销不计入）"""
    return conn.execute(
        "SELECT COUNT(*) c FROM SELECTION_EVENTS"
        " WHERE ROUND_ID=? AND USER_ID=? AND ACTION='cancel' AND OPERATOR=?",
        (round_id, user_id, user_name)).fetchone()["c"]
STATUS_NAMES = {
    "confirmed": "已确认",
    "waiting": "候补中",
    "wish": "志愿待分配",
    "entry": "已报名待抽签",
    "cancelled": "已退选",
}


def to_int(value, default=0):
    """表单数字安全转换：非法输入返回默认值，避免 500"""
    try:
        return int(str(value).strip())
    except (ValueError, TypeError, AttributeError):
        return default


def round_is_open(rnd):
    """轮次是否仍在开放时间窗内（用于退选/申请等写操作的服务端校验）"""
    if not rnd or rnd["STATUS"] != "open":
        return False
    now = now_str()
    return (rnd["START_AT"] or "") <= now <= (rnd["END_AT"] or "")


def notify(conn, user_id, title, content, kind="system", topic_id=None, operator="system"):
    """写一条站内通知（学生端可见，退课/递补/撤销/申请结果均通过它告知学生）"""
    ts = now_str()
    conn.execute(
        "INSERT INTO NOTICES(USER_ID,TITLE,CONTENT,KIND,TOPIC_ID,PUBLISHED_AT,CREATED_BY,CREATED_AT,FLAG)"
        " VALUES(?,?,?,?,?,?,?,?,1)",
        (user_id, title, content, kind, topic_id, ts, operator, ts))


def close_selection(conn, sel, rnd, operator, remark, action="cancel", notice=None,
                    keep_request_id=None):
    """关闭一条有效选题记录（退选/撤销/代退共用）：

    1) 记录置 cancelled 并保留 CANCELLED_AT（不物理删除）；
    2) 写操作日志（含原因）；
    3) 若原状态为 confirmed 且递补策略为 auto，则候补首位自动递补并通知；
    4) notice=(标题, 正文, 类型) 时给学生发站内通知；
    5) 该记录上其它待处理申请自动关闭（keep_request_id 用于正在处理的申请）。
    返回被递补的学生记录（无则 None）。
    """
    ts = now_str()
    # 记录已被取消，同一记录上的其它待处理申请不再有意义，自动关闭避免僵尸待办
    conn.execute(
        "UPDATE CANCEL_REQUESTS SET STATUS='rejected', HANDLE_REMARK=?, HANDLED_BY=?,"
        " HANDLED_AT=?, UPDATED_BY=?, UPDATED_AT=?"
        " WHERE SELECTION_ID=? AND STATUS='pending' AND ID<>?",
        ("选题记录已由其他操作取消，申请自动关闭", operator, ts, operator, ts,
         sel["ID"], keep_request_id or 0))
    conn.execute(
        "UPDATE SELECTIONS SET STATUS='cancelled', CANCELLED_AT=?, QUEUE_NO=NULL,"
        " UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
        (ts, operator, ts, sel["ID"]))
    log_event(conn, sel["ROUND_ID"], sel["TOPIC_ID"], sel["USER_ID"], action,
              sel["STATUS"], "cancelled", wish_no=sel["WISH_NO"],
              queue_no=sel["QUEUE_NO"], operator=operator, remark=remark)
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
    return promoted


# ---------------- 工具 ----------------

def login_required(*roles):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if roles and session.get("role") not in roles:
                flash("无权限访问", "error")
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return wrapper
    return deco


def current_user(conn):
    return conn.execute("SELECT * FROM USERS WHERE ID=?", (session["user_id"],)).fetchone()


def active_round(conn, teacher_id):
    """指定教师当前开放的选题轮次（状态 open 且处于时间窗口内，取最新一个）"""
    if not teacher_id:
        return None
    now = now_str()
    return conn.execute(
        "SELECT * FROM ROUNDS WHERE TEACHER_ID=? AND STATUS='open' AND START_AT<=? AND END_AT>=? AND FLAG=1"
        " ORDER BY ID DESC",
        (teacher_id, now, now),
    ).fetchone()


def log_event(conn, round_id, topic_id, user_id, action,
              from_status=None, to_status=None, wish_no=None,
              queue_no=None, operator="system", remark=None):
    """写入操作日志（留痕，保证过程可追溯、可审计）"""
    ts = now_str()
    conn.execute(
        "INSERT INTO SELECTION_EVENTS(ROUND_ID,TOPIC_ID,USER_ID,ACTION,FROM_STATUS,TO_STATUS,"
        "WISH_NO,QUEUE_NO,OPERATOR,REMARK,OPERATED_AT,CREATED_BY,CREATED_AT,FLAG)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        (round_id, topic_id, user_id, action, from_status, to_status, wish_no, queue_no,
         operator, remark, ts, operator, ts),
    )


def topic_stats(conn, round_id, status="confirmed"):
    rows = conn.execute(
        "SELECT TOPIC_ID, COUNT(*) c FROM SELECTIONS WHERE ROUND_ID=? AND STATUS=? GROUP BY TOPIC_ID",
        (round_id, status),
    ).fetchall()
    return {r["TOPIC_ID"]: r["c"] for r in rows}


def user_active_selection(conn, round_id, user_id):
    """用户在当前轮次的有效记录（confirmed/waiting/wish/entry）"""
    return conn.execute(
        "SELECT s.*, t.TOPIC_NAME, t.TOPIC_CODE FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID"
        " WHERE s.ROUND_ID=? AND s.USER_ID=? AND s.STATUS IN('confirmed','waiting','wish','entry')",
        (round_id, user_id),
    ).fetchall()


def class_allowed(rnd, class_name):
    """分组轮选：班级是否在允许范围内"""
    if not rnd or not rnd["ALLOW_CLASSES"]:
        return True
    allowed = [c.strip() for c in rnd["ALLOW_CLASSES"].split(",") if c.strip()]
    return class_name in allowed


def promote_next(conn, rnd, topic_id, operator="system"):
    """候补递补：队列第一位递补为 confirmed，并记录日志"""
    nxt = conn.execute(
        "SELECT * FROM SELECTIONS WHERE ROUND_ID=? AND TOPIC_ID=? AND STATUS='waiting'"
        " ORDER BY QUEUE_NO LIMIT 1",
        (rnd["ID"], topic_id),
    ).fetchone()
    if not nxt:
        return None
    ts = now_str()
    conn.execute(
        "UPDATE SELECTIONS SET STATUS='confirmed', QUEUE_NO=NULL, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
        (operator, ts, nxt["ID"]),
    )
    log_event(conn, rnd["ID"], topic_id, nxt["USER_ID"], "promote",
              "waiting", "confirmed", queue_no=nxt["QUEUE_NO"], operator=operator,
              remark="退选自动递补" if operator != "teacher" else "教师手动递补")
    topic = conn.execute("SELECT TOPIC_NAME, TOPIC_CODE FROM TOPICS WHERE ID=?",
                         (topic_id,)).fetchone()
    if topic:
        notify(conn, nxt["USER_ID"],
               f"《{topic['TOPIC_NAME']}》候补递补成功",
               f"同学你好，你在《{topic['TOPIC_NAME']}》（{topic['TOPIC_CODE']}）的候补已递补成功，"
               f"当前状态为「已确认」。\n\n【下一步】请到「我的选题」查看，并按教师要求开始课题。",
               kind="promote", topic_id=topic_id, operator=operator)
    return nxt


def own_round(conn):
    """教师自己的轮次（无则返回最新创建的占位）"""
    rnd = conn.execute(
        "SELECT * FROM ROUNDS WHERE TEACHER_ID=? AND FLAG=1 ORDER BY ID DESC",
        (session["user_id"],)).fetchone()
    return rnd


def teacher_classes(conn, teacher_id):
    """教师管理的班级（由其名下学生的班级派生，含人数）"""
    rows = conn.execute(
        "SELECT CLASS_NAME, COUNT(*) c FROM USERS"
        " WHERE ROLE='student' AND ADVISOR_ID=? AND FLAG=1 AND STATUS='active'"
        " AND CLASS_NAME IS NOT NULL AND CLASS_NAME != ''"
        " GROUP BY CLASS_NAME ORDER BY CLASS_NAME",
        (teacher_id,)).fetchall()
    return [dict(name=r["CLASS_NAME"], count=r["c"]) for r in rows]


def topic_visible(topic, class_name):
    """课题是否对该班级可见：TARGET_CLASSES 为空=面向教师名下所有班级"""
    if not topic["TARGET_CLASSES"]:
        return True
    allowed = [c.strip() for c in topic["TARGET_CLASSES"].split(",") if c.strip()]
    return class_name in allowed


def student_scores(conn, user_id):
    """学生各科成绩 {课程名: 分数}（教师端录入）"""
    rows = conn.execute(
        "SELECT c.COURSE_NAME, s.SCORE FROM SCORES s JOIN COURSES c ON c.ID=s.COURSE_ID"
        " WHERE s.USER_ID=? AND s.FLAG=1 AND c.FLAG=1",
        (user_id,)).fetchall()
    return {r["COURSE_NAME"]: r["SCORE"] for r in rows}


def prereq_rules(topic):
    """课题前置规则 {"课程名": 最低分}；无规则返回空 dict"""
    if not topic or not topic["PREREQ_RULES"]:
        return {}
    try:
        rules = json.loads(topic["PREREQ_RULES"])
        return rules if isinstance(rules, dict) else {}
    except (ValueError, TypeError):
        return {}


def prereq_check(conn, user_id, topic):
    """前置校验：返回 (是否满足, 未满足明细[(课程名, 要求分, 实际分或None)])"""
    missing = []
    scores = student_scores(conn, user_id)
    for cname, need in prereq_rules(topic).items():
        actual = scores.get(cname)
        if actual is None or actual < need:
            missing.append((cname, need, actual))
    return (not missing), missing


# ---------------- 认证 ----------------

@app.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session["role"] == "student":
        return redirect(url_for("student_index"))
    if session["role"] == "teacher":
        return redirect(url_for("teacher_index"))
    return redirect(url_for("admin_users"))


# ---------------- 学生端：首页（Dashboard） ----------------

@app.route("/student/index")
@login_required("student")
def student_index():
    conn = get_conn()
    user = current_user(conn)
    advisor = conn.execute("SELECT * FROM USERS WHERE ID=?", (user["ADVISOR_ID"],)).fetchone() \
        if user["ADVISOR_ID"] else None
    rnd = active_round(conn, user["ADVISOR_ID"])
    mine = user_active_selection(conn, rnd["ID"], session["user_id"]) if rnd else []
    confirmed = next((m for m in mine if m["STATUS"] == "confirmed"), None)
    waiting = next((m for m in mine if m["STATUS"] == "waiting"), None)
    wishes = [m for m in mine if m["STATUS"] in ("wish", "entry")]
    topic_total = topic_confirmed = 0
    if advisor:
        topic_total = conn.execute(
            "SELECT COUNT(*) c FROM TOPICS WHERE TEACHER_ID=? AND STATUS='open' AND FLAG=1",
            (user["ADVISOR_ID"],)).fetchone()["c"]
        topic_confirmed = conn.execute(
            "SELECT COUNT(*) c FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID"
            " WHERE t.TEACHER_ID=? AND s.STATUS='confirmed' AND s.ROUND_ID=?",
            (user["ADVISOR_ID"], rnd["ID"] if rnd else 0)).fetchone()["c"]
    announcements = conn.execute(
        "SELECT * FROM ANNOUNCEMENTS WHERE FLAG=1 AND TEACHER_ID=? ORDER BY PUBLISHED_AT DESC LIMIT 5",
        (user["ADVISOR_ID"],)).fetchall() if advisor else []
    # 站内通知（退课提示等）：未读置顶展示
    notices = conn.execute(
        "SELECT * FROM NOTICES WHERE USER_ID=? AND FLAG=1 ORDER BY ID DESC LIMIT 3",
        (session["user_id"],)).fetchall()
    conn.close()
    return render_template("student/index.html", user=user, advisor=advisor, round=rnd,
                           confirmed=confirmed, waiting=waiting, wishes=wishes,
                           topic_total=topic_total, topic_confirmed=topic_confirmed,
                           announcements=announcements, notices=notices,
                           rule_name=RULE_NAMES.get(rnd["RULE"] if rnd else "", ""))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("username", "").strip()
        pwd = request.form.get("password", "")
        conn = get_conn()
        user = conn.execute(
            "SELECT * FROM USERS WHERE (USER_NAME=? OR USER_NO=?) AND FLAG=1", (name, name)
        ).fetchone()
        conn.close()
        if not user:
            flash("账号不存在，如有疑问请联系管理员", "error")
        elif user["STATUS"] != "active":
            flash("该账号已被停用，请联系系统管理员", "error")
        elif check_password_hash(user["PASSWORD_HASH"], pwd):
            csrf_token = session.get("_csrf_token")  # 登录后保留 CSRF 令牌，避免表单失效
            session.clear()
            session.update(user_id=user["ID"], user_name=user["USER_NAME"],
                           real_name=user["REAL_NAME"], role=user["ROLE"])
            if csrf_token:
                session["_csrf_token"] = csrf_token
            return redirect(url_for("index"))
        else:
            flash("账号或密码错误", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- 学生端 ----------------

@app.route("/student/browse")
@login_required("student")
def student_browse():
    kw = request.args.get("kw", "").strip()
    tag = request.args.get("tag", "").strip()
    diff = request.args.get("diff", "").strip()
    conn = get_conn()
    user = current_user(conn)
    advisor = conn.execute("SELECT * FROM USERS WHERE ID=?", (user["ADVISOR_ID"],)).fetchone() \
        if user["ADVISOR_ID"] else None
    rnd = active_round(conn, user["ADVISOR_ID"])
    # 学生只能看到自己指导教师发布的课题；老师未开放轮次时一律不可见（四重过滤）
    sql = "SELECT * FROM TOPICS WHERE STATUS='open' AND FLAG=1 AND TEACHER_ID=?"
    args = [user["ADVISOR_ID"]]
    if kw:
        sql += " AND (TOPIC_NAME LIKE ? OR TOPIC_DESC LIKE ?)"
        args += [f"%{kw}%", f"%{kw}%"]
    if tag:
        sql += " AND TAG=?"
        args.append(tag)
    if diff:
        sql += " AND DIFFICULTY=?"
        args.append(diff)
    topics = conn.execute(sql + " ORDER BY TOPIC_CODE", args).fetchall() if (advisor and rnd) else []
    # 按课题面向班级二次过滤（TARGET_CLASSES）
    topics = [t for t in topics if topic_visible(t, user["CLASS_NAME"])]
    stats = topic_stats(conn, rnd["ID"]) if rnd else {}
    wishes = topic_stats(conn, rnd["ID"], "wish") if rnd else {}
    entries = topic_stats(conn, rnd["ID"], "entry") if rnd else {}
    mine = user_active_selection(conn, rnd["ID"], session["user_id"]) if rnd else []
    tags = [r["TAG"] for r in conn.execute(
        "SELECT DISTINCT TAG FROM TOPICS WHERE TAG IS NOT NULL AND TEACHER_ID=?",
        (user["ADVISOR_ID"],))] if advisor else []
    class_ok = class_allowed(rnd, user["CLASS_NAME"])
    # 前置要求核验：学生选课前即可看到自己是否满足
    prereq_map = {t["ID"]: prereq_check(conn, user["ID"], t) for t in topics}
    conn.close()
    return render_template("student/browse.html", topics=topics, stats=stats, wishes=wishes,
                           entries=entries, mine=mine, round=rnd, tags=tags, kw=kw, tag=tag,
                           diff=diff, rule_name=RULE_NAMES.get(rnd["RULE"] if rnd else "", ""),
                           class_ok=class_ok, advisor=advisor, prereq_map=prereq_map)


@app.route("/student/select/<int:topic_id>", methods=["POST"])
@login_required("student")
def student_select(topic_id):
    """先到先得 / 绩点优先 / 分组轮选模式下的直接选题"""
    conn = get_conn()
    user = current_user(conn)
    rnd = active_round(conn, user["ADVISOR_ID"])
    if not rnd:
        flash("你的指导教师尚未开放选题，请等待老师发布", "error")
        conn.close()
        return redirect(url_for("student_browse"))
    if rnd["RULE"] in ("multi_wish", "lottery"):
        tip = "「志愿填报」" if rnd["RULE"] == "multi_wish" else "「报名」"
        flash(f"本轮为{RULE_NAMES[rnd['RULE']]}模式，请使用{tip}", "error")
        conn.close()
        return redirect(url_for("student_wish"))
    if not class_allowed(rnd, user["CLASS_NAME"]):
        flash(f"本轮仅限班级 [{rnd['ALLOW_CLASSES']}] 选题", "error")
        conn.close()
        return redirect(url_for("student_browse"))
    # 先校验题目归属与可见性，再校验是否已有选题（错误提示更准确）
    topic = conn.execute(
        "SELECT * FROM TOPICS WHERE ID=? AND STATUS='open' AND FLAG=1 AND TEACHER_ID=?",
        (topic_id, user["ADVISOR_ID"])).fetchone()
    if not topic:
        flash("题目不存在、已关闭或不属于你的指导教师", "error")
        conn.close()
        return redirect(url_for("student_browse"))
    if not topic_visible(topic, user["CLASS_NAME"]):
        flash("该题目未面向你的班级开放", "error")
        conn.close()
        return redirect(url_for("student_browse"))
    if user_active_selection(conn, rnd["ID"], user["ID"]):
        flash("每人限选 1 题，你已有选题记录", "error")
        conn.close()
        return redirect(url_for("student_browse"))
    if rnd["RULE"] == "gpa_priority":
        # 只统计「同样能看到该题、且尚未有有效记录」的高绩点同学，
        # 避免其他班级/已选题的同学把名额长期卡住（否则低绩点同学永远选不了）
        rows = conn.execute(
            "SELECT u.GPA, u.CLASS_NAME FROM USERS u WHERE u.ROLE='student' AND u.GPA>? AND u.FLAG=1"
            " AND u.STATUS='active' AND u.ADVISOR_ID=? AND NOT EXISTS(SELECT 1 FROM SELECTIONS s"
            " WHERE s.ROUND_ID=? AND s.USER_ID=u.ID AND s.STATUS IN ('confirmed','waiting','wish','entry'))",
            (user["GPA"], user["ADVISOR_ID"], rnd["ID"]),
        ).fetchall()
        higher = sum(1 for u in rows if topic_visible(topic, u["CLASS_NAME"]))
        if higher > 0:
            flash(f"绩点优先模式下，还有 {higher} 位绩点高于你的同学未选题，请稍候", "error")
            conn.close()
            return redirect(url_for("student_browse"))
    # 前置要求预警：不满足仍允许选（由教师按前置门槛决定是否退课），但明确提示
    ok, missing = prereq_check(conn, user["ID"], topic)
    if not ok:
        miss_text = "；".join(f"{c} 需≥{n}分，你为{a if a is not None else '未录入'}" for c, n, a in missing)
        flash(f"警告：你的成绩暂不满足《{topic['TOPIC_NAME']}》前置要求（{miss_text}），"
              f"教师核验后可能退课，请谨慎选择或改选匹配课题", "error")

    ts = now_str()
    try:
        conn.execute("BEGIN IMMEDIATE")  # 立即写锁，串行化并发选题
        confirmed = conn.execute(
            "SELECT COUNT(*) c FROM SELECTIONS WHERE ROUND_ID=? AND TOPIC_ID=? AND STATUS='confirmed'",
            (rnd["ID"], topic_id),
        ).fetchone()["c"]
        if confirmed < topic["MAX_NUM"]:
            conn.execute(
                "INSERT INTO SELECTIONS(ROUND_ID,TOPIC_ID,USER_ID,STATUS,SELECTED_AT,CREATED_BY,CREATED_AT,FLAG)"
                " VALUES(?,?,?,'confirmed',?,?,?,1)",
                (rnd["ID"], topic_id, user["ID"], ts, user["USER_NAME"], ts),
            )
            log_event(conn, rnd["ID"], topic_id, user["ID"], "select",
                      None, "confirmed", operator=user["USER_NAME"])
            conn.execute("COMMIT")
            flash(f"选题成功：{topic['TOPIC_NAME']}", "success")
        elif rnd["WAITLIST_ON"]:
            queue_no = conn.execute(
                "SELECT COALESCE(MAX(QUEUE_NO),0)+1 q FROM SELECTIONS"
                " WHERE ROUND_ID=? AND TOPIC_ID=? AND STATUS='waiting'",
                (rnd["ID"], topic_id),
            ).fetchone()["q"]
            conn.execute(
                "INSERT INTO SELECTIONS(ROUND_ID,TOPIC_ID,USER_ID,STATUS,QUEUE_NO,SELECTED_AT,CREATED_BY,CREATED_AT,FLAG)"
                " VALUES(?,?,?,'waiting',?,?,?,?,1)",
                (rnd["ID"], topic_id, user["ID"], queue_no, ts, user["USER_NAME"], ts),
            )
            log_event(conn, rnd["ID"], topic_id, user["ID"], "select",
                      None, "waiting", queue_no=queue_no, operator=user["USER_NAME"],
                      remark="满员入候补")
            conn.execute("COMMIT")
            flash(f"《{topic['TOPIC_NAME']}》已满员，你已加入候补第 {queue_no} 位", "success")
        else:
            conn.execute("ROLLBACK")
            flash("该题目已满员且候补未开启", "error")
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        flash("你已有该轮次的选题记录", "error")
    finally:
        conn.close()
    return redirect(url_for("student_browse"))


@app.route("/student/wish", methods=["GET", "POST"])
@login_required("student")
def student_wish():
    """多志愿填报 / 抽签报名"""
    conn = get_conn()
    user = current_user(conn)
    rnd = active_round(conn, user["ADVISOR_ID"])
    if not rnd or rnd["RULE"] not in ("multi_wish", "lottery"):
        flash("当前轮次不是志愿填报/报名模式", "error")
        conn.close()
        return redirect(url_for("student_browse"))
    if not class_allowed(rnd, user["CLASS_NAME"]):
        flash(f"本轮仅限班级 [{rnd['ALLOW_CLASSES']}] 参与", "error")
        conn.close()
        return redirect(url_for("student_browse"))
    topics = conn.execute(
        "SELECT * FROM TOPICS WHERE STATUS='open' AND FLAG=1 AND TEACHER_ID=? ORDER BY TOPIC_CODE",
        (user["ADVISOR_ID"],)).fetchall()
    topics = [t for t in topics if topic_visible(t, user["CLASS_NAME"])]

    if request.method == "POST":
        existing = user_active_selection(conn, rnd["ID"], user["ID"])
        if existing:
            flash("你已提交过志愿/报名，如需修改请联系教师撤销", "error")
            conn.close()
            return redirect(url_for("student_wish"))
        picked = request.form.getlist("wishes")  # ["3:T01", "1:T02"] 形式
        if not picked:
            flash("请至少选择 1 个志愿/报名题目", "error")
            conn.close()
            return redirect(url_for("student_wish"))
        if rnd["RULE"] == "multi_wish" and len(picked) > rnd["MAX_WISH"]:
            flash(f"每人最多填报 {rnd['MAX_WISH']} 个志愿", "error")
            conn.close()
            return redirect(url_for("student_wish"))
        # 服务端校验：数据格式、志愿序号范围、题目不重复（防篡改表单）
        max_wish = rnd["MAX_WISH"] or 3
        normalized = []
        for item in picked:
            parts = item.split(":")
            if len(parts) != 2 or not parts[1].strip():
                flash("志愿数据格式不正确，请刷新页面重试", "error")
                conn.close()
                return redirect(url_for("student_wish"))
            wish_no, code = parts[0].strip(), parts[1].strip()
            if rnd["RULE"] == "multi_wish":
                try:
                    wish_no_int = int(wish_no)
                except ValueError:
                    flash("志愿序号必须是数字", "error")
                    conn.close()
                    return redirect(url_for("student_wish"))
                if not 1 <= wish_no_int <= max_wish:
                    flash(f"志愿序号需在 1~{max_wish} 之间", "error")
                    conn.close()
                    return redirect(url_for("student_wish"))
            normalized.append((wish_no, code))
        codes = [c for _, c in normalized]
        if len(set(codes)) != len(codes):
            flash("不能重复选择同一题目", "error")
            conn.close()
            return redirect(url_for("student_wish"))
        ts = now_str()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for wish_no, code in normalized:
                topic = conn.execute(
                    "SELECT * FROM TOPICS WHERE TOPIC_CODE=? AND STATUS='open' AND TEACHER_ID=?",
                    (code, user["ADVISOR_ID"])).fetchone()
                if not topic or not topic_visible(topic, user["CLASS_NAME"]):
                    continue
                if rnd["RULE"] == "lottery":
                    conn.execute(
                        "INSERT INTO SELECTIONS(ROUND_ID,TOPIC_ID,USER_ID,STATUS,SELECTED_AT,CREATED_BY,CREATED_AT,FLAG)"
                        " VALUES(?,?,?,'entry',?,?,?,1)",
                        (rnd["ID"], topic["ID"], user["ID"], ts, user["USER_NAME"], ts))
                    log_event(conn, rnd["ID"], topic["ID"], user["ID"], "select",
                              None, "entry", operator=user["USER_NAME"], remark="抽签报名")
                else:
                    conn.execute(
                        "INSERT INTO SELECTIONS(ROUND_ID,TOPIC_ID,USER_ID,STATUS,WISH_NO,SELECTED_AT,CREATED_BY,CREATED_AT,FLAG)"
                        " VALUES(?,?,?,'wish',?,?,?,?,1)",
                        (rnd["ID"], topic["ID"], user["ID"], int(wish_no), ts,
                         user["USER_NAME"], ts))
                    log_event(conn, rnd["ID"], topic["ID"], user["ID"], "select",
                              None, "wish", wish_no=int(wish_no), operator=user["USER_NAME"],
                              remark="志愿填报")
            conn.execute("COMMIT")
            flash("提交成功，等待教师执行分配/抽签", "success")
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            flash("提交失败：记录冲突", "error")
        finally:
            conn.close()
        return redirect(url_for("student_wish"))

    mine = user_active_selection(conn, rnd["ID"], user["ID"])
    counts = topic_stats(conn, rnd["ID"], "wish" if rnd["RULE"] == "multi_wish" else "entry")
    conn.close()
    return render_template("student/wish.html", topics=topics, mine=mine, round=rnd,
                           counts=counts, rule_name=RULE_NAMES[rnd["RULE"]])


@app.route("/student/cancel/<int:selection_id>", methods=["POST"])
@login_required("student")
def student_cancel(selection_id):
    """退选：保留 cancelled 历史记录 + 写日志，再按递补策略处理候补"""
    conn = get_conn()
    sel = conn.execute("SELECT * FROM SELECTIONS WHERE ID=?", (selection_id,)).fetchone()
    if not sel or sel["USER_ID"] != session["user_id"] or sel["STATUS"] not in ("confirmed", "waiting"):
        flash("记录不存在或状态不允许退选", "error")
        conn.close()
        return redirect(url_for("student_mine"))
    rnd = conn.execute("SELECT * FROM ROUNDS WHERE ID=?", (sel["ROUND_ID"],)).fetchone()
    if not round_is_open(rnd):
        flash("该轮次已截止，不能自行退选；如需调整请提交「退选申请」由指导教师代为处理", "error")
        conn.close()
        return redirect(url_for("student_mine"))
    # 方案2：每轮退选次数上限（教师撤销不计入学生次数）
    max_cancel = (rnd["MAX_CANCEL"] or 0) if rnd else 0
    if max_cancel > 0:
        used = student_cancel_used(conn, sel["ROUND_ID"], session["user_id"], session["user_name"])
        if used >= max_cancel:
            flash(f"本轮退选次数已用完（上限 {max_cancel} 次），"
                  f"请在「我的选题」提交退选申请，由指导教师代为退选", "error")
            conn.close()
            return redirect(url_for("student_mine"))
    # 方案4：截止前禁止退选，避免名额在截止后空置
    lock_hours = (rnd["CANCEL_LOCK_HOURS"] or 0) if rnd else 0
    if lock_hours > 0:
        end = parse_dt(rnd["END_AT"]) if rnd else None
        if end and datetime.now() > end - timedelta(hours=lock_hours):
            flash(f"截止前 {lock_hours} 小时内不能退选（已进入截止前锁定），"
                  f"如需退选请在「我的选题」提交退选申请，由指导教师代为处理", "error")
            conn.close()
            return redirect(url_for("student_mine"))
    # 方案4：退选原因必选（写入日志，便于审计）
    reason = request.form.get("reason", "").strip()
    if reason not in CANCEL_REASONS:
        flash("请选择退选原因", "error")
        conn.close()
        return redirect(url_for("student_mine"))
    conn.execute("BEGIN IMMEDIATE")
    promoted = close_selection(conn, sel, rnd, session["user_name"],
                               f"学生退选（原因：{reason}）", action="cancel")
    conn.execute("COMMIT")
    msg = "已退选，记录已保留在「我的记录」中"
    if promoted:
        msg += "，候补同学已自动递补"
    elif sel["STATUS"] == "confirmed" and rnd["PROMOTE_MODE"] == "manual":
        msg += "，名额待教师手动递补"
    flash(msg, "success")
    conn.close()
    return redirect(url_for("student_mine"))


@app.route("/student/cancel-request/<int:selection_id>", methods=["POST"])
@login_required("student")
def student_cancel_request(selection_id):
    """提交退选/改选申请。

    退选次数用尽、截止前锁定、或处于志愿/报名阶段时，学生自己无法退选，
    由教师在这里闭环处理（同意后按同一递补策略释放名额并通知学生）。
    """
    conn = get_conn()
    sel = conn.execute("SELECT * FROM SELECTIONS WHERE ID=?", (selection_id,)).fetchone()
    if not sel or sel["USER_ID"] != session["user_id"] or sel["STATUS"] not in ACTIVE_STATUSES:
        flash("记录不存在或已不在有效状态", "error")
        conn.close()
        return redirect(url_for("student_mine"))
    rnd = conn.execute("SELECT * FROM ROUNDS WHERE ID=?", (sel["ROUND_ID"],)).fetchone()
    if not round_is_open(rnd):
        flash("该轮次已截止，无法再提交退选申请", "error")
        conn.close()
        return redirect(url_for("student_mine"))
    reason = request.form.get("reason", "").strip()
    if len(reason) < 4:
        flash("请填写退选/改选原因（不少于 4 个字），便于教师判断", "error")
        conn.close()
        return redirect(url_for("student_mine"))
    if len(reason) > 300:
        flash("原因过长（最多 300 字）", "error")
        conn.close()
        return redirect(url_for("student_mine"))
    if conn.execute("SELECT ID FROM CANCEL_REQUESTS WHERE SELECTION_ID=? AND STATUS='pending'",
                    (selection_id,)).fetchone():
        flash("你已提交过该记录的申请，请等待教师处理", "error")
        conn.close()
        return redirect(url_for("student_mine"))
    ts = now_str()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO CANCEL_REQUESTS(ROUND_ID,TOPIC_ID,USER_ID,SELECTION_ID,REASON,STATUS,"
            "CREATED_BY,CREATED_AT,FLAG) VALUES(?,?,?,?,?,'pending',?,?,1)",
            (sel["ROUND_ID"], sel["TOPIC_ID"], sel["USER_ID"], selection_id, reason,
             session["user_name"], ts))
        log_event(conn, sel["ROUND_ID"], sel["TOPIC_ID"], sel["USER_ID"], "request",
                  sel["STATUS"], sel["STATUS"], operator=session["user_name"],
                  remark=f"学生提交退选/改选申请：{reason}")
        conn.execute("COMMIT")
        flash("申请已提交，教师处理后会收到站内通知；紧急情况也可直接联系指导教师", "success")
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        flash("你已提交过该记录的申请，请等待教师处理", "error")
    finally:
        conn.close()
    return redirect(url_for("student_mine"))


@app.route("/student/mine")
@login_required("student")
def student_mine():
    """我的选题 + 历史记录（含退选痕迹）+ 退选申请进度"""
    conn = get_conn()
    user = current_user(conn)
    rnd = active_round(conn, user["ADVISOR_ID"]) or own_round_for_student(conn, user)
    # 跨轮次展示：新轮次开始后，上一轮的选题与退选记录仍然可查；
    # 只按学生本人过滤（指导教师变更后历史记录仍然可见，不会凭空消失）
    rows = conn.execute(
        "SELECT s.*, t.TOPIC_NAME, t.TOPIC_CODE, t.MAX_NUM, r.ROUND_NAME, r.RULE"
        " FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID JOIN ROUNDS r ON r.ID=s.ROUND_ID"
        " WHERE s.USER_ID=? ORDER BY r.ID DESC, s.ID DESC",
        (user["ID"],),
    ).fetchall()
    active = [r for r in rows if r["STATUS"] in ACTIVE_STATUSES]
    history = [r for r in rows if r["STATUS"] == "cancelled"]
    # 退选次数配额（方案2）：已用/上限，供页面提示
    cancel_used = cancel_max = 0
    if rnd:
        cancel_max = rnd["MAX_CANCEL"] or 0
        if cancel_max > 0:
            cancel_used = student_cancel_used(conn, rnd["ID"], user["ID"], user["USER_NAME"])
    # 是否已被「次数上限 / 截止锁定」挡住自助退选
    quota_blocked = bool(cancel_max and cancel_used >= cancel_max)
    lock_blocked = False
    if rnd and (rnd["CANCEL_LOCK_HOURS"] or 0) > 0:
        end = parse_dt(rnd["END_AT"])
        lock_blocked = bool(end and datetime.now() > end - timedelta(hours=rnd["CANCEL_LOCK_HOURS"]))
    # 我的退选申请（含处理结果）
    reqs = conn.execute(
        "SELECT q.*, t.TOPIC_NAME, t.TOPIC_CODE FROM CANCEL_REQUESTS q"
        " LEFT JOIN TOPICS t ON t.ID=q.TOPIC_ID WHERE q.USER_ID=? AND q.FLAG=1"
        " ORDER BY q.ID DESC LIMIT 20", (user["ID"],)).fetchall()
    pending_ids = {q["SELECTION_ID"] for q in reqs if q["STATUS"] == "pending"}
    conn.close()
    return render_template("student/mine.html", active=active, history=history, round=rnd,
                           rule_name=RULE_NAMES.get(rnd["RULE"] if rnd else "", ""),
                           status_names=STATUS_NAMES, cancel_reasons=CANCEL_REASONS,
                           cancel_used=cancel_used, cancel_max=cancel_max,
                           quota_blocked=quota_blocked, lock_blocked=lock_blocked,
                           reqs=reqs, pending_ids=pending_ids,
                           request_status_names=REQUEST_STATUS_NAMES)


def own_round_for_student(conn, user):
    """学生查看自己老师最新的一个轮次（含未开放的），用于展示历史"""
    if not user["ADVISOR_ID"]:
        return None
    return conn.execute(
        "SELECT * FROM ROUNDS WHERE TEACHER_ID=? AND FLAG=1 ORDER BY ID DESC",
        (user["ADVISOR_ID"],)).fetchone()


@app.route("/student/public")
@login_required("student")
def student_public():
    """结果公示：只看自己指导教师「本轮」的选题结果（历史轮次不混入，避免误读）"""
    conn = get_conn()
    user = current_user(conn)
    rnd = active_round(conn, user["ADVISOR_ID"]) or own_round_for_student(conn, user)
    rows = []
    if rnd:
        rows = conn.execute(
            "SELECT t.TOPIC_CODE, t.TOPIC_NAME, u.REAL_NAME, u.CLASS_NAME, s.SELECTED_AT, s.STATUS"
            " FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID JOIN USERS u ON u.ID=s.USER_ID"
            " WHERE s.STATUS='confirmed' AND s.ROUND_ID=? AND t.TEACHER_ID=?"
            " ORDER BY t.TOPIC_CODE, s.SELECTED_AT",
            (rnd["ID"], user["ADVISOR_ID"]),
        ).fetchall()
    conn.close()
    return render_template("student/public.html", rows=rows, round=rnd)


# ---------------- 教师端：首页（Dashboard） ----------------

@app.route("/teacher/index")
@login_required("teacher")
def teacher_index():
    conn = get_conn()
    rnd = active_round(conn, session["user_id"]) or own_round(conn)
    topics = conn.execute(
        "SELECT * FROM TOPICS WHERE FLAG=1 AND TEACHER_ID=? ORDER BY TOPIC_CODE",
        (session["user_id"],)).fetchall()
    students = conn.execute(
        "SELECT * FROM USERS WHERE ROLE='student' AND ADVISOR_ID=? AND FLAG=1 AND STATUS='active'",
        (session["user_id"],)).fetchall()
    # 每个学生的选题状态
    sel_map = {}
    if rnd:
        for r in conn.execute(
                "SELECT s.USER_ID, s.STATUS, s.WISH_NO, s.QUEUE_NO, t.TOPIC_NAME FROM SELECTIONS s"
                " JOIN TOPICS t ON t.ID=s.TOPIC_ID WHERE s.ROUND_ID=? AND s.STATUS IN('confirmed','waiting','wish','entry')",
                (rnd["ID"],)).fetchall():
            sel_map[r["USER_ID"]] = r
    # 待办统计
    todo_wish = todo_entry = todo_wait = 0
    if rnd:
        todo_wish = conn.execute(
            "SELECT COUNT(*) c FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID"
            " WHERE s.ROUND_ID=? AND s.STATUS='wish' AND t.TEACHER_ID=?",
            (rnd["ID"], session["user_id"])).fetchone()["c"]
        todo_entry = conn.execute(
            "SELECT COUNT(*) c FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID"
            " WHERE s.ROUND_ID=? AND s.STATUS='entry' AND t.TEACHER_ID=?",
            (rnd["ID"], session["user_id"])).fetchone()["c"]
        todo_wait = conn.execute(
            "SELECT COUNT(*) c FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID"
            " WHERE s.ROUND_ID=? AND s.STATUS='waiting' AND t.TEACHER_ID=?",
            (rnd["ID"], session["user_id"])).fetchone()["c"]
    todo_request = conn.execute(
        "SELECT COUNT(*) c FROM CANCEL_REQUESTS q JOIN TOPICS t ON t.ID=q.TOPIC_ID"
        " WHERE q.STATUS='pending' AND q.FLAG=1 AND t.TEACHER_ID=?",
        (session["user_id"],)).fetchone()["c"]
    picked_students = sum(1 for s in students if s["ID"] in sel_map and sel_map[s["ID"]]["STATUS"] == "confirmed")
    classes = teacher_classes(conn, session["user_id"])
    conn.close()
    return render_template("teacher/index.html", round=rnd, topics=topics, students=students,
                           sel_map=sel_map, todo_wish=todo_wish, todo_entry=todo_entry,
                           todo_wait=todo_wait, todo_request=todo_request,
                           picked_students=picked_students,
                           rule_names=RULE_NAMES, classes=classes)


# ---------------- 教师端：我的学生（选题进度一览） ----------------

@app.route("/teacher/students", methods=["GET", "POST"])
@login_required("teacher")
def teacher_students():
    conn = get_conn()
    rnd = active_round(conn, session["user_id"]) or own_round(conn)
    if request.method == "POST":
        action = request.form.get("action")
        ts = now_str()
        if action == "save_gpa":
            # 教师维护自己学生的绩点（绩点优先/多志愿分配策略据此放行）
            uid = to_int(request.form.get("user_id"))
            own = conn.execute("SELECT ID FROM USERS WHERE ID=? AND ROLE='student' AND ADVISOR_ID=?",
                               (uid, session["user_id"])).fetchone()
            if not own:
                flash("只能修改自己学生的绩点", "error")
            else:
                try:
                    gpa = float(request.form.get("gpa", 0) or 0)
                    if 0 <= gpa <= 5:
                        conn.execute("UPDATE USERS SET GPA=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                                     (gpa, session["user_name"], ts, uid))
                        flash("绩点已更新", "success")
                    else:
                        flash("绩点需在 0~5 之间", "error")
                except ValueError:
                    flash("绩点格式不正确", "error")
        elif action == "save_score":
            uid = to_int(request.form.get("user_id"))
            own = conn.execute("SELECT ID FROM USERS WHERE ID=? AND ROLE='student' AND ADVISOR_ID=?",
                               (uid, session["user_id"])).fetchone()
            if not own:
                flash("只能录入自己学生的成绩", "error")
            else:
                cname = request.form.get("course_name", "").strip()
                raw = request.form.get("score", "").strip()
                if not cname or not raw:
                    flash("课程名与成绩不能为空", "error")
                else:
                    try:
                        score = float(raw)
                        if 0 <= score <= 100:
                            # 课程按名归集：不存在则自动建档
                            course = conn.execute("SELECT ID FROM COURSES WHERE COURSE_NAME=? AND FLAG=1",
                                                  (cname,)).fetchone()
                            if not course:
                                cur = conn.execute(
                                    "INSERT INTO COURSES(COURSE_NAME,CREATED_BY,CREATED_AT,FLAG) VALUES(?,?,?,1)",
                                    (cname, session["user_name"], ts))
                                course_id = cur.lastrowid
                            else:
                                course_id = course["ID"]
                            exist = conn.execute(
                                "SELECT ID FROM SCORES WHERE USER_ID=? AND COURSE_ID=? AND FLAG=1",
                                (uid, course_id)).fetchone()
                            if exist:
                                conn.execute(
                                    "UPDATE SCORES SET SCORE=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                                    (score, session["user_name"], ts, exist["ID"]))
                            else:
                                conn.execute(
                                    "INSERT INTO SCORES(USER_ID,COURSE_ID,SCORE,TERM,CREATED_BY,CREATED_AT,FLAG)"
                                    " VALUES(?,?,?,?,?,?,1)",
                                    (uid, course_id, score, request.form.get("term", "").strip() or None,
                                     session["user_name"], ts))
                            flash(f"已录入 {cname}：{score:g} 分", "success")
                        else:
                            flash("成绩需在 0~100 之间", "error")
                    except ValueError:
                        flash("成绩格式不正确", "error")
        conn.commit()
        conn.close()
        return redirect(url_for("teacher_students"))
    kw = request.args.get("kw", "").strip()
    status_filter = request.args.get("status", "").strip()
    sql = ("SELECT * FROM USERS WHERE ROLE='student' AND ADVISOR_ID=? AND FLAG=1")
    args = [session["user_id"]]
    if kw:
        sql += " AND (USER_NAME LIKE ? OR USER_NO LIKE ? OR REAL_NAME LIKE ?)"
        args += [f"%{kw}%"] * 3
    sql += " ORDER BY CLASS_NAME, USER_NO"
    students = conn.execute(sql, args).fetchall()
    sel_map = {}
    if rnd:
        for r in conn.execute(
                "SELECT s.ID AS SEL_ID, s.USER_ID, s.STATUS, s.WISH_NO, s.QUEUE_NO,"
                " t.TOPIC_NAME, t.TOPIC_CODE"
                " FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID"
                " WHERE s.ROUND_ID=? AND s.STATUS IN('confirmed','waiting','wish','entry')",
                (rnd["ID"],)).fetchall():
            sel_map[r["USER_ID"]] = r
    rows = []
    for s in students:
        sel = sel_map.get(s["ID"])
        cancel_used = cancel_max = 0
        if rnd:
            cancel_max = rnd["MAX_CANCEL"] or 0
            if cancel_max > 0:
                cancel_used = student_cancel_used(conn, rnd["ID"], s["ID"], s["USER_NAME"])
        rows.append(dict(student=s, sel=sel,
                         status=sel["STATUS"] if sel else "none",
                         cancel_used=cancel_used, cancel_max=cancel_max,
                         scores=student_scores(conn, s["ID"])))
    if status_filter:
        rows = [r for r in rows if r["status"] == status_filter]
    classes = teacher_classes(conn, session["user_id"])
    course_suggestions = [r["COURSE_NAME"] for r in conn.execute(
        "SELECT COURSE_NAME FROM COURSES WHERE FLAG=1 ORDER BY COURSE_NAME")]
    conn.close()
    return render_template("teacher/students.html", rows=rows, round=rnd, kw=kw,
                           status_filter=status_filter, classes=classes,
                           course_suggestions=course_suggestions)


# ---------------- 教师端：公告管理 ----------------

@app.route("/teacher/announce", methods=["GET", "POST"])
@login_required("teacher")
def teacher_announce():
    conn = get_conn()
    if request.method == "POST":
        action = request.form.get("action")
        ts = now_str()
        if action == "add":
            title = request.form.get("title", "").strip()
            if not title:
                flash("公告标题不能为空", "error")
                conn.close()
                return redirect(url_for("teacher_announce"))
            conn.execute(
                "INSERT INTO ANNOUNCEMENTS(TITLE,CONTENT,TEACHER_ID,PUBLISHED_AT,CREATED_BY,CREATED_AT,FLAG)"
                " VALUES(?,?,?,?,?,?,1)",
                (title, request.form.get("content", "").strip(),
                 session["user_id"], ts, session["user_name"], ts))
            flash("公告已发布，学生首页可见", "success")
        elif action == "delete":
            own = conn.execute("SELECT ID FROM ANNOUNCEMENTS WHERE ID=? AND TEACHER_ID=?",
                               (int(request.form["ann_id"]), session["user_id"])).fetchone()
            if own:
                conn.execute("UPDATE ANNOUNCEMENTS SET FLAG=0 WHERE ID=?", (int(request.form["ann_id"]),))
                flash("公告已撤回", "success")
        conn.commit()
        conn.close()
        return redirect(url_for("teacher_announce"))
    anns = conn.execute(
        "SELECT * FROM ANNOUNCEMENTS WHERE FLAG=1 AND TEACHER_ID=? ORDER BY PUBLISHED_AT DESC",
        (session["user_id"],)).fetchall()
    conn.close()
    return render_template("teacher/announce.html", anns=anns)


# ---------------- 教师端：题库（仅自己的课题） ----------------

@app.route("/teacher/topics", methods=["GET", "POST"])
@login_required("teacher")
def teacher_topics():
    conn = get_conn()
    if request.method == "POST":
        action = request.form.get("action")
        ts = now_str()
        me = session["user_name"]

        def parse_prereq():
            """表单动态行 -> (人话描述, 规则JSON)；无规则返回 (None, None)"""
            text = request.form.get("prereq_text", "").strip() or None
            rules = {}
            for cname, min_s in zip(request.form.getlist("prereq_course"),
                                    request.form.getlist("prereq_score")):
                cname, min_s = cname.strip(), min_s.strip()
                if cname and min_s:
                    try:
                        rules[cname] = float(min_s)
                    except ValueError:
                        continue
            return text, (json.dumps(rules, ensure_ascii=False) if rules else None)

        max_num = to_int(request.form.get("max_num"), 0)
        if action == "add":
            code = request.form.get("topic_code", "").strip()
            tname = request.form.get("topic_name", "").strip()
            if not code or not tname:
                flash("题目编号与名称不能为空", "error")
            elif max_num < 1 or max_num > 99:
                flash("人数上限需为 1~99 的整数", "error")
            elif conn.execute("SELECT ID FROM TOPICS WHERE TOPIC_CODE=?", (code,)).fetchone():
                flash("题目编号已存在", "error")
            else:
                target = ",".join(request.form.getlist("target_classes"))
                ptext, prules = parse_prereq()
                conn.execute(
                    "INSERT INTO TOPICS(TOPIC_CODE,TOPIC_NAME,TOPIC_DESC,TAG,DIFFICULTY,MAX_NUM,TEACHER_ID,TARGET_CLASSES,PREREQ_TEXT,PREREQ_RULES,CREATED_BY,CREATED_AT,FLAG)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?, 'teacher', ?,1)",
                    (code, tname, request.form.get("topic_desc", ""),
                     request.form.get("tag", ""), request.form.get("difficulty", "中等"),
                     max_num, session["user_id"], target, ptext, prules, ts))
                flash("题目已创建", "success")
        elif action == "edit":
            # 仅能修改自己的课题
            own = conn.execute("SELECT ID FROM TOPICS WHERE ID=? AND TEACHER_ID=?",
                               (to_int(request.form.get("topic_id")), session["user_id"])).fetchone()
            confirmed_n = conn.execute(
                "SELECT COUNT(*) c FROM SELECTIONS WHERE TOPIC_ID=? AND STATUS='confirmed'",
                (own["ID"],)).fetchone()["c"] if own else 0
            if not own:
                flash("无权修改他人课题", "error")
            elif max_num < 1 or max_num > 99:
                flash("人数上限需为 1~99 的整数", "error")
            elif max_num < confirmed_n:
                flash(f"该题已有 {confirmed_n} 人确认，人数上限不能低于 {confirmed_n}；"
                      f"如需缩减请先在监控页退课/代退", "error")
            else:
                target = ",".join(request.form.getlist("target_classes"))
                ptext, prules = parse_prereq()
                conn.execute(
                    "UPDATE TOPICS SET TOPIC_NAME=?,TOPIC_DESC=?,TAG=?,DIFFICULTY=?,MAX_NUM=?,TARGET_CLASSES=?,PREREQ_TEXT=?,PREREQ_RULES=?,UPDATED_BY=?,UPDATED_AT=? WHERE ID=?",
                    (request.form["topic_name"].strip(), request.form.get("topic_desc", ""),
                     request.form.get("tag", ""), request.form.get("difficulty", "中等"),
                     max_num, target, ptext, prules, me, ts, own["ID"]))
                flash("题目已更新", "success")
        elif action == "toggle":
            # 课题关闭/重新开放（仅自己的课题）
            own = conn.execute("SELECT ID, STATUS FROM TOPICS WHERE ID=? AND TEACHER_ID=?",
                               (to_int(request.form.get("topic_id")), session["user_id"])).fetchone()
            if not own:
                flash("无权操作他人课题", "error")
            else:
                new_status = "closed" if own["STATUS"] == "open" else "open"
                conn.execute(
                    "UPDATE TOPICS SET STATUS=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                    (new_status, me, ts, own["ID"]))
                if new_status == "closed":
                    n = conn.execute(
                        "SELECT COUNT(*) c FROM SELECTIONS WHERE TOPIC_ID=?"
                        " AND STATUS IN ('confirmed','waiting','wish','entry')",
                        (own["ID"],)).fetchone()["c"]
                    if n:
                        flash(f"课题已关闭（学生不可见），已有 {n} 条选题记录保留；"
                              f"如需调整学生选题，请到「选题监控」退课/代退", "success")
                    else:
                        flash("课题已关闭（学生不可见）", "success")
                else:
                    flash("课题已重新开放", "success")
        conn.commit()
        conn.close()
        return redirect(url_for("teacher_topics"))
    topics = conn.execute(
        "SELECT * FROM TOPICS WHERE FLAG=1 AND TEACHER_ID=? ORDER BY TOPIC_CODE",
        (session["user_id"],)).fetchall()
    classes = teacher_classes(conn, session["user_id"])
    tag_suggestions = [r["TAG"] for r in conn.execute(
        "SELECT DISTINCT TAG FROM TOPICS WHERE TAG IS NOT NULL AND TAG != '' ORDER BY TAG")]
    course_suggestions = [r["COURSE_NAME"] for r in conn.execute(
        "SELECT COURSE_NAME FROM COURSES WHERE FLAG=1 ORDER BY COURSE_NAME")]
    # 前置要求：{课题ID: (人话描述, {课程名: 最低分})}
    prereq_map = {t["ID"]: (t["PREREQ_TEXT"], prereq_rules(t)) for t in topics}
    conn.close()
    return render_template("teacher/topics.html", topics=topics, classes=classes,
                           tag_suggestions=tag_suggestions, prereq_map=prereq_map,
                           course_suggestions=course_suggestions)


@app.route("/teacher/topics/delete/<int:topic_id>", methods=["POST"])
@login_required("teacher")
def teacher_topic_delete(topic_id):
    conn = get_conn()
    own = conn.execute("SELECT ID FROM TOPICS WHERE ID=? AND TEACHER_ID=?",
                       (topic_id, session["user_id"])).fetchone()
    if not own:
        flash("无权删除他人课题", "error")
        conn.close()
        return redirect(url_for("teacher_topics"))
    used = conn.execute(
        "SELECT COUNT(*) c FROM SELECTIONS WHERE TOPIC_ID=? AND STATUS IN('confirmed','waiting','wish','entry')",
        (topic_id,)).fetchone()["c"]
    if used:
        flash("该题目已有选题记录，不能删除", "error")
    else:
        conn.execute("UPDATE TOPICS SET FLAG=0, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                     (session["user_name"], now_str(), topic_id))
        conn.commit()
        flash("题目已删除", "success")
    conn.close()
    return redirect(url_for("teacher_topics"))


# ---------------- 教师端：轮次（仅自己的轮次） ----------------

@app.route("/teacher/round", methods=["GET", "POST"])
@login_required("teacher")
def teacher_round():
    conn = get_conn()
    if request.method == "POST":
        ts = now_str()
        action = request.form.get("action", "save")
        if action == "create":
            name = request.form.get("round_name", "").strip()
            rule = request.form.get("rule", "first_come")
            start_at = request.form.get("start_at", "").strip()
            end_at = request.form.get("end_at", "").strip()
            if not name or not start_at or not end_at:
                flash("轮次名称与起止时间不能为空", "error")
                conn.close()
                return redirect(url_for("teacher_round"))
            if parse_dt(start_at) and parse_dt(end_at) and parse_dt(start_at) >= parse_dt(end_at):
                flash("开始时间必须早于结束时间", "error")
                conn.close()
                return redirect(url_for("teacher_round"))
            conn.execute(
                "INSERT INTO ROUNDS(ROUND_NAME,TEACHER_ID,RULE,START_AT,END_AT,WAITLIST_ON,PROMOTE_MODE,"
                "MAX_WISH,LOTTERY_SEED,ALLOW_CLASSES,MAX_CANCEL,CANCEL_LOCK_HOURS,STATUS,CREATED_BY,CREATED_AT,FLAG)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'teacher',?,1)",
                (name, session["user_id"], rule, start_at, end_at,
                 0 if request.form.get("waitlist_on") == "0" else 1,  # 缺省开启候补（与表默认值一致）
                 request.form.get("promote_mode", "auto"),
                 max(1, to_int(request.form.get("max_wish"), 3)),
                 request.form.get("lottery_seed", "").strip() or None,
                 request.form.get("allow_classes", "").strip() or None,
                 max(0, to_int(request.form.get("max_cancel"), 2)),
                 max(0, to_int(request.form.get("cancel_lock_hours"), 2)),
                 request.form.get("status", "open"), ts))
            conn.commit()
            flash(f"轮次「{name}」已创建（最新轮次即时生效）", "success")
            conn.close()
            return redirect(url_for("teacher_round"))
        rnd_id = request.form.get("round_id")
        own = conn.execute("SELECT ID FROM ROUNDS WHERE ID=? AND TEACHER_ID=?",
                           (to_int(rnd_id), session["user_id"])).fetchone() if rnd_id else None
        if not own:
            flash("无权修改他人轮次", "error")
            conn.close()
            return redirect(url_for("teacher_round"))
        old = conn.execute("SELECT RULE FROM ROUNDS WHERE ID=?", (own["ID"],)).fetchone()
        new_rule = request.form.get("rule", "first_come")
        s_at, e_at = request.form.get("start_at", "").strip(), request.form.get("end_at", "").strip()
        if not s_at or not e_at:
            flash("开始时间与结束时间不能为空", "error")
            conn.close()
            return redirect(url_for("teacher_round"))
        if parse_dt(s_at) and parse_dt(e_at) and parse_dt(s_at) >= parse_dt(e_at):
            flash("开始时间必须早于结束时间", "error")
            conn.close()
            return redirect(url_for("teacher_round"))
        pending_wish = conn.execute(
            "SELECT COUNT(*) c FROM SELECTIONS WHERE ROUND_ID=? AND STATUS='wish'",
            (own["ID"],)).fetchone()["c"]
        pending_entry = conn.execute(
            "SELECT COUNT(*) c FROM SELECTIONS WHERE ROUND_ID=? AND STATUS='entry'",
            (own["ID"],)).fetchone()["c"]
        # 规则切换前必须先处理遗留记录，否则学生状态会卡在「志愿待分配/待抽签」
        if old["RULE"] == "multi_wish" and new_rule != "multi_wish" and pending_wish:
            flash(f"还有 {pending_wish} 条志愿待分配，请先在选题监控执行分配或撤销，再切换规则", "error")
            conn.close()
            return redirect(url_for("teacher_round"))
        if old["RULE"] == "lottery" and new_rule != "lottery" and pending_entry:
            flash(f"还有 {pending_entry} 条报名待抽签，请先在选题监控执行抽签或撤销，再切换规则", "error")
            conn.close()
            return redirect(url_for("teacher_round"))
        conn.execute(
            "UPDATE ROUNDS SET ROUND_NAME=?,RULE=?,START_AT=?,END_AT=?,WAITLIST_ON=?,PROMOTE_MODE=?,"
            "MAX_WISH=?,LOTTERY_SEED=?,ALLOW_CLASSES=?,MAX_CANCEL=?,CANCEL_LOCK_HOURS=?,STATUS=?,UPDATED_BY=?,UPDATED_AT=? WHERE ID=?",
            (request.form.get("round_name", "").strip(), new_rule, s_at,
             e_at, 1 if request.form.get("waitlist_on") else 0,
             request.form.get("promote_mode", "auto"),
             max(1, to_int(request.form.get("max_wish"), 3)),
             request.form.get("lottery_seed", "").strip() or None,
             request.form.get("allow_classes", "").strip() or None,
             max(0, to_int(request.form.get("max_cancel"), 2)),
             max(0, to_int(request.form.get("cancel_lock_hours"), 2)),
             request.form.get("status", "open"), session["user_name"], ts, own["ID"]))
        conn.commit()
        flash("轮次设置已保存", "success")
        conn.close()
        return redirect(url_for("teacher_round"))
    rnd = active_round(conn, session["user_id"]) or own_round(conn)
    rounds = conn.execute(
        "SELECT * FROM ROUNDS WHERE TEACHER_ID=? AND FLAG=1 ORDER BY ID DESC",
        (session["user_id"],)).fetchall()
    classes = teacher_classes(conn, session["user_id"])
    conn.close()
    return render_template("teacher/round.html", round=rnd, rounds=rounds,
                           rule_names=RULE_NAMES, classes=classes)


# ---------------- 教师端：监控与策略执行（仅自己的课题） ----------------

@app.route("/teacher/monitor")
@login_required("teacher")
def teacher_monitor():
    conn = get_conn()
    rnd = active_round(conn, session["user_id"]) or own_round(conn)
    rows = []
    total_picked = total_left = wait_total = wish_total = entry_total = 0
    waiters = {}
    pending = []
    enrolled = []
    if rnd:
        stats = topic_stats(conn, rnd["ID"], "confirmed")
        waits = topic_stats(conn, rnd["ID"], "waiting")
        wishes = topic_stats(conn, rnd["ID"], "wish")
        entries = topic_stats(conn, rnd["ID"], "entry")
        topics = conn.execute(
            "SELECT * FROM TOPICS WHERE FLAG=1 AND TEACHER_ID=? ORDER BY TOPIC_CODE",
            (session["user_id"],)).fetchall()
        for t in topics:
            picked = stats.get(t["ID"], 0)
            waiting = waits.get(t["ID"], 0)
            wish_n = wishes.get(t["ID"], 0)
            entry_n = entries.get(t["ID"], 0)
            total_picked += picked
            total_left += max(0, t["MAX_NUM"] - picked)
            wait_total += waiting
            wish_total += wish_n
            entry_total += entry_n
            rows.append(dict(topic=t, picked=picked, left=t["MAX_NUM"] - picked,
                             waiting=waiting, wish=wish_n, entry=entry_n))
        for r in conn.execute(
                "SELECT s.*, u.REAL_NAME, u.USER_NO, t.TOPIC_NAME FROM SELECTIONS s"
                " JOIN USERS u ON u.ID=s.USER_ID JOIN TOPICS t ON t.ID=s.TOPIC_ID"
                " WHERE s.ROUND_ID=? AND s.STATUS='waiting' AND t.TEACHER_ID=?"
                " ORDER BY s.TOPIC_ID, s.QUEUE_NO",
                (rnd["ID"], session["user_id"])).fetchall():
            waiters.setdefault(r["TOPIC_ID"], []).append(r)
        # 待分配/待抽签名单（教师可撤销误提交的记录，并做前置核验）
        for r in conn.execute(
                "SELECT s.*, u.REAL_NAME, u.USER_NO, u.GPA, t.TOPIC_NAME, t.TOPIC_CODE, t.PREREQ_RULES"
                " FROM SELECTIONS s JOIN USERS u ON u.ID=s.USER_ID JOIN TOPICS t ON t.ID=s.TOPIC_ID"
                " WHERE s.ROUND_ID=? AND s.STATUS IN('wish','entry') AND t.TEACHER_ID=?"
                " ORDER BY t.TOPIC_CODE, s.WISH_NO, u.GPA DESC",
                (rnd["ID"], session["user_id"])).fetchall():
            ok, missing = prereq_check(conn, r["USER_ID"], r)
            pending.append(dict(sel=r, prereq_ok=ok, missing=missing))
        # 已确认/候补名单 + 前置要求核验（不满足可一键退课）
        for r in conn.execute(
                "SELECT s.*, u.REAL_NAME, u.USER_NO, u.CLASS_NAME, u.GPA,"
                " t.TOPIC_NAME, t.TOPIC_CODE, t.PREREQ_TEXT, t.PREREQ_RULES"
                " FROM SELECTIONS s JOIN USERS u ON u.ID=s.USER_ID JOIN TOPICS t ON t.ID=s.TOPIC_ID"
                " WHERE s.ROUND_ID=? AND s.STATUS IN('confirmed','waiting') AND t.TEACHER_ID=?"
                " ORDER BY t.TOPIC_CODE, s.STATUS DESC, s.QUEUE_NO",
                (rnd["ID"], session["user_id"])).fetchall():
            ok, missing = prereq_check(conn, r["USER_ID"], r)
            enrolled.append(dict(sel=r, prereq_ok=ok, missing=missing))
    # 学生退选/改选申请：不限当前轮次，避免轮次切换后申请卡在待处理
    requests = conn.execute(
        "SELECT q.*, t.TOPIC_CODE, t.TOPIC_NAME, u.REAL_NAME, u.USER_NO, u.CLASS_NAME,"
        " s.STATUS AS SEL_STATUS, s.WISH_NO, s.QUEUE_NO, rd.ROUND_NAME"
        " FROM CANCEL_REQUESTS q JOIN TOPICS t ON t.ID=q.TOPIC_ID"
        " JOIN USERS u ON u.ID=q.USER_ID"
        " LEFT JOIN SELECTIONS s ON s.ID=q.SELECTION_ID"
        " LEFT JOIN ROUNDS rd ON rd.ID=q.ROUND_ID"
        " WHERE q.STATUS='pending' AND q.FLAG=1 AND t.TEACHER_ID=?"
        " ORDER BY q.ID", (session["user_id"],)).fetchall()
    conn.close()
    return render_template("teacher/monitor.html", rows=rows, round=rnd,
                           total_picked=total_picked, total_left=total_left,
                           wait_total=wait_total, wish_total=wish_total,
                           entry_total=entry_total, rule_names=RULE_NAMES, waiters=waiters,
                           pending=pending, enrolled=enrolled, requests=requests,
                           status_names=STATUS_NAMES)


@app.route("/teacher/allocate", methods=["POST"])
@login_required("teacher")
def teacher_allocate():
    """多志愿统一分配：按志愿序号依次处理，同志愿超员按绩点优先，未命中转第一志愿候补"""
    conn = get_conn()
    round_id = request.form.get("round_id", "").strip()
    if round_id.isdigit():
        rnd = conn.execute(
            "SELECT * FROM ROUNDS WHERE ID=? AND TEACHER_ID=? AND FLAG=1",
            (int(round_id), session["user_id"])).fetchone()
    elif round_id:
        rnd = None
    else:
        rnd = own_round(conn)
    legacy = request.form.get("legacy") == "1"
    if not rnd:
        flash("轮次不存在或无权操作", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    if rnd["RULE"] != "multi_wish" and not legacy:
        flash("当前轮次不是多志愿模式；如是规则切换前的遗留志愿，请在监控页点「处理遗留志愿」", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    ts = now_str()
    conn.execute("BEGIN IMMEDIATE")
    try:
        all_wishes = conn.execute(
            "SELECT s.*, u.GPA, u.USER_NAME FROM SELECTIONS s JOIN USERS u ON u.ID=s.USER_ID"
            " JOIN TOPICS t ON t.ID=s.TOPIC_ID"
            " WHERE s.ROUND_ID=? AND s.STATUS='wish' AND t.TEACHER_ID=?"
            " ORDER BY s.WISH_NO, u.GPA DESC, s.ID",
            (rnd["ID"], session["user_id"])).fetchall()
        assigned = set()
        promoted_ids = set()
        closed_ids = set()   # 因课题关闭而作废的志愿，第二轮不再处理
        # 统计各题已有确认数（防止先到先得阶段已选 + 规则切换后叠加导致超分配）
        stats = {}
        for r in conn.execute(
                "SELECT TOPIC_ID, COUNT(*) c FROM SELECTIONS"
                " WHERE ROUND_ID=? AND STATUS='confirmed' GROUP BY TOPIC_ID",
                (rnd["ID"],)).fetchall():
            stats[r["TOPIC_ID"]] = r["c"]
        for w in all_wishes:
            if w["USER_ID"] in assigned:
                continue
            topic = conn.execute("SELECT * FROM TOPICS WHERE ID=?", (w["TOPIC_ID"],)).fetchone()
            used = stats.get(w["TOPIC_ID"], 0)
            if topic["STATUS"] != "open" or not topic["FLAG"]:
                # 课题已关闭/删除：该志愿作废（不占用学生分配结果）
                conn.execute(
                    "UPDATE SELECTIONS SET STATUS='cancelled', UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                    (session["user_name"], ts, w["ID"]))
                log_event(conn, rnd["ID"], w["TOPIC_ID"], w["USER_ID"], "allocate",
                          "wish", "cancelled", wish_no=w["WISH_NO"],
                          operator=session["user_name"], remark="课题已关闭，本志愿作废")
                closed_ids.add(w["ID"])
                continue
            if used < topic["MAX_NUM"]:
                conn.execute(
                    "UPDATE SELECTIONS SET STATUS='confirmed', WISH_NO=NULL, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                    (session["user_name"], ts, w["ID"]))
                log_event(conn, rnd["ID"], w["TOPIC_ID"], w["USER_ID"], "allocate",
                          "wish", "confirmed", wish_no=w["WISH_NO"],
                          operator=session["user_name"],
                          remark=f"第{w['WISH_NO']}志愿命中（绩点优先）")
                stats[w["TOPIC_ID"]] = used + 1
                assigned.add(w["USER_ID"])
                promoted_ids.add(w["ID"])
        # 第二轮：已命中的其余志愿作废；未命中的按「最小志愿序号」转候补
        # （修复：只填第二志愿、未填第一志愿的学生原本会永久停留在 wish 状态）
        remaining = [w for w in all_wishes
                     if w["ID"] not in promoted_ids and w["ID"] not in closed_ids]
        for w in remaining:
            if w["USER_ID"] in assigned:
                conn.execute(
                    "UPDATE SELECTIONS SET STATUS='cancelled', UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                    (session["user_name"], ts, w["ID"]))
                log_event(conn, rnd["ID"], w["TOPIC_ID"], w["USER_ID"], "allocate",
                          "wish", "cancelled", wish_no=w["WISH_NO"],
                          operator=session["user_name"], remark="已命中其他志愿，本志愿作废")
        by_user = {}
        for w in remaining:
            if w["USER_ID"] not in assigned:
                by_user.setdefault(w["USER_ID"], []).append(w)
        for uid, ws in by_user.items():
            ws.sort(key=lambda x: (x["WISH_NO"] or 99, x["ID"]))
            first = ws[0]
            q = conn.execute(
                "SELECT COALESCE(MAX(QUEUE_NO),0)+1 q FROM SELECTIONS"
                " WHERE ROUND_ID=? AND TOPIC_ID=? AND STATUS='waiting'",
                (rnd["ID"], first["TOPIC_ID"])).fetchone()["q"]
            conn.execute(
                "UPDATE SELECTIONS SET STATUS='waiting', QUEUE_NO=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                (q, session["user_name"], ts, first["ID"]))
            log_event(conn, rnd["ID"], first["TOPIC_ID"], uid, "allocate",
                      "wish", "waiting", wish_no=first["WISH_NO"], queue_no=q,
                      operator=session["user_name"],
                      remark=f"未命中志愿，按第{first['WISH_NO']}志愿转候补")
            for w in ws[1:]:
                conn.execute(
                    "UPDATE SELECTIONS SET STATUS='cancelled', UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                    (session["user_name"], ts, w["ID"]))
                log_event(conn, rnd["ID"], w["TOPIC_ID"], uid, "allocate",
                          "wish", "cancelled", wish_no=w["WISH_NO"],
                          operator=session["user_name"], remark="未命中志愿，本志愿作废")
            assigned.add(uid)
        conn.execute("COMMIT")
        flash(f"分配完成：共处理 {len(assigned)} 名学生，全部动作已记录日志", "success")
    except Exception as e:
        conn.execute("ROLLBACK")
        flash(f"分配失败：{e}", "error")
    finally:
        conn.close()
    return redirect(url_for("teacher_monitor"))


@app.route("/teacher/lottery", methods=["POST"])
@login_required("teacher")
def teacher_lottery():
    """随机抽签：种子公开可复现，未中签者进候补，全过程写日志"""
    conn = get_conn()
    round_id = request.form.get("round_id", "").strip()
    if round_id.isdigit():
        rnd = conn.execute(
            "SELECT * FROM ROUNDS WHERE ID=? AND TEACHER_ID=? AND FLAG=1",
            (int(round_id), session["user_id"])).fetchone()
    elif round_id:
        rnd = None
    else:
        rnd = own_round(conn)
    legacy = request.form.get("legacy") == "1"
    if not rnd:
        flash("轮次不存在或无权操作", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    if rnd["RULE"] != "lottery" and not legacy:
        flash("当前轮次不是随机抽签模式；如是规则切换前的遗留报名，请在监控页点「处理遗留报名」", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    seed = rnd["LOTTERY_SEED"] or f"{rnd['ID']}-{rnd['ROUND_NAME']}"
    ts = now_str()
    conn.execute("BEGIN IMMEDIATE")
    try:
        topics = conn.execute(
            "SELECT * FROM TOPICS WHERE FLAG=1 AND STATUS='open' AND TEACHER_ID=?",
            (session["user_id"],)).fetchall()
        rng = random.Random(seed)
        confirmed_users = set()
        # 轮次内已确认的学生（含之前其它模式下选题的），一人最多中一题
        for r in conn.execute(
                "SELECT DISTINCT USER_ID FROM SELECTIONS WHERE ROUND_ID=? AND STATUS='confirmed'",
                (rnd["ID"],)).fetchall():
            confirmed_users.add(r["USER_ID"])
        # 课题已关闭/删除的报名：抽签只处理开放课题，这类记录必须显式作废，
        # 否则学生会永久停留在「已报名待抽签」（功能不闭环）
        stray = conn.execute(
            "SELECT s.*, t.TOPIC_NAME FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID"
            " WHERE s.ROUND_ID=? AND s.STATUS='entry' AND t.TEACHER_ID=?"
            " AND (t.STATUS!='open' OR t.FLAG=0)",
            (rnd["ID"], session["user_id"])).fetchall()
        for e in stray:
            conn.execute(
                "UPDATE SELECTIONS SET STATUS='cancelled', CANCELLED_AT=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                (ts, session["user_name"], ts, e["ID"]))
            log_event(conn, rnd["ID"], e["TOPIC_ID"], e["USER_ID"], "lottery",
                      "entry", "cancelled", operator=session["user_name"],
                      remark=f"课题已关闭/删除，报名作废（种子 {seed}）")
            notify(conn, e["USER_ID"], f"《{e['TOPIC_NAME']}》已关闭，报名已作废",
                   f"同学你好，你报名的《{e['TOPIC_NAME']}》已被教师关闭，本次抽签无法参与该题。\n\n"
                   f"【下一步】请到「浏览题库」重新选择仍在开放的课题；如有疑问请联系指导教师。",
                   kind="kick", topic_id=e["TOPIC_ID"], operator=session["user_name"])
        for t in topics:
            entries = conn.execute(
                "SELECT s.*, u.GPA FROM SELECTIONS s JOIN USERS u ON u.ID=s.USER_ID"
                " WHERE s.ROUND_ID=? AND s.TOPIC_ID=? AND s.STATUS='entry'",
                (rnd["ID"], t["ID"])).fetchall()
            if not entries:
                continue
            entries.sort(key=lambda e: (-e["GPA"], e["ID"]))
            rng.shuffle(entries)
            for i, e in enumerate(entries):
                if i < t["MAX_NUM"] and e["USER_ID"] not in confirmed_users:
                    conn.execute(
                        "UPDATE SELECTIONS SET STATUS='confirmed', UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                        (session["user_name"], ts, e["ID"]))
                    log_event(conn, rnd["ID"], t["ID"], e["USER_ID"], "lottery",
                              "entry", "confirmed", operator=session["user_name"],
                              remark=f"抽签中签（种子 {seed}）")
                    confirmed_users.add(e["USER_ID"])
                elif e["USER_ID"] in confirmed_users:
                    # 该生已在其他课题中签（每人限选 1 题），本报名作废并留痕
                    conn.execute(
                        "UPDATE SELECTIONS SET STATUS='cancelled', CANCELLED_AT=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                        (ts, session["user_name"], ts, e["ID"]))
                    log_event(conn, rnd["ID"], t["ID"], e["USER_ID"], "lottery",
                              "entry", "cancelled", operator=session["user_name"],
                              remark=f"已在其他课题中签，本报名作废（种子 {seed}）")
                else:
                    q = conn.execute(
                        "SELECT COALESCE(MAX(QUEUE_NO),0)+1 q FROM SELECTIONS"
                        " WHERE ROUND_ID=? AND TOPIC_ID=? AND STATUS='waiting'",
                        (rnd["ID"], t["ID"])).fetchone()["q"]
                    conn.execute(
                        "UPDATE SELECTIONS SET STATUS='waiting', QUEUE_NO=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                        (q, session["user_name"], ts, e["ID"]))
                    log_event(conn, rnd["ID"], t["ID"], e["USER_ID"], "lottery",
                              "entry", "waiting", queue_no=q, operator=session["user_name"],
                              remark=f"未中签进候补（种子 {seed}）")
        conn.execute("COMMIT")
        flash(f"抽签完成（种子：{seed}，任何人可用该种子复现验证结果；每人最多中 1 题）", "success")
    except Exception as e:
        conn.execute("ROLLBACK")
        flash(f"抽签失败：{e}", "error")
    finally:
        conn.close()
    return redirect(url_for("teacher_monitor"))


@app.route("/teacher/revoke/<int:selection_id>", methods=["POST"])
@login_required("teacher")
def teacher_revoke(selection_id):
    """教师撤销学生的志愿/报名/候补记录（学生误提交后的补救，留痕可查）"""
    conn = get_conn()
    sel = conn.execute(
        "SELECT s.*, t.TEACHER_ID, t.TOPIC_NAME FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID"
        " WHERE s.ID=?", (selection_id,)).fetchone()
    if not sel or sel["STATUS"] not in ("wish", "entry", "waiting"):
        flash("记录不存在或状态不允许撤销", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    if sel["TEACHER_ID"] != session["user_id"]:
        flash("无权操作他人课题的记录", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    rnd = conn.execute("SELECT * FROM ROUNDS WHERE ID=?", (sel["ROUND_ID"],)).fetchone()
    stage = {"wish": "志愿", "entry": "报名", "waiting": "候补"}.get(sel["STATUS"], "选题")
    notice = (f"你在《{sel['TOPIC_NAME']}》的{stage}记录已被教师撤销",
              f"同学你好，你在《{sel['TOPIC_NAME']}》的{stage}记录已被指导教师撤销（多用于误提交补救）。\n\n"
              f"【说明】撤销由教师操作，不计入你的退选次数。\n\n"
              f"【下一步】请到对应的「志愿填报 / 浏览题库」页面重新提交；如有疑问请联系指导教师。",
              "revoke")
    conn.execute("BEGIN IMMEDIATE")
    close_selection(conn, sel, rnd, session["user_name"], "教师撤销（志愿/报名/候补）",
                    action="cancel", notice=notice)
    conn.execute("COMMIT")
    flash(f"已撤销该同学的《{sel['TOPIC_NAME']}》记录并通知学生，学生可重新提交", "success")
    conn.close()
    return redirect(url_for("teacher_monitor"))


@app.route("/teacher/kick/<int:selection_id>", methods=["GET", "POST"])
@login_required("teacher")
def teacher_kick(selection_id):
    """教师按前置要求退掉学生选题：必须填写原因与建议，学生收到站内通知，全程留痕"""
    conn = get_conn()
    sel = conn.execute(
        "SELECT s.*, t.TEACHER_ID, t.TOPIC_NAME, t.TOPIC_CODE, t.PREREQ_TEXT,"
        " u.REAL_NAME, u.USER_NO, u.CLASS_NAME, u.GPA"
        " FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID JOIN USERS u ON u.ID=s.USER_ID"
        " WHERE s.ID=?", (selection_id,)).fetchone()
    if not sel or sel["STATUS"] not in ("confirmed", "waiting", "wish", "entry"):
        flash("记录不存在或状态不允许退课", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    if sel["TEACHER_ID"] != session["user_id"]:
        flash("无权操作他人课题的记录", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    topic = conn.execute("SELECT * FROM TOPICS WHERE ID=?", (sel["TOPIC_ID"],)).fetchone()
    ok, missing = prereq_check(conn, sel["USER_ID"], topic)
    scores = student_scores(conn, sel["USER_ID"])
    if request.method == "POST":
        reason = request.form.get("reason", "").strip()
        advice = request.form.get("advice", "").strip()
        if not reason or not advice:
            flash("退课原因与建议不能为空（将一并通知学生）", "error")
            conn.close()
            return redirect(url_for("teacher_kick", selection_id=selection_id))
        if len(reason) > 500 or len(advice) > 2000:
            flash("原因或建议过长", "error")
            conn.close()
            return redirect(url_for("teacher_kick", selection_id=selection_id))
        rnd = conn.execute("SELECT * FROM ROUNDS WHERE ID=?", (sel["ROUND_ID"],)).fetchone()
        stage = {"wish": "志愿", "entry": "报名"}.get(sel["STATUS"], "选题")
        miss_text = "；".join(
            f"{c} 要求≥{n}分，你的成绩为{a if a is not None else '未录入'}" for c, n, a in missing)
        if ok:
            # 前置要求已满足：属于教师代为退选（学生次数用尽/截止锁定/线下沟通等）
            notice_title = f"你的{stage}《{sel['TOPIC_NAME']}》已被教师代为退选"
            notice_content = (
                f"同学你好，你对《{sel['TOPIC_NAME']}》（{sel['TOPIC_CODE']}）的{stage}已由指导教师代为退选。\n\n"
                f"【退选原因】{reason}\n\n【老师建议】{advice}\n\n"
                f"【说明】本次由教师操作，不计入你的退选次数；名额按轮次递补策略处理。\n\n"
                f"【下一步】请到「浏览题库」重新选择合适课题；如已到截止时间，请与教师确认补选安排。")
            remark = f"教师代为退选｜原因：{reason}｜建议：{advice}"
        else:
            notice_title = f"你的{stage}《{sel['TOPIC_NAME']}》已被教师退课"
            notice_content = (
                f"同学你好，你对《{sel['TOPIC_NAME']}》（{sel['TOPIC_CODE']}）的{stage}经前置要求核验后被指导教师退课。\n\n"
                f"【退课原因】{reason}\n\n"
                f"【前置核验】{miss_text or '详见课题前置要求'}\n\n"
                f"【老师建议】{advice}\n\n"
                f"【说明】本次退课由教师操作，不计入你的退选次数。\n\n"
                f"【下一步】请阅读老师建议，在「浏览题库」中选择与你当前能力匹配的课题重新{stage}；"
                f"如对退课有疑问，请与指导教师沟通。")
            remark = f"前置要求退课｜原因：{reason}｜建议：{advice}"
        conn.execute("BEGIN IMMEDIATE")
        promoted = close_selection(conn, sel, rnd, session["user_name"], remark,
                                   action="kick",
                                   notice=(notice_title, notice_content, "kick"))
        conn.execute("COMMIT")
        msg = f"已退掉 {sel['REAL_NAME']} 的《{sel['TOPIC_NAME']}》，学生将收到站内通知（含原因与建议）"
        if promoted:
            msg += "，候补同学已自动递补"
        flash(msg, "success")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    conn.close()
    return render_template("teacher/kick.html", sel=sel, topic=topic, missing=missing,
                           scores=scores, prereq_ok=ok)


@app.route("/teacher/cancel-request/<int:req_id>", methods=["POST"])
@login_required("teacher")
def teacher_cancel_request(req_id):
    """处理学生的退选/改选申请：同意即代为退选（不占学生次数、按策略递补），驳回也通知学生"""
    conn = get_conn()
    req = conn.execute(
        "SELECT q.*, t.TOPIC_NAME, t.TOPIC_CODE, t.TEACHER_ID, u.REAL_NAME, u.USER_NO"
        " FROM CANCEL_REQUESTS q JOIN TOPICS t ON t.ID=q.TOPIC_ID"
        " JOIN USERS u ON u.ID=q.USER_ID WHERE q.ID=? AND q.FLAG=1", (req_id,)).fetchone()
    if not req or req["TEACHER_ID"] != session["user_id"]:
        flash("申请不存在或无权处理", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    if req["STATUS"] != "pending":
        flash("该申请已处理，无需重复操作", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    action = request.form.get("action")
    remark = request.form.get("remark", "").strip()[:300]
    if action not in ("approve", "reject"):
        flash("未知操作", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    sel = conn.execute("SELECT * FROM SELECTIONS WHERE ID=?", (req["SELECTION_ID"],)).fetchone()
    rnd = conn.execute("SELECT * FROM ROUNDS WHERE ID=?", (req["ROUND_ID"],)).fetchone()
    ts = now_str()
    conn.execute("BEGIN IMMEDIATE")
    try:
        fresh = conn.execute("SELECT STATUS FROM CANCEL_REQUESTS WHERE ID=?",
                             (req_id,)).fetchone()
        if not fresh or fresh["STATUS"] != "pending":
            conn.execute("ROLLBACK")
            flash("该申请已被处理，请刷新页面查看最新状态", "error")
        elif not sel or sel["STATUS"] not in ACTIVE_STATUSES:
            conn.execute(
                "UPDATE CANCEL_REQUESTS SET STATUS='rejected', HANDLE_REMARK=?, HANDLED_BY=?,"
                " HANDLED_AT=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                ("选题记录已失效，系统自动关闭", session["user_name"], ts,
                 session["user_name"], ts, req_id))
            conn.execute("COMMIT")
            flash("该选题记录已失效（可能已被处理），申请已自动关闭", "error")
        elif action == "approve":
            notice = (
                "你的退选申请已通过",
                f"同学你好，你提交的《{req['TOPIC_NAME']}》（{req['TOPIC_CODE']}）退选申请已由指导教师处理。\n\n"
                f"【你的说明】{req['REASON']}\n\n"
                f"【老师意见】{remark or '同意退选，请重新选择合适课题'}\n\n"
                f"【说明】本次由教师代为退选，不计入你的退选次数；名额按轮次递补策略处理。\n\n"
                f"【下一步】请到「浏览题库」重新选择课题；如本轮已截止，请与教师确认补选安排。",
                "kick")
            promoted = close_selection(
                conn, sel, rnd, session["user_name"],
                f"学生申请退选，教师同意｜学生说明：{req['REASON']}｜教师意见：{remark or '无'}",
                action="cancel", notice=notice, keep_request_id=req_id)
            conn.execute(
                "UPDATE CANCEL_REQUESTS SET STATUS='approved', HANDLE_REMARK=?, HANDLED_BY=?,"
                " HANDLED_AT=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                (remark or "同意退选", session["user_name"], ts, session["user_name"], ts, req_id))
            log_event(conn, req["ROUND_ID"], req["TOPIC_ID"], req["USER_ID"], "approve",
                      sel["STATUS"], "cancelled", operator=session["user_name"],
                      remark=f"教师同意退选申请｜{remark or '无'}｜已通知学生")
            conn.execute("COMMIT")
            msg = f"已同意 {req['REAL_NAME']} 的退选申请，记录已取消并通知学生"
            if promoted:
                msg += "，候补同学已自动递补"
            flash(msg, "success")
        else:
            conn.execute(
                "UPDATE CANCEL_REQUESTS SET STATUS='rejected', HANDLE_REMARK=?, HANDLED_BY=?,"
                " HANDLED_AT=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                (remark or "不同意退选", session["user_name"], ts,
                 session["user_name"], ts, req_id))
            notify(conn, req["USER_ID"], "你的退选申请未通过",
                   f"同学你好，你提交的《{req['TOPIC_NAME']}》（{req['TOPIC_CODE']}）退选申请未通过。\n\n"
                   f"【你的说明】{req['REASON']}\n\n【老师意见】{remark or '请与指导教师当面沟通'}\n\n"
                   f"【下一步】当前选题仍然有效，如确有特殊情况请直接联系指导教师。",
                   kind="request", topic_id=req["TOPIC_ID"], operator=session["user_name"])
            log_event(conn, req["ROUND_ID"], req["TOPIC_ID"], req["USER_ID"], "reject",
                      sel["STATUS"] if sel else None, sel["STATUS"] if sel else None,
                      operator=session["user_name"],
                      remark=f"教师驳回退选申请｜{remark or '无'}｜已通知学生")
            conn.execute("COMMIT")
            flash(f"已驳回 {req['REAL_NAME']} 的退选申请，并通知学生", "success")
    except Exception as e:
        conn.execute("ROLLBACK")
        flash(f"处理失败：{e}", "error")
    finally:
        conn.close()
    return redirect(url_for("teacher_monitor"))


@app.route("/teacher/promote/<int:selection_id>", methods=["POST"])
@login_required("teacher")
def teacher_manual_promote(selection_id):
    """教师手动递补候补学生（仅限自己的课题）"""
    conn = get_conn()
    sel = conn.execute(
        "SELECT s.*, t.TEACHER_ID FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID"
        " WHERE s.ID=? AND s.STATUS='waiting'", (selection_id,)).fetchone()
    if not sel:
        flash("候补记录不存在", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    if sel["TEACHER_ID"] != session["user_id"]:
        flash("无权操作他人课题的候补", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    topic = conn.execute("SELECT * FROM TOPICS WHERE ID=?", (sel["TOPIC_ID"],)).fetchone()
    confirmed = conn.execute(
        "SELECT COUNT(*) c FROM SELECTIONS WHERE ROUND_ID=? AND TOPIC_ID=? AND STATUS='confirmed'",
        (sel["ROUND_ID"], sel["TOPIC_ID"])).fetchone()["c"]
    if confirmed >= topic["MAX_NUM"]:
        flash("该题目名额已满，无法递补", "error")
        conn.close()
        return redirect(url_for("teacher_monitor"))
    ts = now_str()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE SELECTIONS SET STATUS='confirmed', QUEUE_NO=NULL, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
        (session["user_name"], ts, selection_id))
    log_event(conn, sel["ROUND_ID"], sel["TOPIC_ID"], sel["USER_ID"], "promote",
              "waiting", "confirmed", queue_no=sel["QUEUE_NO"],
              operator=session["user_name"], remark="教师手动递补")
    notify(conn, sel["USER_ID"], f"《{topic['TOPIC_NAME']}》候补递补成功",
           f"同学你好，你在《{topic['TOPIC_NAME']}》（{topic['TOPIC_CODE']}）的候补已由教师递补为「已确认」。\n\n"
           f"【下一步】请到「我的选题」查看，并按教师要求开始课题。",
           kind="promote", topic_id=sel["TOPIC_ID"], operator=session["user_name"])
    conn.execute("COMMIT")
    flash("已手动递补，并通知学生", "success")
    conn.close()
    return redirect(url_for("teacher_monitor"))


@app.route("/teacher/export")
@login_required("teacher")
def teacher_export():
    conn = get_conn()
    rnd = active_round(conn, session["user_id"]) or own_round(conn)
    rows = conn.execute(
        "SELECT t.TOPIC_CODE, t.TOPIC_NAME, u.USER_NO, u.REAL_NAME, u.CLASS_NAME, u.GPA,"
        " s.STATUS, s.WISH_NO, s.QUEUE_NO, s.SELECTED_AT FROM SELECTIONS s"
        " JOIN TOPICS t ON t.ID=s.TOPIC_ID JOIN USERS u ON u.ID=s.USER_ID"
        " WHERE s.STATUS IN('confirmed','waiting') AND s.ROUND_ID=? AND t.TEACHER_ID=?"
        " ORDER BY t.TOPIC_CODE, s.STATUS, s.QUEUE_NO",
        (rnd["ID"] if rnd else 0, session["user_id"]),
    ).fetchall()
    conn.close()
    buf = io.StringIO()
    buf.write("\ufeff")  # Excel 中文兼容
    w = csv.writer(buf)
    w.writerow(["题目编号", "题目名称", "学号", "姓名", "班级", "绩点", "状态", "志愿序号", "候补序号", "选题时间"])
    status_map = {"confirmed": "已确认", "waiting": "候补中"}
    for r in rows:
        w.writerow([r["TOPIC_CODE"], r["TOPIC_NAME"], r["USER_NO"], r["REAL_NAME"],
                    r["CLASS_NAME"], r["GPA"], status_map[r["STATUS"]],
                    r["WISH_NO"] or "", r["QUEUE_NO"] or "", r["SELECTED_AT"]])
    filename = quote("选题结果.csv")
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             f"attachment; filename=selections.csv; filename*=UTF-8''{filename}"})


# ---------------- 教师端：操作日志（仅自己课题相关） ----------------

@app.route("/teacher/logs")
@login_required("teacher")
def teacher_logs():
    conn = get_conn()
    kw = request.args.get("kw", "").strip()
    action = request.args.get("action", "").strip()
    sql = ("SELECT e.*, t.TOPIC_NAME, u.REAL_NAME, u.USER_NO FROM SELECTION_EVENTS e"
           " LEFT JOIN TOPICS t ON t.ID=e.TOPIC_ID LEFT JOIN USERS u ON u.ID=e.USER_ID"
           " WHERE e.FLAG=1 AND (t.TEACHER_ID=? OR e.TOPIC_ID IS NULL)")
    args = [session["user_id"]]
    if kw:
        sql += " AND (t.TOPIC_NAME LIKE ? OR u.REAL_NAME LIKE ? OR u.USER_NO LIKE ?)"
        args += [f"%{kw}%"] * 3
    if action:
        sql += " AND e.ACTION=?"
        args.append(action)
    sql += " ORDER BY e.OPERATED_AT DESC, e.ID DESC LIMIT 300"
    events = conn.execute(sql, args).fetchall()
    conn.close()
    return render_template("teacher/logs.html", events=events, kw=kw, action=action,
                           action_names=ACTION_NAMES, status_names=STATUS_NAMES)


# ---------------- 系统管理员：用户管理（仅平台账户，不涉及课程） ----------------

@app.route("/admin/users", methods=["GET", "POST"])
@login_required("admin")
def admin_users():
    conn = get_conn()
    if request.method == "POST":
        action = request.form.get("action")
        ts = now_str()
        me = session["user_name"]
        if action == "add":
            name = request.form.get("user_name", "").strip()
            no = request.form.get("user_no", "").strip()
            role = request.form.get("role", "")
            if not name or not no or not request.form.get("real_name", "").strip():
                flash("登录名、学号/工号与姓名不能为空", "error")
                conn.close()
                return redirect(url_for("admin_users"))
            advisor = request.form.get("advisor_id") or None
            if role == "student" and not advisor:
                flash("登记学生必须选择所属指导教师", "error")
                conn.close()
                return redirect(url_for("admin_users"))
            if role != "student":
                advisor = None   # 教师/管理员不挂指导教师，避免出现“教师也有导师”的脏数据
            try:
                gpa = float(request.form.get("gpa", 0) or 0)
            except ValueError:
                flash("绩点格式不正确（应为 0~5 的数字）", "error")
                conn.close()
                return redirect(url_for("admin_users"))
            if not 0 <= gpa <= 5:
                flash("绩点需在 0~5 之间", "error")
                conn.close()
                return redirect(url_for("admin_users"))
            if conn.execute("SELECT ID FROM USERS WHERE USER_NAME=? OR USER_NO=?",
                            (name, no)).fetchone():
                flash("登录名或学号/工号已存在", "error")
            else:
                conn.execute(
                    "INSERT INTO USERS(USER_NO,USER_NAME,PASSWORD_HASH,REAL_NAME,CLASS_NAME,GPA,ROLE,ADVISOR_ID,STATUS,CREATED_BY,CREATED_AT,FLAG)"
                    " VALUES(?,?,?,?,?,?,?,?,'active','admin',?,1)",
                    (no, name, generate_password_hash(request.form.get("password") or "123456"),
                     request.form["real_name"].strip(), request.form.get("class_name", "").strip(),
                     gpa, role, advisor, ts))
                flash(f"用户 {name} 已创建，初始密码 {request.form.get('password') or '123456'}", "success")
        elif action == "edit":
            role = request.form.get("role", "")
            advisor = request.form.get("advisor_id") or None
            if not request.form.get("real_name", "").strip():
                flash("姓名不能为空", "error")
                conn.close()
                return redirect(url_for("admin_users"))
            if role == "student" and not advisor:
                flash("学生必须有所属指导教师", "error")
                conn.close()
                return redirect(url_for("admin_users"))
            if role != "student":
                advisor = None
            try:
                gpa = float(request.form.get("gpa", 0) or 0)
            except ValueError:
                flash("绩点格式不正确（应为 0~5 的数字）", "error")
                conn.close()
                return redirect(url_for("admin_users"))
            if not 0 <= gpa <= 5:
                flash("绩点需在 0~5 之间", "error")
                conn.close()
                return redirect(url_for("admin_users"))
            conn.execute(
                "UPDATE USERS SET REAL_NAME=?,CLASS_NAME=?,GPA=?,ROLE=?,ADVISOR_ID=?,UPDATED_BY=?,UPDATED_AT=? WHERE ID=?",
                (request.form["real_name"].strip(), request.form.get("class_name", "").strip(),
                 gpa, role, advisor, me, ts, to_int(request.form.get("user_id"))))
            flash("用户信息已更新", "success")
        elif action == "toggle":
            uid = to_int(request.form.get("user_id"))
            user = conn.execute("SELECT * FROM USERS WHERE ID=?", (uid,)).fetchone()
            if not user:
                flash("用户不存在", "error")
            elif user["ROLE"] == "admin":
                flash("不能停用管理员账号", "error")
            else:
                new_status = "disabled" if user["STATUS"] == "active" else "active"
                conn.execute("UPDATE USERS SET STATUS=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                             (new_status, me, ts, uid))
                flash(f"账号已{'停用' if new_status == 'disabled' else '启用'}", "success")
        elif action == "reset_pwd":
            conn.execute(
                "UPDATE USERS SET PASSWORD_HASH=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                (generate_password_hash("123456"), me, ts, to_int(request.form.get("user_id"))))
            flash("密码已重置为 123456", "success")
        elif action == "delete":
            uid = to_int(request.form.get("user_id"))
            user = conn.execute("SELECT * FROM USERS WHERE ID=?", (uid,)).fetchone()
            if not user:
                flash("用户不存在", "error")
            elif user["ROLE"] == "admin":
                flash("不能删除管理员账号", "error")
            else:
                used = conn.execute(
                    "SELECT COUNT(*) c FROM SELECTIONS WHERE USER_ID=? AND STATUS != 'cancelled'",
                    (uid,)).fetchone()["c"]
                # 教师名下的学生/课题/轮次是其数据归属，删除会造成“孤儿数据”，必须先转移
                dep_students = dep_topics = dep_rounds = 0
                if user["ROLE"] == "teacher":
                    dep_students = conn.execute(
                        "SELECT COUNT(*) c FROM USERS WHERE ADVISOR_ID=? AND ROLE='student' AND FLAG=1",
                        (uid,)).fetchone()["c"]
                    dep_topics = conn.execute(
                        "SELECT COUNT(*) c FROM TOPICS WHERE TEACHER_ID=? AND FLAG=1",
                        (uid,)).fetchone()["c"]
                    dep_rounds = conn.execute(
                        "SELECT COUNT(*) c FROM ROUNDS WHERE TEACHER_ID=? AND FLAG=1",
                        (uid,)).fetchone()["c"]
                if used:
                    flash("该用户有选题记录，不能删除（可停用）", "error")
                elif dep_students or dep_topics or dep_rounds:
                    flash(f"该教师名下仍有 {dep_students} 名学生、{dep_topics} 个课题、"
                          f"{dep_rounds} 个轮次，请先转移学生并处理课题后再删除，或直接停用账号", "error")
                else:
                    conn.execute("UPDATE USERS SET FLAG=0, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                                 (me, ts, uid))
                    flash("用户已删除", "success")
        conn.commit()
        conn.close()
        return redirect(url_for("admin_users"))

    kw = request.args.get("kw", "").strip()
    role = request.args.get("role", "").strip()
    sql = "SELECT * FROM USERS WHERE FLAG=1"
    args = []
    if kw:
        sql += " AND (USER_NAME LIKE ? OR USER_NO LIKE ? OR REAL_NAME LIKE ?)"
        args += [f"%{kw}%"] * 3
    if role:
        sql += " AND ROLE=?"
        args.append(role)
    sql += " ORDER BY ROLE, USER_NO"
    users = conn.execute(sql, args).fetchall()
    teachers = conn.execute(
        "SELECT ID, REAL_NAME, USER_NO FROM USERS WHERE ROLE='teacher' AND FLAG=1 AND STATUS='active'"
    ).fetchall()
    class_suggestions = [r["CLASS_NAME"] for r in conn.execute(
        "SELECT DISTINCT CLASS_NAME FROM USERS WHERE CLASS_NAME IS NOT NULL AND CLASS_NAME != '' ORDER BY CLASS_NAME")]
    # 教师ID->姓名映射，用于展示学生所属教师
    teacher_map = {t["ID"]: t["REAL_NAME"] for t in teachers}
    conn.close()
    return render_template("admin/users.html", users=users, kw=kw, role=role,
                           teachers=teachers, teacher_map=teacher_map,
                           class_suggestions=class_suggestions)


# ---------------- 通用：自助修改密码 ----------------

@app.route("/password", methods=["GET", "POST"])
@login_required("student", "teacher", "admin")
def change_password():
    """已登录用户自助修改密码（需验证原密码）"""
    if request.method == "POST":
        old = request.form.get("old_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not new or new != confirm:
            flash("两次输入的新密码不一致", "error")
        elif len(new) < 6:
            flash("新密码长度至少 6 位", "error")
        else:
            conn = get_conn()
            user = current_user(conn)
            if not check_password_hash(user["PASSWORD_HASH"], old):
                flash("原密码错误", "error")
            else:
                conn.execute(
                    "UPDATE USERS SET PASSWORD_HASH=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                    (generate_password_hash(new), session["user_name"], now_str(), session["user_id"]))
                conn.commit()
                flash("密码修改成功，请牢记新密码", "success")
            conn.close()
        return redirect(url_for("change_password"))
    return render_template("password.html")


# ---------------- 学生端：站内通知（退课提示等） ----------------

@app.route("/student/notices")
@login_required("student")
def student_notices():
    """站内通知列表；进入页面即标记已读"""
    conn = get_conn()
    notices = conn.execute(
        "SELECT * FROM NOTICES WHERE USER_ID=? AND FLAG=1 ORDER BY ID DESC LIMIT 50",
        (session["user_id"],)).fetchall()
    unread = sum(1 for n in notices if not n["READ_FLAG"])
    if unread:
        conn.execute("UPDATE NOTICES SET READ_FLAG=1, UPDATED_BY=?, UPDATED_AT=? WHERE USER_ID=? AND READ_FLAG=0",
                     (session["user_name"], now_str(), session["user_id"]))
        conn.commit()
    conn.close()
    return render_template("student/notices.html", notices=notices, unread=unread)


def unread_notice_count(conn, user_id):
    """未读通知数（供导航栏红点展示）"""
    return conn.execute(
        "SELECT COUNT(*) c FROM NOTICES WHERE USER_ID=? AND READ_FLAG=0 AND FLAG=1",
        (user_id,)).fetchone()["c"]


@app.context_processor
def inject_unread():
    """学生注入未读通知数；教师注入待处理的退选申请数（导航红点）"""
    count = pending_req = 0
    if "user_id" not in session:
        return dict(unread_notices=0, pending_requests=0)
    try:
        conn = get_conn()
        if session.get("role") == "student":
            count = unread_notice_count(conn, session["user_id"])
        elif session.get("role") == "teacher":
            pending_req = conn.execute(
                "SELECT COUNT(*) c FROM CANCEL_REQUESTS q JOIN TOPICS t ON t.ID=q.TOPIC_ID"
                " WHERE q.STATUS='pending' AND q.FLAG=1 AND t.TEACHER_ID=?",
                (session["user_id"],)).fetchone()["c"]
        conn.close()
    except Exception:
        count = pending_req = 0
    return dict(unread_notices=count, pending_requests=pending_req)


# ---------------- 静态展示页（原型 / 项目主页 / 使用流程） ----------------

PROJECT_ROOT = os.path.dirname(BASE_DIR)  # student-topic-system 目录


@app.route("/prototype")
def prototype_page():
    """可交互原型（无需登录，方便交叉评估时查看）"""
    return send_file(os.path.join(PROJECT_ROOT, "prototype", "prototype.html"))


@app.route("/home")
def home_page():
    """项目介绍主页"""
    return send_file(os.path.join(PROJECT_ROOT, "website", "index.html"))


@app.route("/index.html")
def home_page_html():
    """兼容相对链接：网站内互相跳转使用"""
    return home_page()


@app.route("/flow.html")
def guide_page_html():
    """兼容相对链接"""
    return guide_page()


@app.route("/guide")
def guide_page():
    """图文版使用流程"""
    return send_file(os.path.join(PROJECT_ROOT, "website", "flow.html"))


@app.route("/screenshots/<path:filename>")
def screenshots(filename):
    """文档截图（供项目主页展示）"""
    return send_file(os.path.join(PROJECT_ROOT, "wiki", "screenshots", filename))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
