# live2d-py 项目分析与安装使用说明

> 使用说明文档。
> 适用平台：**Windows x64**，Python 3.10–3.14（示例环境：conda 环境 + Python 3.10.16）。

---

## 一、这个项目是什么

`live2d-py` 是一个**纯 Python + C 扩展**的 Live2D 渲染库：直接在 Python 里用 OpenGL 加载并绘制 Live2D 模型，**不经过 Web 引擎**（不内嵌浏览器/JS 渲染）。

### 关于"模型"的澄清

Live2D 模型**不是神经网络模型**，而是：

> 一幅 2D 美术图被切成很多**部件（parts）** + **三角网格（mesh）** + 一组**参数**（嘴张、眼开、头偏…）+ **变形器（deformer）** + **动作（motion）**

渲染是**确定性计算**：给参数赋值 → SDK 用数学算出网格顶点 → OpenGL 绘制。全程无机器学习推理。
（唯一的例外：`examples/main_facial_bind.py` 面捕示例用 MediaPipe 检测人脸关键点，那是神经网络，但只负责"给参数喂值"，与 Live2D 渲染无关。）

---

## 二、体系架构

### 2.1 分层结构

```
你的 Python 代码
   │  import live2d.v3 as live2d   (或 v2cpp / v2)
   ▼
package/live2d/            Python 包装层（包管理、常量、参数、工具）
   ▼
_v3cpp.pyd / _v2cpp.pyd    CPython C 扩展（编译产物，机器码）
   ▼
Live2D 官方 C++ SDK        解析 moc/moc3、驱动参数、计算网格顶点
   ▼
OpenGL (glad 加载)         实际绘制
```

### 2.2 三个后端模块（API 完全兼容，只改 import）

| 模块 | 实现方式 | 适用模型 | 性能 | 建议 |
|---|---|---|---|---|
| `live2d.v2` | 纯 Python（从 Cubism2 Web SDK 的 JS 转译） | `.moc`（Cubism 2.1 及更早） | 慢 | ❌ 不推荐 |
| `live2d.v2cpp` | **C++ 移植，编译成 `_v2cpp.pyd`** | `.moc`（Cubism 2.1 及更早） | 快 | ✅ 老模型用 |
| `live2d.v3` | **C 扩展封装 Cubism Native，编译成 `_v3cpp.pyd`** | `.moc3`（Cubism 3.0+） | 快 | ✅ 新模型用 |

```python
import live2d.v3 as live2d      # 新模型（推荐）
# import live2d.v2cpp as live2d  # 老模型，只改这一行
```

### 2.3 源码目录结构（关键部分）

```
live2d-py/
├── Wrapper/
│   ├── V2/        C++ 移植 + v2cpp 绑定源码（Init.cpp / PyLAppModel.cpp）
│   └── V3/        v3 绑定源码（Init.cpp / PyModel.cpp）→ 编译为 _v3cpp.pyd
├── Live2D/        Live2D 官方 SDK（git submodule，默认未 checkout）
├── package/live2d/
│   ├── v2/        纯 Python 实现
│   ├── v2cpp/     收口 _v2cpp.pyd（__init__.py 里 from ._v2cpp import *）
│   ├── v3/        收口 _v3cpp.pyd、含 FrameworkShaders 着色器、lapp_model.py
│   └── utils/     canvas / lipsync / opengl_functions 等工具
├── examples/      各种窗口库示例（glfw / pygame / PyQt / PySide…）
└── Resources/     自带样例模型（v2/: Epsilon, haru...  v3/: Haru, llny）
```

### 2.4 与官方 Cubism SDK（`CubismSdkForNative-5-r.5.zip`）的关系

| 模块 | 是否依赖官方 SDK | 说明 |
|---|---|---|
| `live2d.v2` | ❌ 不需要 | 独立纯 Python 实现 |
| `live2d.v2cpp` | ❌ 不需要 | 用的是仓库 `Live2D/V2` 的 C++ 移植 |
| `live2d.v3` | ✅ 需要 | 链接 Cubism **Core**（.moc3 解析/渲染核心）+ Framework |

关键点：

