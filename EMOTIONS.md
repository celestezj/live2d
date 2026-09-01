# 16 情绪细节设计（EMOTIONS 定稿）

> 定稿日期：2026-09-01。配套代码 `desktop_pet.py` 的 `EMOTIONS` 字典。
> 每个表情给出「设计意图」+「llny 参数配方」。配方只使用探针实测**真正能渲染**的参数，
> dead / 近不可见参数已剔除（依据见 §3 可行性实测表）。

---

## 一、速览表

| # | 情绪 | 设计一句话 | 关键参数 |
|---|------|-----------|----------|
| 1 | 平和 | 淡然松弛，半垂眼轻抿嘴 | 眼 0.9、MouthForm 0.1 |
| 2 | 开心 | 明亮大笑，全开眼仰头 | 眼 1.0、咧嘴 0.7、微张 0.15、头仰 Y3 |
| 3 | 兴奋 | 活力张扬，张嘴瞪眼前倾 | 眼 1.0、张嘴 0.35、眉 0.3、头侧 X−4、身伸 Y3 |
| 4 | 惊喜 | 震惊张口，下颌外开后仰 | 张嘴 0.55、Jaw 0.3、眉 0.4、头仰 Y6、身转 X−3 |
| 5 | 温柔 | 软笑垂眉，双颊泛红双腿微并，含羞待放 | 咧嘴 0.35、脸红 1.0、夹腿 −8、垂睫 0.85、眼珠微垂 −0.2、低头 −3 |
| 6 | 关切 | 轻蹙垂睫，侧头倾听凑近 | 眉轻蹙 −0.3/0.3、垂睫眼 0.7、眼珠朝你 0.3、嘴角微压 |
| 7 | 好奇 | 歪头杀打量，眼珠转 | 歪头 Z22、眼珠 0.3/0.1、眉挑 |
| 8 | 期待 | 星星眼 + 眼巴巴抬头 | 星星眼 Param9、头垂 Y−5、歪头 Z2、眉挑 |
| 9 | 无奈 | 倦怠垂眼，嘴角松垮下坠 | 双眉垂 −0.18、眼半开 0.62、眼珠下、嘴角垂 −0.4、微张缝、低头 Y−5、微歪 Z2 |
| 10 | 失望 | 泄气垂头，半垂眼 | 平眉压 −0.25、眼 0.75、眼珠下、闭嘴垂 |
| 11 | 沮丧 | 整脸变黑，呆滞垂头垮肩 | **黑脸 Param7 1.0**、眼 0.55、眼珠下、头垂 Y−7、身下压 −3 |
| 12 | 难过 | 哭容，垂眼皱眉微张嘴 | 哭 Param12 1.0、眼珠垂 0.25、眉内皱 0.4/−0.4、嘴微张 0.2 |
| 13 | 担心 | 紧蹙上挑，大歪头 | 眉紧蹙 −0.5/0.5 + 挑 0.4、眼珠上、嘴微张、歪头 Z6 |
| 14 | 不满 | 噘嘴深下垂，撇头 | **MouthForm −1.0 深垂**、眉压、半眯眼 0.75、歪头 + 偏头 |
| 15 | 生气 | 仰头蔑视，揪嘴俯视 | **仰头 Y10**、眼珠下 0.3、眼窄 0.65、眉强压、**Shrug 1.0 揪嘴** |
| 16 | 愤怒 | 怒吼张嘴，低首侧视 | 咧嘴 0.4、张嘴 0.45、Jaw 0.3、眉强压、头低 Y−5 |

---

## 二、逐个细节设计

### 1. 平和
- **设计**：不设姿态，就是待机状态的表情——眼睛松弛半垂（0.9，非全睁非眯缝），嘴角浅浅带一点抿线（MouthForm 0.1），整体无紧张感。
- **配方**：`ParamMouthForm 0.1, ParamEyeLOpen/ROpen 0.9`
- **备注**：是其它 15 种情绪区分的**基准脸**（探针区分度均以它为对照）。

