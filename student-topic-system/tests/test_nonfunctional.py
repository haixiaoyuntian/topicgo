# -*- coding: utf-8 -*-
"""非功能性验收脚本：执行效率 / 安全性 / 报错处理 / 健壮性

用法（先启动服务，在 src 目录执行）：
    ../.venv/bin/python ../tests/test_nonfunctional.py

说明：
- 效率部分对 14 个页面/接口预热 3 次、连续请求 30 次，取中位数/P95/最大值；
- 安全、报错、健壮性部分通过 HTTP 触发越权、缺 CSRF、非法 ID、404/405、SQLi/XSS 等路径；
- 只读检查，不写业务数据；脚本退出码 0 表示全部通过。
"""
import http.cookiejar
import math
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:5000"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("PASS" if cond else "FAIL"), name, detail)


def new_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def request(op, path, data=None, timeout=20):
    body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body)
    try:
        with op.open(req, timeout=timeout) as resp:
            return resp.status, resp.geturl(), resp.read().decode("utf-8", "ignore"), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.geturl(), exc.read().decode("utf-8", "ignore"), exc.headers


def get(op, path):
    return request(op, path)


def post(op, path, data):
    return request(op, path, data=data)


def csrf(html):
    m = re.search(r'name="_csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else ""


def flash(html):
    return [re.sub(r"<[^>]+>", "", x).strip()
            for x in re.findall(r'<div class="flash[^"]*">(.*?)</div>', html, re.S)]


def login(user, pwd="123456"):
    op = new_opener()
    token = csrf(get(op, "/login")[2])
    status, url, body, _ = post(op, "/login", {"_csrf_token": token, "username": user, "password": pwd})
    return op, token, body


def measure(op, path, warmup=3, times=30):
    for _ in range(warmup):
        request(op, path)
    values = []
    for _ in range(times):
        t0 = time.perf_counter()
        request(op, path)
        values.append((time.perf_counter() - t0) * 1000)
    values.sort()
    return {
        "median": statistics.median(values),
        "avg": sum(values) / len(values),
        "p95": values[max(0, math.ceil(len(values) * 0.95) - 1)],
        "max": max(values),
    }


print("TopicGo 非功能性验收检查")
print("环境：本机 127.0.0.1 / Flask 开发服务器 / SQLite WAL")
print("时间：2026-10-30")
print()

# 登录三个角色，准备只读会话
student, stu_token, _ = login("zhangsan")
teacher, tea_token, _ = login("teacher")
admin, _, _ = login("admin")
anon = new_opener()

# ---------- 1. 执行效率 ----------
print("== 1. 执行效率（GET，预热 3 次 + 连续 30 次，单位 ms）==")
routes = [
    ("登录页", "/login", anon),
    ("学生首页", "/student/index", student),
    ("浏览题库", "/student/browse", student),
    ("我的选题", "/student/mine", student),
    ("结果公示", "/student/public", student),
    ("站内通知", "/student/notices", student),
    ("教师工作台", "/teacher/index", teacher),
    ("选题监控", "/teacher/monitor", teacher),
    ("题库维护", "/teacher/topics", teacher),
    ("选题轮次", "/teacher/round", teacher),
    ("我的学生", "/teacher/students", teacher),
    ("操作日志", "/teacher/logs", teacher),
    ("CSV 导出", "/teacher/export", teacher),
    ("用户管理", "/admin/users", admin),
]
print(f"{'页面':<10}{'中位数':>8}{'平均':>8}{'P95':>8}{'最大':>8}  状态")
slow = []
medians = []
for name, path, op in routes:
    stat = measure(op, path)
    status = get(op, path)[0]
    medians.append(stat["median"])
    if stat["median"] >= 100 or status != 200:
        slow.append(name)
    print(f"{name:<10}{stat['median']:>8.2f}{stat['avg']:>8.2f}"
          f"{stat['p95']:>8.2f}{stat['max']:>8.2f}  {status}")
check("14 个页面/接口中位数均低于 100 ms",
      len(slow) == 0, f"范围 {min(medians):.2f}~{max(medians):.2f} ms")

# 登录 POST 单独计时（scrypt 哈希成本）
login_times = []
for _ in range(3):
    t0 = time.perf_counter()
    login("zhangsan")
    login_times.append((time.perf_counter() - t0) * 1000)
login_avg = sum(login_times) / len(login_times)
print(f"\n登录全流程（GET + POST）：平均 {login_avg:.1f} ms"
      f"（{', '.join(f'{x:.1f}' for x in login_times)}）")
check("登录全流程低于 500 ms", login_avg < 500, f"{login_avg:.1f} ms")

# ---------- 2. 安全性 ----------
print("\n== 2. 安全性 ==")
status, url, body, _ = get(anon, "/teacher/index")
check("未登录访问教师页被拦到登录页",
      status == 200 and url.rstrip("/").endswith("/login") and "登录" in body)

_, _, body = login("nobody")
check("未登记账号不能登录", "账号不存在" in body)

status, url, body, _ = get(student, "/teacher/index")
check("学生访问教师页被拒绝",
      status == 200 and url.rstrip("/").endswith("/student/index") and "无权限访问" in body)

status, _, body, _ = post(student, "/student/select/1", {})
check("缺少 CSRF 令牌的 POST 被拒绝", "表单校验失败（CSRF）" in body)

status, _, body, _ = get(student, "/student/browse?kw=" + urllib.parse.quote("' OR 1=1 --"))
check("SQL 注入字符串按普通文本处理",
      status == 200 and "Traceback" not in body and len(re.findall(r">T0\d<", body)) == 0)

status, _, body, _ = get(student, "/student/browse?kw=" +
                          urllib.parse.quote("<script>alert(1)</script>"))
check("搜索词中的脚本被 HTML 转义",
      status == 200 and "<script>alert(1)</script>" not in body
      and "&lt;script&gt;alert(1)&lt;/script&gt;" in body)

# ---------- 3. 报错处理 ----------
print("\n== 3. 报错处理 ==")
status, _, body, _ = post(student, "/student/select/999999", {"_csrf_token": stu_token})
check("选题不存在的课题：中文提示、无 500",
      status == 200 and "题目不存在" in body, f"HTTP {status}")

status, _, body, _ = get(teacher, "/teacher/kick/999999")
check("退课不存在的记录：中文提示、无 500",
      status == 200 and "记录不存在" in body, f"HTTP {status}")

status, _, _, _ = get(teacher, "/teacher/kick/abc")
check("非法路径参数返回 404", status == 404, f"HTTP {status}")

status, _, _, _ = get(teacher, "/teacher/allocate")
check("GET 调用 POST 接口返回 405", status == 405, f"HTTP {status}")

# ---------- 4. 健壮性 ----------
print("\n== 4. 健壮性 ==")
status, _, body, _ = get(student, "/student/browse?kw=" + "x" * 500)
check("500 字符超长搜索词不报错", status == 200, f"HTTP {status}")

status, _, _, headers = get(teacher, "/teacher/export")
check("CSV 导出返回 text/csv",
      status == 200 and "text/csv" in (headers.get("Content-Type") or ""), f"HTTP {status}")

print()
print(f"===== 汇总: {len(passed)} 通过, {len(failed)} 失败 =====")
if failed:
    print("失败项:", failed)
raise SystemExit(1 if failed else 0)
