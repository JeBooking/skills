"""独立校验：不依赖 NewDiag 仓库，规则对齐 backend/app/services/compiler.py 与 slots.py。

只在找不到平台源码时使用，输出 issues 结构与平台 `validate_doc` 一致
（level / message / nodeId / edgeId）。平台升级校验规则后需要同步这个文件。

它不是「发布级」：平台侧还有签名指纹冻结、源码副本等步骤，这里做不到。
"""

from __future__ import annotations

LEVEL_ERROR = "error"

KINDS = {
    "start",
    "category",
    "investigation",
    "investigationAction",
    "conclusion",
    "remediation",
    "jump",
    "problemSolved",
    "escalation",
}

CONNECTIONS = {
    "start": ["investigation", "category"],
    "category": ["investigation"],
    "investigation": [
        "investigation",
        "category",
        "investigationAction",
        "conclusion",
        "escalation",
        "jump",
    ],
    "investigationAction": ["investigation", "category"],
    "conclusion": ["remediation", "escalation", "jump"],
    "remediation": ["remediation", "problemSolved", "escalation", "jump"],
    "jump": [
        "investigation",
        "category",
        "investigationAction",
        "conclusion",
        "remediation",
        "problemSolved",
        "escalation",
        "jump",
    ],
    "problemSolved": [],
    "escalation": [],
}

TERMINAL_KINDS = {"problemSolved", "escalation"}
SINGLE_SUCCESSOR = {"conclusion", "jump"}
MULTI_TARGET = {"start", "category"}
BRANCHES = ("yes", "no")
DEFAULT_BRANCH = "default"
REQUIRED_TEXT = {"title": "标题", "detail": "说明"}
INVESTIGATION_FIELDS = {"observation": "观测对象", "acquisition": "取数方式", "assessment": "判定依据"}
INPUT_TYPES = ("text", "number", "boolean", "datetime", "file", "array", "object", "json")

SLOT_NAMES = ("constructs", "fetch", "process", "present", "decide")
SLOT_ALLOWED = {
    "start": ("constructs", "fetch", "process"),
    "investigation": ("fetch", "process", "present", "decide"),
}
VIEW_TYPES = ("image", "line", "metrics", "table", "text")

KIND_LABELS = {
    "start": "开始",
    "category": "分类",
    "investigation": "排查",
    "investigationAction": "排查动作",
    "conclusion": "结论",
    "remediation": "处置措施",
    "jump": "跳转",
    "problemSolved": "问题解决",
    "escalation": "上升",
}


def validate(doc, functions=None) -> list[dict]:
    """返回 issue 列表；level=error 阻断发布。functions 为探测结果时核对接口与参数。"""
    issues: list[dict] = []
    if not isinstance(doc, dict):
        return [_issue("文档格式不正确")]

    raw_nodes = doc.get("nodes")
    raw_edges = doc.get("edges")
    nodes = [node for node in raw_nodes if isinstance(node, dict)] if isinstance(raw_nodes, list) else []
    edges = [edge for edge in raw_edges if isinstance(edge, dict)] if isinstance(raw_edges, list) else []

    nodes_by_id: dict[str, dict] = {}
    for node in nodes:
        nid = node.get("id")
        if not nid or not isinstance(nid, str):
            issues.append(_issue("节点缺少 id"))
            continue
        if nid in nodes_by_id:
            issues.append(_issue(f"节点 id 重复：{nid}", node_id=nid))
            continue
        nodes_by_id[nid] = node
    if not nodes_by_id:
        issues.append(_issue("文档至少需要一个排查节点"))
        return issues

    if doc.get("diagnosis") or doc.get("snapshotFields"):
        issues.append(_issue("这是旧格式流程：诊断类与快照字段已移除，请用节点槽位重新编排"))
    for nid, node in nodes_by_id.items():
        kind = node.get("kind")
        if node.get("runtimeInputs") and not node.get("slots"):
            issues.append(_issue("节点现场参数需要先配置槽位", node_id=nid))
        if kind == "dataPreparation":
            issues.append(
                _issue("数据准备节点已移除：取数请配置在开始节点或排查节点的拉取槽", node_id=nid)
            )
        elif kind not in KINDS:
            issues.append(_issue(f"v1 不支持的节点类型：{kind}", node_id=nid))
        legacy = [
            key
            for key in ("binding", "pipeline", "method", "params", "outputs", "operation", "operationArguments")
            if node.get(key)
        ]
        if legacy:
            issues.append(
                _issue(f"节点仍在使用已移除的旧接入字段（{', '.join(legacy)}）：请改为在槽位里选择接口", node_id=nid)
            )
    if doc.get("composition"):
        issues.append(_issue("流程级的对象／资源／操作配置已移除，请改用节点槽位"))

    valid_edges = _check_edges(edges, nodes_by_id, issues)
    shaped_edges = [
        edge for edge in edges if edge.get("from") in nodes_by_id and edge.get("to") in nodes_by_id
    ]
    outgoing = _outgoing_map(nodes_by_id, valid_edges)
    _check_node_fields(nodes_by_id, issues)
    _check_out_edges(nodes_by_id, valid_edges, outgoing, issues)
    entry = _check_start(nodes_by_id, shaped_edges, valid_edges, issues)
    if entry is not None:
        _check_reachability(entry, nodes_by_id, outgoing, issues)
        _check_cycles(entry, outgoing, issues)
    issues.extend(_check_slots(doc, nodes_by_id, functions))
    return issues


