---
name: newdiag-project-integration
description: 把外部工程接入 NewDiag 诊断流程平台时使用。覆盖任意 Python 工程（函数库、有状态类、GUI、CLI、HTTP 服务）以及 Java/Node/C++ 等非 Python 工程的桥接层：探明真实接口、改造不可接入的形态、生成可发布的 workflow.json、按发布口径校验、用生成代码跑冒烟、登记工程并交付接入说明。触发词：接入工程、接入模块、把工程接到平台、把项目接进来、生成 workflow.json、槽位配置、workflow 接入、onboard project、integrate project。
agent_created: true
---

# NewDiag 工程接入

平台把每个节点摊成固定几个槽位，每个槽位填一个外接工程的公开方法，然后生成 Python 代码执行。接入的本质是：**让业务工程的某些公开方法，恰好长成槽位能调用的样子**，再用一份 `workflow.json` 把它们编成诊断流程。

这个 skill 的价值在于三级机器验证——接口探明、发布级校验、生成代码真跑——每一步都调用平台自己的实现，不靠记忆和猜测。

## 交付物

1. `workflow.json`（`format: 1`），能通过发布校验
2. `projects.json` 工程登记表：`name` / `path` / `module` / `python`
3. 必要的适配模块（放在业务工程目录里，不碰原逻辑）
4. 验证记录：探针输出、校验输出、冒烟报告
5. 接入说明：登记名、目录、模块、解释器、启动参数、冒烟走过的路径、未覆盖的分支

## 先判断能不能接

三条硬要求，缺一条就要改造（做法见 `references/integration-patterns.md`）：

| 要求 | 不满足的典型 | 改法 |
|---|---|---|
| 模块能在目标解释器里 import，且 import 无副作用 | 一 import 就开窗口、起服务、等 stdin | 挪进函数或 `if __name__`；GUI 只接 model |
| 接口同步、参数能按名字传 | `async def`、位置专用参数、`*args` | 加同步门面 / 显式关键字包装 |
| 返回值能 JSON 化 | 返回对象、`datetime`、句柄 | 转基础类型；图片走 `node_sdk.save_file` |

工程里**没有**这些问题的部分不要改。接入层优先新增一个 `platform_api.py`，登记时 `module` 填它。

## 流程

### 0. 定位

`scripts/` 下三个脚本会自动判断本机有没有 NewDiag 仓库（`NEWDIAG_ROOT` → skill 位置 → 当前目录向上）：

| 模式 | 触发 | 能力 |
|---|---|---|
| 平台模式 | 找到仓库且能 import `backend` | 探针、发布级校验、生成代码冒烟，结论与平台发布一致 |
| 独立模式 | 找不到仓库，或依赖没装 | 内置探针 + 内置结构校验；冒烟明确跳过 |

独立模式不需要平台源码，靠 `scripts/builtin_probe.py` 与 `scripts/standalone.py` 里的内置规则（对齐 v1.19）。它**不冒充发布级**：输出会标注模式，拿到源码后应重跑一次；这两份内置规则也要随平台升级同步。

在其他仓库里工作时设一次环境变量，可强制进入平台模式：

```bash
export NEWDIAG_ROOT="D:/FILE/Projects/NewDiag"       # bash
$env:NEWDIAG_ROOT = "D:\FILE\Projects\NewDiag"        # PowerShell
```

也可以给任一脚本传 `--root`（显式指定就必须成功，否则报错，不会静默降级）。

工程登记表里的 `path` 支持 `@newdiag/...` 前缀，表示相对平台仓库根（`assets/projects.example.json` 就用它指向仓库自带的示例工程）；独立模式下没有仓库根，换成绝对路径。

命令里的解释器统一用项目 venv：Windows `backend/.venv/Scripts/python.exe`，Linux/macOS `backend/.venv/bin/python`。`smoke_run.py` 需要它（要用到 SQLAlchemy）。

### 1. 读工程，不要读文件名就下结论

看三样：包的入口（`__init__.py` / `__main__.py`）、对外暴露的类与函数、依赖声明（`requirements.txt` / `pyproject.toml`）。

判定形态：纯函数 / 有状态类 / 工厂 / async / GUI / CLI / HTTP / 非 Python。这一步决定第 3 步改什么。

### 2. 探针：拿到真实接口

```bash
<python> scripts/probe_project.py --path <工程目录> --module <模块> [--python <解释器>] [--json out.json]
```

输出按「可构造 / 模块级函数 / 实例方法」分组，标出不可接入项与必填参数。**接口名、参数名、默认值一律以这里为准**，不要从源码里猜，更不要照抄旧文档。

模块导入失败时先修导入，不要往下走。探针报错见 `references/troubleshooting.md` A 节。

### 3. 改造（只在必要时）

按第 1 步的形态选模式。几条通用约定：

