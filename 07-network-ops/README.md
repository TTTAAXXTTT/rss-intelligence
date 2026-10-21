# 可选网络与迁移工具

这套工具保留原部署后新增的白名单 RSS/正文中继、可信 DNS 校验、缓存、抓取预热、健康锁、数据库迁移、完整备份和停用旧核心功能。域名、目标地址与历史环境均已替换为示例；DNS 提供商公开前缀是地址校验规则，保留其语义。

先阅读根目录 DEPLOYMENT.md，使用 `tools/configure_deployment.py` 生成自己的副本。不要在未配置的环境执行安装/迁移脚本。数字 Feed ID 是示例；更新器同时检查原 URL，不匹配会回滚。

| 脚本 | 用途 |
|---|---|
| `rss-source-relay.py` | 回环地址监听、固定 RSS 白名单、Hugging Face 正文和可信 DNS |
| `install-aws-source-relay.sh` / `upgrade-aws-source-page-relay.sh` | 中继安装和升级，Apache 仅允许指定目标 IP |
| `rss-dns-override-update.py` | 拉取并验证可信地址映射 |
| `install-target-dnsproxy.sh` / `stage-dnsproxy-target.sh` | 安装 dnsproxy 与准备目标文件 |
| `configure-target-dns-overrides.sh` | 本机 DNS、缓存、更新服务与 timer |
| `update-target-freshrss-relay.py` / `apply-target-freshrss-relay-cutover.sh` | 事务内切换 Feed URL，保留历史与订阅 ID |
| `install-target-source-page-relay-client.sh` | 安装带受限正文中继支持的翻译器 |
| `install-target-fetch-prewarm.sh` | 安装抓取预热与数据库错误检查 |
| `install-target-source-health-lock.sh` | 来源健康等待刷新锁 |
| `create-target-final-backup.sh` / `create-aws-relay-final-backup.sh` | 可恢复备份，产物必须私密保存 |
| `pause-aws-core-after-cutover.sh` | 停用旧核心定时器并准备恢复脚本 |
| `target-final-acceptance.py` | 特定迁移拓扑的严格验收，按实际来源调整预期 |

安装器中的 `staged` 指向 `/tmp` 暂存文件。准备对应源文件后运行 `python tools/refresh_checksums.py`，使脚本预期 SHA-256 与当前源码一致。第三方 dnsproxy 压缩包需从官方 Release 独立获取，不随仓库发布。