### 2. 开心
- **设计**：真正的开心要"眼睛亮 + 笑得开"。llny 的笑眼参数（EyeLSmile/RSmile）实测**不渲染**，所以用**全开眼**（1.0）代替"亮眼"，咧嘴 MouthForm 0.7 加上**嘴微张 0.15**（笑得微微张口），眉轻挑 0.15，**头仰起 Y3 + 歪头 Z4**（朝气）。
- **配方**：`ParamMouthForm 0.7, ParamMouthOpenY 0.15, ParamEyeLOpen/ROpen 1.0, ParamBrowLY/RY 0.15, ParamAngleZ 4, ParamAngleY 3`
- **区分要点**：与关切（半垂睫、抿嘴、垂首）从眼宽、嘴、头俯仰三个方向对立。

### 3. 兴奋
- **设计**：比开心更满溢——全开眼 + **张嘴 0.35**（张口笑/喊）+ 咧嘴 0.8 + 眉高挑 0.3，眼珠向前下方 0.1/−0.15（看向说话对象），**头侧 X−4 + 前倾 Y5 + 身体拉伸 BodyAngleY 3**（整体冲出去的劲头）。
- **配方**：`ParamMouthForm 0.8, ParamMouthOpenY 0.35, ParamEyeLOpen/ROpen 1.0, ParamBrowLY/RY 0.3, ParamEyeBallX 0.1, ParamEyeBallY -0.15, ParamAngleX -4, ParamAngleZ 2, ParamAngleY 5, ParamBodyAngleY 3`

### 4. 惊喜
- **设计**：震惊得**张不开嘴合不上眼**——张嘴 0.55 + 下颌 Jaw 0.3（嘴型更大）+ 全开眼 + 眉高挑 0.4，**头仰 Y6 后仰 + 歪头 Z−4**，身体微转 X−3。
- **配方**：`ParamMouthOpenY 0.55, ParamEyeLOpen/ROpen 1.0, ParamBrowLY/RY 0.4, Param62 0.3, ParamAngleZ -4, ParamBodyAngleX -3, ParamAngleY 6`

### 5. 温柔
- **设计**（v2 定稿：含羞待放）：最轻最软的笑 + 一点含羞——
  - **眉眼**：浅笑咧嘴 0.35 + **眼睫微微垂落 0.85**（垂眸不睁圆）+ **眼珠微垂 −0.2**（目光低垂、含羞）+ 眉微微下垂 −0.1。
  - **双颊**：脸红 Param13 1.0（实测为左颊轻微变暗 dLum −5，淡淡的红晕，不突兀）。
  - **腿**：Param28 −8（双腿**稍微并拢再收一步**，大腿缝 22px→约 7px，小腿/脚锚定不动）。
  - **头**：歪头 Z3 + **微低头 AngleY −3**（低头含羞，但比沮丧 −7 轻得多）。
- **配方**：`ParamMouthForm 0.35, ParamEyeLOpen/ROpen 0.85, ParamBrowLY/RY -0.1, ParamAngleZ 3, Param13 1.0, Param28 -8, ParamEyeBallY -0.2, ParamAngleY -3`

### 6. 关切
- **设计**（用户定义：带着担忧、专注留意对方，**不是悲伤**）：
  - **眉眼**：眉头**微微轻蹙**（眉内角 −0.3/0.3，注意是"轻"蹙，比担心的 −0.5/0.5 松），**眼睫微微垂落**（眼 0.7，半垂但不闭），目光稳稳落在对方脸上（眼珠 X 0.3），专注不飘忽。
  - **唇**：嘴角**微微下压 −0.25**（抿起，不上扬），欲言又止时轻轻开合（MouthOpenY 0.06）。
  - **头部**：脑袋**微微侧偏 + 探身凑近**（歪头 Z3 + 偏头 X4 + 头垂 Y−2）——认真倾听的姿态。
