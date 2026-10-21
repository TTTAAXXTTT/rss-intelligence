> 历史技术参考，已将部署域名、任务标识和个人环境替换为示例；历史验收记录不代表当前开源副本已完成在线部署。首次部署以 README 和 DEPLOYMENT.md 为准。

# RSS Intelligence V5.2 R3

R3 fixes P46: native tool-plugin manifest generation/validation workflow.

The data pipeline, 4000-article bootstrap quarantine design, snapshot, scheduler, publisher and cutover logic are unchanged from R2.

Stage plugin changes only:
- generated manifest contracts.tools;
- activation.onStartup;
- optional toolMetadata;
- explicit strict empty configSchema in source;
- official plugin:build -> build --check -> validate workflow.
