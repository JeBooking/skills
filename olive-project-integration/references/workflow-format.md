# workflow.json 字段参考

平台导入只认 `format: 1` 的编辑投影；库里的 `format: 2` 分层格式是存储格式，导入时会被转换。写文件时用本文的形态即可。

```json
{
  "format": 1,
  "name": "流程名",
  "nodes": [ ... ],
  "edges": [ ... ]
}
```

`nodes` 与 `edges` 必须是数组。节点 id 自己起，稳定即可（回退、运行记录都按它对账）。

---

## 1. 节点通用字段

| 字段 | 必填 | 说明 |
|---|---|---|
| id | 是 | 唯一字符串 |
| kind | 是 | 见第 2 节 |
| title | 除 start 外必填 | 节点标题；分类节点的选项文案也取它 |
| detail | 除 start / category 外必填 | 面向现场人的说明 |
| position | 否 | `{x, y}`；缺省按顺序排 |
| fields | 仅 investigation 必填 | `observation` / `acquisition` / `assessment` 三项都要非空 |
| inputs | 仅 start | 启动参数声明，见第 5 节 |
| slots | 见第 3 节 | 槽位 |
| runtimeInputs | 槽位用了 `parameter` 时必填 | 现场参数声明 |

`detail` 不是备注，它会显示在待办面板里；写「现场该看什么、做完什么算完成」，不要写实现细节。

排查节点的 `fields` 是给人看的排查逻辑，三句话：

```json
"fields": {
  "observation": "轴承振动曲线",
  "acquisition": "DataCenter.fetch(channel=vibration)",
  "assessment": "峰值超过 6 mm/s 判为异常"
}
```

---

## 2. 节点类型

| kind | 名称 | 要不要槽位 | 出线 |
|---|---|---|---|
| start | 开始 | 可选：`constructs` / `fetch` / `process` | 多条，全 `default` |
| category | 分类 | 不许有 | ≥2 条，全 `default`，目标只能是 investigation |
| investigation | 排查 | `present` + `decide` 必填，`fetch` / `process` 可选 | yes / no 两组 |
| investigationAction | 排查动作 | 不许有 | 一条，接排查或分类 |
| conclusion | 结论 | 不许有 | 恰好一条，接处置 / 上升 / 跳转 |
| remediation | 处置措施 | 不许有 | 一条或多条，接处置 / 问题解决 / 上升 / 跳转 |
| jump | 跳转 | 不许有 | 恰好一条，接除开始外任意节点 |
| problemSolved | 问题解决 | 不许有 | 无（终止） |
| escalation | 上升 | 不许有 | 无（终止） |

自动模式下分类节点等于不存在：平台把它的后继并回前驱，不产生执行记录。要保留「由人选一条思路」的语义就用手动模式启动。

---

## 3. 槽位

每种节点能填的槽是固定的，填错槽位名直接报错。

```json
"slots": {
  "constructs": [
    {"object": "center", "project": "数据源工程", "module": "feeder",
     "interface": "DataCenter", "function": "DataCenter",
     "arguments": {"dataset": {"source": "input", "name": "dataset"}}}
  ],
  "fetch":   {"object": "center", "project": "...", "module": "...",
              "interface": "DataCenter.fetch", "function": "fetch", "arguments": {}},
  "process": {"object": "center", "...": "...", "arguments": {"raw": {"source": "previous"}}},
  "present": {"object": "renderer", "...": "...", "view": "line",
              "arguments": {"data": {"source": "previous"}}},
  "decide":  {"object": "judge", "...": "...", "arguments": {"data": {"source": "previous"}}}
}
```

| 字段 | 说明 |
|---|---|
| object | 构造槽是给新对象起的名字；其余槽是目标对象名。留空表示调模块级函数 |
| project | 工程登记名，必须与平台工程页完全一致 |
| module | 该工程里可导入的模块名 |
| interface | 接口目录里的完整名，探测与校验按它匹配。实例方法必须写成 `类.方法` |
| function | 末级名，实例方法取最后一段；填错会调不到 |
| view | 只有 present：`image` / `line` / `metrics` / `table` / `text` |
| human | 只有 decide：`true` 表示人工判断，此时不要再填接口 |
| useCache | 只有 fetch：`false` 表示跳过缓存重取，取完仍写回缓存 |
| arguments | 参数名 → 来源表达式 |

