# 按工程形态接入

平台只要求三件事：模块能在目标解释器里 import；接口同步、参数按名字传；返回值能 JSON 化（图片走 `node_sdk.save_file`）。其他形态都要先归一化成这三条。

**改哪里**：优先在工程目录里**新增**一个适配模块（如 `platform_api.py`），不动原有逻辑。登记时 `module` 填适配模块，原模块继续给别的系统用。一个工程可以有多个模块，槽位里分别写各自的 `module`。

**抽什么**：判据和 IO 都要收敛成一份。界面读 CSV 与平台读 CSV 若各写一套，解析口径（编码、表头、空行、单位）迟早分叉，同一份数据在窗口里和诊断里会给出两种结论。把读取与判定放进一个不依赖 UI 的模块，界面和适配层都调它。

---

## 1. 纯函数库

**特征**：顶层函数，接收数据返回数据，无状态。

**做法**：直接用。`object` 留空。

```python
def check_peak(samples: list, limit: float = 5):
    """峰值是否超限。"""
    return {"result": max(samples) > limit, "reason": f"峰值 {max(samples)}，阈值 {limit}"}
```

```json
"decide": {"object": "", "project": "判断工程", "module": "checks",
           "interface": "check_peak", "function": "check_peak",
           "arguments": {"samples": {"source": "previous"}}}
```

注意：模块里若有 `if __name__ == "__main__"` 之外的重活（建连接、读大文件、启动服务），把它挪进函数或 `if __name__` 里——探针会 import 模块。

---

## 2. 有状态类

**特征**：数据在对象上，方法读写 `self`。平台需要跨节点保留这份状态时用它。

**做法**：构造槽建对象，其余槽 `object` 指向它。构造参数来自启动输入。

```python
class DataCenter:
    def __init__(self, dataset: str, device: str = ""):
        self.dataset = dataset
        self.device = device
        self.cache = {}

    def fetch(self, channel: str, points: int = 24):
        key = (channel, points)
        if key not in self.cache:
            self.cache[key] = load_from_store(self.dataset, channel, points)
        return list(self.cache[key])
```

```json
"constructs": [{"object": "center", "project": "数据工程", "module": "feeder",
                "interface": "DataCenter", "function": "DataCenter",
                "arguments": {"dataset": {"source": "input", "name": "dataset"}}}]
```

对象的整个 `__dict__` 会被 pickle 保存。**连接、锁、窗口、打开的文件句柄、线程不能挂在对象上**——它们不可序列化，快照时会报错。改成调用中临时打开：

```python
def query(self, sql):
    with connect(self.dsn) as conn:      # dsn 是字符串，可以 pickle
        return conn.execute(sql).fetchall()
```

---

## 3. 工厂函数 / 依赖注入

**特征**：不直接暴露类，通过 `create_xxx()` 造对象，或对象需要外部依赖。

**做法**：构造槽填工厂，`object` 自己起名；工厂的返回值就是后续槽位的目标对象。

```python
def create_rules(limit: float = 5, store=None):
    return ThresholdRules(limit, store or default_store())
```

```json
"constructs": [{"object": "rules", "project": "判断工程", "module": "checks",
                "interface": "create_rules", "function": "create_rules",
                "arguments": {"limit": {"source": "input", "name": "threshold"}}}]
```

工厂若返回不可序列化的东西（数据库连接、客户端句柄），把句柄改成模块级单例或懒加载，只让可序列化的配置进对象。

---

## 4. async 接口

**特征**：`async def`，或底层 SDK 只有 async 客户端。

**做法**：加同步门面。**不要**在门面里 `asyncio.run()`——业务进程可能已有事件循环。用独立线程跑一个循环最稳：

```python
import asyncio, threading

_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()

def run_sync(coro):
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout=60)

async def _read(tag: str):
    ...

def read(tag: str):
    """同步门面：平台只调这个。"""
    return run_sync(_read(tag))
```

槽位里填 `read`，不要填 `_read`。

---

## 5. 位置参数 / `*args` / 关键字不友好

**特征**：`def f(a, b, /)`、`def f(*args)`，或参数名是 `x1, x2` 这种没有语义的。

**做法**：包一层显式关键字函数，把位置参数变成有名字的：

```python
def check_range(low, high, value, /):     # 原接口
    ...

def check(value, low=0.0, high=100.0):    # 适配
    """判定 value 是否落在 [low, high]。"""
    return check_range(low, high, value)
```

探针会把原接口标成「不可接入」。适配后重新探测，平台只看见新接口。

