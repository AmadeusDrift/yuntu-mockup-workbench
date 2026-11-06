# 更新服务部署记录

已通过 Cloudflare 插件部署并实测 HTTPS，2026-11-06（北京时间）。兼容日期按 Cloudflare 当时 UTC 日期 2026-11-05 设置。

- `yuntu.htcerp.app` 绑定原 `yuntu-workbench` Worker。`/updates/stable/latest.yml` 返回 1.1.3，安装包 HEAD 返回 200，大小与清单一致。未修改云图代码、发布清单、私有桶访问策略或旧 dev 地址。已有云图客户端不会因新增域名自动改用新地址。
- `taotu.htcerp.app` 绑定 `taotu-workbench-updates`，仅用于套图更新。绑定独立私有 R2 桶 `taotu-workbench-releases`。无公开写入接口，仅允许明确的版本清单与安装包路径，其他对象不公开。
- `htcerp.app` 原有 `xuanpin` 绑定保持不变。

桌面端从 0.3.1 起支持远程更新。每天首次启动后台检查一次，有新版时提示；可手动检查。下载后验证 Ed25519 签名对应的 SHA256 与大小，安装前再次校验。任务保存失败会中止更新，成功则退出、调用事务式安装器并自动重启。断网不影响本地套图，图片不上传。0.3.0 及更早版本需先手动安装一次 0.3.1。

清单 `/updates/stable/latest.json` 不缓存，内容为签名信封 `{payload,signature}`，均为 Base64。安装包按版本长期缓存。R2 条件写入原子占用版本，避免同时上传互相覆盖。发布接口需要 `PUBLISH_TOKEN`，公共路由不能访问占用记录。网关流式返回完整文件，不支持断点下载。本版未包含新模板方向协议衔接与进一步的预览性能优化。

## 发布新版

更新 `workbench/desktop/package.json`，构建并验证后运行：

```
node workbench/updates/publish.cjs <安装包> <签名私钥> <发布令牌文件> <更新说明文本>
```

脚本分片上传安装包，下载公开文件校验 SHA256，最后发布签名清单。私钥和发布令牌仅保存在发布电脑，禁止打进安装包或提交 GitHub；客户端只带 `update-public-key.pem` 公钥，私钥需妥善备份并保持稳定。

Worker 启用 `nodejs_compat`，绑定 `RELEASES` 桶和 `PUBLISH_TOKEN` secret；重新部署保留 secret。异常中断若遗留 `.claims/<安装包名>`，管理员确认无活动上传、无已完成版本后才能清理，不能覆盖已发布版本。

客户端测试：`node --test workbench/desktop/test-updater.cjs workbench/desktop/test-install-update.cjs`。本地发布与安装报告位于 `release/*verification.json`。

源码 `worker.mjs`；路由与读写边界测试：`node workbench/updates/test-worker.mjs`。部署资源定位见 `deployment.json`，不包含凭据。实际访问报告保存在本地 `.analysis-temp/update-domains-verification.json`。
