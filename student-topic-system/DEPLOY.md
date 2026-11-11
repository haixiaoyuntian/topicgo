# 部署到 Render（固定公网地址）

> 目标：把系统部署到免费云平台，得到一个长期可访问的 `https://xxx.onrender.com` 地址，
> 不再依赖本机的 cloudflared 临时隧道。
> 免费实例不需要信用卡；构建与运行全部在 Render 完成。

## 0. 前置条件

- 一个**公开**的 Git 仓库（当前仓库已可匿名克隆：`https://cnb.cool/jxh-U202414904/software-engineering.git`）
- Render 账号（邮箱或 GitHub 注册）

## 1. 先把部署文件推到仓库

仓库根目录需要以下文件（本仓库已提供）：

```
Dockerfile
.dockerignore
render.yaml
src/requirements.txt     # 已加入 gunicorn
src/app.py               # SECRET_KEY 改为读环境变量
```

提交并推送：

```bash
git add Dockerfile .dockerignore render.yaml src/requirements.txt src/app.py
git commit -m "chore: 支持 Docker 部署到 Render"
git push origin main
```

## 2. 在 Render 新建 Web Service

1. 打开 <https://dashboard.render.com>，用邮箱或 GitHub 注册/登录；
2. 点 **New +** → **Web Service**；
3. 选择 **Public Git repository**，粘贴仓库地址：
   `https://cnb.cool/jxh-U202414904/software-engineering.git`
   （若页面只允许 GitHub/GitLab/Bitbucket，见第 5 节镜像到 GitHub）；
4. 填写：
   - Name：`topicgo`
   - Branch：`main`
   - Runtime：**Docker**（Render 会自动识别根目录的 `Dockerfile`）
   - Region：**Singapore**（离国内最近）
   - Instance Type：**Free**
5. 在 **Environment Variables** 添加：
   - Key：`SECRET_KEY`
   - Value：点 **Generate** 生成随机值（不要用默认值）
6. 点 **Create Web Service**，等待构建（首次约 3~5 分钟）。

构建成功后，页面顶部会显示地址，形如：

```
https://topicgo-xxxx.onrender.com
```

## 3. 部署后自检

| 检查项 | 操作 | 预期 |
| --- | --- | --- |
| 登录页 | 打开 `/login` | 正常显示，非 502 |
| 学生登录 | `zhangsan` / `123456` | 进入学生首页 |
| 教师登录 | `teacher` / `123456` | 进入教师工作台 |
| 管理员 | `admin` / `123456` | 进入用户管理 |
| 展示页 | `/home`、`/prototype`、`/guide` | 均返回 200 |
| 截图 | `/screenshots/01-login.png` | 正常显示 |

## 4. 用 Blueprint 一键创建（可选）

仓库已含 `render.yaml`（free / singapore / healthCheckPath=/login / 自动生成 SECRET_KEY）：

1. Render 控制台 → **New +** → **Blueprint**；
2. 选择仓库 → Render 读取 `render.yaml` → **Apply**。

## 5. 如果 Render 不接受 CNB 地址：镜像到 GitHub

```bash
git clone --mirror https://cnb.cool/jxh-U202414904/software-engineering.git
cd software-engineering.git
git remote add github https://github.com/<你的用户名>/topicgo.git
git push --mirror github
```

然后在 GitHub 仓库 **Settings → General** 确认可见性为 **Public**，
再回到 Render 第 2 步选择该 GitHub 仓库即可。

## 6. 免费实例的注意事项

- **休眠**：15 分钟无访问会休眠，之后第一次打开需要 30~60 秒唤醒，属正常现象；
- **数据**：免费实例没有持久磁盘，重新部署/重启会重建容器，SQLite 回到种子数据（演示足够）；
  若需长期保留数据，需升级付费实例并挂载 Disk；
- **并发**：容器使用 `gunicorn --workers 1 --threads 4`，SQLite 写锁下比多进程更稳，请不要改成多 worker；
- **额度**：免费 Web Service 每月 750 小时，单实例足够；
- **域名**：可绑定自定义域名，但不是必需。

## 7. 本地验证 Docker 镜像（可选）

```bash
docker build -t topicgo .
docker run --rm -p 8000:8000 -e SECRET_KEY=test topicgo
# 浏览器访问 http://127.0.0.1:8000/login
```

## 8. 部署成功后需要同步的地方

- 本文件第 2 步得到的地址；
- 报告附录 B 的“固定地址部署”说明；
- 答辩 PPT 与验收表的“平台网址”；
- `README.md` 的在线体验链接。
