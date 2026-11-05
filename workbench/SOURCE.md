# 界面与引擎来源

界面基础样式 `web/yuntu.css` 复用用户授权的 GitHub 私有项目 AmadeusDrift/htc-image-upload-workbench 的 `src/styles.css`，读取时 blob SHA 为 a44810fa8f53d14b03097ec9aa29ff57c23c5110。布局延续其侧栏、顶栏、蓝色主按钮、卡片、表单和提示样式，套图页面交互位于 app.js。

`core/` 基于本工作区原便携软件的六个 Python 模块；原目录保留。新工作台继续使用原软件自带 Python、Pillow、NumPy 与 PSD 解析依赖。只在127.0.0.1运行，不接原云图工作台的云端上传接口。

变暗（Darken）混合采用逐通道最小值，并通过标准 source-over 透明度合成，公式参考 [W3C Compositing and Blending](https://www.w3.org/TR/compositing-1/#blendingdarken)。去白边铺满的语义参考用户提供的《PS智能对象批量套图-V7-拱形铺满版.jsx》：裁外围透明/近白补边，等比铺满、居中裁切并保留1%出血。脚本本体不随发行版分发。