槽位链只在节点内生效：`构造 → 拉取 → 处理 → 展示 → 判定`。跨节点不传值，下一个节点要数据就自己取（同参数命中缓存），或从对象里读。

开始节点的 `fetch` / `process` 用途是「把数据装进对象」，返回值不往后传。

**`previous` 不是必需的。** 展示与判定可以直接调对象方法，参数从对象、启动输入或现场参数取；只有确实要消费本节点上一步的结果时才用 `previous`。

典型情况：拉取返回 `{columns, rows}`，而 `table(limit)` 只收一个整数，类型接不上。此时展示槽用**相同参数再调一次**即可——取数结果已按参数缓存，重复到访不会再打数据源。

---

## 4. 参数来源

```json
{"lit": 210}
{"source": "input", "name": "device"}
{"source": "parameter", "name": "threshold"}
{"source": "previous"}
{"source": "presentation"}
{"items": [{"source": "previous"}, {"lit": 0}]}
{"fields": {"x": {"lit": 1}, "y": {"source": "input", "name": "y"}}}
```

| 来源 | 能用在哪 | 说明 |
|---|---|---|
| lit | 任何槽 | JSON 固定值；数组、对象直接写 |
| input | 任何槽 | 开始节点声明的启动参数；名字必须一字不差 |
| parameter | 任何槽 | 现场参数；必须在本节点 `runtimeInputs` 声明 |
| previous | process / present / decide | 本节点上一步的结果。process 要求有 fetch；present、decide 要求有 fetch 或 process |
| presentation | 只有 decide | 本次展示槽的返回值；节点必须已配 present |

附加修饰：

- `"path": "a.b[0]"` 下钻字段
- `"file": true` 把上传引用换成本地路径；`"file": false` 保留 `{hash, name}`
- `items` / `fields` 递归组合，元素同样是表达式

构造参数不能引用 `previous`；跨节点也没有 `previous` 可用。

---

## 5. 开始节点的启动参数

```json
"inputs": [
  {"name": "device", "type": "text", "default": "M-1"},
  {"name": "threshold", "type": "number"},
  {"name": "samples", "type": "array", "default": [1, 2, 3]},
  {"name": "report", "type": "file"}
]
```

`type`：`text` / `number` / `boolean` / `datetime` / `file` / `array` / `object` / `json`。

- 名字不能重复
- 有 `default` 时执行页预填，可以改；没默认且被必填参数引用时，启动会要求填写
- 数组、对象、JSON 会解析成原生 Python 结构；时间保持字符串
- 文件上传后传 `{hash, name}`，槽位里默认解析成本地绝对路径

现场参数（`runtimeInputs`）结构相同，只是执行到该节点时才问：

```json
"runtimeInputs": [
  {"name": "channel", "type": "text", "required": true},
  {"name": "points", "type": "number", "required": false, "default": 12}
]
```

**路径没有相对写法。** `text` 类型的默认值写死绝对路径，换机器或搬目录就失效。长期使用的流程二选一：

- 文件用 `type: "file"`，执行时上传，平台把 `{hash, name}` 换成服务器本地路径
- 或者默认值留空、`required: false`，由执行页每次填写

---

## 6. 展示类型的返回契约

| view | 方法必须返回 | 说明 |
|---|---|---|
| text | 字符串，或可 JSON 化的对象 | 对象按缩进 JSON 显示 |
| line | 非空数字数组 | `[1, 2, 3]`；布尔不算数字 |
| metrics | `[{"label": "...", "value": ..., "unit": "可选"}]` | 指标卡 |
| table | `{"columns": ["a", "b"], "rows": [[1, 2], [3, 4]]}` | 每行长度必须等于列数 |
| image | 附件摘要字符串（64 位十六进制 sha256） | 先调 `node_sdk.save_file(bytes)`；不接受路径或 URL |