`**kwargs` 可以只绑显式声明的参数；`*args` 一个都不传——需要可变参数时同样包一层。

---

## 6. 桌面 GUI（MVC / Qt / Tk）

**特征**：`main.py` 一跑就开窗口，逻辑在 controller 里，取数在 model 里。

**做法**：只接 **model + 无头 controller**，不 import 视图。若 controller 依赖视图，加一个无头适配：

```python
# platform_api.py —— 不进 GUI 包，直接引 model
from desktop_app.model import DeviceModel
from desktop_app.source import read_channels

class Diagnosis:
    def __init__(self, port: str):
        self.model = DeviceModel(port)      # 只依赖数据层

    def snapshot(self, channel: str):
        return read_channels(self.model.port, channel)

    def judge(self, data):
        """与界面上那个按钮同一套判据。"""
        return {"result": max(data) > self.model.limit,
                "reason": f"峰值 {max(data)} 超过 {self.model.limit}"}
```

要点：

- 适配模块**不能** `import` 会创建 `QApplication` / `Tk()` 的模块；探针 import 时会卡住或弹窗
- 判据必须复用界面的同一份逻辑，不要复制一份阈值——两份迟早不一致
- Tk Canvas 的绘制必须有窗口，平台进程里无头渲染不出来。曲线把数值数组交给 `view: line`；确实要图片就另用 PIL / matplotlib（`Agg` 后端）重画一份，不要试图对 Canvas 截图
- 需要人看图像时，把渲染函数抽出来单独调用，返回 PNG 字节给 `node_sdk.save_file`

**Qt 例外：窗口本身就是展示物。** 当图里必须同时有基类画的坐标轴/网格/水印和子类叠加的曲线/表格时，另画一份无法保证像素一致，正确做法是在离屏 `QApplication` 里构造原窗口再 `grab()`——这仍然只碰展示层，不算把界面拖进业务逻辑。

```python
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # 必须在 import Qt 之前

_app = None
def _app():
    global _app
    if _app is None:
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        # offscreen 枚举不到系统字体，不注册的话中文全是方块
        for path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
            if Path(path).is_file():
                QFontDatabase.addApplicationFont(path)
    return _app

def window_png(widget):
    _app()
    pixmap = widget.grab()       # 会触发 paintEvent 与子类的 overlay
    buffer = QBuffer(); buffer.open(QBuffer.WriteOnly)
    pixmap.save(buffer, "PNG")
    return node_sdk.save_file(bytes(buffer.data()), "png")
```

- `QApplication` 是全局单例，**不要挂在业务对象上**（不可 pickle），放模块级懒加载
- 每次 `grab()` 几十毫秒，同一张图别在每个节点重复渲染；能复用的结果先在对象里缓存
- 容器或精简系统没有中文字体时，注册 Noto CJK / 文泉驿，否则图里全是方块
- 本地自测图片接口时没有 `node_sdk`：临时 stub 一个 `save_file(bytes, ext)`（写文件 + 返回 sha256 摘要）即可，正式冒烟由平台注入真实实现

**图层验收。** 展示物的语义是「窗口里看到的画面」时，导出图必须包含所有绘制层。自检方法：把基类的绘制钩子临时置空再导出一次，图应明显变化（坐标轴、水印消失）；如果两次完全一样，说明你另画了一份，基类那层已经丢了。

参考 `examples/desktop-mvc/`。

---

## 7. CLI 工具

**特征**：只有命令行入口，输出文本或文件。

**做法**：包装成函数，用 `subprocess` 调用，强制 JSON 输出；不要让子进程读 stdin。

```python
import json, subprocess

def analyze(path: str, mode: str = "fast"):
    """跑分析工具，返回结构化结果。"""
    completed = subprocess.run(
        ["analyzer", "--json", "--mode", mode, path],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip()[-500:] or "分析失败")
    return json.loads(completed.stdout)
```

要点：

- 显式 `timeout`；平台默认自动步骤超时 120 秒，超时按执行报错处理
- 不要用 shell 拼接参数（中文路径、空格都会出事）
- 工具只输出文本时，在适配层解析成数组 / `{columns, rows}`，别把原始 stdout 丢给展示槽
- 退出码非 0 要抛异常，不要返回空列表——空列表会被当成「正常但没数据」

---

## 8. HTTP 服务

**特征**：有 REST/HTTP 接口，业务在远端。

**做法**：适配层发请求，把响应压成展示/判定需要的形状。

