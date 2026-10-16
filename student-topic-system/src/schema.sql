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