# ---------------------------------------------------------------- 连线


def _check_edges(edges: list[dict], nodes_by_id: dict[str, dict], issues: list[dict]) -> list[dict]:
    seen: set[str] = set()
    valid: list[dict] = []
    for edge in edges:
        eid = edge.get("id")
        if not eid or not isinstance(eid, str):
            issues.append(_issue("连边缺少 id"))
            continue
        if eid in seen:
            issues.append(_issue(f"连边 id 重复：{eid}", edge_id=eid))
            continue
        seen.add(eid)

        src, dst = edge.get("from"), edge.get("to")
        if src not in nodes_by_id:
            issues.append(_issue(f"连边起点不存在：{src}", edge_id=eid))
            continue
        if dst not in nodes_by_id:
            issues.append(_issue(f"连边终点不存在：{dst}", edge_id=eid))
            continue

        from_kind = nodes_by_id[src].get("kind", "")
        to_kind = nodes_by_id[dst].get("kind", "")
        if not _can_connect(from_kind, to_kind):
            message = (
                "分类节点的出线只能连到排查节点"
                if from_kind == "category"
                else f"不允许的连接：{_label(nodes_by_id[src])} → {_label(nodes_by_id[dst])}"
            )
            issues.append(_issue(message, edge_id=eid))
            continue
        valid.append(edge)
    return valid


def _can_connect(from_kind: str, to_kind: str) -> bool:
    targets = CONNECTIONS.get(from_kind)
    return bool(targets) and to_kind in targets