- 适配层是新文件，原函数不改；判据必须复用界面/原流程的同一份逻辑
- 对象上不挂连接、锁、窗口、线程、文件句柄；需要用就调用时临时开
- 重依赖（模型、GPU）懒加载，并在 `__getstate__` 里排除
- 改完**重跑探针**，以新接口为准

### 4. 写 workflow.json

从 `assets/workflow-minimal.json` 复制骨架，替换四处：`project` / `module` / `interface` / `function`，再改 `inputs`、`fields`、标题。

设计顺序（不要先画框）：

1. 一台设备上要判断几件事 → 每件事一个排查节点
2. 每件事看什么 → `fields.observation` + present 的接口
3. 怎么算异常 → `fields.assessment` + decide 的接口（算不出来就 `human: true`）
4. 数据从哪来 → 每个节点自己的 fetch
5. 异常之后做什么 → 结论 → 处置 → 问题解决 / 上升

字段、参数来源、返回形状、连线规则：`references/workflow-format.md`。

### 5. 三级验证，循环到全绿

```bash
# ① 发布级校验：结构 + 连线 + 槽位 + 接口 + 必填参数
<python> scripts/validate_workflow.py workflow.json --projects projects.json

# ② 生成代码冒烟：真的执行，自动应答人工待办
<python> scripts/smoke_run.py workflow.json --projects projects.json \
         [--input k=v] [--params 节点.参数=v] [--decisions 节点=yes|no]
```

冒烟默认：启动输入取开始节点的 `default`，人工判断答 `yes`，现场参数取 `default` 或按类型兜底，问题解决后结束剩余思路。要走别的分支：

- 改 `--input`（阈值这类自动判定，只能靠输入改变结果；`--decisions` 管不到自动判定）
- `--decisions 节点=no`，或 `节点=no,yes` 按到访顺序取值

**通过标准**：到达终止节点，且 `errors` 为空。有执行错误但别的分支跑到了终止，仍算未通过——这正是要抓的接口/形状问题。

### 6. 登记与交付

登记（二选一）：

```bash
# API；name 登记后不可改，取稳定的业务名
curl -X POST http://127.0.0.1:8000/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name":"数据源工程","path":"D:/proj/data","module":"feeder","python":"D:/proj/.venv/Scripts/python.exe"}'
```

或在平台「工程管理」页登记同样的四个字段。Windows 下中文路径建议把 body 写成 UTF-8 文件，用 `--data-binary @project.json`。

导入流程（body 是 `{"doc": <workflow.json 的内容>}`）：

```bash
python -c "import json,pathlib; d=json.loads(pathlib.Path('workflow.json').read_text(encoding='utf-8')); pathlib.Path('payload.json').write_text(json.dumps({'doc': d}, ensure_ascii=False), encoding='utf-8')"
curl -X POST http://127.0.0.1:8000/api/flows/import \
  -H "Content-Type: application/json" --data-binary @payload.json
```

交付说明里写清：登记名、目录、模块、解释器、启动参数默认值、冒烟走过的节点路径、**没有覆盖的分支**（以及怎么走）。

## 硬规则

1. **接口不猜**：任何 `interface` / `function` / 参数名，都必须出现在探针输出里
2. **有平台时三级验证缺一不可**：probe → validate → smoke，任一没过就不算接完。独立模式只能做到结构预检，接入平台后必须补冒烟
3. **跨节点不传值**：`previous` 只指本节点上一步。下个节点要数据就自己 fetch（命中缓存）
4. **present 的 view 与返回形状必须一致**：同一方法只能配一种 view
5. **一个槽位只填一个调用**：要串联就分开填 fetch / process / present / decide
6. **不改平台代码**：`backend/` 是平台。需要新能力先改 spec 再动手，不在接入任务里顺手改
7. **不动业务逻辑**：适配层只做形态转换，阈值、判据、算法必须复用原实现
8. **工程名不可改**：它是槽位与签名的引用键，改名会让已发布流程找不到实现
9. **源码副本语义**：实例跑的是发布版本的源码快照，改原工程只影响新发布的版本
10. **图片只认摘要**：业务不能返回路径或 URL，必须 `node_sdk.save_file(bytes)`

## 命令速查

| 目的 | 命令 |
|---|---|
| 看接口 | `scripts/probe_project.py --path <dir> --module <mod>` |
| 校验流程 | `scripts/validate_workflow.py <workflow.json> --projects <projects.json>` |
| 真跑一遍 | `scripts/smoke_run.py <workflow.json> --projects <projects.json>` |
| 只校验结构 | 加 `--no-probe` |
| 看槽位调用清单 | 校验命令默认打印，一行一个槽位 |
| 保留冒烟现场 | 加 `--keep`（或 `--dir <目录>`） |
| 机器可读结果 | 加 `--json out.json` |

## 排错

`references/troubleshooting.md` 按阶段分五节：接口发现、校验发布、运行时、结果不对、登记导入。先跑第 5 步定位阶段，再按节查。

`references/integration-patterns.md` 是十二种工程形态的改法与代码骨架；`references/workflow-format.md` 是字段与校验的完整口径。
