"""学生选题系统 TopicGo - 应用入口与路由"""
import csv
import io
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (Flask, flash, redirect, render_template, request,
                   Response, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_conn, init_db, now_str

app = Flask(__name__)
app.secret_key = "topicgo-secret-key"

init_db()


# ---------------- 工具 ----------------

def login_required(role=None):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                flash("无权限访问", "error")
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return wrapper
    return deco


def current_user(conn):
    return conn.execute("SELECT * FROM USERS WHERE ID=?", (session["user_id"],)).fetchone()


def active_round(conn):
    """返回当前开放的选题轮次（按窗口时间与状态判断）"""
    now = now_str()
    return conn.execute(
        "SELECT * FROM ROUNDS WHERE STATUS='open' AND START_AT<=? AND END_AT>=? AND FLAG=1",
        (now, now),
    ).fetchone()


def topic_stats(conn, round_id):
    rows = conn.execute(
        "SELECT TOPIC_ID, COUNT(*) c FROM SELECTIONS WHERE ROUND_ID=? AND STATUS='confirmed' GROUP BY TOPIC_ID",
        (round_id,),
    ).fetchall()
    return {r["TOPIC_ID"]: r["c"] for r in rows}


def wait_counts(conn, round_id):
    rows = conn.execute(
        "SELECT TOPIC_ID, COUNT(*) c FROM SELECTIONS WHERE ROUND_ID=? AND STATUS='waiting' GROUP BY TOPIC_ID",
        (round_id,),
    ).fetchall()
    return {r["TOPIC_ID"]: r["c"] for r in rows}


# ---------------- 认证 ----------------

@app.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return redirect(url_for("student_browse" if session["role"] == "student" else "teacher_topics"))


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
        if user and check_password_hash(user["PASSWORD_HASH"], pwd):
            session.clear()
            session.update(user_id=user["ID"], user_name=user["USER_NAME"],
                           real_name=user["REAL_NAME"], role=user["ROLE"])
            return redirect(url_for("index"))
        flash("账号或密码错误", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- 学生端 ----------------

@app.route("/student/browse")
@login_required()
def student_browse():
    kw = request.args.get("kw", "").strip()
    tag = request.args.get("tag", "").strip()
    diff = request.args.get("diff", "").strip()
    conn = get_conn()
    rnd = active_round(conn)
    sql = "SELECT * FROM TOPICS WHERE STATUS='open' AND FLAG=1"
    args = []
    if kw:
        sql += " AND (TOPIC_NAME LIKE ? OR TOPIC_DESC LIKE ?)"
        args += [f"%{kw}%", f"%{kw}%"]
    if tag:
        sql += " AND TAG=?"
        args.append(tag)
    if diff:
        sql += " AND DIFFICULTY=?"
        args.append(diff)
    topics = conn.execute(sql + " ORDER BY TOPIC_CODE", args).fetchall()
    stats = topic_stats(conn, rnd["ID"]) if rnd else {}
    waits = wait_counts(conn, rnd["ID"]) if rnd else {}
    mine = None
    if session["role"] == "student" and rnd:
        mine = conn.execute(
            "SELECT s.*, t.TOPIC_NAME FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID"
            " WHERE s.ROUND_ID=? AND s.USER_ID=? AND s.STATUS IN('confirmed','waiting')",
            (rnd["ID"], session["user_id"]),
        ).fetchone()
    tags = [r["TAG"] for r in conn.execute("SELECT DISTINCT TAG FROM TOPICS WHERE TAG IS NOT NULL")]
    conn.close()
    return render_template("student/browse.html", topics=topics, stats=stats, waits=waits,
                           mine=mine, round=rnd, tags=tags, kw=kw, tag=tag, diff=diff)


@app.route("/student/select/<int:topic_id>", methods=["POST"])
@login_required("student")
def student_select(topic_id):
    conn = get_conn()
    user = current_user(conn)
    rnd = active_round(conn)
    if not rnd:
        flash("当前不在选题窗口期内", "error")
        return redirect(url_for("student_browse"))
    # 是否已有选题
    exist = conn.execute(
        "SELECT ID FROM SELECTIONS WHERE ROUND_ID=? AND USER_ID=? AND STATUS IN('confirmed','waiting')",
        (rnd["ID"], user["ID"]),
    ).fetchone()
    if exist:
        flash("每人限选 1 题，你已有选题记录", "error")
        conn.close()
        return redirect(url_for("student_browse"))
    # 绩点优先模式：高绩点未选同学优先
    if rnd["RULE"] == "gpa_priority":
        higher = conn.execute(
            "SELECT COUNT(*) c FROM USERS u WHERE u.ROLE='student' AND u.GPA>? AND u.FLAG=1"
            " AND NOT EXISTS(SELECT 1 FROM SELECTIONS s WHERE s.ROUND_ID=? AND s.USER_ID=u.ID"
            " AND s.STATUS='confirmed')",
            (user["GPA"], rnd["ID"]),
        ).fetchone()["c"]
        if higher > 0:
            flash(f"绩点优先模式下，还有 {higher} 位绩点高于你的同学未选题，请稍候", "error")
            conn.close()
            return redirect(url_for("student_browse"))
    topic = conn.execute("SELECT * FROM TOPICS WHERE ID=?", (topic_id,)).fetchone()
    if not topic or topic["STATUS"] != "open":
        flash("题目不存在或已关闭", "error")
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
            msg = f"选题成功：{topic['TOPIC_NAME']}"
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
            msg = f"《{topic['TOPIC_NAME']}》已满员，你已加入候补第 {queue_no} 位"
        else:
            msg = "该题目已满员且候补未开启"
        conn.execute("COMMIT")
        flash(msg, "success" if "成功" in msg or "候补" in msg else "error")
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        flash("你已有该轮次的选题记录", "error")
    finally:
        conn.close()
    return redirect(url_for("student_browse"))


@app.route("/student/cancel/<int:selection_id>", methods=["POST"])
@login_required("student")
def student_cancel(selection_id):
    conn = get_conn()
    sel = conn.execute("SELECT * FROM SELECTIONS WHERE ID=?", (selection_id,)).fetchone()
    if not sel or sel["USER_ID"] != session["user_id"] or sel["STATUS"] != "confirmed":
        flash("记录不存在", "error")
        conn.close()
        return redirect(url_for("student_browse"))
    ts = now_str()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE SELECTIONS SET STATUS='cancelled', CANCELLED_AT=?, UPDATED_BY=?, UPDATED_AT=? WHERE ID=?",
                 (ts, session["user_name"], ts, selection_id))
    # 候补递补：队列第一位递补
    next_w = conn.execute(
        "SELECT * FROM SELECTIONS WHERE ROUND_ID=? AND TOPIC_ID=? AND STATUS='waiting'"
        " ORDER BY QUEUE_NO LIMIT 1",
        (sel["ROUND_ID"], sel["TOPIC_ID"]),
    ).fetchone()
    promoted = None
    if next_w:
        conn.execute(
            "UPDATE SELECTIONS SET STATUS='confirmed', QUEUE_NO=NULL, UPDATED_BY='system', UPDATED_AT=? WHERE ID=?",
            (ts, next_w["ID"]),
        )
        promoted = next_w["USER_ID"]
    conn.execute("COMMIT")
    flash("已退选" + ("，候补同学已自动递补" if promoted else ""), "success")
    conn.close()
    return redirect(url_for("student_browse"))


@app.route("/student/mine")
@login_required()
def student_mine():
    conn = get_conn()
    rnd = active_round(conn)
    rows = []
    if session["role"] == "student" and rnd:
        rows = conn.execute(
            "SELECT s.*, t.TOPIC_NAME, t.TOPIC_CODE, t.MAX_NUM, r.ROUND_NAME FROM SELECTIONS s"
            " JOIN TOPICS t ON t.ID=s.TOPIC_ID JOIN ROUNDS r ON r.ID=s.ROUND_ID"
            " WHERE s.ROUND_ID=? AND s.USER_ID=? ORDER BY s.ID DESC",
            (rnd["ID"], session["user_id"]),
        ).fetchall()
    conn.close()
    return render_template("student/mine.html", rows=rows, round=rnd)


@app.route("/student/public")
@login_required()
def student_public():
    conn = get_conn()
    rows = conn.execute(
        "SELECT t.TOPIC_CODE, t.TOPIC_NAME, u.REAL_NAME, u.CLASS_NAME, s.SELECTED_AT, s.STATUS"
        " FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID JOIN USERS u ON u.ID=s.USER_ID"
        " WHERE s.STATUS='confirmed' ORDER BY t.TOPIC_CODE, s.SELECTED_AT"
    ).fetchall()
    conn.close()
    return render_template("student/public.html", rows=rows)


# ---------------- 教师端 ----------------

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
                conn.execute(
                    "INSERT INTO TOPICS(TOPIC_CODE,TOPIC_NAME,TOPIC_DESC,TAG,DIFFICULTY,MAX_NUM,TEACHER_ID,CREATED_BY,CREATED_AT,FLAG)"
                    " VALUES(?,?,?,?,?,?,?,'teacher',?,1)",
                    (code, request.form["topic_name"].strip(), request.form.get("topic_desc", ""),
                     request.form.get("tag", ""), request.form.get("difficulty", "中等"),
                     int(request.form.get("max_num", 4)), session["user_id"], ts),
                )
                flash("题目已创建", "success")
        elif action == "edit":
            conn.execute(
                "UPDATE TOPICS SET TOPIC_NAME=?,TOPIC_DESC=?,TAG=?,DIFFICULTY=?,MAX_NUM=?,UPDATED_BY=?,UPDATED_AT=? WHERE ID=?",
                (request.form["topic_name"].strip(), request.form.get("topic_desc", ""),
                 request.form.get("tag", ""), request.form.get("difficulty", "中等"),
                 int(request.form.get("max_num", 4)), me, ts, int(request.form["topic_id"])),
            )
            flash("题目已更新", "success")
        conn.commit()
        conn.close()
        return redirect(url_for("teacher_topics"))
    topics = conn.execute("SELECT * FROM TOPICS WHERE FLAG=1 ORDER BY TOPIC_CODE").fetchall()
    conn.close()
    return render_template("teacher/topics.html", topics=topics)


@app.route("/teacher/topics/delete/<int:topic_id>", methods=["POST"])
@login_required("teacher")
def teacher_topic_delete(topic_id):
    conn = get_conn()
    used = conn.execute("SELECT COUNT(*) c FROM SELECTIONS WHERE TOPIC_ID=? AND STATUS IN('confirmed','waiting')",
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


@app.route("/teacher/round", methods=["GET", "POST"])
@login_required("teacher")
def teacher_round():
    conn = get_conn()
    if request.method == "POST":
        ts = now_str()
        conn.execute(
            "UPDATE ROUNDS SET ROUND_NAME=?,RULE=?,START_AT=?,END_AT=?,WAITLIST_ON=?,STATUS=?,"
            "UPDATED_BY=?,UPDATED_AT=? WHERE ID=?",
            (request.form["round_name"], request.form["rule"], request.form["start_at"],
             request.form["end_at"], 1 if request.form.get("waitlist_on") else 0,
             request.form["status"], session["user_name"], ts, int(request.form["round_id"])),
        )
        conn.commit()
        flash("轮次设置已保存", "success")
        conn.close()
        return redirect(url_for("teacher_round"))
    rnd = active_round(conn) or conn.execute(
        "SELECT * FROM ROUNDS WHERE FLAG=1 ORDER BY ID DESC").fetchone()
    conn.close()
    return render_template("teacher/round.html", round=rnd)


@app.route("/teacher/monitor")
@login_required("teacher")
def teacher_monitor():
    conn = get_conn()
    rnd = active_round(conn) or conn.execute(
        "SELECT * FROM ROUNDS WHERE FLAG=1 ORDER BY ID DESC").fetchone()
    rows = []
    total_picked = total_left = wait_total = 0
    if rnd:
        stats = topic_stats(conn, rnd["ID"])
        waits = wait_counts(conn, rnd["ID"])
        topics = conn.execute("SELECT * FROM TOPICS WHERE FLAG=1 ORDER BY TOPIC_CODE").fetchall()
        for t in topics:
            picked = stats.get(t["ID"], 0)
            waiting = waits.get(t["ID"], 0)
            total_picked += picked
            total_left += max(0, t["MAX_NUM"] - picked)
            wait_total += waiting
            rows.append(dict(topic=t, picked=picked, left=t["MAX_NUM"] - picked,
                             waiting=waiting))
    conn.close()
    return render_template("teacher/monitor.html", rows=rows, round=rnd,
                           total_picked=total_picked, total_left=total_left,
                           wait_total=wait_total)


@app.route("/teacher/export")
@login_required("teacher")
def teacher_export():
    conn = get_conn()
    rows = conn.execute(
        "SELECT t.TOPIC_CODE, t.TOPIC_NAME, u.USER_NO, u.REAL_NAME, u.CLASS_NAME, u.GPA,"
        " s.STATUS, s.QUEUE_NO, s.SELECTED_AT FROM SELECTIONS s"
        " JOIN TOPICS t ON t.ID=s.TOPIC_ID JOIN USERS u ON u.ID=s.USER_ID"
        " WHERE s.STATUS IN('confirmed','waiting') ORDER BY t.TOPIC_CODE, s.STATUS, s.QUEUE_NO"
    ).fetchall()
    conn.close()
    buf = io.StringIO()
    buf.write("\ufeff")  # Excel 中文兼容
    w = csv.writer(buf)
    w.writerow(["题目编号", "题目名称", "学号", "姓名", "班级", "绩点", "状态", "候补序号", "选题时间"])
    for r in rows:
        w.writerow([r["TOPIC_CODE"], r["TOPIC_NAME"], r["USER_NO"], r["REAL_NAME"],
                    r["CLASS_NAME"], r["GPA"], "已确认" if r["STATUS"] == "confirmed" else "候补中",
                    r["QUEUE_NO"] or "", r["SELECTED_AT"]])
    from urllib.parse import quote
    filename = quote("选题结果.csv")
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             f"attachment; filename=selections.csv; filename*=UTF-8''{filename}"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
