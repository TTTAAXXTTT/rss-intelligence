# RSS 情报站 · RSS Intelligence

## 下载已构建运行包

**[下载 Linux amd64 运行包 v5.4.0](https://github.com/TTTAAXXTTT/rss-intelligence/releases/download/v5.4.0/rss-intelligence-Linux-amd64-v5.4.0.tar)** · [校验与发布说明](https://github.com/TTTAAXXTTT/rss-intelligence/releases/tag/v5.4.0)

包含预构建容器镜像、Python/Node/OpenClaw、已编译插件和 FreshRSS。解压后运行 `bash rss-runtime.sh load`，再执行 `bash rss-runtime.sh selftest`；无需编译或安装应用依赖。需要 Linux amd64 与 Docker；真实订阅、模型账户、域名和调度仍按部署说明配置。

**RSS 常被忽略，但分散在其中的重要信息值得持续关注。** 官方公告、研究机构更新、开源项目发布、独立作者的深入分析，往往散落在不同网站。只依赖信息流中的热门内容，容易错过持续更新的小众来源。本项目希望把这些来源变成一个可长期维护、可核对原文的个人情报入口。

RSS 的价值在于：你主动选择值得跟踪的来源，持续接收更新，并保留通往原文的链接。一个变化在成为热门话题之前，可能已经出现在项目发布记录、监管公告或研究博客中。这里的“情报”指公开信息的收集与整理，不意味着独家消息、完整覆盖或收益保证。

本项目基于完整 V5.4 管线：FreshRSS 聚合 → 不可变源快照 → 有界模型分类 → 本地确定性终稿 → 完整中文翻译 → 四种 RSS 输出。保留源发现、订阅审阅、来源健康、失败重试、定时补跑、48 小时过期、部署迁移和回滚工具。

## 为什么值得做

- **积累可靠来源**：把官方、研究与持续产出的独立来源作为长期观察对象，减少每天从零搜索的重复劳动。
- **发现分散的重要变化**：版本更新、政策窗口、新研究、基础设施变化与小众商业线索，未必得到大范围转发。
- **降低跨语言阅读成本**：保留原文链接，同时生成中文整理与完整译文，方便先判断相关性、再核对细节。
- **让判断可以追溯**：分类、合并和过滤均有记账；AI 只参与受限步骤，发布由本机程序进行严格校验。

来源数量不等于信息质量。RSS 覆盖取决于源站是否提供 Feed、订阅选择和访问可用性；AI 可能误判，译文也应对照原文。重要决策应回到一手资料。本站默认关注技术、AI、基础设施、研究、政策和公开商业信息，分类与规则可以自行调整。

## 能输出什么

| 输出 | 用途 |
|---|---|
| `all.xml` | 全部入选内容，附原文与中文全文入口 |
| `priority.xml` | 高优先级入选内容 |
| `reports.xml` | 完整日报，便于集中阅读 |
| `brief.xml` | 每条一句话的精简日报 |
| 来源健康页 | 展示来源连续错误与状态，辅助维护 |

中文页面严格保留原有 48 小时窗口，过期先重投影 Feed 再删除页面。缺少完整译文、覆盖不精确、会话混淆或源快照不完整时拒绝发布。私有订阅路径在部署时随机生成，不提供共享的在线站点或真实访问令牌。

## 本地开发与离线验证

运行完整调度需要 Ubuntu/systemd、Docker FreshRSS、Apache、OpenClaw 和你自己的模型账户；离线测试不需要生产凭据。Python 3.12、Node 24.16+（24.x）为推荐开发环境。

```bash
git clone https://github.com/TTTAAXXTTT/rss-intelligence.git
cd rss-intelligence
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
npm ci --prefix 02-rss-intelligence-stage
npm run build --prefix 02-rss-intelligence-stage
npm test --prefix 02-rss-intelligence-stage
python tools/run_fixtures.py
python tools/check_publication.py
```

Linux 全包检查：

```bash
RSS_TYPESCRIPT_LIB="$PWD/02-rss-intelligence-stage/node_modules/typescript/lib/typescript.js" \
RSS_V53_PYTHON_BIN="$PWD/.venv/bin/python" \
bash 00-preflight/package-selftest.sh "$PWD"
```

包内自检会验证 Linux 权限、锁、systemd calendar 与 shell 管线；Windows 只能运行其中可移植部分。完整 Linux 检查由 GitHub Actions 提供。真实订阅、线上模型调用、TLS 与定时器最终验收应在使用者自己的部署中完成。

## 从零部署与已有环境升级

先读 [DEPLOYMENT.md](DEPLOYMENT.md)，配置 [.env.example](.env.example)，再按 [INSTALL_V53.md](INSTALL_V53.md) 和 [INSTALL_V54.md](INSTALL_V54.md) 安装增强管线。`INSTALL_V52.md` 保留旧版升级过程；其中任务 ID 和域名均为示例，首次部署不要执行旧服务器 Cutover。

默认系统路径沿用 `/home/ubuntu/rss-intelligence-v5.2`，这是 V5.4 的兼容安装目录名。定时器和运行器使用这一路径；不要仅修改目录名而漏改配置。

## 完整代码与目录

| 目录 | 职责 |
|---|---|
| `00-preflight/` | 环境检查、包自检、12 类回归/事故重放入口 |
| `01-init-kit/` | 来源发现、批次约束、验证、去重与覆盖缺口 |
| `02-rss-intelligence-stage/` | 原生 OpenClaw 私有 staging 插件、TypeScript 测试 |
| `03-workspace-files/` | 晨报、午报、审阅提示和发布政策 |
| `04-daily-pipeline/` | 快照、分类、本地终稿、翻译、发布、健康和补跑 |
| `04-retry-scheduler/` | 完整 systemd services/timers、锁和有界重试 |
| `05-apache/` | 静态目录映射和私有内容响应头 |
| `06-cutover/` | 旧部署切换、回滚、旧调度审计和端到端验收 |
| `07-network-ops/` | 可选白名单中继、DNS、迁移、备份与恢复 |
| `08-rss-curator/` | 开源补齐的 FreshRSS 只读工具及本地报告/待审阅存储 |
| `tools/` | 跨平台验证、清单与发布检查 |

完整性清单见 [COMPLETENESS.md](COMPLETENESS.md)。历史说明保留技术演进，但真实凭据、私有订阅、生产运行数据与原始第三方全文均由模板和合成夹具替代。

## 安全与许可

MIT 开源；外部程序与内容遵循各自许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本项目不会自动修改 FreshRSS 订阅；来源发现只产生待审阅建议。网络运维脚本是可选部署模板，内有示例地址与白名单，必须按自身环境配置后使用。

请勿将 `.env`、运行数据库、私有发布路径或真实备份加入 Git。更多见 [SECURITY.md](SECURITY.md)。

参考：[RSS 2.0 规范](https://www.rssboard.org/rss-specification)、[FreshRSS GReader API](https://freshrss.github.io/FreshRSS/en/developers/06_GoogleReader_API.html)。