```python
import requests

def fetch_series(device: str, channel: str, points: int = 24):
    response = requests.get(
        f"{BASE}/series",
        params={"device": device, "channel": channel, "points": points},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["values"]
```

要点：

- 每个方法都要 `timeout`，否则平台侧只能等自动步骤超时
- 用参数做缓存键：同样的 GET 会被平台缓存，别在函数内部再加一层时间相关逻辑
- 远端返回的时间戳/枚举要转成 JSON 基础类型；`datetime` 对象不能直接进展示块

---

## 9. 非 Python 工程（Java / Node / C++ / MATLAB）

**特征**：业务不在 Python 里，无法直接 import。

**做法**：写一个 Python 桥接工程（这就是要登记的那个工程），二选一：

- **进程桥**：`subprocess` 调可执行文件 / `java -jar` / `node script.js`，约定 JSON 进出
- **服务桥**：对方起 HTTP 服务，Python 侧按第 8 节调用

```python
# bridge.py
import json, subprocess

def run(command: str, payload: dict):
    completed = subprocess.run(
        ["java", "-jar", "engine.jar", command],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip()[-500:])
    return json.loads(completed.stdout)
```

登记的是桥接工程目录，不是对方的源码目录。桥接层要放在版本库里，和对方的版本号一起记；对方升级导致 JSON 字段变化时，桥接层是唯一要改的地方。

---

## 10. 重依赖 / 模型 / GPU

**特征**：import 就要几十秒，或首次调用加载模型。

**做法**：

- 依赖装进**工程登记的那个解释器**，不要装进平台 venv（平台 venv 只装 `backend/requirements.txt`）
- 模块顶层只做轻量 import；模型加载放进类的构造器或懒加载缓存：

```python
class Detector:
    def __init__(self, weights: str):
        self.weights = weights
        self._model = None

    def _load(self):
        if self._model is None:
            import torch                       # 重依赖延后
            self._model = torch.load(self.weights, map_location="cpu")
        return self._model

    def score(self, path: str):
        return float(self._load()(path))
```

- 模型对象不可 pickle。要么把权重路径存进对象、每次用时取（`_model` 加 `__getstate__` 排除），要么实现：

```python
def __getstate__(self):
    state = self.__dict__.copy()
    state["_model"] = None          # 快照不保存模型，恢复后重新加载
    return state
```

- 显存/句柄类资源同理，快照只留配置。

---

## 11. 会写文件、有临时产物

**特征**：分析过程生成图片、报告、缓存文件。

**做法**：

- 临时文件用 `tempfile.mkdtemp()`，调用结束就删；不要写进工程源码目录（实例跑的是源码副本，写进去会污染快照）
- 要给现场看的图，走 `node_sdk.save_file(bytes)` 换成摘要，再由 `view: image` 展示
- 要留档的报告，返回文件路径没有意义——平台只认内容寻址的附件摘要

```python
import node_sdk, tempfile, os

def render(self, data):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "chart.png")
        draw(data, path)
        with open(path, "rb") as fh:
            return node_sdk.save_file(fh.read())
```

---

## 12. 多工程协作

**特征**：数据、算法、判断分属不同团队或不同仓库。

**做法**：一个工程登记一次，一个工程在开始节点建一个对象；跨工程只传 JSON 和文件，业务对象句柄出不了进程。

```
开始：construct center（数据工程）、processor（算法工程）、judge（判断工程）
排查：fetch center → process processor → present processor/renderer → decide judge
```

判断：

- 需要共享同一个**对象**→ 合并成一个工程（同进程）
- 只需要共享**数据**→ 拆开，用 `previous` 在节点内传，或各自重新取数（命中缓存）

参考 `examples/slot-showcase/`：三个完全独立的工程，互不 import。

---

## 选型速查

| 现场情况 | 接法 |
|---|---|
| 函数能直接调，参数有名字 | 直接用 |
| 数据在对象里、跨节点要用 | 构造槽 + 对象名 |
| 只有工厂能造对象 | 构造槽填工厂 |
| async | 加同步门面（线程跑循环） |
| 位置参数 / 无意义参数名 | 包一层显式关键字函数 |
| GUI | 只接 model + 无头判据 |
| CLI | subprocess + JSON |
| HTTP | requests + timeout |
| 非 Python | Python 桥接工程 |
| 模型/GPU | 懒加载 + `__getstate__` 排除 |
| 产物是文件 | `save_file` 换摘要 |
| 多团队 | 多工程 + 只传 JSON |