- **Cubism Core 受 Live2D 许可限制，不随仓库分发**，源码里 `Live2D/V3/Core` 是空的；从源码编译 v3 时，`setup.py` 会自动从 Live2D 官网下载 SDK zip 并解压。
- **官方发布的 wheel 已在 CI 上编译好**，Core 是静态库，已被**静态链接进 `_v3cpp.pyd`**。
- 因此：**下载 wheel 使用的话，既不需要编译，运行时也不需要 SDK zip**。zip 只在"想自己从源码编译 v3"时才有用。

### 2.5 渲染主循环

库**不开窗**，假设你已经有一个 OpenGL 上下文（glfw / pygame / Qt / tkinter 都行）。标准流程：

```
创建 GL 上下文（glfw.create_window + make_context_current）
  → live2d.init(); live2d.glInit()
  → model = LAppModel(); model.LoadModelJson(路径); model.Resize(w, h)
  → 每帧：model.Update()  (更新动作/物理/眨眼/呼吸/口型)
          clearBuffer()    (清屏)
          GL.glEnable(GL.GL_BLEND)
          model.Draw()     (画模型)
          swap_buffers()
  → live2d.dispose()
```

---

## 三、安装

### 3.1 环境要求

- Windows x64（Linux/macOS 也有对应 wheel）
- Python 3.10+（**C 扩展的 cp 标签必须和你的 Python 版本完全一致**，差一位 pip 就拒绝安装）
- 需要 OpenGL 上下文（桌面 Windows 自带）

### 3.2 方式一：pip 安装（最快）

```bash
pip install live2d-py
```

PyPI 版目前只发 Windows/macOS wheel；Linux 需从 GitHub Release 下 manylinux wheel。

### 3.3 方式二：GitHub Release 下载 wheel（推荐）

到 <https://github.com/EasyLive2D/live2d-py/releases/latest> 下载对应你 Python 版本的文件：

| 你的 Python 版本 | 下载的 wheel（0.7.0.4，Windows 64 位） |
|---|---|
| 3.10 | `live2d_py-0.7.0.4-cp310-cp310-win_amd64.whl` |
| 3.11 | `live2d_py-0.7.0.4-cp311-cp311-win_amd64.whl` |
| 3.12 | `live2d_py-0.7.0.4-cp312-cp312-win_amd64.whl` |
| 3.13 | `live2d_py-0.7.0.4-cp313-cp313-win_amd64.whl` |
| 3.14 | `live2d_py-0.7.0.4-cp314-cp314-win_amd64.whl` |

`cpXXX` = 编译针对的 CPython 版本，`win_amd64` = Windows 64 位。**选错装不上**（报 `is not a supported wheel on this platform`）。

```bash
# 以 Python 3.10 为例
curl -L -o live2d_py-0.7.0.4-cp310-cp310-win_amd64.whl \
  https://github.com/EasyLive2D/live2d-py/releases/download/v0.7.0.4/live2d_py-0.7.0.4-cp310-cp310-win_amd64.whl
pip install live2d_py-0.7.0.4-cp310-cp310-win_amd64.whl
```

> wheel 本质是一个 ZIP 压缩包（约 430KB），内含编译好的 `_v2cpp.pyd` / `_v3cpp.pyd` 和所有 Python 源码。`pip install` 会把 `.pyd` 解压到 `<venv>/lib/site-packages/live2d/v3/_v3cpp.pyd`。
> 依赖 `numpy`、`pyopengl`、`pillow` 由 pip 自动安装。

### 3.4 方式三：源码编译（可选，不推荐）

需要 Visual Studio + CMake + Python 开发头文件，且编译 v3 时 `setup.py` 会下载官方 SDK：

```bash
cmake -S . -B build -G "Visual Studio 18 2026" -T host=x64 -A x64
cmake --build build --config Release --target Live2DV2Wrapper -j 24   # v2cpp
cmake --build build --config Release --target Live2DWrapper  -j 24   # v3
```

### 3.5 再装一个窗口库（二选一）

```bash
pip install glfw        # 轻量，示例多用它
# pip install pygame    # 或 pygame
```

---

## 四、使用示例

### 4.1 最小可运行示例（v3 + glfw）

