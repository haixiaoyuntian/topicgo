"""截图脚本：自动截取系统各页面截图，用于文档配图

用法：
  1. 先启动服务（cd src && python app.py）
  2. （可选）python ../tests/demo_seed.py 生成更丰富的演示数据
  3. 在 src 目录执行：python ../tests/take_screenshots.py
截图输出到 wiki/screenshots/ 目录。
"""
import os

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "wiki", "screenshots")
os.makedirs(OUT, exist_ok=True)


def login(page, who):
    page.goto(f"{BASE}/login")
    page.fill("input[name=username]", who)
    page.fill("input[name=password]", "123456")
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")


def shot(page, name):
    path = os.path.join(OUT, name)
    page.screenshot(path=path, full_page=True)
    print("saved:", path)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})

    # 登录页
    page.goto(f"{BASE}/login")
    shot(page, "01-login.png")

    # 学生端
    login(page, "zhangsan")
    shot(page, "02-student-home.png")
    page.goto(f"{BASE}/student/browse")
    shot(page, "03-student-browse.png")

    # 教师端
    login(page, "teacher")
    shot(page, "04-teacher-home.png")
    page.goto(f"{BASE}/teacher/topics")
    shot(page, "05-teacher-topics.png")
    page.goto(f"{BASE}/teacher/round")
    shot(page, "06-teacher-round.png")
    page.goto(f"{BASE}/teacher/students")
    shot(page, "07-teacher-students.png")

    # 管理员
    login(page, "admin")
    shot(page, "08-admin-users.png")

    # 原型演示
    page.goto(f"{BASE}/prototype")
    page.wait_for_timeout(500)
    shot(page, "09-prototype-student.png")
    page.click("text=教师")
    page.click("text=登 录")
    page.wait_for_timeout(500)
    shot(page, "10-prototype-teacher.png")

    browser.close()
print("全部截图完成 ->", OUT)
