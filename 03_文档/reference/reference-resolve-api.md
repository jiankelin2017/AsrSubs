---
title: 参考 · Resolve API 事实（真机实测）
type: reference
status: Active
version: 1.0.0
last_update: 2026-11-05
applies_to: AsrSubs 1.2.5（实测环境 Resolve Studio 21.1.2.10）
maintainer: Jian
supersedes: archive\archive-dev-doc-20261105.md §6.6 / §6.8.4
superseded_by: —
---

# 参考 · Resolve API 事实

## 一句话摘要

改落轨 / 字幕相关代码前必读：这里登记 Resolve 21.1.2 上**真机实测**的 API 可用性，以及已被证伪的写法。

> 适用条件：全部结论来自 `archive\archive-dev-doc-20261105.md` 记录的 2026-10-31 ~ 11-01 真机实测（Resolve Studio 21.1.2.10）与官方文档交叉确认。本次治理**未重跑真机**；升级 Resolve 后须重新取证。

## 当前有效结论

1. **21.1.2 上没有任何原生 API 能修改字幕文本**。因此「导出 SRT → 重导入 → `AppendToTimeline`」不是绕路，是**唯一受支持的更新路径**。
2. 字幕轨上 `GetStartFrame()/GetEndFrame()` 和 `GetMediaPoolItem()` **都返回 `None`**；帧位置必须用 `GetStart()/GetEnd()`，识别本插件字幕必须靠**边车 spans**。
3. `SetTrackEnable`（**无 d**）是落轨定向的唯一手段，且启用后必须回读 `GetIsTrackEnabled` 校验。
4. `hasattr` 对 `Timeline` 对象**不可信**（SWIG 万能属性壳，对任何名字都返回 True），必须用 `dir()` 或 `getattr() is None` 判定。
5. Resolve 21.1 的 `TimelineExportType` **没有 SRT / 字幕类导出类型**，也无 `EXPORT_SRT` 常量 → R3 不可行（见 `..\adr\adr-0002-abandon-r3-export.md`）。

## 关键变更（仅影响使用的里程碑）

- v4.0.6：R1 从 `GetStartFrame/GetEndFrame` 改用 `GetStart/GetEnd`。
- v4.1.1：`_set_track_enabled` 从错误方法名改用 `SetTrackEnable`，并补启用回读校验。
- v4.0.9：确认无轨道高度 / 折叠 API，改走 Fusion 自绘清单。
- 逐版本细节见 `..\CHANGELOG.md`。

## 操作 / 参考细节

### 字幕轨 API 主表

| 事实 | 说明 | 影响 |
|---|---|---|
| `GetTrackCount("subtitle")` | 可用 | 字幕轨数量 |
| `GetItemListInTrack("subtitle", i)` | 可用（异常时条数按 0 处理） | 读轨上条目 |
| `TimelineItem.GetStart()/GetEnd()` | **可用**（字幕轨） | 字幕帧位置 |
| `TimelineItem.GetStartFrame()/GetEndFrame()` | **返回 None**（字幕轨） | 不可用 |
| `TimelineItem.GetMediaPoolItem()` | **返回 None**（字幕轨） | 无法按来源 stem 匹配 → 改用边车 spans |
| `AppendToTimeline([{mediaPoolItem}])` | 可用；字幕轨**忽略 `recordFrame`**，只认**播放头位置** | 落轨前须 `SetCurrentTimecode(GetStartTimecode())` 把播放头钉到起点 |
| `AddTrack("subtitle")` | 可用，但曾在已落字幕的时间线上凭空造出空 ST2 | 优先复用专用轨；失败后回收自建轨 |
| `DeleteTrack("subtitle", idx)` | **可用**（官方 API 文档 v20.2 / v21.0.4 一致） | 清理历史遗留空字幕轨的依据 |
| `SetTrackEnable`（**无 d**） | 可用；调用后 `GetIsTrackEnabled` 回读一致 | 落轨定向的唯一手段 |
| `SetTrackEnabled` / `SetTrackEnableState` | 属性为 **None**（21.1.2） | 旧包装函数静默失败 → 多轨时落错轨 |
| `GetIsTrackEnabled("subtitle", i)` | 对**不存在**的轨返回 `nil` | Lua 判定必须写 `en = (raw == true)`；写 `raw ~= false` 会把 `nil` 误判成启用 |
| `GetTrackName("subtitle", i)` | `.pyi` 签名支持，但真机可能返回空串或抛异常 | 必须 `pcall` + 空串回退成 `ST编号` |