```python
import glfw
import live2d.v3 as live2d          # C 扩展
from OpenGL import GL

W, H = 800, 600
MODEL = "/path/to/live2d-py/Resources/v3/Haru/Haru.model3.json"

glfw.init()
win = glfw.create_window(W, H, "live2d", None, None)
glfw.make_context_current(win)

live2d.init(); live2d.glInit()
model = live2d.LAppModel()
model.LoadModelJson(MODEL)
model.Resize(W, H)

while not glfw.window_should_close(win):
    glfw.poll_events()
    model.Update()
    live2d.clearBuffer(1, 1, 1, 1)
    GL.glEnable(GL.GL_BLEND)
    model.Draw()
    glfw.swap_buffers(win)

live2d.dispose(); glfw.terminate()
```

### 4.2 常用模型路径（仓库自带）

- v3 新模型：`/path/to/live2d-py/Resources/v3/Haru/Haru.model3.json`（或 `.../llny/llny.model3.json`）
- v2 老模型：`/path/to/live2d-py/Resources/v2/haru/haru.model.json`（用 `live2d.v2cpp`）

### 4.3 常用 API 一览

```python
model.LoadModelJson(path)        # 加载模型
model.Resize(w, h)               # 视口
model.Update() / model.Draw()    # 每帧
model.StartMotion(group, no, priority, onStart, onFinish)   # 播放动作
model.SetParameterValue(id, v)   # 控制参数（如嘴张）
model.HitPart(x, y, False)       # 点击检测，返回命中的部件 id 列表
model.SetPartOpacity(idx, v)     # 部件透明度
model.GetCanvasSize()            # 画布尺寸等诊断信息
live2d.clearBuffer(r, g, b, a)   # 清屏
live2d.dispose()                 # 释放
```

### 4.4 透明悬浮窗（桌宠）

把人物渲染成**透明背景、无边框、置顶**的悬浮窗口，桌面上只看到角色在动（类似桌宠）：

```bash
python desktop_pet.py                         # 默认加载 llny，右上角悬浮
python desktop_pet.py --model /path/to/xxx.model3.json
python desktop_pet.py --width 800 --height 1200 --x 100 --y 100  # 指定窗口（物理像素）
python desktop_pet.py --scale 0.6             # 人物缩放（1.0 = 撑满窗口；>1 会裁头顶）
python desktop_pet.py --viewer                # 原生动态（接近 Live2DViewer 默认）
python desktop_pet.py --emotion 开心          # 情绪模式：从该情绪开始
python desktop_pet.py --emotion 兴奋 --lipsync /path/to/speech.wav --lipsync-device 0
                                              # 自播 wav + 说话自动对口型
python desktop_pet.py --emotion 开心 --listen # 监听系统播放，任何播放器自动对口
python desktop_pet.py --control-port 5000     # 开 TCP 控制口，外部随时切情绪/嘴
python desktop_pet.py --self-test             # 只渲染一帧透明图并输出 alpha 统计
```