- **配方**：`ParamBrowLAngle -0.3, ParamBrowRAngle 0.3, ParamEyeLOpen/ROpen 0.7, ParamEyeBallX 0.3, ParamMouthForm -0.25, ParamMouthOpenY 0.06, ParamAngleZ 3, ParamAngleX 4, ParamAngleY -2`
- **与担忧的区别**：**关切 = 轻蹙**（专注共情）；**担心 = 紧蹙 + 眉上挑**（焦虑）。
- **模型限制**："眉心褶皱""瞳孔收束"无对应参数，用眉内角 + 眼窄化近似。

### 7. 好奇
- **设计**：**歪头杀——大幅度歪头 Z22**（打量，头带 bbox 宽 231→275px）+ 眼珠转动向上偏 0.3/0.1 + 眉一挑一平 0.2/0.05 + 嘴微张 0.15（好奇得有点想说话）。llny 角度响应偏弱，歪头要拉到 20+ 才明显。
- **配方**：`ParamAngleZ 22, ParamEyeBallX 0.3, ParamEyeBallY 0.1, ParamBrowLY 0.2, ParamBrowRY 0.05, ParamMouthOpenY 0.15, ParamMouthForm 0.1, ParamEyeLOpen/ROpen 0.95`

### 8. 期待
- **设计**：**星星眼（Param9 = 1.0）**——这是用户批准的创意；配合眉上挑 0.2、眼开 0.95，**头微微垂下 Y−5**（眼巴巴抬头看人）+ 歪头 Z2 + 眼珠向下 0.1。
- **配方**：`ParamBrowLY/RY 0.2, ParamEyeLOpen/ROpen 0.95, ParamMouthForm 0.3, ParamMouthOpenY 0.1, ParamAngleY -5, ParamAngleZ 2, ParamEyeBallY -0.1, Param9 1.0`

### 9. 无奈
- **设计**（2026-09-01 重写，按用户细节描写）：眉峰微微塌下垂、眉头不紧皱（**双眉垂 −0.18**，非一挑一垂）；眼皮半松（**眼开 0.62**），眼眸淡淡视线放空微下（眼珠 Y −0.1），眼尾耷拉；嘴角不扬也不大哭下撇，**平平松垮向下坠（MouthForm −0.4）**，唇微张一条细缝（OpenY 0.06，像没叹出来的气）；整脸无紧绷、无鼓脸无脸红；头微垂（AngleY −5）+"算了就这样吧"的微歪（Z2）。删掉 Shrug 揪嘴与髋摆（那是俏皮不是倦怠）。
- **配方**：`ParamBrowLY/RY -0.18, ParamEyeLOpen/ROpen 0.62, ParamEyeBallY -0.1, ParamMouthForm -0.4, ParamMouthOpenY 0.06, ParamAngleY -5, ParamAngleZ 2`

### 10. 失望
- **设计**：**泄气**——平眉下压 −0.25、**半垂眼 0.75 + 眼珠下垂 0.1**、嘴闭合下压 −0.35（不张口），头侧 X4 + 身体转 X3（别过脸去）。
- **配方**：`ParamBrowLY/RY -0.25, ParamMouthForm -0.35, ParamEyeLOpen/ROpen 0.75, ParamEyeBallY -0.1, ParamAngleX 4, ParamBodyAngleX 3`
- **与沮丧的区别**：失望脸不黑、没有垮肩；沮丧是黑脸 + 更深的下垂。

