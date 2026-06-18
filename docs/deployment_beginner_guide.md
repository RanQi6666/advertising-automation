# 新手部署指南

这份文档记录第一版生产部署方案：一台云服务器、Docker Compose、PostgreSQL、FastAPI 后端、React 前端，以及服务器本地 `storage` 保存图片和视频。

## 1. 目标效果

部署完成后，用户访问一个地址：

```text
https://ai.example.com
```

这个地址同时提供：

```text
/                 前端页面
/api/v1           后端接口
/storage          图片和视频文件
```

最终 JSON 里的素材地址会像这样：

```text
https://ai.example.com/storage/images/xxx.jpeg
https://ai.example.com/storage/videos/xxx.mp4
```

## 2. 服务器建议

第一版建议：

```text
系统：Ubuntu 22.04 LTS
配置：2 vCPU / 4 GiB
磁盘：80 GiB 起步
公网带宽：5 Mbps 起步
安全组放行：22、80、443
```

如果暂时没有域名，可以先用服务器 IP 测试：

```text
http://YOUR_SERVER_IP
```

## 3. 登录服务器

在 Windows PowerShell 中执行：

```powershell
ssh root@YOUR_SERVER_IP
```

第一次连接会问是否信任，输入：

```text
yes
```

然后输入服务器密码。密码输入时屏幕不会显示，这是正常现象。

## 4. 安装 Docker

登录服务器后执行：

```bash
apt update
apt install -y ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
docker --version
docker compose version
```

看到版本号就说明 Docker 安装成功。

## 5. 拉取代码

```bash
cd /opt
git clone https://github.com/RanQi6666/advertising-automation.git
cd advertising-automation
```

如果仓库是私有仓库，GitHub 可能要求登录或 token。不要把 token 写进公开文档。

## 6. 创建生产环境变量

复制模板：

```bash
cp .env.production.example .env.production
nano .env.production
```

至少要改这些：

```text
POSTGRES_PASSWORD
SECRET_KEY
PUBLIC_BASE_URL
AD_GENERATION_REVIEW_BASE_URL
CORS_ORIGINS
VOLCENGINE_API_KEY
VOLCENGINE_MODEL
VOLCENGINE_IMAGE_MODEL
VOLCENGINE_VIDEO_MODEL
```

没有域名时先这样填：

```text
PUBLIC_BASE_URL=http://YOUR_SERVER_IP
AD_GENERATION_REVIEW_BASE_URL=http://YOUR_SERVER_IP
CORS_ORIGINS=http://YOUR_SERVER_IP
```

有域名和 HTTPS 后改成：

```text
PUBLIC_BASE_URL=https://ai.example.com
AD_GENERATION_REVIEW_BASE_URL=https://ai.example.com
CORS_ORIGINS=https://ai.example.com
```

保存 `nano`：

```text
Ctrl + O
Enter
Ctrl + X
```

## 7. 启动系统

在项目根目录执行：

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

如果只是想先检查配置文件能不能被 Docker 正确解析，可以用模板文件：

```bash
APP_ENV_FILE=.env.production.example docker compose --env-file .env.production.example -f docker-compose.prod.yml config
```

查看状态：

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

查看日志：

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f backend
```

## 8. 验证访问

打开：

```text
http://YOUR_SERVER_IP
http://YOUR_SERVER_IP/api/v1/health/live
```

健康接口返回类似下面内容就说明后端正常：

```json
{"status":"ok"}
```

## 9. 更新代码

以后代码更新后，在服务器项目目录执行：

```bash
git pull
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

系统会重新构建并启动。数据库和图片视频保存在 Docker volume 中，不会因为重新构建镜像而丢失。

## 10. 常用排查命令

查看容器：

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

查看后端日志：

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f backend
```

查看前端日志：

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f web
```

重启：

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml restart
```

停止：

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml down
```

注意：不要轻易删除 Docker volumes，否则数据库和素材文件会丢。
