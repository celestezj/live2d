# CLAUDE.md — live2d 透明桌宠项目

## 项目一句话
透明 Live2D 桌宠（llny 模型，live2d-py v3），Windows 分层窗口（`WS_EX_LAYERED` + `UpdateLayeredWindow`）。AI 语音驱动：16 情绪 + WAV 对口型 + 监听系统音频 + TCP 外部控制（切情绪 / 头顶说话框 `{"say":..}` / 嘴）+ 鼠标交互（拖动/锁定/拽头转身/拽身体转身/害羞/穿脱外套）。

## 环境（必须遵守）
- 运行/验证一律用 `/d/anaconda/envs/voice-asr/python.exe`（装 glfw、live2d 0.7.0.4、sounddevice、pyaudiowpatch）。别用系统 python。
- 仓库就是本目录（git master，无 remote，直接提交）。
- 改 README 时路径一律用 `/path/to/...` 占位，**绝不用 `E:` 绝对路径**。
- 项目记忆在 `C:\Users\dymic\.claude\projects\E--temp-live2d\memory\`（跨会话；MEMORY.md 是索引）。

## 快速跑起来
```bash
python desktop_pet.py                                  # 默认演示（render_frame）
python desktop_pet.py --viewer                         # 原生待机（viewer_frame）
python desktop_pet.py --emotion 开心 --random-actions  # 情绪 + 随机身体动作
python desktop_pet.py --emotion 兴奋 --lipsync /path/to/speech.wav --lipsync-device 0
python desktop_pet.py --emotion 开心 --listen          # 监听系统音频自动对口型
python desktop_pet.py --control-port 5000              # TCP 控制口（每行一个 JSON）
python desktop_pet.py --self-test --emotion 兴奋       # 自检一帧（不弹窗）
```
全部 CLI：`--model --width/--height/--x/--y --scale --viewer --emotion --lipsync --listen --lipsync-device --listen-device --control-port --click-through --self-test --random-actions`。

## 铁律（不得违反）
- 默认演示 `render_frame` 和 `--viewer` 的 `viewer_frame` 是既有逻辑，**改功能时保持不动**。
- 用户偏好：新行为要"加开关、默认关"（最小改动防衰退）；`--random-actions` 就是这种模式。
- GLFW/模型**只在主线程**；`PetControl`（`threading.Lock`）是主线程与 sounddevice/TCP 线程之间唯一共享状态。音频/TCP 线程只经 `PetControl` 写 emotion/mouth/bubble/clothes（主线程每帧读 `control.bubble` 决定是否合成说话框）。
- 写参数前按模型实际参数 id 过滤（防非 llny 模型缺参数崩）。
- 只在用户明确说"提交"时 commit；message 结尾带 `Co-Authored-By: Claude Code <noreply@anthropic.com>`；提交前 `git status` 确认无探针/临时文件。
- 用中文回复。

## 导航地图（desktop_pet.py，~1765 行）
| 关注点 | 位置 |
|---|---|
| 16 情绪配方 | `EMOTIONS` (L571)；说话音量系数 `MOUTH_AMP` (L645) |
| 随机动作 | `ACTIVE_ACTION_EMOTIONS` (L1108) + `GESTURES` (L1122) + 秒间隔 `GESTURE_IDLE_MIN_S/MAX_S` (L1112)；帧驱动 `_random_gesture_frame` (L1138) |
| 情绪帧渲染 | `express_frame` (L1182)：blend 交叉淡入→眨眼→常驻摆动→手势→嘴→motion→tug→Update→Draw |
| 待机 / 默认演示 | `viewer_frame` (L1328) / `render_frame` (L1268)——**不要改** |
| 拖拽反应 | `_apply_tug` (L1043)；常量 `HEAD_*_AMP` `BODY_*_AMP` `SHY_*_AMP` (L989 起)、tug 攻/放速率 `TUG_ATTACK/RELEASE_RATE` (L1029/L1030) |
| 点击/害羞分区 | `HEAD_ZONE` (L985) `LEG_ZONE` (L998) `DBL_ZONE_TOP/BOTTOM` (L1001/L1005)（画布比例，`_zone_y` (L304) 换算，scale-aware，见记忆） |
| 窗口/命中 | `LayeredWindow` (L155)：`set_scale` (L253)；`resize` (L260)；**`begin_frame` (L291) 每帧清整窗+把视口钉在 content 带**；`_hit_character` (L245) 用上一帧 alpha；`_zone_y` (L304)；tug 攻 0.15/放 0.07 渐近回 0 |
| 顶部预留天空带 | `sky` 字段 (`__init__`，= `round(content_h*BUBBLE_SKY_FRAC)`，窗口 = content+sky 高，main L1518 算)；**宠物锚底部 content 带，永不因气泡缩放/位移**；`_zone_y`/拖拽归一/tug 均按 content 高 (win.h-sky)；`begin_frame` 只在 sky>0 时动 GL |
| 跨线程状态 | `PetControl` (L911)：emotion/mouth/**bubble**(说话框文本，None=隐藏)/clothes；`set_bubble` (L935) 线程安全；`new_emotion_state` (L959) 建每帧状态 |
| 说话框合成 | `LayeredWindow.present()` (L443)：overlay(由主循环每帧 `win.overlay=_bubble_overlay(win,control.bubble)` 喂入，L1735)在 premultiply 前 alpha-over 进 raw；`_blend_overlay` (L476)。**overlay 不进 `last_rgba`**→命中/头冠检测不受气泡影响 |
| 说话框渲染/定位 | 模块函数块 (L658 起)：常量 `BUBBLE_*`(暖米渐变/浅棕褐描边/投影/**`BUBBLE_CORNER_FRAC`=0.10 统一小圆角**)、`_grad_body` (L748，渐变填充同描边外缘)、`_render_bubble` (L768，**扁平圆角矩形、四角统一小圆角**、无尾巴、**常规**微软雅黑、按 ink bbox 双向居中、下方淡投影，按 (text,fs,textw,lines) 缓存)、`_bubble_overlay` (L853)。**固定 ≤3 行**、字数多先缩字号 (~0.62 倍仍放不下才末行截断加 `…`)。气泡主体底**恒浮在实测头顶 (crown) 上方留 `BUBBLE_CROWN_GAP=7` 空隙**、定位用主体不透明 bbox (>200 alpha，阴影淡可排除)；crown 在 content 带顶 (~sky+13)，气泡整体落在 sky 带内，**宠物零缩放/零位移**；字号按 content 高 `(win.h-sky)*BUBBLE_FONT_FRAC`。sky 不够大时 `oy` 转负向上越界（`_blend_overlay` 裁顶）——只裁气泡顶，**永不压头/遮脸**。**无 talk/`_bubble_need` 机制**（已删） |
| 音频 | `lipsync.py`（自播 WAV，`RMS_FLOOR=0.012`/`RMS_FULL=0.16`）；`system_listen.py`（WASAPI loopback，滚动峰值自适应，回调须返回 tuple） |
| TCP 控制 | `make_control_handler` (L1385) / `start_control_server` (L1425)：每行 JSON 对象；键=独立通道：`emotion` str=切/`null`=复位平和、`say` str=显示气泡/`null`=隐藏（空串同隐藏）、`mouth` 数=开/`null`=释放；`{}` no-op，未知键忽略（向前兼容）。协议详见 README §4.4 |
| 工具脚本 | `mock_control.py`（TCP 测试端，含 `say`/`emotion <名>`/`emotion null` 命令）、`model_api.py`（导出参数表 `--out`）、`param_control.py`（`list`/`set --param`/`anim`） |
| 音频 | `lipsync.py`（自播 WAV，`RMS_FLOOR=0.012`/`RMS_FULL=0.16`）；`system_listen.py`（WASAPI loopback，滚动峰值自适应，回调须返回 tuple） |
| TCP 控制 | `make_control_handler` (L1357) / `start_control_server` (L1397)：每行 JSON 对象；键=独立通道：`emotion` str=切/`null`=复位平和、`say` str=显示气泡/`null`=隐藏（空串同隐藏）、`mouth` 数=开/`null`=释放；`{}` no-op，未知键忽略（向前兼容）。协议详见 README §4.4 |
| 工具脚本 | `mock_control.py`（TCP 测试端，含 `say`/`emotion <名>`/`emotion null` 命令）、`model_api.py`（导出参数表 `--out`）、`param_control.py`（`list`/`set --param`/`anim`） |

## llny 参数语义（实测结论，别重测）
- `ParamAngleX` = 头 yaw（+ = 朝观察者右侧）、`ParamAngleY` = 头 pitch（+ = 上）——**与 Cubism 命名相反**。
- `ParamBodyAngleX` = **躯干转身**（chest 横扫、脚锚定、+ 与 AX 同号）——不是前后倾；`ParamBodyAngleY` = 垂直伸缩（− = 下蹲变矮）；`ParamBodyAngleZ` = 髋反向摆。
- `Param2` "去外套"：**0 = 穿着（默认），1 = 脱掉**（代码注释曾写反，已修正）。
- `Param28` "yy" 负值夹腿；`Param27` "zz" 是全身绕脚旋转（不能夹腿）。
- `Param41` = 左臂、`Param43` = 右臂（±30 手只到髋部高度，**做不了双手在身前交叉遮挡**）。
- `ParamEyeBallX + = 右`、`ParamEyeBallY + = 上`（害羞看下用负值）。
- 角度物理会甩发，像素度量用**皮肤质心**；探针要 `ResetParameters()` + 预热 20 帧。

## 验证方法（探针模式）
- 一进程一窗口：`glfw.init()` + `live2d.init()` + `live2d.glInit()` → `LayeredWindow` → `LAppModel.LoadModelJson`。
- 度量用 `win.last_rgba` 像素统计（不透明像素数 / 带区质心 / bbox 宽高）；**勿用 alpha 阈值量瞳孔**（该用 RGB 亮度）。
- **验证真实功能用模块的 `render_frame`/`express_frame` 并传真 `idle_motion`（`find_idle_motion`），别只测孤立函数**——曾因只测 `_random_gesture_frame` 漏掉真实管线问题（tug 残留卡死手势）。
- 探针文件写完用完即删；别进 commit。

## 文档
- README.md：§4.4 桌宠全部功能、§4.5 测试/自检命令、§5 参数原理与工具、§5.6 llny 参数速查。
- 记忆文件（跨会话，优先读）：`live2d-desktop-pet-architecture.md`（架构/参数语义/改动落点/验证法）、`llny-scale-aware-click-zones.md`、`pyaudiowpatch-loopback-callback-tuple.md`。