### 11. 沮丧
- **设计**（用户点名"眼部区域变黑、脸变黑"）：**整脸变黑（Param7 = 1.0）**——该参数是 0/1 阈值开关，0.6 时实测几乎不可见，必须取 1.0；配合**呆滞半眯眼 0.55 + 眼珠下垂 0.2**、眉下压、嘴下垂 −0.5，**头垂 Y−7 + 身体下压 −3**（垮肩萎靡）。
- **配方**：`ParamBrowLY/RY -0.3, ParamBrowLAngle -0.15, ParamBrowRAngle 0.15, ParamMouthForm -0.5, ParamEyeLOpen/ROpen 0.55, ParamEyeBallY -0.2, Param7 1.0, ParamAngleX 5, ParamBodyAngleX 4, ParamAngleY -7, ParamBodyAngleY -3`
- **备注**：泪参数（Param26）实测死，沮丧不靠泪，靠黑脸。

### 12. 难过
- **设计**（用户点名"泪花闪闪"，但 **llny 泪参数是死的**，只能做到"哭容"）：哭 Param12 = 1.0（眼眶微亮、脸颊微潮）+ **眼珠垂落 0.25** + **眉内皱 0.4/−0.4**（哭的皱眉）+ **嘴微张 0.2 下压 −0.5**（哭嘴）+ 头垂 Y−4 + 身体下压 −2。
- **配方**：`ParamBrowLY/RY -0.2, ParamBrowLAngle 0.4, ParamBrowRAngle -0.4, ParamMouthForm -0.5, ParamMouthOpenY 0.2, ParamEyeLOpen/ROpen 0.85, ParamEyeBallY -0.25, Param12 1.0, ParamAngleX 4, ParamBodyAngleX 2, ParamAngleY -4, ParamBodyAngleY -2`
- **模型限制**：无法呈现"泪花"（Param26 泪 / 泪珠自绘均无），用哭容 + 垂眼替代。

### 13. 担心
- **设计**：**眉紧蹙（内角 −0.5/0.5）+ 眉上挑 0.4**（担心是"皱起又挑高"）+ 眼微开 0.85 + **眼珠上偏 0.1**（四处张望找）+ 嘴微张 0.15 下压 −0.3 + **大歪头 Z6**。
- **配方**：`ParamBrowLY/RY 0.4, ParamBrowLAngle -0.5, ParamBrowRAngle 0.5, ParamMouthForm -0.3, ParamMouthOpenY 0.15, ParamEyeLOpen/ROpen 0.85, ParamEyeBallX 0.2, ParamEyeBallY 0.1, ParamAngleZ 6`

### 14. 不满
- **设计**（用户点名"嘴凸起揪起来"，实测撅嘴参数全死，选**最深下垂**嘴型）：眉下压 −0.35 + 内角 −0.4/0.4 + **嘴 MouthForm −1.0（最深下垂）** + 半眯眼 0.75 + 歪头 Z3 + **偏头 X−3**（不对称噘嘴）。
- **配方**：`ParamBrowLY/RY -0.35, ParamBrowLAngle -0.4, ParamBrowRAngle 0.4, ParamMouthForm -1.0, ParamEyeLOpen/ROpen 0.75, ParamAngleZ 3, ParamAngleX -3`
- **模型限制**：真正的"撅嘴凸起"（Funnel/撅嘴）参数死；在可选范围里 MouthForm −1 是嘴部最强线索（探针 414px）。

### 15. 生气
- **设计**（用户点名"头仰起蔑视 + 嘴揪起来"）：
  - **仰头蔑视**：头仰 AngleY 10（下巴抬起、居高临下）+ **眼珠下 0.3**（俯视）+ 眼窄 0.65（眯眼瞪）+ 眉强压内角 −0.5/0.5 + 眉下压 −0.4。
  - **嘴揪起来**：撅嘴参数死，用 **Param61 Shrug 1.0**（嘴角上提收拢）+ MouthForm −0.3 模拟"揪起"。
  - 头侧 X4 + 身体转 X4（身体朝向对方）+ 生气记号 Param11 1.0。
- **配方**：`ParamBrowLY/RY -0.4, ParamBrowLAngle -0.5, ParamBrowRAngle 0.5, ParamMouthForm -0.3, ParamEyeLOpen/ROpen 0.65, ParamEyeBallY -0.3, Param11 1.0, Param61 1.0, ParamAngleY 10, ParamAngleX 4, ParamBodyAngleX 4`