- **实现原理**：Windows 上 GLFW 的 `TRANSPARENT_FRAMEBUFFER` 只支持 macOS，所以用 Win32 **分层窗口**（`WS_EX_LAYERED`）——每帧把 GL 渲染结果（背景清成 `rgba(0,0,0,0)`）读回，预乘 alpha 后经 `UpdateLayeredWindow` 呈现，背景真正透明、人物边缘平滑。
- **拖动**：**按住左键在人物身上拖动可移动位置**。命中检测用上一帧的 alpha 通道——只有按在人物可见像素上才触发拖动，透明区域点不到。
- **锁定位置**：**右键单击人物** → 位置锁定、不能拖动（防止误拖走）；再右键单击解除锁定。单击需要落在人物身上，并且会等约 0.5 秒确认不是双击后才生效（所以不影响下面的右键双击穿脱外套）。
- **拽动反馈（锁定状态下）**：位置锁定时，**左键按住人物并拖动**，人物会朝**拽的方向**作出反馈，而且**按拽住的位置区分**：
  - 拽住**头部**：往左右拽 → 头向左右**转动**（`ParamAngleX`）；往上/往下拽 → 头**抬头/低头**（`ParamAngleY`）。llny 的这两个角度与 Cubism 命名习惯相反——实测 `ParamAngleX` 才是 yaw（转头）、`ParamAngleY` 是 pitch（点头/抬头），所以左右分量接 `ParamAngleX`、上下分量接 `ParamAngleY`。
  - 拽住**身体**：往左右拽 → 身体**朝向转向**拽的方向。llny **没有躯干 yaw 网**（实测 `ParamBodyAngleY` 其实是垂直拉伸、不是转身），所以用"头领转 + 上身轻微倾转"合成转身：头转向拽的方向（`ParamAngleX`，最大约 15°）+ 躯干沿拽的方向微倾（`ParamBodyAngleZ`，肩膀朝拽的方向移、脚保持锚定）；上下拽 → 身体轻微前倾/后仰（`ParamBodyAngleX`）。
  - 拽住**双腿 / 大腿 / 大腿根**（scale 1 时窗口下约 55% 起；触发线随人物缩放移动，改 `--scale`/`+`/`-` 不会让分区漂移）→ **害羞反应 + 大腿并拢**：不管往哪个方向拽，反应强度随拽动幅度增大——**轻微下蹲**（`ParamBodyAngleY` 取负，整个身体下沉、实测身高矮 40 多像素）+ **大腿根到大腿、膝盖整段夹紧并拢**（实测 `Param28`"yy" 是腿部专属参数，取负值即并拢，两条腿列从上到下合并成一条、脚踝仍分开）+ **双手微微内收**挡在身前（`Param41` 左臂 / `Param43` 右臂；注意 llny 的手部 rig 只会让手臂在身体两侧摆动，**做不了真正"双手交叉挡私处"的姿势**，这是模型限制）+ **非常害羞的脸**（`Param13` 脸红、`Param58` 鼓脸、眼睛下瞟 + 向旁躲闪、眼皮半闭、微微低头、抿嘴）。注意同为 "yy/zz" 系的 `Param27` 是全身绕脚下旋转（头会横移），不能用来夹腿。
  - **左键双击大腿及以下区域**（两次左键落在人物身上、0.5 秒内且相隔不远）→ 立即触发和拽大腿完全相同的害羞反应（下蹲 + 大腿并拢 + 手臂内收 + 害羞脸），不需要锁定；按住保持、松开缓缓淡出。**实测触发区是大腿及以下**——双击**胸部、私处不触发**，双击头部也不触发。触发边界跟随人物缩放（改 `--scale`/`+`/`-` 时判定区不漂移，不会因为人物变小又让胸部变可触发）。注意两腿之间或边缘的半透明区域点不到（命中检测按上一帧 alpha，透明像素不触发）。
  - 松手即**平滑回到正常姿势**：按住时拉力快速跟手（每帧 15%），松开后**放慢到每帧 7%** 缓缓扭回，避免回正瞬间的抖动。位移与角度成比例，拽得越远幅度越大（头部转动/抬头最大约 20~25°，身体转身最大约 15°），并叠加在现有情绪/待机姿势之上。
