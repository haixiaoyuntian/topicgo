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
import io
import os
import random
import sqlite3
from functools import wraps
from urllib.parse import quote

from flask import (Flask, flash, redirect, render_template, request,
                   Response, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from db import BASE_DIR, get_conn, init_db, now_str

app = Flask(__name__)
app.secret_key = "topicgo-secret-key"

init_db()

RULE_NAMES = {
    "first_come": "先到先得",
    "gpa_priority": "绩点优先",
    "multi_wish": "多志愿分配",
    "lottery": "随机抽签",
}
ACTION_NAMES = {
    "select": "选题/报名",
    "cancel": "退选",
    "promote": "递补",
    "allocate": "志愿分配",
    "lottery": "抽签",
}
STATUS_NAMES = {
    "confirmed": "已确认",
    "waiting": "候补中",
    "wish": "志愿待分配",
    "entry": "已报名待抽签",
    "cancelled": "已退选",
}


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
    """指定教师当前开放的选题轮次（状态 open 且处于时间窗口内）"""
    if not teacher_id:
        return None
    now = now_str()
    return conn.execute(
        "SELECT * FROM ROUNDS WHERE TEACHER_ID=? AND STATUS='open' AND START_AT<=? AND END_AT>=? AND FLAG=1",
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
    conn.close()
    return render_template("student/index.html", user=user, advisor=advisor, round=rnd,
                           confirmed=confirmed, waiting=waiting, wishes=wishes,
                           topic_total=topic_total, topic_confirmed=topic_confirmed,
                           announcements=announcements,
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
            session.clear()
            session.update(user_id=user["ID"], user_name=user["USER_NAME"],
                           real_name=user["REAL_NAME"], role=user["ROLE"])
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
    # 学生只能看到自己指导教师发布的课题
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
    topics = conn.execute(sql + " ORDER BY TOPIC_CODE", args).fetchall() if advisor else []
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
    conn.close()
    return render_template("student/browse.html", topics=topics, stats=stats, wishes=wishes,
                           entries=entries, mine=mine, round=rnd, tags=tags, kw=kw, tag=tag,
                           diff=diff, rule_name=RULE_NAMES.get(rnd["RULE"] if rnd else "", ""),
                           class_ok=class_ok, advisor=advisor)


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
    if user_active_selection(conn, rnd["ID"], user["ID"]):
        flash("每人限选 1 题，你已有选题记录", "error")
        conn.close()
        return redirect(url_for("student_browse"))
    if rnd["RULE"] == "gpa_priority":
        higher = conn.execute(
            "SELECT COUNT(*) c FROM USERS u WHERE u.ROLE='student' AND u.GPA>? AND u.FLAG=1"
            " AND u.STATUS='active' AND u.ADVISOR_ID=? AND NOT EXISTS(SELECT 1 FROM SELECTIONS s"
            " WHERE s.ROUND_ID=? AND s.USER_ID=u.ID AND s.STATUS='confirmed')",
            (user["GPA"], user["ADVISOR_ID"], rnd["ID"]),
        ).fetchone()["c"]
        if higher > 0:
            flash(f"绩点优先模式下，还有 {higher} 位绩点高于你的同学未选题，请稍候", "error")
            conn.close()
            return redirect(url_for("student_browse"))
    # 只能选自己老师发布的、且面向自己班级的课题
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
        ts = now_str()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for item in picked:
                wish_no, code = item.split(":")
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
    ts = now_str()
    conn.execute("BEGIN IMMEDIATE")
    # 1) 原记录置为 cancelled（保留历史，不删除）
    conn.execute(
        "UPDATE SELECTIONS SET STATUS='cancelled', CANCELLED_AT=?, QUEUE_NO=NULL,"
        " UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
        (ts, session["user_name"], ts, selection_id))
    log_event(conn, sel["ROUND_ID"], sel["TOPIC_ID"], sel["USER_ID"], "cancel",
              sel["STATUS"], "cancelled", wish_no=sel["WISH_NO"],
              queue_no=sel["QUEUE_NO"], operator=session["user_name"])
    promoted = None
    # 2) 仅"已确认"退选才释放名额；候补退出不触发递补
    if sel["STATUS"] == "confirmed":
        if rnd["PROMOTE_MODE"] == "auto":
            promoted = promote_next(conn, rnd, sel["TOPIC_ID"], operator="system")
        elif rnd["PROMOTE_MODE"] == "manual":
            log_event(conn, sel["ROUND_ID"], sel["TOPIC_ID"], None, "promote",
                      remark="名额待教师手动递补", operator="system")
    conn.execute("COMMIT")
    msg = "已退选，记录已保留在「我的记录」中"
    if promoted:
        msg += "，候补同学已自动递补"
    elif sel["STATUS"] == "confirmed" and rnd["PROMOTE_MODE"] == "manual":
        msg += "，名额待教师手动递补"
    flash(msg, "success")
    conn.close()
    return redirect(url_for("student_mine"))


@app.route("/student/mine")
@login_required("student")
def student_mine():
    """我的选题 + 历史记录（含退选痕迹）"""
    conn = get_conn()
    user = current_user(conn)
    rnd = active_round(conn, user["ADVISOR_ID"]) or own_round_for_student(conn, user)
    active, history = [], []
    if rnd:
        rows = conn.execute(
            "SELECT s.*, t.TOPIC_NAME, t.TOPIC_CODE, t.MAX_NUM, r.ROUND_NAME, r.RULE"
            " FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID JOIN ROUNDS r ON r.ID=s.ROUND_ID"
            " WHERE s.ROUND_ID=? AND s.USER_ID=? ORDER BY s.ID DESC",
            (rnd["ID"], user["ID"]),
        ).fetchall()
        active = [r for r in rows if r["STATUS"] in ("confirmed", "waiting", "wish", "entry")]
        history = [r for r in rows if r["STATUS"] == "cancelled"]
    conn.close()
    return render_template("student/mine.html", active=active, history=history, round=rnd,
                           rule_name=RULE_NAMES.get(rnd["RULE"] if rnd else "", ""),
                           status_names=STATUS_NAMES)


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
    """结果公示：只能看到自己指导教师课题的选题结果"""
    conn = get_conn()
    user = current_user(conn)
    rows = conn.execute(
        "SELECT t.TOPIC_CODE, t.TOPIC_NAME, u.REAL_NAME, u.CLASS_NAME, s.SELECTED_AT, s.STATUS"
        " FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID JOIN USERS u ON u.ID=s.USER_ID"
        " WHERE s.STATUS='confirmed' AND t.TEACHER_ID=? ORDER BY t.TOPIC_CODE, s.SELECTED_AT",
        (user["ADVISOR_ID"],),
    ).fetchall()
    conn.close()
    return render_template("student/public.html", rows=rows)


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
    picked_students = sum(1 for s in students if s["ID"] in sel_map and sel_map[s["ID"]]["STATUS"] == "confirmed")
    classes = teacher_classes(conn, session["user_id"])
    conn.close()
    return render_template("teacher/index.html", round=rnd, topics=topics, students=students,
                           sel_map=sel_map, todo_wish=todo_wish, todo_entry=todo_entry,
                           todo_wait=todo_wait, picked_students=picked_students,
                           rule_names=RULE_NAMES, classes=classes)


# ---------------- 教师端：我的学生（选题进度一览） ----------------

@app.route("/teacher/students")
@login_required("teacher")
def teacher_students():
    conn = get_conn()
    rnd = active_round(conn, session["user_id"]) or own_round(conn)
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
                "SELECT s.USER_ID, s.STATUS, s.WISH_NO, s.QUEUE_NO, t.TOPIC_NAME, t.TOPIC_CODE"
                " FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID"
                " WHERE s.ROUND_ID=? AND s.STATUS IN('confirmed','waiting','wish','entry')",
                (rnd["ID"],)).fetchall():
            sel_map[r["USER_ID"]] = r
    rows = []
    for s in students:
        sel = sel_map.get(s["ID"])
        rows.append(dict(student=s, sel=sel,
                         status=sel["STATUS"] if sel else "none"))
    if status_filter:
        rows = [r for r in rows if r["status"] == status_filter]
    classes = teacher_classes(conn, session["user_id"])
    conn.close()
    return render_template("teacher/students.html", rows=rows, round=rnd, kw=kw,
                           status_filter=status_filter, classes=classes)


# ---------------- 教师端：公告管理 ----------------

@app.route("/teacher/announce", methods=["GET", "POST"])
@login_required("teacher")
def teacher_announce():
    conn = get_conn()
    if request.method == "POST":
        action = request.form.get("action")
        ts = now_str()
        if action == "add":
            conn.execute(
                "INSERT INTO ANNOUNCEMENTS(TITLE,CONTENT,TEACHER_ID,PUBLISHED_AT,CREATED_BY,CREATED_AT,FLAG)"
                " VALUES(?,?,?,?,?,?,1)",
                (request.form["title"].strip(), request.form.get("content", "").strip(),
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
        if action == "add":
            code = request.form["topic_code"].strip()
            if conn.execute("SELECT ID FROM TOPICS WHERE TOPIC_CODE=?", (code,)).fetchone():
                flash("题目编号已存在", "error")
            else:
                target = ",".join(request.form.getlist("target_classes"))
                conn.execute(
                    "INSERT INTO TOPICS(TOPIC_CODE,TOPIC_NAME,TOPIC_DESC,TAG,DIFFICULTY,MAX_NUM,TEACHER_ID,TARGET_CLASSES,CREATED_BY,CREATED_AT,FLAG)"
                    " VALUES(?,?,?,?,?,?,?,?,'teacher',?,1)",
                    (code, request.form["topic_name"].strip(), request.form.get("topic_desc", ""),
                     request.form.get("tag", ""), request.form.get("difficulty", "中等"),
                     int(request.form.get("max_num", 4)), session["user_id"], target, ts))
                flash("题目已创建", "success")
        elif action == "edit":
            # 仅能修改自己的课题
            own = conn.execute("SELECT ID FROM TOPICS WHERE ID=? AND TEACHER_ID=?",
                               (int(request.form["topic_id"]), session["user_id"])).fetchone()
            if not own:
                flash("无权修改他人课题", "error")
            else:
                target = ",".join(request.form.getlist("target_classes"))
                conn.execute(
                    "UPDATE TOPICS SET TOPIC_NAME=?,TOPIC_DESC=?,TAG=?,DIFFICULTY=?,MAX_NUM=?,TARGET_CLASSES=?,UPDATED_BY=?,UPDATED_AT=? WHERE ID=?",
                    (request.form["topic_name"].strip(), request.form.get("topic_desc", ""),
                     request.form.get("tag", ""), request.form.get("difficulty", "中等"),
                     int(request.form.get("max_num", 4)), target, me, ts, int(request.form["topic_id"])))
                flash("题目已更新", "success")
        conn.commit()
        conn.close()
        return redirect(url_for("teacher_topics"))
    topics = conn.execute(
        "SELECT * FROM TOPICS WHERE FLAG=1 AND TEACHER_ID=? ORDER BY TOPIC_CODE",
        (session["user_id"],)).fetchall()
    classes = teacher_classes(conn, session["user_id"])
    tag_suggestions = [r["TAG"] for r in conn.execute(
        "SELECT DISTINCT TAG FROM TOPICS WHERE TAG IS NOT NULL AND TAG != '' ORDER BY TAG")]
    conn.close()
    return render_template("teacher/topics.html", topics=topics, classes=classes,
                           tag_suggestions=tag_suggestions)


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
        rnd_id = int(request.form["round_id"])
        own = conn.execute("SELECT ID FROM ROUNDS WHERE ID=? AND TEACHER_ID=?",
                           (rnd_id, session["user_id"])).fetchone()
        if not own:
            flash("无权修改他人轮次", "error")
            conn.close()
            return redirect(url_for("teacher_round"))
        conn.execute(
            "UPDATE ROUNDS SET ROUND_NAME=?,RULE=?,START_AT=?,END_AT=?,WAITLIST_ON=?,PROMOTE_MODE=?,"
            "MAX_WISH=?,LOTTERY_SEED=?,ALLOW_CLASSES=?,STATUS=?,UPDATED_BY=?,UPDATED_AT=? WHERE ID=?",
            (request.form["round_name"], request.form["rule"], request.form["start_at"],
             request.form["end_at"], 1 if request.form.get("waitlist_on") else 0,
             request.form.get("promote_mode", "auto"), int(request.form.get("max_wish", 3)),
             request.form.get("lottery_seed", "").strip() or None,
             request.form.get("allow_classes", "").strip() or None,
             request.form["status"], session["user_name"], ts, rnd_id))
        conn.commit()
        flash("轮次设置已保存", "success")
        conn.close()
        return redirect(url_for("teacher_round"))
    rnd = active_round(conn, session["user_id"]) or own_round(conn)
    classes = teacher_classes(conn, session["user_id"])
    conn.close()
    return render_template("teacher/round.html", round=rnd, rule_names=RULE_NAMES, classes=classes)


# ---------------- 教师端：监控与策略执行（仅自己的课题） ----------------

@app.route("/teacher/monitor")
@login_required("teacher")
def teacher_monitor():
    conn = get_conn()
    rnd = active_round(conn, session["user_id"]) or own_round(conn)
    rows = []
    total_picked = total_left = wait_total = wish_total = entry_total = 0
    waiters = {}
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
    conn.close()
    return render_template("teacher/monitor.html", rows=rows, round=rnd,
                           total_picked=total_picked, total_left=total_left,
                           wait_total=wait_total, wish_total=wish_total,
                           entry_total=entry_total, rule_names=RULE_NAMES, waiters=waiters)


@app.route("/teacher/allocate", methods=["POST"])
@login_required("teacher")
def teacher_allocate():
    """多志愿统一分配：按志愿序号依次处理，同志愿超员按绩点优先，未命中转第一志愿候补"""
    conn = get_conn()
    rnd = own_round(conn)
    if not rnd or rnd["RULE"] != "multi_wish":
        flash("当前轮次不是多志愿模式", "error")
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
        stats = {}
        for w in all_wishes:
            if w["USER_ID"] in assigned:
                continue
            topic = conn.execute("SELECT * FROM TOPICS WHERE ID=?", (w["TOPIC_ID"],)).fetchone()
            used = stats.get(w["TOPIC_ID"], 0)
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
        for w in all_wishes:
            if w["ID"] in promoted_ids:
                continue
            if w["USER_ID"] in assigned:
                conn.execute(
                    "UPDATE SELECTIONS SET STATUS='cancelled', UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                    (session["user_name"], ts, w["ID"]))
                log_event(conn, rnd["ID"], w["TOPIC_ID"], w["USER_ID"], "allocate",
                          "wish", "cancelled", wish_no=w["WISH_NO"],
                          operator=session["user_name"], remark="已命中其他志愿，本志愿作废")
                continue
            if w["WISH_NO"] == 1:
                q = conn.execute(
                    "SELECT COALESCE(MAX(QUEUE_NO),0)+1 q FROM SELECTIONS"
                    " WHERE ROUND_ID=? AND TOPIC_ID=? AND STATUS='waiting'",
                    (rnd["ID"], w["TOPIC_ID"])).fetchone()["q"]
                conn.execute(
                    "UPDATE SELECTIONS SET STATUS='waiting', QUEUE_NO=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                    (q, session["user_name"], ts, w["ID"]))
                log_event(conn, rnd["ID"], w["TOPIC_ID"], w["USER_ID"], "allocate",
                          "wish", "waiting", wish_no=w["WISH_NO"], queue_no=q,
                          operator=session["user_name"], remark="未命中志愿，转第一志愿候补")
                assigned.add(w["USER_ID"])
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
    rnd = own_round(conn)
    if not rnd or rnd["RULE"] != "lottery":
        flash("当前轮次不是随机抽签模式", "error")
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
                if i < t["MAX_NUM"]:
                    conn.execute(
                        "UPDATE SELECTIONS SET STATUS='confirmed', UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                        (session["user_name"], ts, e["ID"]))
                    log_event(conn, rnd["ID"], t["ID"], e["USER_ID"], "lottery",
                              "entry", "confirmed", operator=session["user_name"],
                              remark=f"抽签中签（种子 {seed}）")
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
        flash(f"抽签完成（种子：{seed}，任何人可用该种子复现验证结果）", "success")
    except Exception as e:
        conn.execute("ROLLBACK")
        flash(f"抽签失败：{e}", "error")
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
    conn.execute("COMMIT")
    flash("已手动递补", "success")
    conn.close()
    return redirect(url_for("teacher_monitor"))


@app.route("/teacher/export")
@login_required("teacher")
def teacher_export():
    conn = get_conn()
    rows = conn.execute(
        "SELECT t.TOPIC_CODE, t.TOPIC_NAME, u.USER_NO, u.REAL_NAME, u.CLASS_NAME, u.GPA,"
        " s.STATUS, s.WISH_NO, s.QUEUE_NO, s.SELECTED_AT FROM SELECTIONS s"
        " JOIN TOPICS t ON t.ID=s.TOPIC_ID JOIN USERS u ON u.ID=s.USER_ID"
        " WHERE s.STATUS IN('confirmed','waiting') AND t.TEACHER_ID=?"
        " ORDER BY t.TOPIC_CODE, s.STATUS, s.QUEUE_NO",
        (session["user_id"],),
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
            name = request.form["user_name"].strip()
            no = request.form["user_no"].strip()
            role = request.form["role"]
            advisor = request.form.get("advisor_id") or None
            if role == "student" and not advisor:
                flash("登记学生必须选择所属指导教师", "error")
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
                     float(request.form.get("gpa", 0) or 0), role, advisor, ts))
                flash(f"用户 {name} 已创建，初始密码 {request.form.get('password') or '123456'}", "success")
        elif action == "edit":
            role = request.form["role"]
            advisor = request.form.get("advisor_id") or None
            if role == "student" and not advisor:
                flash("学生必须有所属指导教师", "error")
                conn.close()
                return redirect(url_for("admin_users"))
            conn.execute(
                "UPDATE USERS SET REAL_NAME=?,CLASS_NAME=?,GPA=?,ROLE=?,ADVISOR_ID=?,UPDATED_BY=?,UPDATED_AT=? WHERE ID=?",
                (request.form["real_name"].strip(), request.form.get("class_name", "").strip(),
                 float(request.form.get("gpa", 0) or 0), role, advisor, me, ts,
                 int(request.form["user_id"])))
            flash("用户信息已更新", "success")
        elif action == "toggle":
            uid = int(request.form["user_id"])
            user = conn.execute("SELECT * FROM USERS WHERE ID=?", (uid,)).fetchone()
            if user["ROLE"] == "admin":
                flash("不能停用管理员账号", "error")
            else:
                new_status = "disabled" if user["STATUS"] == "active" else "active"
                conn.execute("UPDATE USERS SET STATUS=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                             (new_status, me, ts, uid))
                flash(f"账号已{'停用' if new_status == 'disabled' else '启用'}", "success")
        elif action == "reset_pwd":
            conn.execute(
                "UPDATE USERS SET PASSWORD_HASH=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                (generate_password_hash("123456"), me, ts, int(request.form["user_id"])))
            flash("密码已重置为 123456", "success")
        elif action == "delete":
            uid = int(request.form["user_id"])
            user = conn.execute("SELECT * FROM USERS WHERE ID=?", (uid,)).fetchone()
            if user["ROLE"] == "admin":
                flash("不能删除管理员账号", "error")
            else:
                used = conn.execute(
                    "SELECT COUNT(*) c FROM SELECTIONS WHERE USER_ID=? AND STATUS != 'cancelled'",
                    (uid,)).fetchone()["c"]
                if used:
                    flash("该用户有选题记录，不能删除（可停用）", "error")
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
