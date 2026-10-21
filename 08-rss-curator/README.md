# RSS Curator 兼容插件

原生产管线依赖单独部署的 `rss-curator`，其源码未包含在历史交付包中。本目录是为公开仓库补齐的兼容实现，并非对旧服务器插件源码的恢复。保留六个原工具名称，日常 V5.4 管线仍直接使用自身的只读 GReader 客户端。

它提供 FreshRSS 订阅和最近文章读取，以及本地报告/待审阅建议存储。没有新增、删除或移动订阅接口；`changes` 永远只是提案。真实配置通过环境变量提供，需保证 OpenClaw Gateway 进程继承 `FRESHRSS_API_URL`、`FRESHRSS_API_USER`、`FRESHRSS_API_PASSWORD`。

```bash
npm ci
npm test
npm run build
npm run plugin:build
npm run plugin:validate
openclaw plugins install --link --force "$PWD"
```

存储目录默认 `~/.openclaw/rss-intelligence/reports` 与 `reviews`，可以设置 `RSS_INTELLIGENCE_ROOT`。文件创建使用独占写入，目录 0700、文件 0600；不接受以工具参数指定文件路径。配置凭据不会出现在工具返回值中。

本地测试覆盖存储、待审阅状态和只读端点边界。接入实际 OpenClaw 的工具加载与模型调用需在部署环境验收。
