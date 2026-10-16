"""并发选题冒烟测试：N 个用户同时抢 1 个题目，验证不超卖"""
import sqlite3
import threading
import urllib.request
import urllib.parse

from werkzeug.security import generate_password_hash

BASE = "http://127.0.0.1:5000"
DB = "topicgo.db"

# 1. 造 6 个测试学生（必须绑定指导教师，否则看不到课题）
conn = sqlite3.connect(DB)
teacher_id = conn.execute("SELECT ID FROM USERS WHERE USER_NAME='teacher'").fetchone()[0]
for i in range(1, 7):
    no, name = f"TEST{i:02d}", f"test{i}"
    if not conn.execute("SELECT 1 FROM USERS WHERE USER_NAME=?", (name,)).fetchone():
        conn.execute(
            "INSERT INTO USERS(USER_NO,USER_NAME,PASSWORD_HASH,REAL_NAME,CLASS_NAME,GPA,ROLE,ADVISOR_ID,STATUS,CREATED_BY,CREATED_AT,FLAG)"
            " VALUES(?,?,?,?,'软工TEST',3.0,'student',?,'active','test',?,1)",
            (no, name, generate_password_hash("123456"), f"测试{i}", teacher_id, "2026-01-01 00:00:00"),
        )
    else:
        # 兼容历史数据：确保已绑导师
        conn.execute("UPDATE USERS SET ADVISOR_ID=? WHERE USER_NAME=?", (teacher_id, name))
conn.commit()

# 找到 T03 题目 ID
topic_id = conn.execute("SELECT ID FROM TOPICS WHERE TOPIC_CODE='T03'").fetchone()[0]
# 重置 T03 选题记录，并清空测试学生在其它题目上的选题（保证限选1题不干扰）
conn.execute("DELETE FROM SELECTIONS WHERE TOPIC_ID=?", (topic_id,))
conn.execute("DELETE FROM SELECTIONS WHERE USER_ID IN (SELECT ID FROM USERS WHERE USER_NAME LIKE 'test%')")
conn.execute("UPDATE ROUNDS SET START_AT='2020-01-01 00:00:00', END_AT='2030-01-01 00:00:00'")
conn.commit()
max_num = conn.execute("SELECT MAX_NUM FROM TOPICS WHERE ID=?", (topic_id,)).fetchone()[0]
conn.close()
print(f"T03 人数上限: {max_num}，6 人并发抢题")

results = []
lock = threading.Lock()


def grab(i):
    # 登录
    cookie = urllib.request.HTTPCookieProcessor()
    opener = urllib.request.build_opener(cookie)
    data = urllib.parse.urlencode({"username": f"test{i}", "password": "123456"}).encode()
    opener.open(f"{BASE}/login", data)
    # 选题
    req = urllib.request.Request(f"{BASE}/student/select/{topic_id}", method="POST")
    try:
        resp = opener.open(req)
        body = resp.read().decode()
        ok = "成功" in body
    except Exception as e:
        ok = False
    with lock:
        results.append((i, ok))


threads = [threading.Thread(target=grab, args=(i,)) for i in range(1, 7)]
for t in threads:
    t.start()
for t in threads:
    t.join()

# 验证结果
conn = sqlite3.connect(DB)
confirmed = conn.execute(
    "SELECT COUNT(*) FROM SELECTIONS WHERE TOPIC_ID=? AND STATUS='confirmed'", (topic_id,)
).fetchone()[0]
waiting = conn.execute(
    "SELECT COUNT(*) FROM SELECTIONS WHERE TOPIC_ID=? AND STATUS='waiting'", (topic_id,)
).fetchone()[0]
conn.close()
print(f"结果: 已确认 {confirmed} 人, 候补 {waiting} 人")
assert confirmed <= max_num, "超卖！已确认人数超过上限"
assert confirmed + waiting == 6, "记录数异常"
print(f"PASS: 未超卖（确认数 {confirmed} <= 上限 {max_num}），候补队列正常")
