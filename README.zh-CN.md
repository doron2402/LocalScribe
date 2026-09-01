# LocalScribe

[English](README.md) · [עברית](README.he.md) · [Español](README.es.md) · **简体中文** · [Français](README.fr.md)

在你的 Mac 上录制会议、转写并生成摘要——全部在本机完成。音频永远不会离开这台
机器，使用默认的摘要引擎时，文本也不会。

```
mic ─────────┐
             ├─► 16 kHz stereo WAV ─► Whisper ─► transcript ─► local LLM ─► summary.md
loopback ────┘   (ch0 = you,          (offline)               (Ollama)
                  ch1 = them)
```

## 快速开始

```bash
git clone git@github.com:doron2402/LocalScribe.git
cd LocalScribe
./scripts/setup.sh
```

然后开始录制：

```bash
localscribe record --label "Standup"
```

正常说话，会议结束时按 `Ctrl-C`，等待几秒钟，你会得到三个文件：

```
~/LocalScribe/audio/standup_2026-09-01_1000.wav          录音
~/LocalScribe/transcripts/standup_2026-09-01_1000.md     谁说了什么
~/LocalScribe/summaries/standup_2026-09-01_1000.md       摘要、决定、待办事项
```

第一次开真正的会议之前，有三件事值得知道：

