# 部署、初始化与升级

本项目有完整离线开发路径，也提供完整服务器管线源码。实际运行依赖你自己的 FreshRSS、OpenClaw、模型账户、域名与 TLS。源码测试通过不等于这些外部服务已经配置完成。

## 1. 准备主机和路径

推荐 Ubuntu 24.04、Python 3.12、Node 24.16+（24.x）、Docker Compose、Apache、用户级 systemd。安装 `python3-venv curl jq unzip openssl util-linux apache2`，确保 Docker 当前用户可访问，并按 OpenClaw 官方说明安装和完成模型配置。

原运行器和 unit 兼容安装目录为 `/home/ubuntu/rss-intelligence-v5.2`，运行状态为 `/home/ubuntu/.openclaw/rss-intelligence`。V5.4 仍沿用该路径。其他用户和域名可先渲染一份独立部署副本：

```bash
python3 tools/configure_deployment.py --domain rss.your-domain.net \
  --relay-domain relay.your-domain.net --target-ip 192.0.2.50 \
  --home /home/ubuntu --destination /tmp/rss-deployment
```

上面的目标 IP 仍是文档示例，若使用可选中继，应换成实际接收服务器 IP。此命令只生成新目录，不接触服务和真实配置，不会覆盖已有目录。将生成目录安装到兼容路径；安装后重算 `checksums.sha256`。仓库原件中的 `rss.example.com`、`rss.example.org` 和 TEST-NET IP 不提供服务。

## 2. FreshRSS

可以使用现有 FreshRSS，或在项目根运行 `docker compose up -d`。提供的 Compose 仅将 FreshRSS 暴露于 `127.0.0.1:8080`，数据库卷名固定为 `freshrss_data`，与备份/读取模板兼容。通过 SSH 隧道访问首次配置页面，创建自己的用户；使用 SQLite，并启用 GReader API 和专用 API 密码。

`rssowner` 是脚本中示例用户名。若创建其他用户，更新 `.env` 中的 `FRESHRSS_DB_PATH`，并同步替换可选运维脚本中的数据库路径。配置自己的来源，可以导入 OPML，也可通过下一节发现候选再人工审阅。

在自己的域名上配置 Apache HTTPS VirtualHost，反向代理 `/` 到 `http://127.0.0.1:8080/`，启用 `proxy proxy_http ssl headers alias`。证书由部署者自己的 CA/证书客户端管理。先确认 FreshRSS 网站和 `api/greader.php` 可用，再配置情报输出。

## 3. Python 和公开内容目录

```bash
python3 -m venv /home/ubuntu/.local/share/rss-intelligence-v53-venv
/home/ubuntu/.local/share/rss-intelligence-v53-venv/bin/pip install -r requirements.txt
install -d -m 0700 /home/ubuntu/.openclaw/rss-intelligence
install -d -m 0700 /home/ubuntu/.openclaw/workspace/rss-daily-stage-v52
sudo install -d -m 0755 -o ubuntu -g ubuntu /var/www/rss-intelligence
```

将 `.env.example` 中需要的变量合并到 `/home/ubuntu/.openclaw/.env`，权限设为 `0600`。不要覆盖已有 OpenClaw 配置。填写自己的 FreshRSS API 地址、用户名和专用密码；`RSS_PUBLISH_BASE_URL` 应为自己的 HTTPS 域名加 `/intelligence`。执行 `python3 00-preflight/setup-publish-env.py` 会为缺失的发布令牌生成随机值并保留受保护备份，不打印令牌。该工具须在已渲染为自己域名的副本中执行。

在已存在的 HTTPS VirtualHost 上先运行 `05-apache/patch_apache_ai_rss.py --check`，再使用 `--apply` 添加静态例外，按 `INSTALL_V53.md` 安装响应头。完成 `apache2ctl configtest` 后重载。静态发布路径属于持有链接即可访问的私有路径，阅读器配置和运行日志都应妥善保管。

## 4. OpenClaw 插件与隔离代理

在 `02-rss-intelligence-stage` 和 `08-rss-curator` 中分别执行 `npm ci`、`npm run plugin:build`、`npm run plugin:validate`、`openclaw plugins install --link --force "$PWD"`。`rss-curator` 是本次开源补齐的兼容实现，区别见其 README。

保证 Gateway 继承同一组 FreshRSS 环境变量；可为 `openclaw-gateway.service` 配置用户 unit drop-in，使用 `EnvironmentFile=/home/ubuntu/.openclaw/.env`，然后执行用户级 daemon-reload 和 Gateway 重启。勿将环境文件复制到模型可读工作区。

