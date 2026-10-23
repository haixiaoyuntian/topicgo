"""全功能回归测试：退选留痕 / 候补递补 / 权限管控 / 多志愿 / 抽签 / 分组限制 / 并发 / CSRF / 新建轮次 / 防反复退选"""
import re
import sqlite3
import threading
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

BASE = "http://127.0.0.1:5000"
DB = "topicgo.db"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("PASS" if cond else "FAIL"), name, detail)


def fetch_csrf(opener, path="/login"):
    """从页面 meta 标签获取当前会话的 CSRF 令牌"""
    try:
        html = opener.open(BASE + path).read().decode()
    except Exception:
        return ""
    m = re.search(r'name="csrf-token" content="([0-9a-f]+)"', html)
    return m.group(1) if m else ""


def login(who, pwd="123456"):
    cookie = urllib.request.HTTPCookieProcessor()
    opener = urllib.request.build_opener(cookie)
    token = fetch_csrf(opener)
    opener._csrf = token
    data = urllib.parse.urlencode(
        {"username": who, "password": pwd, "_csrf_token": token}).encode()
    opener.open(f"{BASE}/login", data)
    return opener


def post(opener, path, data=None, csrf=True):
    data = dict(data or {})
    if csrf:
        token = getattr(opener, "_csrf", None)
        if not token:
            token = fetch_csrf(opener, "/")
            opener._csrf = token
        data["_csrf_token"] = token
    body = urllib.parse.urlencode(data, doseq=True).encode() if data else None
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    try:
        return opener.open(req).read().decode()
    except Exception as e:
        return str(e)


def get(opener, path):
    try:
        return opener.open(BASE + path).read().decode()
    except Exception as e:
        return str(e)


