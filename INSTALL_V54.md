# V5.4 部署与回填说明

## 运行依赖

- Ubuntu 用户级 systemd；
- OpenClaw 专用无工具翻译代理 `rss-v53-translator`；
- Python 虚拟环境 `/home/ubuntu/.local/share/rss-intelligence-v53-venv`，包含 `certifi`、`urllib3`、`trafilatura`；
- FreshRSS 只读 GReader 接口、SQLite `PRAGMA query_only=ON` 精确条目读取，以及当期不可变源快照，作为正文降级来源。

## 部署顺序

1. 停止晨间、午间、补跑和译文清理 timers，并确认相关 services 不在运行。
2. 备份生产脚本、systemd unit、发布数据、译文状态、成功水位和当前公开目录。
3. 安装 V5.4 文件，重算校验和，执行 `package-selftest.sh` 和 `systemd-analyze verify`。
4. 在全局 daily lock 下，对仍在原 48 小时窗口内的 stage 执行翻译；必须得到精确完整 manifest。
5. 用 `host-publish.py ... --presentation-only` 回填展示层；该模式不提交 bootstrap、不写成功水位、不修改晨报基线。
6. 执行 `host-publish.py --reconcile-expired`，再执行 `host-translate.py --cleanup-only`。
7. 在线核对四个 XML、全部中文页、磁盘/HTTPS 哈希和 systemd timer，再恢复 timers。

旧 stage 的 tombstone 必须原样保留，因此回填只修复尚未到期的内容，不能给历史文章重新开始一个 48 小时窗口。

## 回滚

停止相关 timers，恢复同一次部署备份中的脚本、unit、发布数据、译文状态和水位，执行 `systemctl --user daemon-reload` 后恢复原 timers。不要只回滚代码而保留不兼容的数据结构。
