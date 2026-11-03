# 套图工作台

离线 Windows 桌面 PSD 批量套图工具，当前版本 **0.2.0**。界面沿用云图工作台的蓝白样式，桌面窗口使用 Electron，本地 Python 引擎负责 PSD 解析和批量导出。图片不需要上传到在线服务。

## 使用流程

导入 PSD → 查看模板和场景缩略图 → 设置换图、固定或不导出 → 选择印花文件夹与保存位置 → 预览 → 批量导出。

- 自动查找母稿及同源智能对象，不要求统一命名为 `product`；识别不准时可手动选择图层。
- 模板与印花都支持缩略图预览，支持多模板批量处理。
- 默认自动适配、沿用 PSD 原尺寸；透明印花去外围透明边后完整放入，不透明图按比例判断小幅裁切或完整放入。
- 固定场景仍输出到每套产品中，同一批次只编码一次，然后复制。
- PSD 首次使用建立缓存，后续重复使用；磁盘缓存按容量与最近使用时间清理，内存中只保留当前模板渲染器。
- 支持暂停、停止保存进度、继续批次；两个产品并行处理。

## 源码结构

| 路径 | 内容 |
| --- | --- |
| `workbench/web/` | 可视化页面、样式和交互 |
| `workbench/desktop/main.cjs` | 桌面窗口、本地进程管理和原生文件选择 |
| `workbench/desktop/preload.cjs` | 桌面界面桥接 |
| `workbench/desktop/build.py` | 离线便携 EXE 与 NSIS 安装包构建 |
| `workbench/core/` | PSD 缓存编译、渲染、混合模式及批量处理 |
| `workbench/catalog.py` | 模板识别、图层关联和缩略图 |
| `workbench/cache.py` | 缓存校验、容量与清理 |
| `workbench/service.py` | 配置、预览、批次与历史记录 |
| `workbench/server.py` | 桌面内部使用的本地接口 |
| `workbench/tests/` | 自动测试 |
| `docs/superpowers/plans/` | 已完成的实施计划 |
| `PS智能对象批量套图-V3-选模板与印区预检.jsx` | 原 JSX 参考脚本 |

## 本机开发和测试

仓库仅保存源码。原便携软件的 Python 运行环境、第三方 vendor 依赖、PSD、印花、缓存、用户配置和 `release/` 安装包不上传。

现有 Windows 工程使用如下目录，保留已有运行环境即可复现本机测试：

```text
工程目录/
  workbench/
  通用PSD高速套图_正片叠底_2套并行便携版/
    runtime/python.exe
    vendor/
```

在工程根目录用 PowerShell 执行：

```powershell
& '.\通用PSD高速套图_正片叠底_2套并行便携版\runtime\python.exe' -I -B '.\workbench\tests\run.py'
```

若运行环境放在其他位置，设置 `MOCKUP_RUNTIME_ROOT` 指向含 `runtime/` 和 `vendor/` 的目录。`requirements.txt` 记录当前核心 Python 依赖版本；当前构建脚本仍依赖上述已准备好的便携运行环境，不会自动下载或建立环境。

## 构建 EXE

在现有 Windows 构建机上执行：

```powershell
& '.\通用PSD高速套图_正片叠底_2套并行便携版\runtime\python.exe' -I -B '.\workbench\desktop\build.py'
```

当前离线脚本需要本机已缓存的官方 Electron `45.1.0` Windows x64 ZIP，以及 electron-builder 缓存中的 NSIS `3.0.4.1`。脚本还会读取本地 `workbench/data/templates.json` 和 `workbench/data/thumbs/` 作为初始模板目录；新环境需先导入自己的 PSD 建立这些本地数据。构建脚本使用的具体缓存路径见 `build.py`。

输出为 `release/套图工作台-v0.2.0/套图工作台.exe` 和 `release/套图工作台安装.exe`。便携版需要保留整个文件夹；安装后可从桌面图标启动。根目录的 `启动套图工作台.bat` 用于启动已经构建好的便携版。

## 验证与限制

本版本已有 35 项 Python 自动测试、13 份本地 PSD 的 91 场景验证，以及桌面程序实际选择文件夹和导出 21 张 JPG 的脚本验证。真实 PSD 验收需要本地模板，不随仓库提供。

自动适配按透明度与比例判断，不能识别图片主体和文字。特殊构图可在高级设置里调整。多场景模板推荐使用顶层数字场景组；陌生 PSD 的复杂滤镜和组效果仍可能不支持，导出前先预览。尚未完成全部场景与 Photoshop 的逐像素对比。

来源说明见 [workbench/SOURCE.md](workbench/SOURCE.md)，操作说明见 [使用套图工作台.md](使用套图工作台.md)。仓库包含基于原便携软件修改的引擎和复用的界面样式，未新增开源许可声明。