- **先运行 `localscribe doctor`。** 它会告诉你缺什么以及怎么解决。
- **安装 BlackHole**，否则你只会录到自己的声音，而不是其他与会者的。
  `setup.sh` 会安装它，但需要你的密码并重启。参见
  [系统音频](#系统音频重要)。
- **首次运行会下载约 1.6 GB 的语音模型。** `setup.sh` 会提前下载好，
  以免在会议中途才开始下载。

没有服务器需要启动，没有后台常驻进程，不需要 API 密钥，也不需要联网。

## 使用的开源组件

| 用途 | 软件包 | 许可证 |
|---|---|---|
| 音频采集 | [sounddevice](https://github.com/spatialaudio/python-sounddevice) / PortAudio | MIT |
| WAV 读写 | [soundfile](https://github.com/bastibe/python-soundfile) / libsndfile | BSD-3 |
| 重采样 | [soxr](https://github.com/dofuuz/python-soxr) | LGPL-2.1 |
| 语音识别 | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) + CTranslate2 | MIT |
| 语音识别（GPU） | [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) | MIT |
| 摘要生成 | [Ollama](https://github.com/ollama/ollama) + Llama 3.1 | MIT / Llama 许可证 |
| 系统音频回环 | [BlackHole](https://github.com/ExistentialAudio/BlackHole) | GPL-3.0 |

## `setup.sh` 做了什么

它创建虚拟环境、安装 BlackHole、下载 Whisper 模型、安装 Ollama 并拉取摘要模型、
把 `localscribe` 命令链接到你的 PATH，然后运行测试和 `doctor`。可以放心重复运行，
每一步都会先检查当前状态。

```bash
./scripts/setup.sh --no-llm                    # 跳过 Ollama
./scripts/setup.sh --no-audio                  # 跳过 BlackHole（需要 sudo 并重启）
./scripts/setup.sh --whisper small.en          # 更小更快的语音模型
```

在 Apple Silicon 上，脚本坚持使用 **arm64** 的 Python——x86_64 的 Python 会让
CTranslate2 跑在 Rosetta 下，转写耗时大约会变成三倍。

### 系统音频（重要）
<a id="系统音频重要"></a>

macOS 没有内置方式录制扬声器输出的声音，因此如果没有回环驱动，你只能采集到自己的
麦克风，也就是通话中你这一半。等 `setup.sh` 安装好 BlackHole 并重启之后：

1. 打开 **Audio MIDI Setup**（位于 `/Applications/Utilities`）。
2. `+` → **Create Multi-Output Device**，同时勾选你的耳机／扬声器**和**
   BlackHole 2ch。
3. 把这个 Multi-Output Device 设为 Mac 的声音输出。

你依然能正常听到通话，同时 BlackHole 会传送一份副本供 LocalScribe 读取。
运行 `localscribe doctor` 可以确认它是否找到了该设备。

## 需要服务器吗？

不需要。LocalScribe 是一次性命令：录制、转写、生成摘要、写出两个 markdown 文件，
然后退出。没有任何东西监听端口，会议之间没有任何后台进程，整个过程也不需要网络。

唯一的例外是本地摘要引擎。Ollama 是运行在 `127.0.0.1:11434` 的守护进程，
如果它没有启动，LocalScribe 会按需把它拉起来——所以这件事也不用你记着。
设置 `LOCALSCRIBE_OLLAMA_AUTOSTART=0` 可以自己管理它，或者使用
`--backend extractive`，那样就完全没有守护进程。

## 用法

`scripts/setup.sh` 会把 `localscribe` 命令链接到你的 PATH。否则可以在仓库目录里
运行 `./bin/localscribe`——效果相同，无需激活虚拟环境。

```bash
localscribe doctor                       # 检查安装状态
localscribe devices                      # 列出输入设备

# 录制直到按下 Ctrl-C，然后转写并生成摘要
localscribe record --label "Latency sync"

# 到达设定时长后自动停止
localscribe record --label "Standup" --duration 20m

# 重新处理已有录音（例如修改提示词或模型之后）
localscribe process ~/LocalScribe/audio/standup_2026-08-31_1000.wav

# 重新生成摘要，不重新转写
localscribe summarize ~/LocalScribe/transcripts/standup_2026-08-31_1000.json

localscribe list                         # 目前录制过的内容
```

输出写入 `~/LocalScribe/{audio,transcripts,summaries}`。

## 谁说了哪句话

两路音频分别保存在不同声道——你的麦克风在左声道，系统回环在右声道——因此
LocalScribe 通过逐词比较两个声道的能量来标注说话人，而不是运行声纹分离模型。
这样得到的是 **You** 和 **Them**，而不是真实姓名，但它精确、免费，也不需要任何
需要申请权限的 Hugging Face 模型。词尾的 `?`（如 `Them?`）表示该词处两个声道都
有声音，判断结果比较接近。

想要真实姓名的话，把转写文本交给摘要引擎——语言模型通常能从人们互相称呼的方式中
推断出来。

## 识别引擎

两个语音引擎，输出格式一致。`auto`（默认值）在 Apple Silicon 上装有 `mlx` 时选择
它，其他情况下选择 `faster-whisper`。

```bash
localscribe process recording.wav --engine mlx             # Metal GPU
localscribe process recording.wav --engine faster-whisper  # CPU，哪里都能跑
```

262 秒音频，`base.en` 模型，包含逐词说话人归属的完整流程，在 M 系列芯片的 Mac 上：

| 引擎 | 计算方式 | 耗时 |
|---|---|---|
| faster-whisper (CTranslate2) | CPU int8 | 11.9 秒 |
| **mlx-whisper** | **Metal GPU** | **4.2 秒** |

在全新安装中第一次运行 `mlx` 会额外花大约 30 秒，用于 Metal 编译其内核。
这是一次性开销并会被缓存；`scripts/setup.sh` 会替你付掉，这样它就不会落在你的
第一次真实会议上。

如果你在考虑用编译型语言重写，这一点值得了解：让它快起来的并不是宿主语言。
在同一台机器、同一段音频上，whisper.cpp——Go 或 Rust 版本会通过 cgo 绑定的那个
C++ 引擎——在 **CPU 上需要 10.2 秒**，在 **GPU 上需要 2.2 秒**，而完成同样工作的
CTranslate2 是 5.7 秒、MLX 是 2.9 秒。协调这一切的 Python 在上面 11.9 秒里占用
不到 0.1 秒；完整流程与其纯粹的语音识别调用在误差范围内是同一个数字。
真正的杠杆是 GPU，而不是语言。

## 选择 Whisper 模型

| 模型 | 大小 | 速度（M 系列，int8） | 适用场景 |
|---|---|---|---|
| `base.en` | 140 MB | 约 15 倍实时 | 快速验证、干净音频 |
| `small.en` | 460 MB | 约 8 倍实时 | 尚可，仅英语 |
| `medium.en` | 1.5 GB | 约 3 倍实时 | 效果不错 |
| `large-v3-turbo` | 1.6 GB | 约 4 倍实时 | **默认**——对口音支持最好 |

```bash
localscribe process recording.wav --model small.en
```

以上速度是 CPU 引擎的数据；`mlx` 引擎大约比它们各快三倍。一小时的会议使用
`large-v3-turbo`，在 CPU 上约需 15 分钟，在 GPU 上约需 5 分钟。

## 摘要引擎

- `--backend ollama`（默认）——本地语言模型，完全离线。
- `--backend anthropic`——Claude API。只发送转写的**文本**，绝不发送音频。
  需要 `ANTHROPIC_API_KEY`。
- `--backend extractive`——完全不用模型：按关键词排序的句子加上正则匹配出的待办
  事项。比较粗糙，但即时且无依赖。当所选引擎不可用时，它也是自动的兜底方案。

超过约 1800 词的转写会以 map-reduce 的方式生成摘要：每个分块单独阅读，然后合并
各自的笔记。

## 开发

```bash
.venv/bin/pytest          # 60 个测试，不需要音频硬件，也不下载模型
.venv/bin/ruff check .
```

这些测试覆盖的是那些会悄无声息失败的部分：说话人归属（轮次切分、扬声器串入麦克风、
增益差异、同时说话）、录音器的时钟漂移处理、引擎选择及其回退，以及摘要的后处理。

```
localscribe/
├── localscribe/
│   ├── audio.py        采集、重采样、两个时钟的对齐
│   ├── engines.py      faster-whisper（CPU）与 mlx（Metal GPU），统一结构
│   ├── transcribe.py   语音识别 + 逐词说话人归属
│   ├── summarize.py    ollama / anthropic / extractive 后端
│   ├── config.py       环境变量覆盖，全部可选
│   └── cli.py          record, process, summarize, devices, doctor, list
├── bin/localscribe     启动器：在任何位置运行虚拟环境里的 CLI
├── scripts/setup.sh    一条命令完成安装
└── tests/
```

## 录制他人之前

录音会录下通话中的每一个人。各地对录音同意的规定各不相同，有些地区要求所有参与方
都同意——请告诉在场的人录音已经开始。