判定槽接受：`True` / `False`、`"yes"` / `"no"`、`{"result": bool, "reason": "..."}`。给了 `reason` 会写进执行轨迹。

图片是唯一需要业务代码感知平台的场景：

```python
import node_sdk

def chart(self, data):
    return node_sdk.save_file(render_png(data))   # 返回 64 位摘要
```

`node_sdk` 由平台在业务进程里注入，不需要安装，也不要写进 requirements。

---

## 7. 连线

```json
{"id": "e1", "from": "n1", "to": "n2", "branch": "yes", "order": 1}
```

- 排查节点的每条出线必须有 `branch: "yes"` 或 `"no"`；同组可以多条，`order` 小的先执行
- 其余节点一律 `default`（不写 `branch`）
- 开始节点可以同时连多条，视为多条并列思路
- 分类节点的 `order` 就是选项顺序

目标类型限制：

```
start              → investigation / category
category           → investigation
investigation      → investigation / category / investigationAction / conclusion / escalation / jump
investigationAction→ investigation / category
conclusion         → remediation / escalation / jump
remediation        → remediation / problemSolved / escalation / jump
jump               → 除 start 外任意
problemSolved      → 无
escalation         → 无
```

---

## 8. 发布必过的校验

按这套顺序自查，能省掉大半来回：

1. 恰好一个开始节点，无入边、至少一条出线；开始节点不能连到同一个目标两次
2. 连线符合第 7 节；排查的出线都有 branch
3. 除 start 外都有 title + detail；排查另外要 fields 三项
4. 结论恰好一条出线；跳转恰好一条
5. 每条路径都能到终止节点；不允许死路、不可达节点、没有出口的环
6. 构造槽的 object 合法（标识符）且唯一；没有构造槽时所有槽只能是模块级函数
7. 槽位引用的接口探测得到，且同步、支持关键字参数
8. 参数来源合法；`parameter` 已声明；`previous` 用在允许的槽且上游存在
9. 排查必须有 present（带 view）与 decide（方法或 `human: true`）
10. 启动参数名字不重复、类型受支持；`input` 引用必须在开始节点声明
11. 分类节点：有 title、出线 ≥2 且全 default、目标只能是排查

`validate_workflow.py` 会把这些连同接口签名一起跑一遍，报错文案与平台发布时一致。

---

## 9. 设计顺序

从 OCAP 文档落到图，按这个顺序想，不要先画框：

1. **一台设备上要判断几件事**：每件事一个排查节点，标题用现象（「轴承振动超限」「母线电压不足」）
2. **每件事看什么**：`fields.observation`；看的东西由哪个方法产出，就是 present 的接口
3. **怎么算异常**：`fields.assessment`；能写成函数就写判定接口，写不出来就 `human: true`
4. **数据从哪来**：每个排查节点自己填 fetch，参数用启动输入或现场参数；同参数自动命中缓存
5. **异常之后做什么**：结论（说清问题）→ 处置（现场动作）→ 问题解决 / 上升
6. **对象什么时候建**：要跨节点保留的状态放构造槽；纯函数工程可以不填构造槽

常见错误是先画一条长链，再想办法把数据从上一个节点传下去。平台不支持跨节点传值，这类设计要在取数处重取或从对象读。

---

## 10. 可直接改的骨架

`assets/workflow-minimal.json` 是六节点最小流程（开始 → 排查 → 结论 → 处置 → 问题解决 + 上升），配套 `assets/projects.example.json` 能直接跑通。接到新工程时：

1. 复制两份文件到工作目录
2. 把 `project` / `module` / `interface` / `function` 换成探针输出的真实值
3. 改 `inputs`、`fields` 与标题文案
4. `validate_workflow.py` 通过后再 `smoke_run.py`

完整能力样例见 `examples/slot-showcase/workflow.json`：五种展示、人工判断、现场参数、多对象、缓存、跳转、并行分支都在里面。