def _outgoing_map(nodes_by_id: dict[str, dict], edges: list[dict]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {nid: [] for nid in nodes_by_id}
    for edge in edges:
        src = edge.get("from")
        if src in result:
            result[src].append(edge.get("to"))
    return result


def _check_node_fields(nodes_by_id: dict[str, dict], issues: list[dict]) -> None:
    for nid, node in nodes_by_id.items():
        kind = node.get("kind", "")
        if kind == "start":
            continue
        if kind == "category":
            if not _text(node.get("title")):
                issues.append(_issue("分类节点缺少标题", node_id=nid))
            continue
        for key, label in REQUIRED_TEXT.items():
            if not _text(node.get(key)):
                issues.append(_issue(f"{_kind_label(kind)}缺少{label}", node_id=nid))
        if kind == "investigation":
            fields = node.get("fields")
            fields = fields if isinstance(fields, dict) else {}
            for key, label in INVESTIGATION_FIELDS.items():
                if not _text(fields.get(key)):
                    issues.append(_issue(f"排查节点缺少{label}", node_id=nid))


def _check_out_edges(
    nodes_by_id: dict[str, dict], edges: list[dict], outgoing: dict[str, list[str]], issues: list[dict]
) -> None:
    for nid, node in nodes_by_id.items():
        kind = node.get("kind", "")
        targets = outgoing.get(nid, [])
        out_edges = [edge for edge in edges if edge.get("from") == nid]

        if kind == "start":
            continue
        if kind == "category":
            _check_category_edges(nid, out_edges, issues)
            continue
        if kind in TERMINAL_KINDS:
            for edge in out_edges:
                issues.append(_issue("终止节点不允许有出线", node_id=nid, edge_id=edge.get("id")))
            continue
        if not out_edges:
            issues.append(_issue("节点至少需要一条出线", node_id=nid))
            continue

        if kind == "investigation":
            for edge in out_edges:
                if edge.get("branch") not in BRANCHES:
                    issues.append(
                        _issue("排查节点的出线必须指定是/否分支", node_id=nid, edge_id=edge.get("id"))
                    )
            pairs = [(edge.get("to"), edge.get("branch")) for edge in out_edges]
            for pair in {item for item in pairs if pairs.count(item) > 1}:
                issues.append(_issue(f"重复连接：{pair[0]}（{pair[1]}）", node_id=nid))
        elif kind in SINGLE_SUCCESSOR:
            if len(out_edges) != 1:
                issues.append(_issue(f"{_kind_label(kind)}节点必须恰好一条出线", node_id=nid))
        elif len(_unique(targets)) > 1 and kind not in MULTI_TARGET:
            issues.append(_issue("不支持隐式分叉，请为每个后继单独建节点", node_id=nid))


def _check_category_edges(node_id: str, out_edges: list[dict], issues: list[dict]) -> None:
    if len(out_edges) < 2:
        issues.append(_issue("分类节点至少需要两条出线", node_id=node_id))
    for edge in out_edges:
        if edge.get("branch") not in (None, "", DEFAULT_BRANCH):
            issues.append(_issue("分类节点的出线不能带是/否分支", node_id=node_id, edge_id=edge.get("id")))


def _check_start(
    nodes_by_id: dict[str, dict],
    shaped_edges: list[dict],
    valid_edges: list[dict],
    issues: list[dict],
) -> str | None:
    starts = sorted(nid for nid, node in nodes_by_id.items() if node.get("kind") == "start")
    if not starts:
        issues.append(_issue("缺少开始节点：流程必须有且只有一个开始节点，请新建后连到第一个排查节点"))
        return None
    if len(starts) > 1:
        issues.append(_issue(f"开始节点必须全局唯一，当前有 {len(starts)} 个：{', '.join(starts)}"))

    for nid in starts:
        for edge in shaped_edges:
            if edge.get("to") == nid:
                issues.append(_issue("开始节点不能有入线", node_id=nid, edge_id=edge.get("id")))
        targets = [edge.get("to") for edge in valid_edges if edge.get("from") == nid]
        if not targets:
            issues.append(_issue("开始节点至少需要一条出线", node_id=nid))
        for target in _unique([item for item in targets if targets.count(item) > 1]):
            issues.append(_issue(f"开始节点重复连接到 {target}", node_id=nid))
        _check_start_inputs(nid, nodes_by_id[nid], issues)
    return starts[0]


def _check_start_inputs(nid: str, node: dict, issues: list[dict]) -> None:
    raw = node.get("inputs")
    if raw is None:
        return
    if not isinstance(raw, list):
        issues.append(_issue("开始节点的启动参数格式不正确", node_id=nid))
        return
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            issues.append(_issue("启动参数格式不正确", node_id=nid))
            continue
        name = _text(item.get("name"))
        if not name:
            issues.append(_issue("启动参数缺少名称", node_id=nid))
            continue
        if item.get("type", "text") not in INPUT_TYPES:
            issues.append(_issue(f"启动参数 {name} 的类型不支持", node_id=nid))
        if name in seen:
            issues.append(_issue(f"启动参数 {name} 重复", node_id=nid))
            continue
        seen.add(name)


def _check_reachability(
    entry: str, nodes_by_id: dict[str, dict], outgoing: dict[str, list[str]], issues: list[dict]
) -> None:
    seen = {entry}
    queue = [entry]
    while queue:
        node = queue.pop()
        for nxt in outgoing.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    terminals = {nid for nid, node in nodes_by_id.items() if node.get("kind") in TERMINAL_KINDS}
    if not terminals:
        issues.append(_issue("流程缺少终止节点（问题解决或上升）"))
    for nid in nodes_by_id:
        if nid not in seen:
            issues.append(_issue("节点不可达：从入口无法到达，且到不了终止节点", node_id=nid))


def _check_cycles(entry: str, outgoing: dict[str, list[str]], issues: list[dict]) -> None:
    white, gray, black = 0, 1, 2
    color = {node: white for node in outgoing}
    path: list[str] = []
    stack: list[tuple[str, object]] = [(entry, iter(outgoing.get(entry, [])))]
    color[entry] = gray
    path.append(entry)
    cycles: list[list[str]] = []

    while stack:
        node, iterator = stack[-1]
        descended = False
        for nxt in iterator:  # type: ignore[union-attr]
            if color.get(nxt, black) == white:
                color[nxt] = gray
                path.append(nxt)
                stack.append((nxt, iter(outgoing.get(nxt, []))))
                descended = True
                break
            if color.get(nxt) == gray:
                cycles.append(path[path.index(nxt):])
        if descended:
            continue
        color[node] = black
        stack.pop()
        path.pop()

    reported: set[frozenset[str]] = set()
    for cycle in cycles:
        key = frozenset(cycle)
        if key in reported:
            continue
        reported.add(key)
        if not any(outgoing.get(n, []) and any(t not in key for t in outgoing[n]) for n in cycle):
            issues.append(_issue(f"存在没有出口的环：{' → '.join(cycle)}", node_id=cycle[0]))


# ---------------------------------------------------------------- 槽位


def _check_slots(doc: dict, nodes_by_id: dict[str, dict], functions) -> list[dict]:
    issues: list[dict] = []
    nodes = list(nodes_by_id.values())
    start = nodes_by_id.get(_start_id(doc)) if _start_id(doc) else None

    for message in _shape_errors(doc):
        issues.append(_issue(message))

    objects: dict[str, dict] = {}
    for node in nodes:
        if node.get("kind") != "start":
            continue
        for call in _slots_of(node).get("constructs") or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("object") or "").strip()
            if not name.isidentifier():
                issues.append(_issue(f"构造槽的对象名必须是合法标识符：{name or '（空）'}"))
            elif name in objects:
                issues.append(_issue(f"构造槽的对象名重复：{name}"))
            else:
                objects[name] = call

    for node in nodes:
        kind = node.get("kind")
        slots = _slots_of(node)
        label = f"节点 {node.get('id')}"
        if kind == "investigation" and slots:
            # 完全没有槽位的旧文档按人工节点执行，不强制展示与判定
            if not isinstance(slots.get("present"), dict):
                issues.append(_issue(f"{label} 缺少展示槽"))
            decide = slots.get("decide")
            if not isinstance(decide, dict):
                issues.append(_issue(f"{label} 缺少判定槽"))
            elif not decide.get("human") and not (decide.get("interface") or decide.get("function")):
                issues.append(_issue(f"{label} 的判定槽必须选择方法或人工判断"))
        for name, call in _calls_in_node(node):
            if name != "constructs":
                target = str(call.get("object") or "").strip()
                if target and target not in objects:
                    issues.append(_issue(f"{label} 的{name}槽引用了未声明的对象：{target}"))

    for node in nodes:
        declared = {
            str(item.get("name"))
            for item in node.get("runtimeInputs") or []
            if isinstance(item, dict) and item.get("name")
        }
        for name, call in _calls_in_node(node):
            for value in (call.get("arguments") or {}).values():
                for item in _walk_argument(value):
                    if item.get("source") == "parameter" and item.get("name") not in declared:
                        issues.append(
                            _issue(f"节点 {node.get('id')} 的{name}槽引用了未声明的现场参数 {item.get('name')}")
                        )

    declared_inputs = set()
    for item in (start or {}).get("inputs") or []:
        if isinstance(item, dict) and _text(item.get("name")):
            declared_inputs.add(_text(item.get("name")))
    for node in nodes:
        for name, call in _calls_in_node(node):
            for value in (call.get("arguments") or {}).values():
                for item in _walk_argument(value):
                    if item.get("source") == "input" and item.get("name") not in declared_inputs:
                        issues.append(
                            _issue(
                                f"节点 {node.get('id')} 的{name}槽引用了未在开始节点声明的启动输入 {item.get('name')}"
                            )
                        )

    if functions is not None:
        issues.extend(_check_functions(nodes, functions))
    return issues