执行 `bash 00-preflight/configure-classifier.sh` 创建独立 `rss-v52-classifier`，再执行 `bash 00-preflight/configure-v53-translator.sh` 创建独立 `rss-v53-translator`。分类/翻译只处理宿主传入的内容，禁止 shell、文件写入、网页抓取与订阅变更工具。原配置使用 `opencode-go/deepseek-v4-flash` 与 `opencode-go/deepseek-v4-pro`；应先确认模型目录、账户权限和网关 CLI 能力。替换服务商时必须同步检查环境变量、代理默认模型及历史模板。

将 `03-workspace-files/` 提示复制到 OpenClaw 工作区，同时将 `RSS_POLICY.md` 作为来源评价规则。源发现依赖 main 代理的 read/web_search/web_fetch 及 curator 工具，并要求 `/agent` 只读挂载指向宿主工作区。日常分类和翻译不授予这些工具。

## 5. 来源初始化

已有订阅不必重新发现。使用 `01-init-kit/batches.tsv` 和覆盖目标逐批发现候选；将该目录复制到 `~/.openclaw/workspace/rss-init-kit-v52`，按 `run-init.sh` 的 usage 选择单批命令。初始化产生 pending review，不会自动更改订阅。人工核对来源、Feed、分类和许可后，通过 FreshRSS 自行添加。

不要把真实订阅库、待审阅内容或初始化运行日志提交回源码仓库。

## 6. 安装调度但暂不启用

```bash
install -d -m 0755 ~/.local/bin ~/.config/systemd/user
install -m 0755 04-retry-scheduler/*.sh ~/.local/bin/
install -m 0755 04-daily-pipeline/rss-v53-daily-catchup.sh ~/.local/bin/
install -m 0644 04-retry-scheduler/*.service 04-retry-scheduler/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemd-analyze --user verify ~/.config/systemd/user/rss-v5*.service ~/.config/systemd/user/rss-v5*.timer
```

需保持用户级服务在注销后运行时，按主机管理策略为运行用户配置 linger。不要同时启用 FreshRSS 容器内 CRON_MIN、旧 actualize timer 和新抓取 timer；多个调度可能竞争数据库。

先运行 README 的 Linux 全包自检。随后手动执行抓取 service，确认 `fetch-state-v52/last-success.json`；再执行 morning service，核对 snapshot、candidate accounting、stage、完整翻译 manifest、四个 XML、原文链接和 HTTPS 内容哈希。源站无新内容时不应硬凑固定条数；可选 `target-final-acceptance.py` 保留特定迁移环境的附加断言，需先调整来源映射和预期数量。

确认成功后，按 `INSTALL_V53.md` 启用 timer，同时启用 `rss-v52-review.timer`。默认时区 Asia/Shanghai：07:00/13:00 抓取、07:10/13:10 处理、08:30/14:30 补跑；来源健康检查在抓取后运行；每分钟清理过期译文；每月 15、28 日执行来源审阅。通过 `systemctl --user list-timers` 核对实际日程。

`00-preflight/preflight.sh` 和 `06-cutover/` 是旧 V5.1→V5.2 升级工具，会检查旧任务 ID、旧调度和旧目录。全新部署无需满足不存在的旧环境；不要为了通过历史 Gate 虚构生产状态。

## 7. 可选网络中继和迁移

`07-network-ops/` 包含原后续补充功能：限定 RSS 白名单、限定正文主机、可信 DNS 地址校验、缓存、来源健康锁、抓取预热、数据库迁移、备份和停用旧核心。没有网络问题时不必启用。

这些脚本使用安装路径、systemd、Apache 和数据库，必须先审阅并按自己的环境配置。`update-target-freshrss-relay.py` 的数字 Feed ID 只是示例，必须核对 ID 与原 URL 的一一对应；程序发现 URL 不匹配会回滚事务。

运行可选安装前，将对应源文件放到脚本 `staged` 指定的 `/tmp` 路径，并在渲染后的副本中运行 `python tools/refresh_checksums.py` 更新内容校验。保留公网访问的源 IP 限制，不要部署为任意 URL 代理。更详细说明见 `07-network-ops/README.md`。

## 8. 备份和恢复

升级前停止晨间、午间、补跑和过期清理 timer，确认服务已退出；同时备份代码、user units、FreshRSS SQLite/卷、`.openclaw/.env`、发布数据、译文 manifest、成功水位和公开目录。备份含敏感内容，只应在受保护目录存放。

恢复时使用同一次备份的代码与状态，重新检查权限、配置、Apache、Gateway 和调度。不要仅回滚代码却保留不兼容状态；不要通过重放旧 stage 延长 48 小时发布窗口。V5.4 展示回填使用 `--presentation-only`，按 `INSTALL_V54.md` 执行。
