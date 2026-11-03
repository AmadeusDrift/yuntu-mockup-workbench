# 套图工作台实施计划

> For agentic workers: use subagent-driven-development for isolated frontend and compositor tasks; root implements template/cache/API integration. User approved implementation in this chat.

Goal: 在本地运行可视化套图页面，复用云图工作台样式，支持自动识别印花、逐场景换图/固定/不导出、受控缓存、预览和可恢复批次。
Architecture: 保留原便携软件。workbench/core 为独立引擎副本；catalog.py 负责PSD检查和模板设置；cache.py 负责指纹和磁盘缓存；service.py 提供本地任务；server.py 提供127.0.0.1页面/API。web 使用原工作台CSS与本地JavaScript，无外部网页依赖。

- [x] 模板适配：测试同源母稿识别、陌生PSD手选、固定场景和自然排序；仅在内存重命名，不改PSD。
- [x] 缓存：测试源文件/场景设置变化使缓存失效；原子完成、失败清理；容量限制和最近使用清理；预览/批次只加载当前模板。
- [x] 合成：在独立core补柔光/滤色及动态印花剪贴；通过参考像素、透明度与原批次回归，生成13份真实PSD测试报告。
- [x] 页面：复用原CSS，提供模板库、导入扫描、手动印花选择、场景设置、预览、导出参数、进度/暂停/停止/续跑、缓存管理。
- [x] 本地API：原生文件选择、持久化配置、后台任务、串行修改保护、受限文件路由和同源校验。
- [x] 验收：Python unittest，node --check web/app.js，真实PSD逐一编译/换图，批次固定图一致性及恢复，浏览器检查。

Commands: bundled runtime/python.exe -I -B workbench/tests/run.py; node --check workbench/web/app.js; bundled runtime/python.exe -I -B workbench/acceptance.py.
Original PSDs and original portable package stay unchanged. Any unsupported feature is reported with layer location, never silently omitted.

Validation: 23 new tests +11 original regressions PASS; 13 PSD /91 scenes PASS; 2 templates ×2 artworks /28 full-quality JPGs PASS, fixed pages identical and resume does not rewrite completed files. Browser real preview and controls checked.