- **窗口与人物大小**：默认窗口是逻辑 `520x720` 按显示器 DPI 缩放后的物理像素（本机 4K/250% 即 `1300x1800`），所以高 DPI 屏上人物不会再显得小；100% 缩放时仍是 `520x720`。想更大就调大窗口：`--width` / `--height`（物理像素），人物会等比变大且头顶不被裁。
- **缩放**：`--scale` 设初始大小；运行中按 **`+` / `-`** 实时放大/缩小、**`0`** 复位。缩放由 `model.SetScale()` 实现（绝对系数，围绕窗口中心，实测 1.0=撑满、2.0=两倍、0.5=一半）。注意 `--scale` **没有上限**，但人物在 scale 1.0 时已占满窗口高度，再放大**头顶会裁掉**——所以要"更大的人物"优先加大窗口而不是调 scale。
- **穿脱外套**：**右键双击人物**（两次右键落在人物身上、0.5 秒内且相隔不远）→ 脱掉外套；再右键双击穿回来。双击那一对点击会被吞掉，不会误触发上面的"右键单击锁定"。切换约 0.5 秒平滑过渡（外套就是 llny 的 `Param2`，默认演示里本来就在自动缓慢穿脱；手动切换后由你接管，自动摆动停止）。所有模式（演示 / `--viewer` / 情绪）都生效。
- **退出**：按 `ESC`（或 `Alt+F4`）。
- 内置演示动画：去水印（Param14）+ 眨眼 + 说话 + 轻微摇头 + 外套缓慢穿脱。自测输出示例：`corner alpha mean = 0.000`（背景全透明）、`opaque px = 19%`（人物实体）。
- **`--viewer` 待机模式（接近 Live2DViewer 默认）**：复刻 Live2DViewer 加载 llny 后的默认待机——Python 驱动**规律眨眼**（约每 2.8 秒一次）+ **多轴头/身体摆动**（ParamAngleX/Y/Z ±6~10°、ParamBodyAngleX/Y/Z），这些角度正是 `llny.physics3.json` 的物理输入；`model.Update()` 内部求值物理，把 84 个 ArtMesh 旋转参数（丸子头、束发、后发、草莓结等）带动 ±5~13°——这就是原版里"耳朵/头发会动"的机制。模型自带的孤儿 idle 运动 `motions/idel.motion3.json`（约 3 秒：呼吸 + 轻微肢体摆动）持续循环。比纯 SDK 自动模式（眨眼稀疏、摆动不可调）更接近原版。拖拽/缩放/退出按键与默认模式完全一致。

- **`--emotion NAME` 情绪模式（express）**：在 `--viewer` 那套待机（规律眨眼 + 多轴摆动 + 物理甩发）之上叠加情绪姿态，共 **16 种**：平和、开心、兴奋、惊喜、温柔、关切、好奇、期待、无奈、失望、沮丧、难过、担心、不满、生气、愤怒。每种都由 llny 现有参数设计（眉毛角度/形态、眼开/笑、嘴型/开合、脸红/生气/哭等叠画开关、头/身角度），例如 愤怒 = 眉下压 + 咧嘴 + 生气记号 + 脸红 + 泪 + 头微侧，温柔 = 嘴角上扬 + 浅笑 + 脸红。情绪切换约 **0.25 秒平滑交叉淡入**（`blend = blend*0.82 + target*0.18`）；**未被当前情绪使用的叠画开关会自动淡回 0**，不会残留上一个表情的生气/脸红/鼓脸。纯 `--viewer` 不加任何参数即回到原待机，默认演示（`render_frame`）与 `--viewer` 逻辑保持不变。

- **`--lipsync wav` 说话对口型**：宠物自己播放该音轨（soundfile 解码，wav/flac/ogg 均可），并**逐块计算 RMS 能量**（0~1）驱动 `ParamMouthOpenY`——**嘴巴跟随音频能量开合，对上口型**；音轨循环播放。每段情绪的"说话音量"不同（`MOUTH_AMP`：兴奋/愤怒 1.25~1.3 张得更大，温柔/沮丧 0.6 张得更小），播放间隙嘴回到该情绪自带的嘴型开度。没有可用音频输出（如远程桌面的"幽灵"设备）时打印警告并自动跳过，不会卡死；用 `--lipsync-device` 指定输出设备号（先 `python -c "import sounddevice as sd; print(sd.query_devices())"` 查看）。

- **`--listen` 监听系统播放（自动对口）**：宠物**不需要拿到音频文件**——它用 WASAPI **回环捕获**实时"偷听"电脑正在输出的声音，逐块 RMS 驱动嘴巴。你的 AI 管线（或任何播放器）正常放声音，嘴就自动跟上；不说话就闭口。这就是"随着音频播放器自动控制"。依赖 `pyaudiowpatch`（已装入 voice-asr 环境）。自动选第一个 `[Loopback]` 设备；可用 `--listen-device` 指定设备号（先 `python -c "from system_listen import list_loopback_devices; list_loopback_devices()"` 查看）。注意：回环会听到**系统所有声音**（音乐、视频也会带动嘴）；且需要一台有真实音频输出的机器，远程桌面的幽灵设备上不会出声。`--lipsync` 与 `--listen` 互斥。