def _check_functions(nodes: list[dict], functions: dict) -> list[dict]:
    issues: list[dict] = []
    for node in nodes:
        for name, call in _calls_in_node(node):
            if name == "decide" and call.get("human"):
                continue
            key = _interface_key(call)
            params = functions.get(key)
            if not isinstance(params, list):
                issues.append(_issue(f"接口 {key} 尚未成功探测", node_id=node.get("id")))
                continue
            supplied = set((call.get("arguments") or {}).keys())
            for param in params:
                if param.get("required") and param.get("name") not in supplied:
                    issues.append(
                        _issue(f"节点 {node.get('id')} 的{name}槽缺少必填参数 {param.get('name')}", node_id=node.get("id"))
                    )
            known = {str(p.get("name")) for p in params if not str(p.get("name", "")).startswith("$")}
            keywords = any(
                p.get("name") == "$keywords" and p.get("type") == "yes" for p in params
            )
            if not keywords and supplied - known:
                issues.append(
                    _issue(f"节点 {node.get('id')} 的{name}槽包含接口未声明的参数", node_id=node.get("id"))
                )
    return issues


def _shape_errors(doc: dict) -> list[str]:
    errors: list[str] = []

    def walk(value, owner: str, where: str, allow_previous=True, allow_presentation=False) -> None:
        if not isinstance(value, dict):
            errors.append(f"{owner} 的{where}必须为对象")
            return
        tags = set(value) & {"lit", "source", "items", "fields"}
        if len(tags) != 1:
            errors.append(f"{owner} 的{where}必须且只能指定一种来源")
            return
        if "source" in value:
            source = value.get("source")
            if source == "previous":
                if not allow_previous:
                    errors.append("构造参数不能引用上一槽返回值")
                return
            if source == "presentation":
                if not allow_presentation:
                    errors.append(f"{owner} 的{where}引用了展示结果，但只有判定槽能这样做，且必须先有展示槽")
                return
            if source not in ("input", "parameter"):
                errors.append(f"{owner} 的{where}来源无效：{source}")
                return
            if not isinstance(value.get("name"), str) or not value["name"].strip():
                errors.append(f"{owner} 的{where}缺少名称")
            if "file" in value and not isinstance(value["file"], bool):
                errors.append(f"{owner} 的{where}文件选项必须为布尔值")
        elif "items" in value:
            items = value["items"]
            if not isinstance(items, list):
                errors.append(f"{owner} 的组合列表无效")
            else:
                for item in items:
                    walk(item, owner, where, allow_previous, allow_presentation)
        elif "fields" in value:
            fields = value["fields"]
            if not isinstance(fields, dict):
                errors.append(f"{owner} 的组合字段无效")
            else:
                for item in fields.values():
                    walk(item, owner, where, allow_previous, allow_presentation)

    for node in doc.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        kind = node.get("kind")
        label = f"节点 {node.get('id')}"
        slots = node.get("slots")
        if slots is None:
            continue
        if not isinstance(slots, dict):
            errors.append(f"{label} 的槽位必须为对象")
            continue
        allowed = SLOT_ALLOWED.get(kind)
        for name in slots:
            if name not in SLOT_NAMES:
                errors.append(f"{label} 的槽位名无效：{name}")
            elif allowed is not None and name not in allowed:
                errors.append(f"{label} 不能配置{name}槽")
        constructs = slots.get("constructs")
        if constructs is not None:
            if not isinstance(constructs, list) or not constructs:
                errors.append(f"{label} 的构造槽必须为非空列表")
            else:
                for call in constructs:
                    if not isinstance(call, dict):
                        errors.append(f"{label} 的构造槽必须为对象")
        for name in SLOT_NAMES:
            value = slots.get(name)
            if name == "constructs" or value is None:
                continue
            if not isinstance(value, dict):
                errors.append(f"{label} 的{name}槽必须为对象")
                continue
            if name == "present" and value.get("view") not in VIEW_TYPES:
                errors.append(f"{label} 的展示槽必须选择展示类型")
            if name != "present" and value.get("view"):
                errors.append(f"{label} 只有展示槽能指定展示类型")
            if name == "decide" and value.get("human") and (value.get("interface") or value.get("function")):
                errors.append(f"{label} 的判定槽要么选人工判断，要么选方法")
        for name, call in _calls_in_node(node):
            owner = f"{label} 的{name}槽"
            if not call.get("project"):
                errors.append(f"{owner} 缺少工程")
            if not (call.get("interface") or call.get("function")):
                errors.append(f"{owner} 缺少接口")
            if "useCache" in call:
                if name != "fetch":
                    errors.append(f"{owner} 不支持缓存开关：只有拉取槽会缓存")
                elif not isinstance(call["useCache"], bool):
                    errors.append(f"{owner} 的使用缓存开关必须为布尔值")
            arguments = call.get("arguments")
            if arguments is not None and not isinstance(arguments, dict):
                errors.append(f"{owner} 的参数必须为参数名到来源的对象")
            else:
                presentation = name == "decide" and isinstance(slots.get("present"), dict)
                for key, value in (arguments or {}).items():
                    if not isinstance(key, str) or not key.strip():
                        errors.append(f"{owner} 存在空参数名")
                    walk(value, owner, "参数", name != "constructs", presentation)
        _previous_scope(slots, label, errors)
    return errors