### v4.1.1 多字幕轨场景

| 事实 | 实测 | 影响 |
|---|---|---|
| 只启用目标轨后 Append | 正确落入该轨（ST1→ST1，ST2→ST2） | 启用态定向本身有效 |
| 启用失败（方法名错 / 新轨未就绪） | Append 落到上一条启用轨 | 落到**非空轨**会把整批字幕顺移到该轨末尾（实测首帧 30→420） |
| `AddTrack` 后立刻启用 | 有时不可用（需回读确认） | 落轨前必须 `_enable_and_verify` |

### v4.1.0 文本写入穷举（Resolve Studio 21.1.2.10）

| 尝试 | 实测返回 | 结论 |
|---|---|---|
| `TimelineItem.SetName("x")` | **False**（3/3 全部失败，`GetName` 不变） | 不能改字幕文本 |
| `TimelineItem.SetProperty("Name"/"Text"/"Subtitle"/"Title"/"String"/"Content", "x")` | **False**（6 键全失败） | 没有可写的文本属性 |
| `TimelineItem.SetClipProperty` | 方法不存在 | — |
| `Timeline.ImportSubtitleFromFile` / `ImportSubtitle` / `AddSubtitle` / `ImportSRT` | 属性均为 **None** | 时间线级字幕导入不存在 |
| `MediaStorage.AddItemListToMediaPool([srt])` | **可用**，返回 `list[1]` | ImportMedia 全挂时的第三兜底候选（当时未接线） |

### Fusion UI（UIDispatcher）事实

| 事实 | 实测 | 影响 |
|---|---|---|
| 主窗口 `Show()` 之前第二次 `dispatcher:AddWindow` | **静默返回 nil**，日志无报错 | 设置 / 日志窗口不能「启动就建好、用时 Show」 |
| 点击事件里 `AddWindow` | 正常 | 与「关于」「选择字幕」「诊断」同一机制，是唯一可行时机 |
| 窗口关闭事件绑定 | 必须 `w.On["窗口ID"].Close = function() ... end` | `w.On.Close` 永不派发；处理器里 `return false` 会取消关闭 |
| 弹窗几何 | `w.Geometry` 可读可写（`{x,y,w,h}`） | 记住大小靠它；校验不能过严 |
| `Label` 宽度 | **按创建时文本算死** | 要显示的标签必须用最长初始文本占位 |
| `local function` 可见性 | 只对其**之后**的代码可见 | 处理器调用「后面才定义」的函数会解析成全局 nil → 抛错、后续语句不执行 |
| `TextEdit` 文本 | `PlainText` 可读写，`Text` 亦可 | 日志 / 热词统一写 `PlainText` |

### 轨道折叠能力的四路取证（结论：不存在）

| 验证手段 | 结果 |
|---|---|
| 本地权威 `DaVinciResolveScript.pyi` 全文 | `Timeline` 类 72 个成员全查过，无 `SetTrackHeight` / `Collapse` / `Expand` / `Fold` / `Compact` |
| 真机 `dir(timeline)` 内省 | 同样 72 个成员，无任何高度 / 折叠方法；`hasattr` 全返回 True（不可信） |
| 工程设置键扫描 | `GetSetting()` 共 159 项，只有 `limitSubtitleCPL` 之类内容限制，无轨道高度键 |
| UI Automation 控件树 | Resolve 纯 Qt 渲染，只拿到 5 个顶层控件，无法定位折叠控件 |
| 5 个悬停点截图 | 字幕轨道头**不存在**三角形折叠箭头，5 张图一致 |

### 官方来源

DaVinci Resolve Scripting API Doc v21.0.4 —— `AddTrack(trackType, optionalSubTrackType)` / `DeleteTrack(trackType, trackIndex)` / `SetTrackEnable(trackType, trackIndex, Bool)` / `GetIsTrackEnabled(trackType, trackIndex)`。

> ⚠ **待人工确认**：以上均为 21.1.2 的实测记录；当前机器上的 Resolve 版本与这些结论是否仍一一对应，本次治理未重新内省验证。

## 失效内容说明

- 「字幕落轨必须先新建空时间线」是 v3.2.6 及更早的结论，**已被架构红线推翻**，见 `..\adr\adr-0001-in-place-timeline.md`。
- 「靠来源媒体项 stem 匹配识别本插件字幕」已被 `GetMediaPoolItem() → None` 证伪。
- 「`SetName` 能直接改字幕文本」在 21.1.2 上 3/3 失败；代码里的 `SetName` 只是**前向尝试**，回读失败即本会话不再试。