- **`--control-port PORT` 外部随时控制**：在 `127.0.0.1:PORT` 起一个 TCP 服务，**每行一个 JSON** 即可随时切换情绪 / 控制嘴巴开合——适合接 AI 语音输出管线：
  ```python
  import socket
  s = socket.create_connection(("127.0.0.1", 5000))
  s.sendall(b'{"emotion":"开心"}\n')      # 切情绪（未知情绪被忽略并打印）
  s.sendall(b'{"emotion":"愤怒"}\n')
  s.sendall(b'{"mouth":0.7}\n')           # 强制嘴开 0.7（0~1）
  s.sendall(b'{"mouth":null}\n')          # 释放嘴，回到待机/对口型的自动嘴
  ```
  命令在一条连接里可连发多条；情绪切换同样约 0.25 秒平滑过渡。

- **`mock_control.py`（控制端测试工具）**：一个交互式 TCP 客户端，连上 `--control-port` 端口后直接输入命令，不用写代码就能快速测试表情和嘴型：
  ```bash
  python desktop_pet.py --control-port 5000    # 终端 A：先起宠物
  python mock_control.py --port 5000           # 终端 B：再开控制端
  ```
  进入后直接输入情绪名（`help` 看全部 16 种）、`mouth <0..1>` 强制开嘴、`mouth null` 释放嘴、`demo` 自动轮播几个情绪、`quit` 退出。与 AI 管线用的是**同一个协议**（每行一个 JSON），所以测通了这里 = 管线也能通。

- **三者组合（推荐场景）**：`--emotion 兴奋 --lipsync /path/to/speech.wav --control-port 5000` 同时开启——宠物自播 wav 对口型，AI 管线经 TCP 随时切当前情绪。注意：**说话期间 TCP 的 `{"mouth":..}` 会被音频能量覆盖**（对口型拥有嘴），发 `{"mouth":null}` 才释放回待机。

---

## 五、参数控制原理

### 5.1 核心原理

Live2D 的"动"本质是一条数据流，全程无机器学习：

```
每帧给参数赋值 → model.Update() 驱动参数/物理/动作 → model.Draw() 画网格 → 换帧
```

`model.SetParameterValue(param_id, value)` 可对**任意参数**赋值，一帧内可同时改几十个参数。Viewer 里拖动滑轨只是"人肉逐帧设参"，用代码就是把这个动作写进循环——一次写好，60+ FPS 稳定驱动，这正是"预设动作场景"的基础。

```python
while running:
    for k, v in scene_params.items():
        model.SetParameterValue(k, v)
    model.Update()
    model.Draw()
```

> **⚠️ 自动眨眼/呼吸会覆盖你的参数。** C++ SDK（`_v3cpp.pyd`）默认在每次 `model.Update()` 内部驱动自己的自动眨眼与呼吸，把 `ParamEyeLOpen`/`ParamEyeROpen`/`ParamBreath` 覆盖回它算的值——导致"眼睛不听代码指挥、只眨了一次"。程序控制前先关掉它们：
> ```python
> model.SetAutoBlinkEnable(False)
> model.SetAutoBreathEnable(False)
> ```
> `model_api.ModelSession` 默认已关闭（可传 `auto=True` 保留）；`param_control.py` 内部也已关闭。

### 5.2 两种动画实现方式

**方式一：程序化（靠时间/正弦函数现场算）** — 适合眨眼、说话、呼吸这类循环：

```python
"ParamEyeLOpen": 1.0 if (f // 30) % 4 != 2 else 0.05,   # 眨眼
"ParamMouthOpenY": 0.5 + 0.5 * math.sin(t * 6),          # 说话
```

**方式二：关键帧列表（预设场景，逐帧遍历+插值）** — 适合编排完整动作剧情：

```python
keyframes = [{"ParamMouthOpenY": 0.0, "ParamAngleZ": 0},
             {"ParamMouthOpenY": 0.7, "ParamAngleZ": -8},
             {"ParamMouthOpenY": 0.0, "ParamAngleZ": 0}]
# 相邻两帧线性插值，逐帧应用
```