### 16. 愤怒
- **设计**：**怒吼**——咧嘴 MouthForm 0.4 + **张嘴 0.45 + Jaw 0.3**（嘴张到最大）+ 全开眼 + 眉强压内角 −0.5/0.5 + 生气 Param11 1.0 + **头低 Y−5**（怒吼前倾）+ 歪头 Z−4。
- **配方**：`ParamBrowLY/RY -0.45, ParamBrowLAngle -0.5, ParamBrowRAngle 0.5, ParamMouthForm 0.4, ParamMouthOpenY 0.45, ParamEyeLOpen/ROpen 1.0, Param11 1.0, Param62 0.3, ParamAngleY -5, ParamAngleZ -4`
- **与生气区别**：生气是"仰头蔑视 + 抿嘴揪起"；愤怒是"低首怒吼 + 张嘴咆哮"，且眼全开。

---

## 三、参数可行性实测表（2026-09-01 探针，llny）

| 参数 | 单 cue 强度（脸区 2D diff px） | 结论 |
|---|---|---|
| `ParamEyeLOpen/R`（眼开） | 0.5 → **865** | ✅ 最强 face cue |
| `ParamBrowLY/RY`（眉位上下） | 231 / 245 | ✅ 强 |
| `ParamEyeBallY/X`（眼珠方向） | 249 | ✅ 强 |
| `Param61` Shrug（嘴角） | 281 | ✅ 可用（嘴部最强之一） |
| `Param51` Mouth X（嘴侧移） | 574 | ✅ 可用（之前漏验，强） |
| `ParamMouthForm` ±1 | 246 / 414 | ⚠️ 弱但可用 |
| `Param62` Jaw | 95 | ⚠️ 弱，辅助张嘴 |
| `ParamMouthOpenY` 1.0 | 206 | ⚠️ 弱 |
| `Param7` 黑脸 | **1.0 → 1982**；0.6 → 39 | ⚠️ 0/1 阈值开关，只在 1.0 显效 |
| `Param12` 哭 | 288 | ⚠️ 弱，唯一"泪"cue |
| `Param9` 星星眼 | 152 | ⚠️ 弱但可见 |
| `Param11` 生气 | 100 | ⚠️ 弱记号 |
| `Param13` 脸红 | 0.6 → 38（dLum −3） | 👻 近不可见 |
| `Param58` 鼓脸 | 0.7 → 78 | 👻 近不可见 |
| `ParamEyeLSmile/RSmile`（笑眼） | **0** | 💀 死 |
| `Param26`（泪） | **0** | 💀 死 |
| `Param59` Funnel（撅嘴） | **20** | 💀 死 |
| `Param60` Fucker | ~0 | 💀 死 |
| `ParamCheek` LipOpen | 43 | 💀 死 |

**设计原则**：嘴部参数整体弱、overlay 叠画多半死/不可见，所以差异化主要靠 **眼开宽度 + 眉位/眉角 + 眼珠方向 + 头/身角度** 这些结构 cue，嘴与 overlay 只做辅助点缀。

---

## 四、与代码的对应

- 配方写在 `desktop_pet.py` 的 `EMOTIONS` 字典（16 键）；`MOUTH_AMP` 是各情绪说话时嘴巴张大的音量系数。
- `express_frame` 每帧：交叉淡入（`blend*0.82 + target*0.18`，约 0.25s 到位）→ 眨眼覆盖在情绪眼开之上 → 常驻摆动（头/身 ±1~3°）→ 手势 → 嘴 → Update → Draw；未被使用的叠画开关自动淡回 0。
- 所有配方值均在 llny 参数范围内；写参数前按模型实际参数 id 过滤（`new_emotion_state`），非 llny 模型不会崩。
