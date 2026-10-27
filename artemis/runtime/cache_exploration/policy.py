"""Fail-closed navigation policy. Coordinates are derived from observed nodes only."""

import hashlib
import re
import xml.etree.ElementTree as ET

DENY = re.compile(
    r"删除|发送|分享|购买|支付|付款|充值|订阅|提交|保存|录音|录制|更新|安装|卸载|退出|注销|修改|编辑|重置|清空|清除|授权|允许|登录|注册|绑定|解绑|兑换|邀请|创建|新建|添加|启用|停用|开通|确认|确定|同意|同意|delete|send|share|buy|purchase|pay|subscribe|submit|save|record|update|install|logout|sign.?in|sign.?up|reset|clear|allow|approve|edit|create|add|enable|disable|confirm|accept",
    re.I,
)
NAV_LABEL = re.compile(
    r"^(首页|主页|我的|设置|关于(?:本机|我们|手机)?|帮助(?:中心)?|最近|所有|发现|浏览|日历|文件夹|返回|搜索|连接|已连接的设备|通知|隐私|home|settings|about(?: us)?|help|recent|all|discover|browse|calendar|folders|back|search|profile|notifications)$",
    re.I,
)
SENSITIVE = re.compile(
    r"密码|验证码|登录|支付|付款|授权|允许|password|verification code|sign.?in|log.?in|payment|permission",
    re.I,
)


def truth(value):
    return value is True or str(value).lower() == "true"


def bounds(value):
    numbers = [int(n) for n in re.findall(r"-?\d+", str(value))]
    return numbers if len(numbers) == 4 else None


def unsafe_window(xml):
    try:
        root = ET.fromstring(xml or "")
    except ET.ParseError:
        return True
    for node in root.iter():
        a = node.attrib
        if any(kind in a.get("class", "") for kind in ("Dialog", "PopupWindow")) or a.get(
            "resource-id"
        ) in {
            "android:id/alertTitle",
            "android:id/parentPanel",
            "android:id/button1",
            "android:id/button2",
            "android:id/button3",
        }:
            return True
        label = a.get("text", "") + " " + a.get("content-desc", "")
        if "EditText" in a.get("class", "") and (
            truth(a.get("password")) or SENSITIVE.search(label)
        ):
            return True
        if ("Dialog" in a.get("class", "") or truth(a.get("clickable"))) and SENSITIVE.search(
            label
        ):
            return True
    return False


def elements(xml, package, size):
    try:
        root = ET.fromstring(xml or "")
    except ET.ParseError:
        return []
    result = []

    def visit(node, ancestors):
        a = node.attrib
        inherited = " ".join(x.get("text", "") + " " + x.get("content-desc", "") for x in ancestors)
        b = bounds(a.get("bounds", ""))
        if (
            a.get("package") == package
            and b
            and 0 <= b[0] < b[2] <= size[0]
            and 0 <= b[1] < b[3] <= size[1]
            and not truth(a.get("password"))
            and not any(
                item.get(key) in (False, "false")
                for item in [*ancestors, a]
                for key in ("enabled", "visible", "visible-to-user")
            )
        ):
            label = (a.get("text") or a.get("content-desc") or "").strip()
            kind = a.get("class", "")
            forbidden = (
                bool(DENY.search(label + " " + inherited))
                or any(
                    value in kind
                    for value in (
                        "Switch",
                        "CheckBox",
                        "SeekBar",
                        "EditText",
                        "Toggle",
                        "RatingBar",
                    )
                )
                or truth(a.get("checkable"))
                or a.get("role") in {"slider", "seekbar", "adjustable"}
                or any(key in a for key in ("range-info", "rangeInfo", "aria-valuenow"))
            )
            nav_evidence = bool(NAV_LABEL.fullmatch(label)) and (
                truth(a.get("clickable")) or any(truth(p.get("clickable")) for p in ancestors[-1:])
            )
            # A container named "navigation" is not proof of an unknown child's semantics.
            scroll = truth(a.get("scrollable")) and not forbidden
            if not forbidden and (nav_evidence or scroll):
                result.append(
                    {
                        "ref": len(result) + 1,
                        "label": label if nav_evidence else "scrollable list",
                        "bounds": b,
                        "navigation": bool(nav_evidence),
                        "scrollable": scroll,
                        "refreshable": any(
                            "refresh" in p.get("class", "").lower() for p in ancestors
                        ),
                        "resource_id": a.get("resource-id", ""),
                    }
                )
        for child in node:
            visit(child, [*ancestors, a])

    visit(root, [])
    return result[:100]


def permit(candidate, nodes, size, can_back, *, allow_down=False):
    if not isinstance(candidate, dict):
        return None
    action = candidate.get("action")
    if action == "back" and set(candidate) == {"action"}:
        return {"action": "back"} if can_back else None
    if action == "navigate" and set(candidate) == {"action", "element_ref"}:
        if type(candidate["element_ref"]) is not int:
            return None
        item = next((n for n in nodes if n["ref"] == candidate["element_ref"]), None)
        if item and item["navigation"]:
            x1, y1, x2, y2 = item["bounds"]
            return {
                "action": action,
                "x": (x1 + x2) // 2,
                "y": (y1 + y2) // 2,
                "key": hashlib.sha256(
                    (item["resource_id"] + item["label"] + str(item["bounds"])).encode()
                ).hexdigest(),
            }
    if action == "scroll" and set(candidate) == {"action", "element_ref", "direction"}:
        if type(candidate["element_ref"]) is not int:
            return None
        item = next((n for n in nodes if n["ref"] == candidate["element_ref"]), None)
        if item and item["scrollable"] and candidate["direction"] in {"up", "down"}:
            if candidate["direction"] == "down" and (not allow_down or item.get("refreshable")):
                return None
            x1, y1, x2, y2 = item["bounds"]
            x, height = (x1 + x2) // 2, y2 - y1
            # Keep gestures well within a verified list, away from pull-to-refresh and system edges.
            if height > size[1] * 0.2 and size[0] * 0.05 < x < size[0] * 0.95:
                top = max(int(size[1] * 0.05), int(y1 + height * 0.35))
                bottom = min(int(size[1] * 0.9), int(y1 + height * 0.7))
                if top >= bottom:
                    return None
                start, end = (bottom, top) if candidate["direction"] == "up" else (top, bottom)
                return {
                    "action": action,
                    "x": x,
                    "start": start,
                    "end": end,
                    "key": "scroll:" + str(item["bounds"]) + candidate["direction"],
                    "container_key": str(item["bounds"]),
                    "direction": candidate["direction"],
                }
    return None
