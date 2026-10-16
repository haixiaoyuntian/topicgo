"""演示数据脚本：生成多师生账号，方便多人同时登录手工测试

用法：
  1. 先启动服务（cd src && python app.py）
  2. 运行：python tests/demo_seed.py
  3. 用下面打印的账号在多个浏览器/隐私窗口同时登录测试
"""
import sqlite3
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

DB = "topicgo.db"
ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
end = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# 清理上一次演示数据
for name in ["demo_t1", "demo_t2"] + [f"demo_s{i}" for i in range(1, 13)]:
    row = conn.execute("SELECT ID FROM USERS WHERE USER_NAME=?", (name,)).fetchone()
    if row:
        conn.execute("DELETE FROM SELECTIONS WHERE USER_ID=?", (row["ID"],))
        conn.execute("DELETE FROM SELECTION_EVENTS WHERE USER_ID=?", (row["ID"],))
        conn.execute("DELETE FROM ANNOUNCEMENTS WHERE CREATED_BY=?", (name,))
        conn.execute("DELETE FROM USERS WHERE ID=?", (row["ID"],))
conn.commit()

# 两位教师
conn.execute(
    "INSERT INTO USERS(USER_NO,USER_NAME,PASSWORD_HASH,REAL_NAME,ROLE,STATUS,CREATED_BY,CREATED_AT,FLAG)"
    " VALUES('DEMO_T1','demo_t1',?,'王老师（演示）','teacher','active','demo',?,1)",
    (generate_password_hash("123456"), ts))
conn.execute(
    "INSERT INTO USERS(USER_NO,USER_NAME,PASSWORD_HASH,REAL_NAME,ROLE,STATUS,CREATED_BY,CREATED_AT,FLAG)"
    " VALUES('DEMO_T2','demo_t2',?,'李老师（演示）','teacher','active','demo',?,1)",
    (generate_password_hash("123456"), ts))

# 12 名学生：A/B 班归王老师，C 班归李老师
for i in range(1, 13):
    cls = "演示A班" if i <= 4 else "演示B班" if i <= 8 else "演示C班"
    advisor = "demo_t1" if i <= 8 else "demo_t2"
    conn.execute(
        "INSERT INTO USERS(USER_NO,USER_NAME,PASSWORD_HASH,REAL_NAME,CLASS_NAME,GPA,ROLE,ADVISOR_ID,STATUS,CREATED_BY,CREATED_AT,FLAG)"
        " VALUES(?,?,?,?,?,?,'student',(SELECT ID FROM USERS WHERE USER_NAME=?),'active','demo',?,1)",
        (f"DEMO_S{i:02d}", f"demo_s{i}", generate_password_hash("123456"),
         f"学生{i}", cls, round(2.8 + i * 0.08, 2), advisor, ts))

# 王老师课题：A班专属 + 公共
conn.execute(
    "INSERT INTO TOPICS(TOPIC_CODE,TOPIC_NAME,TOPIC_DESC,TAG,DIFFICULTY,MAX_NUM,TEACHER_ID,TARGET_CLASSES,STATUS,CREATED_BY,CREATED_AT,FLAG)"
    " VALUES('D01','图书管理系统','基于Flask实现图书借阅管理','Web开发','较易',2,"
    "(SELECT ID FROM USERS WHERE USER_NAME='demo_t1'),'演示A班','open','demo',?,1)", (ts,))
conn.execute(
    "INSERT INTO TOPICS(TOPIC_CODE,TOPIC_NAME,TOPIC_DESC,TAG,DIFFICULTY,MAX_NUM,TEACHER_ID,TARGET_CLASSES,STATUS,CREATED_BY,CREATED_AT,FLAG)"
    " VALUES('D02','选课系统数据分析','对选课数据做统计与大屏','数据分析','中等',3,"
    "(SELECT ID FROM USERS WHERE USER_NAME='demo_t1'),NULL,'open','demo',?,1)", (ts,))