（`param_control.py anim` 已内置线性插值的关键帧引擎。）

### 5.3 参数信息从哪里来

一个模型的参数信息由两部分构成：

| 信息 | 来源文件 | 怎么读 |
|---|---|---|
| 参数 id、范围 min/max、默认值 | `*.moc3`（二进制） | 运行时枚举：`GetParameterCount()` + `GetParameter(i)`（返回 `.id/.min/.max/.default/.value`） |
| 参数的显示名（"去外套"等） | `*.cdi3.json`（DisplayInfo） | `json.load(...)` 后取 `Parameters` 列表的 `Id`→`Name` 映射 |

关联结构（在 `*.model3.json` 里声明）：

```json
{
  "Moc": "llny.moc3",
  "Textures": ["llny.4096/texture_00.png", "..."],
  "DisplayInfo": "llny.cdi3.json"
}
```

> `model3.json` 里的 `Groups` 还标注了标准用途，如 `EyeBlink` → `["ParamEyeLOpen","ParamEyeROpen"]`。

### 5.4 可复用 API `model_api.py`

把参数获取封装成可直接 import 的接口（**隐藏窗口**加载模型，无需弹窗）：

```python
from model_api import ModelSession, parameter_table

with ModelSession("/path/to/llny.model3.json") as m:
    params = m.parameters()        # 遍历全部 Parameter(id/name/min/max/default/value)
    pmap = m.parameter_map()       # {参数id: Parameter}
    m.set("Param14", 1.0)          # 去水印（自动 clamp 到 [min,max]）
    m.set("Param2", 0.6)           # 脱外套 60%
    v = m.read("ParamMouthOpenY")  # 读当前值

# 一次性拿全量参数表（窗口内部自动创建/销毁）
table = parameter_table("/path/to/llny.model3.json")
for p in table:
    print(p.id, p.name, f"[{p.min}, {p.max}]", "default=", p.default)
```

命令行直接导出：`python model_api.py --model <model3.json> --out params.txt`

### 5.5 现成工具 `param_control.py`

| 命令 | 作用 |
|---|---|
| `python param_control.py list` | 枚举模型全部参数（id/名称/min/max/default），自动读取同目录 `.cdi3.json` 配名字 |
| `python param_control.py set --param Param14=1 Param2=0.6 --out x.png` | 设参数截一帧 |
| `python param_control.py anim --frames 180` | 关键帧动画演示（去水印+眨眼+说话+摇头+脱外套） |

配套的 **`lipsync.py`** 是 `--lipsync` 的底层模块：`play_wav_with_energy(path, on_energy, on_done, device=None)` 播放任意 wav 并逐块回调 `on_energy(rms01)`（0~1 归一化 RMS，阈值 `RMS_FLOOR=0.012`、满开 `RMS_FULL=0.16`），播完回调 `on_done()` 一次；放后台线程调用即可给宠物喂"嘴型能量"。

### 5.6 llny 模型参数速查

**表情/服饰开关（0~1，一个参数控制一个效果）**：`Param`比心、`Param2`去外套、`Param3`眼镜、`Param4`口罩、`Param5`荷包蛋、`Param6`阿尼亚、`Param7`黑脸、`Param8`舌头、`Param9`星星、`Param11`生气、`Param12`哭、`Param13`脸红、`Param14`去掉水印（设1）。

**姿态五官（Cubism 标准）**：`ParamAngleX/Y/Z`(-30~30)、`ParamBodyAngleX/Y/Z`(-10~10)、`ParamEyeLOpen/R`(0~1)、`ParamMouthOpenY`(0~1)、`ParamMouthForm`(-1~1)、`ParamBreath`(0~1)、`ParamHairFront/Side/Back`(-1~1)。

**细节部件旋转（-45~45）**：`Param_Angle_Rotation_N_ArtMeshXXX`，对应丸子头/后发/袜子结/草莓结/外套结等单个部件。

完整清单见同目录 `llny_params.txt`。

---

## 六、Live2DViewer 桌面查看器

仓库自带的独立 **Qt6 桌面应用**（源码在 `Live2DViewer/`，C++17），一个 Live2D 模型查看器，与 Python 库无关，无需 Python 依赖。

