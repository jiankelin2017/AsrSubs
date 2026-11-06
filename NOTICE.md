# 致谢与说明 / NOTICE

AsrSubs 的字幕落轨（subtitle landing）实现为作者自行编写，不包含任何外部项目的代码或署名。

实现要点（自研）：

1. 原地落轨（in-place）
   先禁用全部字幕轨、只启用一条空字幕轨 → 用
   `SetCurrentTimecode(GetStartTimecode())` 把播放头钉在时间线起点 →
   `AppendToTimeline` 对真实 SRT 只传 `{mediaPoolItem}`（不传
   `recordFrame` / `trackIndex`，也不传 `startFrame` / `endFrame` / `mediaType`）
   → 字幕按 SRT 内部时间码从轨道头铺开，与始于起点的视频天然对齐。
   旧字幕轨保持禁用、不删除、不还原；全程只在当前打开的时间线上原地操作，
   绝不新建时间线。

2. 对齐校验与失败处理（原地路径内）
   落完用媒体项 ID 差核对上轨条数，并校验首条起始帧是否对齐预期偏移；
   若未对齐或落 0 条，只把诊断信息返回用户，不再新建时间线兜底，
   也不复制或重建素材。视频 / 音频轨原样保留。

技术细节以 DaVinci Resolve 21.1 自带脚本 API 文档为准：`MediaPool.AppendToTimeline`
接受 `AppendClipInfo` 字典，`startFrame` / `endFrame` / `mediaType` / `trackIndex` /
`recordFrame` 均标注为**可选字段**（官方 `README.md` 第 595–602 行、
`DaVinciResolveScript.pyi` 第 124–135 行）。上文「只传 `{mediaPoolItem}`、不传其余
字段」是本项目针对字幕媒体项的**实测约束**，不是官方 API 的限制。

## 运行时依赖与来源

本项目运行时依赖下列外部开源组件，各自按其原许可证分发；本仓库不收录它们的源码或二进制，安装阶段由安装器联网下载：

| 组件 | 用途 | 下载来源 |
|---|---|---|
| CPython 3.12.9（embeddable） | 运行期 Python 解释器 | python.org / npmmirror / 清华镜像 |
| PyTorch（CPU 或 CUDA 版） | 模型推理 | download.pytorch.org / 上海交大镜像 |
| transformers | 模型类加载 | PyPI 镜像；必要时回退到固定提交 |
| Qwen3-ASR-0.6B / Qwen3-ForcedAligner-0.6B | 语音识别与词级对齐 | hf-mirror / ModelScope / Hugging Face |
| ffmpeg | 音频抽取与裁段 | 随运行环境提供 |

安装器对 Python embeddable 包配置了 SHA-256 白名单，其余下载件在安装日志中记录实际 SHA-256 供审计；wheel 类依赖由 pip 依索引自带哈希校验。

—— AsrSubs 作者
