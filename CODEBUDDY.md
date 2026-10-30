# CODEBUDDY.md · FocusLog 焦点监控（agent 入口）

> 这一页是**地图，不是说明书**：功能亮点 / 快速开始 / 隐私模型看 `README.md`，
> 纪律、发版规则、已踩过的坑看 `docs/开发纪律.md` —— 本页**不复制**它们，两处能编辑就会漂。
> 本页四节只答四件事：这是什么 / 谁动哪 / 什么不能碰 / 去哪查。
> 路径取自 2026-10-31 当场实测（不是从 README 抄的：它「目录结构」段的写法已与实测对不上过）。

## 0-30 秒定向

纯本地、零第三方运行时依赖的 Windows 时间记录工具。后台每 10 秒采样前台窗口，
写入**纯文本**日志 `专注记录/YYYY-MM-DD.txt`；面板是本机 HTTP 服务，双击 `打开面板.bat`
后浏览器开 `http://127.0.0.1:47823/`（默认端口 `DEFAULT_PORT`，`专注记录/panel_server.py:52`；
被占会往后顺延 9 个端口，**以 bat 实际打开的地址为准**）。
数据不出本机、不上传、无遥测。

```
双击 `打开面板.bat`          # 日常唯一入口：看数据 + 所有开关
双击 `开始记录.bat`          # 开始记录 + 设开机自启（一步到位）
python 工具/检查面板.py      # 改过面板 / 前端必跑（JS 语法、挂载、双主题）
python tests/selftest.py     # 老记录兼容性自检（只测历史格式吃不吃得下）
```

- 项目身份与功能：`README.md`　·　纪律与坑表：`docs/开发纪律.md`　·　统计口径：`docs/统计规则.txt`

## 目录职责表

| 路径 | 职责 | 动它的前提 |
|---|---|---|
| `焦点监控/` | 记录进程与共享模块：`focus_tracker.py`（采样）、`category_utils.py`（分类）、`archive_utils.py`（归档）、`backup_utils.py`（备份）；打包产物 `focus_tracker.exe` 同目录 | 改采样 / 分类 / 归档逻辑 |
| `专注记录/` | 面板服务（`panel_server.py` / `dashboard.py` / `summary.py` / `chart_templates.py`）+ **全部数据**：`YYYY-MM-DD.txt` 日志、`goals.json`、`focus_state.json`、`category.txt`（分类规则，**换机必拷**，见 `docs/迁移手册.md`）、运行时日志（`maintenance.log` / `panel.console.log` / `startup.log`）、`归档/`、`时长统计/`、`深耕记录/`、`focuspanel.exe` | 改面板交互 / 统计算法；**数据文件只读不动** |
| `分类方案/` | 社区分类方案（`README.md` + `通用版.txt`），面板「分类方案专区」一键导入（增量合并，不覆盖自配） | 加 / 改方案规则 |
| `工具/` | 进阶入口：`发版.py`、`检查面板.py`、`打包发布.bat`、`migrate.py`、`make_release.py`、`weekly_report.py`、`build.bat`、`更新.ps1`、`清理旧版.ps1`、`迁移旧数据.bat`、`停止监控.bat`、`取消自启.bat`、`面板（快照）.bat`、`检查README图片.py` | 发版 / 打包 / 迁移 / 体检 |
| `docs/` | 深度手册：`CHANGELOG.md`、`开发纪律.md`、`开发与经验.md`、`迁移手册.md`、`统计规则.txt` + `screenshots/`、`周报/` | 记决策与规则 |
| `tests/` | `selftest.py`（自检） | 加断言 |
| 根目录 | `打开面板.bat` / `开始记录.bat` / `更新.bat` / `使用说明.txt` / `VERSION.txt` / `README.md` / `CODEBUDDY.md`（本页）/ `LICENSE` / `.gitignore` | 改用户入口（**根目录只留这些**） |

## 硬红线

1. **数据不出本机、不联网**：面板只绑 `127.0.0.1`（`专注记录/panel_server.py` 里
   `ThreadingHTTPServer(("127.0.0.1", p), Handler)` 的字面量；`DEFAULT_PORT` 只决定默认端口），
   不监听局域网 / 公网；无遥测。唯一联网动作是用户**主动**双击 `更新.bat`（只下程序，数据从不上传）。
2. **`专注记录/` 下的 `.txt` 是数据不是代码**：只追加、不手改、不删。改统计算法去 `专注记录/summary.py`，
   别在数据上动手脚；窗口标题脱敏用面板开关，不要手改历史日志。
3. **发版只走 `python 工具/发版.py`**：不带 `--push` 只做本地；**`--push` 推 GitHub 必须等用户当次明确点头**。
   且 `docs/CHANGELOG.md` 必须已有本版本条目（发版脚本会拒绝）。
4. **零第三方运行时依赖**：技术栈 = Python 3 标准库 + 前端零框架零构建，打包 PyInstaller onefile。别引依赖。
5. **改完必过自检**：改过面板 / 前端跑 `python 工具/检查面板.py`（查 JS 语法、函数挂载、双主题成对）；
   动过日志解析 / 统计再跑 `python tests/selftest.py`（**只测老记录兼容性**，不测面板 —— 别拿它当全部自检）；
   改过 `.py` 还要跑 `工具/build.bat` 重打包 exe（exe 是发布物，不重打就还是旧的）。
6. **GBK 控制台安全**：`print` 别用打不出的字符，stdout / stderr 加 `errors="replace"`
   （`docs/开发纪律.md` 已记）。

## 按任务入口

| 我要干的事 | 入口 |
|---|---|
| 看数据 / 改分类 / 开关自启 / 导入旧数据 | `打开面板.bat` → `http://127.0.0.1:47823/` |
| 开始 / 停止记录、开机自启 | `开始记录.bat` · `工具/停止监控.bat` · `工具/取消自启.bat` |
| 一键体检（记录进程 / 日志在写 / 自启指向 / 旧副本 / 归档欠账） | **只在面板里**：「工具 → 一键体检」（`专注记录/panel_server.py` 的 `checkup_payload`）。`python 工具/检查面板.py` 是另一件事 —— 发版前的**面板前端自检**，替代不了体检 |
| 发版 / 打包 | `python 工具/发版.py` · `工具/打包发布.bat`（规则见 `docs/开发纪律.md`） |
| 升级 / 换机 / 迁旧数据 | `更新.bat`（只换程序、不动数据）· `工具/迁移旧数据.bat` · `docs/迁移手册.md` |
| 改采样 / 分类 / 归档 | `焦点监控/focus_tracker.py` · `焦点监控/category_utils.py` · `焦点监控/archive_utils.py` |
| 改面板 / 统计 | `专注记录/panel_server.py` · `专注记录/dashboard.py` · `专注记录/summary.py`（口径见 `docs/统计规则.txt`） |
| 写变更 / 记决策 / 看历史 | `docs/CHANGELOG.md` · `docs/开发与经验.md` · `docs/周报/` |
