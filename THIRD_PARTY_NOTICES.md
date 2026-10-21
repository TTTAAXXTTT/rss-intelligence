# 第三方依赖和内容

本项目原创代码采用 MIT；不重新授权第三方软件或订阅文章。

- [FreshRSS](https://github.com/FreshRSS/FreshRSS)：独立安装，通过其 GReader API 及只读 SQLite 接口获取自有实例数据。
- [OpenClaw](https://github.com/openclaw/openclaw)：独立运行时和插件 SDK，经 npm 安装。版本与完整性锁定在插件 package-lock.json。
- TypeBox、TypeScript、Vitest、Node 类型声明以及 Python 依赖：通过各自包管理器安装，保留包内原始许可。Python 版本清单见 requirements-v53.txt。
- 可选 [AdGuard dnsproxy](https://github.com/AdguardTeam/dnsproxy)：独立下载，未捆绑其二进制。
- RSS 来源、源站正文与模型服务遵循各自条款和权利。合成夹具不包含生产文章全文。是否有权抓取、翻译和发布，应由部署者依照来源许可判断。

不包含 OpenClaw/FreshRSS/DWS 的私有登录信息、模型密钥或商业模型权重。