def sql(q, args=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(q, args).fetchall()
    conn.close()
    return rows


def reset_round(rule="first_come", **kw):
    conn = sqlite3.connect(DB)
    fields = {"RULE": rule, "START_AT": "2020-01-01 00:00:00", "END_AT": "2030-01-01 00:00:00",
              "STATUS": "open", "ALLOW_CLASSES": None, "PROMOTE_MODE": "auto",
              "MAX_WISH": 3, "LOTTERY_SEED": None,
              "MAX_CANCEL": 2, "CANCEL_LOCK_HOURS": 0}
    fields.update(kw)
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE ROUNDS SET {sets}", tuple(fields.values()))
    conn.execute("DELETE FROM SELECTIONS")
    conn.execute("DELETE FROM SELECTION_EVENTS")
    conn.commit()
    conn.close()


rid = sql("SELECT ID FROM ROUNDS")[0]["ID"]
TEACHER_ID = sql("SELECT ID FROM USERS WHERE USER_NAME='teacher'")[0]["ID"]

# ---------- 1. 退选留痕 ----------
print("\n== 1. 退选留痕 ==")
reset_round()
zhang = login("zhangsan")
post(zhang, "/student/select/5")  # T05 区块链
check("选题成功", sql("SELECT COUNT(*) c FROM SELECTIONS WHERE STATUS='confirmed' AND USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='zhangsan')")[0]["c"] == 1)
sel_id = sql("SELECT ID FROM SELECTIONS WHERE USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='zhangsan')")[0]["ID"]
post(zhang, f"/student/cancel/{sel_id}", data={"reason": "选错了，想换题"})
row = sql("SELECT * FROM SELECTIONS WHERE ID=?", (sel_id,))[0]
check("退选后记录保留(cancelled)", row["STATUS"] == "cancelled" and row["CANCELLED_AT"] is not None)
ev = sql("SELECT * FROM SELECTION_EVENTS WHERE ACTION='cancel'")
check("退选事件已记录", len(ev) == 1 and ev[0]["FROM_STATUS"] == "confirmed")
ev2 = sql("SELECT * FROM SELECTION_EVENTS WHERE ACTION='select'")
check("选题事件已记录", len(ev2) == 1)

# ---------- 2. 候补 + 自动递补 ----------
print("\n== 2. 候补与递补 ==")
reset_round()
for i, who in enumerate(["zhangsan", "lisi", "wangwu", "zhaoliu"]):
    login(who)
for who in ["zhangsan", "lisi", "wangwu"]:
    post(login(who), "/student/select/1")  # T01 上限3
li = login("zhaoliu")
body = post(li, "/student/select/1")
check("满员入候补", "候补" in body and sql("SELECT COUNT(*) c FROM SELECTIONS WHERE STATUS='waiting'")[0]["c"] == 1)
# zhangsan 退选 -> zhaoliu 自动递补
sid = sql("SELECT ID FROM SELECTIONS WHERE USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='zhangsan')")[0]["ID"]
post(login("zhangsan"), f"/student/cancel/{sid}", data={"reason": "与其它课程/安排冲突"})
zl = sql("SELECT STATUS FROM SELECTIONS WHERE USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='zhaoliu')")[0]["STATUS"]
check("退选后自动递补", zl == "confirmed")
check("递补事件已记录", sql("SELECT COUNT(*) c FROM SELECTION_EVENTS WHERE ACTION='promote'")[0]["c"] == 1)

# ---------- 3. 手动递补模式 ----------
print("\n== 3. 手动递补 ==")
reset_round(promote_mode="manual")
for who in ["zhangsan", "lisi", "wangwu"]:
    post(login(who), "/student/select/1")   # T01 上限3 选满
li = login("zhaoliu")
post(li, "/student/select/1")               # 第4人入候补
check("满员入候补(手动模式)", sql("SELECT COUNT(*) c FROM SELECTIONS WHERE STATUS='waiting'")[0]["c"] == 1)
sid = sql("SELECT ID FROM SELECTIONS WHERE USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='lisi')")[0]["ID"]
post(login("lisi"), f"/student/cancel/{sid}", data={"reason": "题目难度或方向不适合"})
zl = sql("SELECT STATUS FROM SELECTIONS WHERE USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='zhaoliu')")[0]["STATUS"]
check("手动模式下不自动递补", zl == "waiting")
tea = login("teacher")
wid = sql("SELECT ID FROM SELECTIONS WHERE USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='zhaoliu') AND STATUS='waiting'")[0]["ID"]
post(tea, f"/teacher/promote/{wid}")
zl = sql("SELECT STATUS FROM SELECTIONS WHERE USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='zhaoliu')")[0]["STATUS"]
check("教师手动递补成功", zl == "confirmed")

# ---------- 4. 权限管控 ----------
print("\n== 4. 权限管控 ==")
admin = login("admin")
post(admin, "/admin/users", data={"action": "add", "user_no": "TEST99", "user_name": "test99",
                                  "real_name": "测试99", "class_name": "软工2309", "gpa": "3.0",
                                  "role": "student", "advisor_id": TEACHER_ID})
check("管理员创建用户", sql("SELECT COUNT(*) c FROM USERS WHERE USER_NAME='test99'")[0]["c"] == 1)
t99 = login("test99")
check("新用户可登录", "浏览题库" in get(t99, "/student/browse"))
uid = sql("SELECT ID FROM USERS WHERE USER_NAME='test99'")[0]["ID"]
post(admin, "/admin/users", data={"action": "toggle", "user_id": uid})
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
st = conn.execute("SELECT STATUS FROM USERS WHERE ID=?", (uid,)).fetchone()["STATUS"]
conn.close()
check("账号已停用", st == "disabled")
cookie = urllib.request.HTTPCookieProcessor()
op = urllib.request.build_opener(cookie)
token = fetch_csrf(op)
data = urllib.parse.urlencode({"username": "test99", "password": "123456", "_csrf_token": token}).encode()
resp = op.open(f"{BASE}/login", data).read().decode()
check("停用账号无法登录", "已被停用" in resp or "密码错误" in resp or "账号不存在" in resp)
post(admin, "/admin/users", data={"action": "toggle", "user_id": uid})
check("学生不能访问管理页", "无权限" in get(login("zhangsan"), "/admin/users") or "无权限" in get(login("zhangsan"), "/admin/users"))
check("教师不能访问用户管理", "无权限" in get(login("teacher"), "/admin/users"))
check("教师可访问监控", "选题监控" in get(login("teacher"), "/teacher/monitor"))
check("管理员不涉及课程业务(不能访问监控)", "无权限" in get(admin, "/teacher/monitor"))
check("管理员不能访问题库", "无权限" in get(admin, "/teacher/topics"))
check("管理员可访问用户管理", "用户管理" in get(admin, "/admin/users"))
# CSRF：缺少令牌的 POST 必须被拒绝
zhang = login("zhangsan")
body = post(zhang, "/student/select/5", csrf=False)
check("缺少 CSRF 令牌的 POST 被拒绝", "表单校验失败" in body)
# CSRF：页面必须渲染真实的隐藏表单域（防止被转义成文本导致浏览器不提交令牌）
body = get(zhang, "/password")
check("表单渲染真实 CSRF 隐藏域", '<input type="hidden" name="_csrf_token"' in body)

# ---------- 5. 未登记用户不能登录 ----------
print("\n== 5. 未登记用户拒绝 ==")
cookie = urllib.request.HTTPCookieProcessor()
op = urllib.request.build_opener(cookie)
token = fetch_csrf(op)
data = urllib.parse.urlencode({"username": "hacker", "password": "123456", "_csrf_token": token}).encode()
resp = op.open(f"{BASE}/login", data).read().decode()
check("未登记账号被拒绝", "账号不存在" in resp)

# ---------- 6. 分组轮选 ----------
print("\n== 6. 分组轮选 ==")
reset_round(allow_classes="软工2302")
zhang = login("zhangsan")  # 软工2301
body = post(zhang, "/student/select/5")
check("非允许班级被拒绝", "仅限班级" in body)
wang = login("wangwu")  # 软工2302
body = post(wang, "/student/select/5")
check("允许班级可选题", "成功" in body)

# ---------- 7. 多志愿分配 ----------
print("\n== 7. 多志愿分配 ==")
reset_round(rule="multi_wish", max_wish=2)
post(login("zhangsan"), "/student/wish", data={"wishes": ["1:T05", "2:T06"]})  # gpa3.8
post(login("lisi"), "/student/wish", data={"wishes": ["1:T05"]})               # gpa3.2
post(login("chenqi"), "/student/wish", data={"wishes": ["1:T05", "2:T06"]})   # gpa2.9
tea = login("teacher")
post(tea, "/teacher/allocate")
z = sql("SELECT t.TOPIC_CODE, s.STATUS FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID WHERE s.USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='zhangsan') AND s.STATUS='confirmed'")
check("高绩点第一志愿命中(T05)", len(z) == 1 and z[0]["TOPIC_CODE"] == "T05")
l = sql("SELECT t.TOPIC_CODE FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID WHERE s.USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='lisi') AND s.STATUS='confirmed'")
check("lisi 第一志愿命中(T05)", len(l) == 1 and l[0]["TOPIC_CODE"] == "T05")
cq = sql("SELECT t.TOPIC_CODE FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID WHERE s.USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='chenqi') AND s.STATUS='confirmed'")
check("chenqi 第一志愿满员->第二志愿命中(T06)", len(cq) == 1 and cq[0]["TOPIC_CODE"] == "T06")
zc = sql("SELECT COUNT(*) c FROM SELECTIONS s JOIN TOPICS t ON t.ID=s.TOPIC_ID WHERE s.USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='zhangsan') AND s.STATUS='cancelled' AND t.TOPIC_CODE='T06'")
check("已命中志愿的其他志愿作废并留痕", zc[0]["c"] == 1)
check("分配事件已记录", sql("SELECT COUNT(*) c FROM SELECTION_EVENTS WHERE ACTION='allocate'")[0]["c"] >= 3)

# 教师撤销误提交的志愿：重置后由 chenqi 重新填报，教师撤销，学生可再次提交
reset_round(rule="multi_wish", max_wish=2)
post(login("chenqi"), "/student/wish", data={"wishes": ["1:T05"]})
wish_id = sql("SELECT ID FROM SELECTIONS WHERE STATUS='wish' AND USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='chenqi')")[0]["ID"]
tea = login("teacher")
body = post(tea, f"/teacher/revoke/{wish_id}")
check("教师可撤销学生志愿", "已撤销" in body and sql("SELECT STATUS FROM SELECTIONS WHERE ID=?", (wish_id,))[0]["STATUS"] == "cancelled")
check("撤销事件已记录", sql("SELECT COUNT(*) c FROM SELECTION_EVENTS WHERE ACTION='cancel' AND REMARK LIKE '%教师撤销%'")[0]["c"] >= 1)
body = post(login("chenqi"), "/student/wish", data={"wishes": ["1:T06"]})
check("撤销后学生可重新提交志愿", "成功" in body)

# ---------- 8. 随机抽签 ----------
print("\n== 8. 随机抽签 ==")
reset_round(rule="lottery", lottery_seed="SEED-2026")
for who in ["zhangsan", "lisi", "wangwu", "zhaoliu", "chenqi"]:
    post(login(who), "/student/wish", data={"wishes": ["1:T05"]})  # T05 上限2，5人报名
tea = login("teacher")
post(tea, "/teacher/lottery")
conf = sql("SELECT COUNT(*) c FROM SELECTIONS WHERE STATUS='confirmed' AND TOPIC_ID=(SELECT ID FROM TOPICS WHERE TOPIC_CODE='T05')")[0]["c"]
wait = sql("SELECT COUNT(*) c FROM SELECTIONS WHERE STATUS='waiting' AND TOPIC_ID=(SELECT ID FROM TOPICS WHERE TOPIC_CODE='T05')")[0]["c"]
check("抽签后确认数=上限", conf == 2, f"conf={conf}")
check("未中签进候补", wait == 3, f"wait={wait}")
check("抽签种子写入日志", sql("SELECT COUNT(*) c FROM SELECTION_EVENTS WHERE ACTION='lottery' AND REMARK LIKE '%SEED-2026%'")[0]["c"] >= 5)

# 同一学生报名多个题目：每人最多中 1 题，不得因唯一索引冲突导致整体失败
reset_round(rule="lottery", lottery_seed="SEED-MULTI")
for who in ["zhangsan", "lisi", "wangwu"]:
    post(login(who), "/student/wish", data={"wishes": ["1:T05", "1:T06"]})  # T05上限2 / T06上限4
body = post(login("teacher"), "/teacher/lottery")
check("多题报名抽签不失败", "抽签完成" in body)
dup = sql("SELECT COUNT(*) c FROM (SELECT USER_ID FROM SELECTIONS WHERE STATUS='confirmed' GROUP BY USER_ID HAVING COUNT(*)>1)")[0]["c"]
check("抽签后无学生重复中签", dup == 0)
conf_total = sql("SELECT COUNT(*) c FROM SELECTIONS WHERE STATUS='confirmed'")[0]["c"]
check("每人最多中1题", conf_total <= 3, f"conf={conf_total}")
check("多中作废记录留痕", sql("SELECT COUNT(*) c FROM SELECTION_EVENTS WHERE REMARK LIKE '%已在其他课题中签%'")[0]["c"] >= 1)

# ---------- 9. 并发防超卖 ----------
print("\n== 9. 并发防超卖 ==")
reset_round()
for i in range(1, 7):
    no, name = f"CC{i:02d}", f"cc{i}"
    if not sql("SELECT 1 FROM USERS WHERE USER_NAME=?", (name,)):
        conn = sqlite3.connect(DB)
        from werkzeug.security import generate_password_hash
        conn.execute(
            "INSERT INTO USERS(USER_NO,USER_NAME,PASSWORD_HASH,REAL_NAME,CLASS_NAME,GPA,ROLE,ADVISOR_ID,STATUS,CREATED_BY,CREATED_AT,FLAG)"
            " VALUES(?,?,?,?,'软工TEST',3.0,'student',?,'active','test','2026-01-01',1)",
            (no, name, generate_password_hash("123456"), f"并发{i}", TEACHER_ID))
        conn.commit()
        conn.close()
    # 兼容历史数据：确保并发测试用户都绑定指导教师
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE USERS SET ADVISOR_ID=? WHERE USER_NAME LIKE 'cc%'", (TEACHER_ID,))
    conn.commit()
    conn.close()
results = []
lock = threading.Lock()


def grab(i):
    op = login(f"cc{i}")
    try:
        op.open(urllib.request.Request(f"{BASE}/student/select/2", method="POST",
                                       data=urllib.parse.urlencode(
                                           {"_csrf_token": op._csrf}).encode()))
    except Exception:
        pass


threads = [threading.Thread(target=grab, args=(i,)) for i in range(1, 7)]
for t in threads:
    t.start()
for t in threads:
    t.join()
conf = sql("SELECT COUNT(*) c FROM SELECTIONS WHERE STATUS='confirmed' AND TOPIC_ID=(SELECT ID FROM TOPICS WHERE TOPIC_CODE='T02')")[0]["c"]
check("6人抢3名额不超卖", conf <= 3, f"conf={conf}")

# ---------- 10. 师生归属隔离 ----------
print("\n== 10. 师生归属隔离 ==")
admin = login("admin")
post(admin, "/admin/users", data={"action": "add", "user_no": "T3002", "user_name": "teacher2",
                                  "real_name": "王老师", "role": "teacher"})
post(admin, "/admin/users", data={"action": "add", "user_no": "20230106", "user_name": "stu2",
                                  "real_name": "周同学", "class_name": "软工2303", "gpa": "3.6",
                                  "role": "student", "advisor_id": TEACHER_ID})
T2 = sql("SELECT ID FROM USERS WHERE USER_NAME='teacher2'")[0]["ID"]
post(admin, "/admin/users", data={"action": "add", "user_no": "20230107", "user_name": "stu_of_t2",
                                  "real_name": "吴同学", "class_name": "软工2303", "gpa": "3.1",
                                  "role": "student", "advisor_id": T2})
reset_round()
# teacher2 发布自己的课题
t2 = login("teacher2")
post(t2, "/teacher/topics", data={"action": "add", "topic_code": "U01", "topic_name": "王老师的课题A",
                                  "tag": "数据分析", "difficulty": "中等", "max_num": "2"})
check("teacher2 发布自己的课题", sql("SELECT COUNT(*) c FROM TOPICS WHERE TOPIC_CODE='U01' AND TEACHER_ID=?", (T2,))[0]["c"] == 1)
# teacher2 通过「新建轮次」自建选题轮次（新功能）
body = post(t2, "/teacher/round", data={"action": "create", "round_name": "T2测试轮次",
                                        "rule": "first_come", "start_at": "2020-01-01 00:00:00",
                                        "end_at": "2030-01-01 00:00:00", "status": "open"})
check("教师可自建选题轮次", "已创建" in body and sql("SELECT COUNT(*) c FROM ROUNDS WHERE TEACHER_ID=?", (T2,))[0]["c"] >= 1)
body = get(t2, "/teacher/round")
check("轮次页展示轮次列表", "我的轮次" in body)
# stu_of_t2 只能看到 U01，看不到刘老师的 T05
body = get(login("stu_of_t2"), "/student/browse")
check("stu_of_t2 看不到其他老师的课题", "U01" in body and "T05" not in body and "区块链" not in body)
body = post(login("stu_of_t2"), "/student/select/5")
check("stu_of_t2 不能选其他老师的课题", "不属于你的指导教师" in body or "尚未开放选题" in body)
# teacher2 看不到刘老师的课题
body = get(t2, "/teacher/topics")
check("teacher2 看不到他人课题", "U01" in body and "T05" not in body and "T01" not in body)
# teacher2 没有轮次时监控为空
body = get(t2, "/teacher/monitor")
check("teacher2 无轮次时监控为空", "题目总数" in body)
# 教师只能改自己的课题：teacher2 尝试编辑刘老师的 T05（ID=5）
post(t2, "/teacher/topics", data={"action": "edit", "topic_id": "5", "topic_name": "篡改",
                                  "max_num": "99"})
name5 = sql("SELECT TOPIC_NAME FROM TOPICS WHERE ID=5")[0]["TOPIC_NAME"]
check("教师不能改他人课题", name5 != "篡改")
# 学生登录后能看到自己老师的课题
body = get(login("zhangsan"), "/student/browse")
check("zhangsan 看到刘老师的课题", "区块链" in body or "T05" in body)

# ---------- 11. Dashboard / 我的学生 / 公告 ----------
print("\n== 11. Dashboard与公告 ==")
reset_round()
body = get(login("zhangsan"), "/student/index")
check("学生首页可访问", "你好" in body)
body = get(login("teacher"), "/teacher/index")
check("教师首页可访问", "教师工作台" in body)
body = get(login("teacher"), "/teacher/students")
check("我的学生页可访问", "我的学生" in body and "同学A" in body)
check("学生不能访问教师页", "无权限" in get(login("zhangsan"), "/teacher/students"))
tea = login("teacher")
# 清理上次运行残留的同名公告，保证断言可重复执行
conn = sqlite3.connect(DB)
conn.execute("DELETE FROM ANNOUNCEMENTS WHERE TITLE='本周五截止选题'")
conn.commit()
conn.close()
post(tea, "/teacher/announce", data={"action": "add", "title": "本周五截止选题", "content": "请尽快完成选题"})
check("教师发布公告", sql("SELECT COUNT(*) c FROM ANNOUNCEMENTS WHERE TITLE='本周五截止选题'")[0]["c"] == 1)
body = get(login("zhangsan"), "/student/index")
check("学生首页看到老师公告", "本周五截止选题" in body)
# 其他老师的学生看不到该公告
body = get(login("stu_of_t2"), "/student/index")
check("非本老师学生看不到公告", "本周五截止选题" not in body)

# ---------- 12. 课题面向班级 ----------
print("\n== 12. 课题面向班级 ==")
reset_round()
tea = login("teacher")
# 发布仅面向软工2302的课题
post(tea, "/teacher/topics", data={"action": "add", "topic_code": "C01", "topic_name": "仅2302的课题",
                                   "tag": "测试", "difficulty": "中等", "max_num": "2",
                                   "target_classes": ["软工2302"]})
# 发布面向所有班级的课题
post(tea, "/teacher/topics", data={"action": "add", "topic_code": "C02", "topic_name": "所有班级的课题",
                                   "tag": "测试", "difficulty": "中等", "max_num": "2"})
c1 = sql("SELECT ID FROM TOPICS WHERE TOPIC_CODE='C01'")[0]["ID"]
c2 = sql("SELECT ID FROM TOPICS WHERE TOPIC_CODE='C02'")[0]["ID"]
body_z = get(login("zhangsan"), "/student/browse")   # 软工2301
body_w = get(login("wangwu"), "/student/browse")     # 软工2302
check("2301学生看不到仅面向2302的课题", "仅2302的课题" not in body_z)
check("2302学生看得到面向2302的课题", "仅2302的课题" in body_w)
check("所有班级课题对2301可见", "所有班级的课题" in body_z)
body = post(login("zhangsan"), f"/student/select/{c1}")
check("越班选题被拒绝", "未面向你的班级开放" in body)
body = post(login("wangwu"), f"/student/select/{c1}")
check("同班选题成功", "成功" in body)
# 教师首页展示管理的班级
body = get(tea, "/teacher/index")
check("教师首页显示管理的班级", "软工2301" in body and "软工2302" in body)
# 题库页显示面向班级列
body = get(tea, "/teacher/topics")
check("题库页显示面向班级", "面向班级" in body and "软工2302" in body)

# ---------- 13. 课题关闭与重新开放 ----------
print("\n== 13. 课题关闭与开放 ==")
reset_round()
tea = login("teacher")
body = post(tea, "/teacher/topics", data={"action": "toggle", "topic_id": 1})  # T01 图书管理系统
check("教师可关闭课题", "已关闭" in body and sql("SELECT STATUS FROM TOPICS WHERE ID=1")[0]["STATUS"] == "closed")
body = get(login("zhangsan"), "/student/browse")
check("关闭后学生不可见", "图书管理系统" not in body)
body = post(tea, "/teacher/topics", data={"action": "toggle", "topic_id": 1})
check("教师可重新开放课题", "重新开放" in body and sql("SELECT STATUS FROM TOPICS WHERE ID=1")[0]["STATUS"] == "open")
body = get(login("zhangsan"), "/student/browse")
check("重新开放后学生可见", "图书管理系统" in body)

# ---------- 14. 自助修改密码 ----------
print("\n== 14. 自助修改密码 ==")
uid99 = sql("SELECT ID FROM USERS WHERE USER_NAME='test99'")[0]["ID"]
body = get(login("test99"), "/password")
check("修改密码页可访问", "修改密码" in body)
t99 = login("test99")
body = post(t99, "/password", data={"old_password": "123456", "new_password": "abc12345",
                                    "confirm_password": "abc12345"})
check("修改密码成功", "修改成功" in body)
cookie = urllib.request.HTTPCookieProcessor()
op = urllib.request.build_opener(cookie)
token = fetch_csrf(op)
resp = op.open(f"{BASE}/login", urllib.parse.urlencode(
    {"username": "test99", "password": "123456", "_csrf_token": token}).encode()).read().decode()
check("旧密码无法登录", "密码错误" in resp or "账号不存在" in resp)
check("新密码可登录", "首页" in get(login("test99", "abc12345"), "/student/index"))
body = post(login("test99", "abc12345"), "/password", data={"old_password": "000000", "new_password": "abc12345",
                                                             "confirm_password": "abc12345"})
check("原密码错误被拒绝", "原密码错误" in body)
admin = login("admin")
post(admin, "/admin/users", data={"action": "reset_pwd", "user_id": uid99})
check("管理员可重置密码", "首页" in get(login("test99"), "/student/index"))

# ---------- 15. 防反复退选（方案2+4） ----------
print("\n== 15. 防反复退选（退选次数上限/原因必选/截止前禁退） ==")
REASON = "选错了，想换题"
reset_round(rule="first_come", max_cancel=1, cancel_lock_hours=0)
zhang = login("zhangsan")
post(zhang, "/student/select/1")   # 选 T01
sid1 = sql("SELECT ID FROM SELECTIONS WHERE USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='zhangsan')")[0]["ID"]
post(zhang, f"/student/cancel/{sid1}", data={"reason": REASON})
check("第1次退选成功（上限1次）", sql("SELECT STATUS FROM SELECTIONS WHERE ID=?", (sid1,))[0]["STATUS"] == "cancelled")
check("退选原因写入日志", sql("SELECT COUNT(*) c FROM SELECTION_EVENTS WHERE ACTION='cancel' AND REMARK LIKE ?", (f"%原因：{REASON}%",))[0]["c"] == 1)
# 次数用尽但无有效记录时，仍允许重新选题（只限制退选，不限制选题）
post(zhang, "/student/select/2")
sid2 = sql("SELECT ID FROM SELECTIONS WHERE USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='zhangsan') AND STATUS='confirmed'")[0]["ID"]
check("退选用尽后仍可正常选题", sid2 > 0)
body = post(zhang, f"/student/cancel/{sid2}", data={"reason": REASON})
check("退选次数用尽后被拒绝", "退选次数已用完" in body)
check("拒绝后记录保持已确认", sql("SELECT STATUS FROM SELECTIONS WHERE ID=?", (sid2,))[0]["STATUS"] == "confirmed")
# 原因必选
body = post(login("lisi"), "/student/select/1")
sid3 = sql("SELECT ID FROM SELECTIONS WHERE USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='lisi')")[0]["ID"]
check("未选原因被拒绝", "请选择退选原因" in post(login("lisi"), f"/student/cancel/{sid3}", data={"reason": ""}))
check("非法原因值被拒绝", "请选择退选原因" in post(login("lisi"), f"/student/cancel/{sid3}", data={"reason": "随便填"}))
# 教师撤销不计入学生次数
reset_round(rule="multi_wish", max_cancel=1, cancel_lock_hours=0)
post(login("chenqi"), "/student/wish", data={"wishes": ["1:T05"]})
wish_id = sql("SELECT ID FROM SELECTIONS WHERE STATUS='wish'")[0]["ID"]
post(login("teacher"), f"/teacher/revoke/{wish_id}")
tea2 = login("teacher")
post(tea2, "/teacher/round", data={"action": "save", "round_id": rid, "round_name": "t", "rule": "first_come",
                                   "start_at": "2020-01-01 00:00:00", "end_at": "2030-01-01 00:00:00",
                                   "waitlist_on": "1", "promote_mode": "auto", "max_wish": 3,
                                   "max_cancel": 1, "cancel_lock_hours": 0, "status": "open"})
post(login("chenqi"), "/student/select/6")
sid4 = sql("SELECT ID FROM SELECTIONS WHERE USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='chenqi') AND STATUS='confirmed'")[0]["ID"]
body = post(login("chenqi"), f"/student/cancel/{sid4}", data={"reason": REASON})
check("教师撤销不计入学生退选次数", sql("SELECT STATUS FROM SELECTIONS WHERE ID=?", (sid4,))[0]["STATUS"] == "cancelled")
# 截止前禁止退选
reset_round(rule="first_come", max_cancel=5, cancel_lock_hours=2,
            start_at="2020-01-01 00:00:00", end_at=(datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"))
post(login("wangwu"), "/student/select/1")
sid5 = sql("SELECT ID FROM SELECTIONS WHERE USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='wangwu')")[0]["ID"]
body = post(login("wangwu"), f"/student/cancel/{sid5}", data={"reason": REASON})
check("截止前禁止退选", "截止前 2 小时内不能退选" in body)
check("锁定期间记录不变", sql("SELECT STATUS FROM SELECTIONS WHERE ID=?", (sid5,))[0]["STATUS"] == "confirmed")
reset_round(rule="first_come", max_cancel=5, cancel_lock_hours=2,
            end_at=(datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"))
post(login("wangwu"), "/student/select/1")
sid6 = sql("SELECT ID FROM SELECTIONS WHERE USER_ID=(SELECT ID FROM USERS WHERE USER_NAME='wangwu')")[0]["ID"]
post(login("wangwu"), f"/student/cancel/{sid6}", data={"reason": REASON})
check("非锁定窗口可正常退选", sql("SELECT STATUS FROM SELECTIONS WHERE ID=?", (sid6,))[0]["STATUS"] == "cancelled")
# 教师「我的学生」页展示退选次数
body = get(login("teacher"), "/teacher/students")
check("我的学生页展示退选次数", "退选次数" in body)

# ---------- 汇总 ----------
print(f"\n===== 结果: {len(passed)} 通过, {len(failed)} 失败 =====")
if failed:
    print("失败项:", failed)