- 支持 **Cubism 3.0+**（`.model3.json`），不支持老 `.moc` 模型
- 渲染引擎与 Python `live2d.v3` 是同一套 `Live2D::V3` SDK
- 功能：多模型标签页、部件面板、网格面板、点击移动/缩放、中/英/日多语言

### 使用（预编译版，推荐）

1. 下载约 70MB 的预编译包并解压运行：

```bash
curl -L -o Live2DViewer-win64.zip \
  https://github.com/EasyLive2D/live2d-py/releases/download/v0.7.0.4/Live2DViewer-win64.zip
unzip Live2DViewer-win64.zip -d Live2DViewer
cd Live2DViewer && ./Live2DViewer.exe
```

2. 点"打开模型"选择一个 `.model3.json`（例如 `/path/to/live2d-py/Resources/v3/Haru/Haru.model3.json`）；每个模型开一个标签页，可同时打开多个。

3. 交互操作：

| 操作 | 效果 |
|---|---|
| 点击画布后 ↑↓←→ | 移动模型 |
| `-` / `=` 键 | 缩小 / 放大 |
| 鼠标右键 | 唤出菜单，清空选中状态 |
| 部件面板 / 网格面板点行 | 选中对应绘制区域 |

界面语言自动跟随系统（`Live2DViewer/Translations/` 含 zh_CN / en / ja_JP）。

### 从源码编译（不推荐）

- 依赖 Qt 6.8/6.9 + CMake + Cubism SDK（v3 的 `Live2D/` submodule 与 Core）
- ⚠️ `Live2DViewer/CMakeLists.txt` 首行硬编码了作者的 Linux Qt 路径，Windows 上需先改 `CMAKE_PREFIX_PATH`
- 构建：`cmake --build build --config Release --target Live2DViewer`

---

## 七、验证结果（示例环境：conda + Python 3.10.16）

实测通过：

- 安装 `live2d_py-0.7.0.4-cp310-cp310-win_amd64.whl` + `glfw` ✅
- `_v3cpp.pyd` 落点：`<venv>/lib/site-packages/live2d/v3/_v3cpp.pyd`（约 440KB）
- 加载 Haru 模型：42 参数 / 19 部件 / 6 动作 ✅
- 渲染：180 帧 @ **463 FPS**（C 扩展性能）✅
- 截图：`/path/to/live2d/verify_v3.png` ✅
- 验证脚本：`/path/to/live2d/verify_v3.py`

---

## 八、常见问题与注意点

1. **`model.Draw()` 前必须开混合**：`GL.glEnable(GL.GL_BLEND)`，否则边缘出现黑边。
2. **必须先建 GL 上下文再 `live2d.init()`**：顺序反了会崩。
3. **cp 标签必须匹配 Python 版本**：C 扩展 ABI 绑定死，选错 pip 拒绝 / import 报 `DLL load failed`。
4. **模型文件是相对路径引用的**：贴图、动作都在模型 json 同级目录，别只拷单个 json。
5. **不要用 `live2d.v2`（纯 Python）**：性能差一个量级；老模型用 `v2cpp`、新模型用 `v3`。
6. **运行官方 `examples/` 时注意**：`examples/resources.py` 会把本地 `package/` 塞进 `sys.path`。如果装的是 pip/wheel 版且本地源码未编译 `.pyd`，会优先 import 到缺二进制的本地源码而报错。自行写脚本（不 import resources）则没这个问题。
7. **release 里的 `Live2DViewer-win64.zip`** 是独立的编译版桌面应用，与 Python 库无关，不需要。
8. **`CubismSdkForNative-5-r.5.zip`** 仅在源码编译 v3 时需要；用 wheel 则完全不需要。

---

## 附：参考资料

- 上游仓库：<https://github.com/EasyLive2D/live2d-py>
- 中文文档（Wiki）：<https://github.com/EasyLive2D/live2d-py/wiki>
- 官方样例模型：<https://www.live2d.com/en/learn/sample/>
- Cubism Native SDK 下载：<https://www.live2d.com/en/sdk/download/native/>
