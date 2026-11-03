# 套图工作台桌面版与简化操作实施计划

Goal: 交付可双击运行的离线桌面EXE及安装包，缩略图识别模板/印花，选择输入与保存文件夹，默认逐图自动适配，高级参数折叠。
Architecture: 使用本机缓存的官方Electron45.1.0作为桌面窗口，打包既有Python运行时/引擎，无需用户安装Python或打开浏览器。Electron启动本地后台并管理退出；数据写入用户自己的应用目录。保留原蓝白界面和批次机制。

- [x] 前端：模板多选卡片含缩略图；印花文件夹扫描和缩略图；选单张作预览；清晰保存位置及打开目录；高级导出折叠，fit默认auto。
- [x] 引擎：先写自动适配测试；透明图去外透明边完整放入；不透明比例相近时小幅居中裁切，差异大时完整放入；逐slot计算，旧显式模式不变；预览解释判断。
- [x] 后端：印花分页扫描缩略图、输出目录创建/打开、配置保存、应用数据目录支持；校验原图不修改。
- [x] 桌面：Electron安全窗口、隐藏启动Python、关闭保存任务、单实例、原生目录/文件选择；资源离线打包；NSIS用户级安装和快捷方式，卸载保留个人数据。
- [x] 验证：新旧Python测试、JS语法、实际透明与满版印花、13份PSD、EXE原生窗口交互、单实例/关闭回收、安装包构建和内容清单。

Use subagent-driven-development for independent frontend and fit/backend tasks; root integrates and packages desktop. Source PSDs and original portable package remain untouched.

验收：35项Python测试通过；13份PSD使用auto/原尺寸生成91场景通过且源文件指纹未变；EXE脚本实际原生目录选择、3类自动适配、21张JPG导出、单实例和正常关闭退出后台通过。安装包构建成功，大小216459370字节。临时调试端口随测试进程退出关闭。
