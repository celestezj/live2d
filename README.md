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

---

## 五、验证结果（示例环境：conda + Python 3.10.16）

实测通过：

- 安装 `live2d_py-0.7.0.4-cp310-cp310-win_amd64.whl` + `glfw` ✅
- `_v3cpp.pyd` 落点：`<venv>/lib/site-packages/live2d/v3/_v3cpp.pyd`（约 440KB）
- 加载 Haru 模型：42 参数 / 19 部件 / 6 动作 ✅
- 渲染：180 帧 @ **463 FPS**（C 扩展性能）✅
- 截图：`/path/to/live2d/verify_v3.png` ✅
- 验证脚本：`/path/to/live2d/verify_v3.py`

---

## 六、常见问题与注意点

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
