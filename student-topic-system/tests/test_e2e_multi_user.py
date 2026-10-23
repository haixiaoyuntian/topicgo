"""多用户协同 E2E 测试：2 位教师 + 12 名学生、3 个班级共同操作的完整场景

场景设计：
  李老师(e2e_t2) 管 C 班(4人)      —— 多志愿模式
  王老师(e2e_t1) 管 A 班(4人)+B 班(4人) —— 先到先得模式
  TP01 仅面向A班(上限2) / TP02 面向所有(上限2) / TP03 仅面向C班(上限1) / TP04 面向所有(上限1)

验证点：并发选题不超卖、班级隔离、候补递补、志愿分配、数据可见性、日志隔离
"""
import re
import sqlite3
import threading
import urllib.request
import urllib.parse

from werkzeug.security import generate_password_hash

BASE = "http://127.0.0.1:5000"
DB = "topicgo.db"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("PASS" if cond else "FAIL"), name, detail)


def sql(q, args=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(q, args).fetchall()
    conn.close()
    return rows


def fetch_csrf(op, path="/login"):
    """从页面 meta 标签获取当前会话的 CSRF 令牌"""
    try:
        html = op.open(BASE + path).read().decode()
    except Exception:
        return ""
    m = re.search(r'name="csrf-token" content="([0-9a-f]+)"', html)
    return m.group(1) if m else ""


def login(who):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    token = fetch_csrf(op)
    op._csrf = token
    op.open(BASE + "/login", urllib.parse.urlencode(
        {"username": who, "password": "123456", "_csrf_token": token}).encode())
    return op


def post(op, path, data=None):
    data = dict(data or {})
    token = getattr(op, "_csrf", None)
    if not token:
        token = fetch_csrf(op, "/")
        op._csrf = token
    data["_csrf_token"] = token
    body = urllib.parse.urlencode(data, doseq=True).encode() if data else None
    try:
        return op.open(urllib.request.Request(BASE + path, data=body, method="POST")).read().decode()
    except Exception as e:
        return str(e)


def get(op, path):
    try:
        return op.open(BASE + path).read().decode()
    except Exception as e:
        return str(e)


def uid(name):
    return sql("SELECT ID FROM USERS WHERE USER_NAME=?", (name,))[0]["ID"]


# ---------- 0. 准备测试数据 ----------
print("== 0. 准备数据 ==")
conn = sqlite3.connect(DB)
# 清理旧测试数据
for name in ["e2e_t1", "e2e_t2"] + [f"e2e_a{i}" for i in range(1, 5)] + \
            [f"e2e_b{i}" for i in range(1, 5)] + [f"e2e_c{i}" for i in range(1, 5)]:
    row = conn.execute("SELECT ID FROM USERS WHERE USER_NAME=?", (name,)).fetchone()
    if row:
        conn.execute("DELETE FROM SELECTIONS WHERE USER_ID=?", (row[0],))
        conn.execute("DELETE FROM SELECTION_EVENTS WHERE USER_ID=?", (row[0],))
        conn.execute("DELETE FROM USERS WHERE ID=?", (row[0],))
for code in ["TP01", "TP02", "TP03", "TP04"]:
    row = conn.execute("SELECT ID FROM TOPICS WHERE TOPIC_CODE=?", (code,)).fetchone()
    if row:
        conn.execute("DELETE FROM SELECTIONS WHERE TOPIC_ID=?", (row[0],))
        conn.execute("DELETE FROM SELECTION_EVENTS WHERE TOPIC_ID=?", (row[0],))
        conn.execute("DELETE FROM TOPICS WHERE ID=?", (row[0],))
# 清理两位 E2E 教师的历史轮次（学生记录已在上面删除，不会产生孤儿记录）
for tname in ["e2e_t1", "e2e_t2"]:
    trow = conn.execute("SELECT ID FROM USERS WHERE USER_NAME=?", (tname,)).fetchone()
    if trow:
        conn.execute("DELETE FROM ROUNDS WHERE TEACHER_ID=?", (trow[0],))
conn.commit()
ts = "2026-01-01 00:00:00"
for no, name, real, role in [("E2E_T1", "e2e_t1", "王老师", "teacher"),
                             ("E2E_T2", "e2e_t2", "李老师", "teacher")]:
    conn.execute(
        "INSERT INTO USERS(USER_NO,USER_NAME,PASSWORD_HASH,REAL_NAME,ROLE,STATUS,CREATED_BY,CREATED_AT,FLAG)"
        " VALUES(?,?,?,?,?,'active','e2e',?,1)",
        (no, name, generate_password_hash("123456"), real, role, ts))
# 学生：A/B班归王老师，C班归李老师；绩点递增加以验证绩点优先
students = ([(f"e2e_a{i}", f"E2E_A{i:02d}", f"A同学{i}", "e2e_classA", 3.0 + i * 0.1, "e2e_t1") for i in range(1, 5)] +
            [(f"e2e_b{i}", f"E2E_B{i:02d}", f"B同学{i}", "e2e_classB", 3.2 + i * 0.1, "e2e_t1") for i in range(1, 5)] +
            [(f"e2e_c{i}", f"E2E_C{i:02d}", f"C同学{i}", "e2e_classC", 3.4 + i * 0.1, "e2e_t2") for i in range(1, 5)])
for name, no, real, cls, gpa, advisor in students:
    conn.execute(
        "INSERT INTO USERS(USER_NO,USER_NAME,PASSWORD_HASH,REAL_NAME,CLASS_NAME,GPA,ROLE,ADVISOR_ID,STATUS,CREATED_BY,CREATED_AT,FLAG)"
        " VALUES(?,?,?,?,?,?,'student',(SELECT ID FROM USERS WHERE USER_NAME=?),'active','e2e',?,1)",
        (no, name, generate_password_hash("123456"), real, cls, gpa, advisor, ts))
conn.commit()
conn.close()
check("数据准备完成", len(sql("SELECT ID FROM USERS WHERE USER_NAME LIKE 'e2e_%'")) == 14)

# ---------- 1. 两位教师各自发布课题（含面向班级） ----------
print("\n== 1. 教师发布课题 ==")
t1, t2 = login("e2e_t1"), login("e2e_t2")
post(t1, "/teacher/topics", data={"action": "add", "topic_code": "TP01", "topic_name": "A班专属课题",
                                  "tag": "Web开发", "difficulty": "中等", "max_num": "2",
                                  "target_classes": ["e2e_classA"]})
post(t1, "/teacher/topics", data={"action": "add", "topic_code": "TP02", "topic_name": "公共课题",
                                  "tag": "数据分析", "difficulty": "较易", "max_num": "2"})
post(t2, "/teacher/topics", data={"action": "add", "topic_code": "TP03", "topic_name": "C班专属课题",
                                  "tag": "人工智能", "difficulty": "较难", "max_num": "1",
                                  "target_classes": ["e2e_classC"]})
post(t2, "/teacher/topics", data={"action": "add", "topic_code": "TP04", "topic_name": "李老师公共课题",
                                  "tag": "区块链", "difficulty": "中等", "max_num": "1"})
check("t1 发布 2 个课题", sql("SELECT COUNT(*) c FROM TOPICS WHERE TEACHER_ID=? AND TOPIC_CODE LIKE 'TP0%'", (uid("e2e_t1"),))[0]["c"] == 2)
check("t2 发布 2 个课题", sql("SELECT COUNT(*) c FROM TOPICS WHERE TEACHER_ID=? AND TOPIC_CODE LIKE 'TP0%'", (uid("e2e_t2"),))[0]["c"] == 2)

# ---------- 2. 开放轮次（t1先到先得 / t2多志愿，走「新建轮次」接口） ----------
print("\n== 2. 开放轮次 ==")
post(login("e2e_t1"), "/teacher/round", data={"action": "create", "round_name": "E2E轮次-t1",
                                              "rule": "first_come", "start_at": "2020-01-01 00:00:00",
                                              "end_at": "2030-01-01 00:00:00", "status": "open",
                                              "waitlist_on": "1", "promote_mode": "auto"})
post(login("e2e_t2"), "/teacher/round", data={"action": "create", "round_name": "E2E轮次-t2",
                                              "rule": "multi_wish", "start_at": "2020-01-01 00:00:00",
                                              "end_at": "2030-01-01 00:00:00", "status": "open",
                                              "waitlist_on": "1", "promote_mode": "auto"})
check("t1/t2 轮次已开放", len(sql("SELECT ID FROM ROUNDS WHERE TEACHER_ID IN (?,?) AND STATUS='open'", (uid("e2e_t1"), uid("e2e_t2")))) == 2)

# ---------- 3. 并发抢课：A班4人抢 TP01（上限2） ----------
print("\n== 3. A班4人并发抢 TP01（上限2） ==")
tp01 = sql("SELECT ID FROM TOPICS WHERE TOPIC_CODE='TP01'")[0]["ID"]
results, lock = [], threading.Lock()


def grab(who):
    body = post(login(who), f"/student/select/{tp01}")
    with lock:
        results.append((who, body))


threads = [threading.Thread(target=grab, args=(f"e2e_a{i}",)) for i in range(1, 5)]
for t in threads: t.start()
for t in threads: t.join()
conf = sql("SELECT COUNT(*) c FROM SELECTIONS WHERE TOPIC_ID=? AND STATUS='confirmed'", (tp01,))[0]["c"]
wait = sql("SELECT COUNT(*) c FROM SELECTIONS WHERE TOPIC_ID=? AND STATUS='waiting'", (tp01,))[0]["c"]
check("并发不超卖：恰好2人确认", conf == 2, f"conf={conf}")
check("其余2人候补", wait == 2, f"wait={wait}")

# ---------- 4. 班级隔离 ----------
print("\n== 4. 班级隔离 ==")
body_b = post(login("e2e_b1"), f"/student/select/{tp01}")
check("B班学生不能选A班专属课题", "未面向你的班级开放" in body_b)
tp03 = sql("SELECT ID FROM TOPICS WHERE TOPIC_CODE='TP03'")[0]["ID"]
body_a = post(login("e2e_b1"), f"/student/select/{tp03}")
check("他师学生不能选C班老师的课题", "不属于你的指导教师" in body_a)
browse_a = get(login("e2e_a1"), "/student/browse")
check("A班学生页面不含C班课题", "C班专属课题" not in browse_a)
browse_c = get(login("e2e_c1"), "/student/browse")
check("C班学生页面不含A班课题", "A班专属课题" not in browse_c)

# ---------- 5. 退选与自动递补 ----------
print("\n== 5. 退选与递补 ==")
conf_sel = sql("SELECT s.ID, u.USER_NAME FROM SELECTIONS s JOIN USERS u ON u.ID=s.USER_ID"
               " WHERE s.TOPIC_ID=? AND s.STATUS='confirmed' ORDER BY s.ID LIMIT 1", (tp01,))
if conf_sel:
    post(login(conf_sel[0]["USER_NAME"]), f"/student/cancel/{conf_sel[0]['ID']}", data={"reason": "选错了，想换题"})
conf = sql("SELECT COUNT(*) c FROM SELECTIONS WHERE TOPIC_ID=? AND STATUS='confirmed'", (tp01,))[0]["c"]
cancelled = sql("SELECT COUNT(*) c FROM SELECTIONS WHERE TOPIC_ID=? AND STATUS='cancelled'", (tp01,))[0]["c"]
check("退选后仍为2人确认（候补递补）", conf == 2, f"conf={conf}")
check("退选记录保留", cancelled == 1, f"cancelled={cancelled}")

# ---------- 6. C班多志愿模式 ----------
print("\n== 6. C班多志愿填报与分配 ==")
for i in range(1, 5):
    wishes = ["1:TP03", "2:TP04"] if i % 2 == 1 else ["1:TP04", "2:TP03"]
    post(login(f"e2e_c{i}"), "/student/wish", data={"wishes": wishes})
check("C班4人都提交了志愿", sql(
    "SELECT COUNT(*) c FROM SELECTIONS WHERE STATUS='wish' AND USER_ID IN (SELECT ID FROM USERS WHERE USER_NAME LIKE 'e2e_c%')")[0]["c"] == 8)
post(login("e2e_t2"), "/teacher/allocate")
tp03_conf = sql("SELECT u.USER_NAME FROM SELECTIONS s JOIN USERS u ON u.ID=s.USER_ID WHERE s.TOPIC_ID=? AND s.STATUS='confirmed'", (tp03,))
check("TP03(上限1)分配1人", len(tp03_conf) == 1)
check("分配给出绩点最高者(c3先报TP03, 3.7)", tp03_conf and tp03_conf[0]["USER_NAME"] == "e2e_c3",
      tp03_conf[0]["USER_NAME"] if tp03_conf else "")
tp04_conf = sql("SELECT COUNT(*) c FROM SELECTIONS WHERE TOPIC_ID=(SELECT ID FROM TOPICS WHERE TOPIC_CODE='TP04') AND STATUS='confirmed'")[0]["c"]
check("TP04(上限1)也分配1人", tp04_conf == 1, f"conf={tp04_conf}")
check("无超分配：C班确认总数=2", sql(
    "SELECT COUNT(*) c FROM SELECTIONS s JOIN USERS u ON u.ID=s.USER_ID WHERE s.STATUS='confirmed' AND u.USER_NAME LIKE 'e2e_c%'")[0]["c"] == 2)

# ---------- 7. 教师数据隔离 ----------
print("\n== 7. 教师数据隔离 ==")
mon_t1 = get(login("e2e_t1"), "/teacher/monitor")
mon_t2 = get(login("e2e_t2"), "/teacher/monitor")
check("t1 监控只看得到自己的课题", "A班专属课题" in mon_t1 and "C班专属课题" not in mon_t1)
check("t2 监控只看得到自己的课题", "C班专属课题" in mon_t2 and "A班专属课题" not in mon_t2)
logs_t1 = get(login("e2e_t1"), "/teacher/logs")
check("t1 日志只含自己的课题", "A班专属课题" in logs_t1 and "C班专属课题" not in logs_t1)

# ---------- 8. 最终一致性校验 ----------
print("\n== 8. 最终一致性 ==")
ok = True
for code, max_num in [("TP01", 2), ("TP02", 2), ("TP03", 1), ("TP04", 1)]:
    c = sql("SELECT COUNT(*) c FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID"
            " WHERE t.TOPIC_CODE=? AND s.STATUS='confirmed'", (code,))[0]["c"]
    if c > max_num:
        ok = False
        print("  超卖:", code, c, ">", max_num)
check("所有课题均无超卖", ok)
dup = sql("SELECT COUNT(*) c FROM (SELECT ROUND_ID, USER_ID FROM SELECTIONS WHERE STATUS='confirmed' GROUP BY ROUND_ID, USER_ID HAVING COUNT(*)>1)")[0]["c"]
check("无学生重复确认", dup == 0)

print(f"\n===== E2E结果: {len(passed)} 通过, {len(failed)} 失败 =====")
if failed:
    print("失败项:", failed)
