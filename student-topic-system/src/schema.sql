-- 学生选题系统数据库脚本（SQLite）
-- 所有表统一包含 CREATED_BY / CREATED_AT / UPDATED_BY / UPDATED_AT / FLAG 五个公共字段

CREATE TABLE IF NOT EXISTS USERS (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    USER_NO        TEXT NOT NULL UNIQUE,              -- 学号/工号
    USER_NAME      TEXT NOT NULL UNIQUE,              -- 登录名
    PASSWORD_HASH  TEXT NOT NULL,                     -- 密码哈希
    REAL_NAME      TEXT NOT NULL,
    CLASS_NAME     TEXT,
    GPA            REAL DEFAULT 0.0,                  -- 绩点
    ROLE           TEXT NOT NULL DEFAULT 'student',   -- student / teacher / admin
    DEPT           TEXT,
    ADVISOR_ID     INTEGER,                           -- 学生所属指导教师（教师/管理员为空）
    STATUS         TEXT NOT NULL DEFAULT 'active',    -- active / disabled（停用后禁止登录）
    CREATED_BY     TEXT,
    CREATED_AT     TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS TOPICS (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    TOPIC_CODE     TEXT NOT NULL UNIQUE,              -- 题目编号 T01...
    TOPIC_NAME     TEXT NOT NULL,
    TOPIC_DESC     TEXT,
    TAG            TEXT,
    DIFFICULTY     TEXT DEFAULT '中等',                -- 较易/中等/较难
    MAX_NUM        INTEGER NOT NULL DEFAULT 4,        -- 人数上限
    TEACHER_ID     INTEGER,
    TARGET_CLASSES TEXT,                              -- 面向班级（逗号分隔，空=教师名下所有班级）
    PREREQ_TEXT    TEXT,                              -- 前置要求（人话描述，学生选课前可见）
    PREREQ_RULES   TEXT,                              -- 前置规则 JSON，如 {"数据结构":75,"高等数学":70}
    STATUS         TEXT DEFAULT 'open',               -- open / closed
    CREATED_BY     TEXT,
    CREATED_AT     TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ROUNDS (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    ROUND_NAME     TEXT NOT NULL,
    TEACHER_ID     INTEGER,                           -- 轮次归属教师（每位教师管理自己的轮次）
    RULE           TEXT DEFAULT 'first_come',         -- first_come/gpa_priority/multi_wish/lottery
    START_AT       TEXT,
    END_AT         TEXT,
    WAITLIST_ON    INTEGER DEFAULT 1,                 -- 候补开关
    PROMOTE_MODE   TEXT DEFAULT 'auto',               -- 递补方式: auto/manual/off
    MAX_WISH       INTEGER DEFAULT 3,                 -- 多志愿模式每人志愿数
    LOTTERY_SEED   TEXT,                              -- 随机抽签种子（公开可复现，保证公平可审计）
    ALLOW_CLASSES  TEXT,                              -- 分组轮选：允许选题的班级（逗号分隔，空=不限）
    MAX_CANCEL     INTEGER DEFAULT 2,                 -- 防反复退选：每轮每生最多退选次数（0=不限）
    CANCEL_LOCK_HOURS INTEGER DEFAULT 2,              -- 防截止前恶意退选：截止前多少小时内禁止退选（0=不限）
    STATUS         TEXT DEFAULT 'open',               -- open / closed
    CREATED_BY     TEXT,
    CREATED_AT     TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS SELECTIONS (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    ROUND_ID       INTEGER NOT NULL,
    TOPIC_ID       INTEGER NOT NULL,
    USER_ID        INTEGER NOT NULL,
    STATUS         TEXT NOT NULL DEFAULT 'confirmed', -- confirmed/waiting/wish/entry/cancelled
    WISH_NO        INTEGER,                           -- 志愿序号（多志愿模式，1=第一志愿）
    QUEUE_NO       INTEGER,                           -- 候补序号
    SELECTED_AT    TEXT,
    CANCELLED_AT   TEXT,
    CREATED_BY     TEXT,
    CREATED_AT     TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);

-- 同一轮次中，一个用户只能有一条 confirmed 记录（并发防重双保险）
CREATE UNIQUE INDEX IF NOT EXISTS UK_SELECTION_USER
    ON SELECTIONS (ROUND_ID, USER_ID) WHERE STATUS = 'confirmed';

CREATE INDEX IF NOT EXISTS IDX_SELECTION_TOPIC ON SELECTIONS (TOPIC_ID, STATUS);

-- 操作日志表：选题/退选/递补/分配/抽签等全部动作留痕，保证过程可追溯、可审计
CREATE TABLE IF NOT EXISTS SELECTION_EVENTS (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    ROUND_ID       INTEGER NOT NULL,
    TOPIC_ID       INTEGER,
    USER_ID        INTEGER,
    ACTION         TEXT NOT NULL,                     -- select/cancel/promote/allocate/lottery/manual_promote
    FROM_STATUS    TEXT,
    TO_STATUS      TEXT,
    WISH_NO        INTEGER,
    QUEUE_NO       INTEGER,
    OPERATOR       TEXT,                              -- 操作人（学生本人/system/教师）
    REMARK         TEXT,                              -- 备注（如抽签种子）
    OPERATED_AT    TEXT NOT NULL,
    CREATED_BY     TEXT,
    CREATED_AT     TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS IDX_EVENTS_ROUND ON SELECTION_EVENTS (ROUND_ID, OPERATED_AT);

-- 课程字典：教师录成绩时按课程名归集（学分仅作展示）
CREATE TABLE IF NOT EXISTS COURSES (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    COURSE_NAME    TEXT NOT NULL UNIQUE,
    CREDIT         REAL DEFAULT 0.0,
    CREATED_BY     TEXT,
    CREATED_AT     TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);

-- 学生课程成绩：由教师按自己管理的学生录入；同一学生同一课程只保留一条（重复录入即更新）
CREATE TABLE IF NOT EXISTS SCORES (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    USER_ID        INTEGER NOT NULL,
    COURSE_ID      INTEGER NOT NULL,
    SCORE          REAL NOT NULL,
    TERM           TEXT,                              -- 学期，如 2026春
    CREATED_BY     TEXT,
    CREATED_AT      TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS UK_SCORE_USER_COURSE ON SCORES (USER_ID, COURSE_ID) WHERE FLAG = 1;

CREATE INDEX IF NOT EXISTS IDX_SCORES_USER ON SCORES (USER_ID);

-- 站内通知：目前用于「教师退课提示」（含原因与建议），学生登录后可见
CREATE TABLE IF NOT EXISTS NOTICES (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    USER_ID        INTEGER NOT NULL,                  -- 接收人（学生）
    TITLE          TEXT NOT NULL,
    CONTENT        TEXT,
    KIND           TEXT DEFAULT 'kick',               -- kick 退课提醒
    TOPIC_ID       INTEGER,
    READ_FLAG      INTEGER DEFAULT 0,                 -- 0 未读 / 1 已读
    PUBLISHED_AT   TEXT,
    CREATED_BY     TEXT,
    CREATED_AT     TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS IDX_NOTICES_USER ON NOTICES (USER_ID, READ_FLAG);

CREATE TABLE IF NOT EXISTS ANNOUNCEMENTS (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    TITLE          TEXT NOT NULL,
    CONTENT        TEXT,
    TEACHER_ID     INTEGER,                           -- 公告归属教师（学生只看自己老师的公告）
    PUBLISHED_AT   TEXT,
    CREATED_BY     TEXT,
    CREATED_AT     TEXT,
    UPDATED_BY     TEXT,
    UPDATED_AT     TEXT,
    FLAG           INTEGER DEFAULT 1
);
