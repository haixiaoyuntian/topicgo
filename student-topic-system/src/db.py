"""数据库连接、初始化、迁移与种子数据"""
import os
import sqlite3
from datetime import datetime

from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "topicgo.db")


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn():
    """返回连接：WAL 模式 + 手动事务，保证并发写安全"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.isolation_level = None
    return conn


def _columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn):
    """轻量迁移：老库缺少的列/表在此补齐，保留历史数据"""
    user_cols = _columns(conn, "USERS")
    if "STATUS" not in user_cols:
        conn.execute("ALTER TABLE USERS ADD COLUMN STATUS TEXT DEFAULT 'active'")
    round_cols = _columns(conn, "ROUNDS")
    for col, ddl in [
        ("TEACHER_ID", "INTEGER"),
        ("PROMOTE_MODE", "TEXT DEFAULT 'auto'"),
        ("MAX_WISH", "INTEGER DEFAULT 3"),
        ("LOTTERY_SEED", "TEXT"),
        ("ALLOW_CLASSES", "TEXT"),
    ]:
        if col not in round_cols:
            conn.execute(f"ALTER TABLE ROUNDS ADD COLUMN {col} {ddl}")
    if "ADVISOR_ID" not in user_cols:
        conn.execute("ALTER TABLE USERS ADD COLUMN ADVISOR_ID INTEGER")
    sel_cols = _columns(conn, "SELECTIONS")
    if "WISH_NO" not in sel_cols:
        conn.execute("ALTER TABLE SELECTIONS ADD COLUMN WISH_NO INTEGER")
    ann_cols = _columns(conn, "ANNOUNCEMENTS")
    if "TEACHER_ID" not in ann_cols:
        conn.execute("ALTER TABLE ANNOUNCEMENTS ADD COLUMN TEACHER_ID INTEGER")
    topic_cols = _columns(conn, "TOPICS")
    if "TARGET_CLASSES" not in topic_cols:
        conn.execute("ALTER TABLE TOPICS ADD COLUMN TARGET_CLASSES TEXT")
    conn.execute("""CREATE TABLE IF NOT EXISTS SELECTION_EVENTS (
        ID INTEGER PRIMARY KEY AUTOINCREMENT,
        ROUND_ID INTEGER NOT NULL, TOPIC_ID INTEGER, USER_ID INTEGER,
        ACTION TEXT NOT NULL, FROM_STATUS TEXT, TO_STATUS TEXT,
        WISH_NO INTEGER, QUEUE_NO INTEGER, OPERATOR TEXT, REMARK TEXT,
        OPERATED_AT TEXT NOT NULL, CREATED_BY TEXT, CREATED_AT TEXT,
        UPDATED_BY TEXT, UPDATED_AT TEXT, FLAG INTEGER DEFAULT 1)""")
    conn.execute("CREATE INDEX IF NOT EXISTS IDX_EVENTS_ROUND ON SELECTION_EVENTS (ROUND_ID, OPERATED_AT)")


def init_db():
    with open(os.path.join(BASE_DIR, "schema.sql"), encoding="utf-8") as f:
        schema = f.read()
    conn = get_conn()
    conn.executescript(schema)
    migrate(conn)
    if conn.execute("SELECT COUNT(*) c FROM USERS").fetchone()["c"] == 0:
        seed(conn)
    # 确保存在 admin 账号
    if not conn.execute("SELECT 1 FROM USERS WHERE ROLE='admin' AND FLAG=1").fetchone():
        ts = now_str()
        conn.execute(
            "INSERT INTO USERS(USER_NO,USER_NAME,PASSWORD_HASH,REAL_NAME,ROLE,DEPT,STATUS,CREATED_BY,CREATED_AT,FLAG)"
            " VALUES('ADMIN','admin',?,?,'admin','软件学院','active','system',?,1)",
            (generate_password_hash("123456"), "系统管理员", ts),
        )
    conn.close()


def seed(conn):
    ts = now_str()
    users = [
        ("20230101", "zhangsan", "123456", "同学A", "软工2301", 3.8, "student"),
        ("20230102", "lisi", "123456", "同学B", "软工2301", 3.2, "student"),
        ("20230103", "wangwu", "123456", "同学C", "软工2302", 3.9, "student"),
        ("20230104", "zhaoliu", "123456", "同学D", "软工2302", 3.5, "student"),
        ("20230105", "chenqi", "123456", "同学E", "软工2301", 2.9, "student"),
        ("T2001", "teacher", "123456", "刘老师", None, 0, "teacher"),
    ]
    # 刘老师(T2001)的工号为 6，学生默认绑定到刘老师
    for no, name, pwd, real, cls, gpa, role in users:
        conn.execute(
            "INSERT INTO USERS(USER_NO,USER_NAME,PASSWORD_HASH,REAL_NAME,CLASS_NAME,GPA,ROLE,DEPT,ADVISOR_ID,STATUS,CREATED_BY,CREATED_AT,FLAG)"
            " VALUES(?,?,?,?,?,?,?,'软件学院',CASE WHEN ?='student' THEN 6 ELSE NULL END,'active','system',?,1)",
            (no, name, generate_password_hash(pwd), real, cls, gpa, role, role, ts),
        )
    topics = [
        ("T01", "基于Flask的图书管理系统", "实现图书的增删改查、借阅管理与读者统计", "Web开发", "较易", 3),
        ("T02", "学生选题系统（TopicGo）", "公正公开的课设在线选题平台，支持候补递补", "Web开发", "中等", 3),
        ("T03", "基于YOLOv5的课堂专注度识别", "摄像头实时检测学生课堂行为并统计专注度", "人工智能", "较难", 2),
        ("T04", "电商销售数据分析与可视化", "爬取公开销售数据，完成清洗、分析与大屏展示", "数据分析", "中等", 4),
        ("T05", "区块链五子棋积分结算游戏", "游戏结束时用区块链结算积分，降低链上事务负载", "区块链", "较难", 2),
        ("T06", "校园二手交易小程序", "二手商品发布、聊天议价与线下交付管理", "Web开发", "中等", 4),
    ]
    for code, name, desc, tag, diff, max_num in topics:
        conn.execute(
            "INSERT INTO TOPICS(TOPIC_CODE,TOPIC_NAME,TOPIC_DESC,TAG,DIFFICULTY,MAX_NUM,TEACHER_ID,STATUS,CREATED_BY,CREATED_AT,FLAG)"
            " VALUES(?,?,?,?,?,?,6,'open','system',?,1)",
            (code, name, desc, tag, diff, max_num, ts),
        )
    # 每位教师默认拥有一个自己的轮次（刘老师 ID=6）
    teacher = conn.execute("SELECT ID FROM USERS WHERE ROLE='teacher' AND FLAG=1").fetchone()
    if teacher:
        conn.execute(
            "INSERT INTO ROUNDS(ROUND_NAME,TEACHER_ID,RULE,START_AT,END_AT,WAITLIST_ON,PROMOTE_MODE,MAX_WISH,STATUS,CREATED_BY,CREATED_AT,FLAG)"
            " VALUES('2026秋季课设选题·第一轮',?,'first_come','2026-09-01 08:00:00','2026-12-31 18:00:00',1,'auto',3,'open','system',?,1)",
            (teacher["ID"], ts),
        )
    conn.execute(
        "INSERT INTO ANNOUNCEMENTS(TITLE,CONTENT,PUBLISHED_AT,CREATED_BY,CREATED_AT,FLAG)"
        " VALUES('选题须知','每人限选1题；满员题目可加入候补；退选后名额按递补策略处理。',?,'system',?,1)",
        (ts, ts),
    )
    conn.commit()
