# 鲸海拾贝官网 · Taowhale Site

聚焦 AIGC 创作的学习、展示与商业转化平台官网。React 前端 + FastAPI 后端 + Docker 一键部署。

## 功能
- 五大板块:学员作品 / 商单大厅 / 知识库 / 课程教程 / 社区论坛,内容全部数据库化,管理后台可视化录入(初始为占位种子数据,待替换真实素材)
- 双主题(明亮默认 / 深色),WebGL 流体绸缎主屏,五板块各有主题动效
- 账号系统:邮箱 + 密码注册登录(pbkdf2 哈希、HMAC 签名 token)
- 论坛:发帖 / 回帖 / 浏览计数 / 置顶 / 公告权限,SQLite 落盘
- 管理后台(`#/admin`,仅官方账号):数据概览、内容录入、帖子置顶/删除、用户封禁/角色管理、工作组管理;官方账号密码由 `ADMIN_PASSWORD` 环境变量注入
- 知识库三级分级:完整知识库(对外公开)/组专属(课程组、班主任组、助教组等,可自定义)/个人专属;工作人员(导师/官方/任意组成员)可浏览全部层级并向自己的组库与个人库发布文档,访客与学员仅见公开级
- 前端无后端时自动回退演示数据,`static/index.html` 双击即可本地预览

## 目录
```
backend/            FastAPI 后端(main.py 单文件)
static/index.html   前端(单文件 React,CDN 加载)
Dockerfile          构建镜像
docker-compose.yml  一键启动(80 端口,JWT_SECRET 须替换)
部署指南.md          面向 43.128.2.110 的逐步上线文档
data/               SQLite 数据目录(.db 不入库)
```

## 快速部署
```bash
sed -i "s/CHANGE_ME_TO_RANDOM_64_CHARS/$(openssl rand -hex 32)/" docker-compose.yml
docker compose up -d --build
curl http://127.0.0.1/api/health
```

## 版本里程
- V1 静态多页站 → V2 React 重构 → V3 高级感重设计(对标 Linear/Vercel)
- V3.1 论坛 → V3.2 流体绸缎主屏 → V3.3 双主题 → V3.4 板块动效 → V3.5 登录注册 → V4 真实后端
- V4.1 管理后台:官方账号激活(ADMIN_PASSWORD)、管理 API、/admin 面板、论坛版主操作、用户封禁
- V4.2 内容管理:作品/商单/案例/文档/课程统一 items 表,公开读取 + 管理员 CRUD,后台「内容管理」可视化录入,前端四板块动态化(API 优先,失败回退演示数据)
- V4.3 品牌更名 taowhale;V4.4 知识库三级分级:完整(公开)/组专属/个人专属,工作组体系(多组成员),工作人员全量可见 + 自有库写入,文档正文与详情页,后台组管理