# 李老师课题：C班专属 + 公共
conn.execute(
    "INSERT INTO TOPICS(TOPIC_CODE,TOPIC_NAME,TOPIC_DESC,TAG,DIFFICULTY,MAX_NUM,TEACHER_ID,TARGET_CLASSES,STATUS,CREATED_BY,CREATED_AT,FLAG)"
    " VALUES('D03','区块链积分结算游戏','游戏积分上链结算','区块链','较难',2,"
    "(SELECT ID FROM USERS WHERE USER_NAME='demo_t2'),'演示C班','open','demo',?,1)", (ts,))
conn.execute(
    "INSERT INTO TOPICS(TOPIC_CODE,TOPIC_NAME,TOPIC_DESC,TAG,DIFFICULTY,MAX_NUM,TEACHER_ID,TARGET_CLASSES,STATUS,CREATED_BY,CREATED_AT,FLAG)"
    " VALUES('D04','校园二手交易平台','小程序+Web 双端','Web开发','中等',2,"
    "(SELECT ID FROM USERS WHERE USER_NAME='demo_t2'),NULL,'open','demo',?,1)", (ts,))

# 两位教师的轮次（先到先得 / 多志愿，可自行在页面修改体验）
for teacher, rule in [("demo_t1", "first_come"), ("demo_t2", "multi_wish")]:
    exists = conn.execute("SELECT ID FROM ROUNDS WHERE TEACHER_ID=(SELECT ID FROM USERS WHERE USER_NAME=?)", (teacher,)).fetchone()
    if exists:
        conn.execute("UPDATE ROUNDS SET RULE=?, START_AT='2020-01-01 00:00:00', END_AT=?, STATUS='open' WHERE ID=?",
                     (rule, end, exists["ID"]))
    else:
        conn.execute(
            "INSERT INTO ROUNDS(ROUND_NAME,TEACHER_ID,RULE,START_AT,END_AT,WAITLIST_ON,PROMOTE_MODE,MAX_WISH,STATUS,CREATED_BY,CREATED_AT,FLAG)"
            " VALUES('演示轮次',(SELECT ID FROM USERS WHERE USER_NAME=?),?,'2020-01-01 00:00:00',?,1,'auto',3,'open','demo',?,1)",
            (teacher, rule, end, ts))

# 两条公告
conn.execute(
    "INSERT INTO ANNOUNCEMENTS(TITLE,CONTENT,TEACHER_ID,PUBLISHED_AT,CREATED_BY,CREATED_AT,FLAG)"
    " VALUES('选题开始通知','同学们请在截止前完成选题，每人限选1题。',(SELECT ID FROM USERS WHERE USER_NAME='demo_t1'),?,'demo_t1',?,1)",
    (ts, ts))
conn.execute(
    "INSERT INTO ANNOUNCEMENTS(TITLE,CONTENT,TEACHER_ID,PUBLISHED_AT,CREATED_BY,CREATED_AT,FLAG)"
    " VALUES('志愿填报说明','请填报2个志愿，1为最想去；截止后统一分配。',(SELECT ID FROM USERS WHERE USER_NAME='demo_t2'),?,'demo_t2',?,1)",
    (ts, ts))
conn.commit()
conn.close()

print("=" * 56)
print("演示数据已生成！可用以下账号在多个浏览器同时登录测试：")
print("（所有密码均为 123456，访问 http://localhost:5000）")
print("-" * 56)
print("管理员  admin      —— 登记/分配学生给老师")
print("教师    demo_t1    —— 王老师：管 演示A班/演示B班（先到先得）")
print("教师    demo_t2    —— 李老师：管 演示C班（多志愿分配）")
print("-" * 56)
for i in range(1, 13):
    cls = "演示A班" if i <= 4 else "演示B班" if i <= 8 else "演示C班"
    print(f"学生    demo_s{i:<3} —— {cls} 学生{i}")
print("=" * 56)
print("建议测试路径：")
print("1. admin 登录 → 观察学生都绑定了对应老师")
print("2. demo_t1 登录 → 发布课题（可改面向班级）→ 开放轮次")
print("3. demo_s1~s8 多开浏览器登录 → 只有对应班级能看到对应课题")
print("4. demo_t2 + demo_s9~s12 → 体验多志愿填报与分配")