def _previous_scope(slots: dict, label: str, errors: list[str]) -> None:
    def uses(name: str) -> bool:
        call = slots.get(name)
        if not isinstance(call, dict):
            return False
        for value in (call.get("arguments") or {}).values():
            if any(item.get("source") == "previous" for item in _walk_argument(value)):
                return True
        return False

    if uses("process") and not isinstance(slots.get("fetch"), dict):
        errors.append(f"{label} 的处理槽引用了上一槽结果，但本节点没有拉取槽")
    for name in ("present", "decide"):
        if uses(name) and not any(isinstance(slots.get(key), dict) for key in ("fetch", "process")):
            errors.append(f"{label} 的{name}槽引用了上一槽结果，但本节点没有拉取或处理槽")


def _slots_of(node: dict) -> dict:
    slots = node.get("slots")
    return slots if isinstance(slots, dict) else {}


def _calls_in_node(node: dict) -> list[tuple[str, dict]]:
    slots = _slots_of(node)
    result: list[tuple[str, dict]] = []
    constructs = slots.get("constructs")
    if isinstance(constructs, list):
        for call in constructs:
            if isinstance(call, dict):
                result.append(("constructs", call))
    for name in ("fetch", "process", "present", "decide"):
        call = slots.get(name)
        if isinstance(call, dict):
            result.append((name, call))
    return result


def _walk_argument(value):
    if not isinstance(value, dict):
        return
    yield value
    for item in value.get("items") or []:
        yield from _walk_argument(item)
    for item in (value.get("fields") or {}).values():
        yield from _walk_argument(item)


def _interface_key(call: dict) -> str:
    return "{}:{}:{}".format(
        call.get("project", ""),
        call.get("module", ""),
        call.get("interface") or call.get("function", ""),
    )


def _start_id(doc: dict) -> str | None:
    for node in doc.get("nodes") or []:
        if isinstance(node, dict) and node.get("kind") == "start":
            return node.get("id")
    return None


# ---------------------------------------------------------------- 小工具


def _issue(message: str, node_id: str | None = None, edge_id: str | None = None) -> dict:
    item: dict = {"level": LEVEL_ERROR, "message": message}
    if node_id:
        item["nodeId"] = node_id
    if edge_id:
        item["edgeId"] = edge_id
    return item


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _label(node: dict) -> str:
    label = _kind_label(node.get("kind", ""))
    title = _text(node.get("title"))
    return f"{label}「{title}」" if title else label


def _kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
