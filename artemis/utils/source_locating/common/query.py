"""把短定位描述还原为资源名称，并保留能从当前 Android 节点验证的限制。

```text
「点击顶部的搜索按钮」
  → 去掉一次明确的点击指令
  → 取出位置前缀「顶部」和角色后缀「按钮」
  → 用「搜索」查资源，用「顶部」「按钮」核对真机节点
```

「按钮」在成对引号内时属于控件原名。无法识别的修饰词保持原样，防止删掉有业务含义的字词。
调用方仍会保留原始查询，优先匹配完整名称。
"""

from dataclasses import dataclass
import re


# 只去掉明确的点击／打开指令。「取消」「删除」等词可能就是按钮名，不能作为动作前缀剥离。
_ACTION = re.compile(
    r"^(?:请?(?:点击|点按|轻触|轻点|打开)\s*|(?:please\s+)?(?:click|tap|open)\s+(?:the\s+)?)", re.I
)

# 通用控件名词映射到本模块内部角色；英文单词边界由匹配表达式约束，避免截断资源 ID。
_ROLES = {
    "按钮": "button",
    "button": "button",
    "开关": "switch",
    "switch": "switch",
    "toggle": "switch",
    "输入框": "input",
    "input": "input",
    "text field": "input",
    "入口": "entry",
    "entry": "entry",
}
_ROLE_SUFFIX = re.compile(
    r"(按钮|开关|输入框|入口|\s+(?:button|switch|toggle|input|text field|entry))$", re.I
)

# 仅消费开头的屏幕区域。列表项、弹窗等容器描述保留在名称内，无法证实时交回常规定位。
_REGIONS = {
    "顶部": "top",
    "底部": "bottom",
    "左上角": "top-left",
    "右上角": "top-right",
    "左下角": "bottom-left",
    "右下角": "bottom-right",
    "top": "top",
    "bottom": "bottom",
    "top-left": "top-left",
    "top-right": "top-right",
    "bottom-left": "bottom-left",
    "bottom-right": "bottom-right",
}
_REGION_PREFIX = re.compile(
    r"^(?:(左上角|右上角|左下角|右下角|顶部|底部)的?\s*|"
    r"(top[- ]left|top[- ]right|bottom[- ]left|bottom[- ]right|top|bottom)\s+)",
    re.I,
)
# 只接受成对包裹整个名称的引号，避免从有多个目标或上下文的句子里任取一个词。
_QUOTES = {"「": "」", "“": "”", '"': '"', "‘": "’", "'": "'"}

# 使用 Android 无障碍报告的控件类区分可编辑框、开关与按钮；未报告角色时不能猜测。
_ROLE_CLASSES = {
    "button": {"android.widget.Button", "android.widget.ImageButton"},
    "input": {
        "android.widget.EditText",
        "android.widget.AutoCompleteTextView",
        "android.widget.MultiAutoCompleteTextView",
    },
    "switch": {"android.widget.Switch", "android.widget.ToggleButton"},
}


@dataclass(frozen=True)
class TargetQuery:
    """供源码读取器查名称、供当前帧验证器核对约束的不可变短查询。"""

    # 源码资源查找名称，保留大小写；空字符串表示没有可查的目标。
    name: str
    # 前缀给出的粗略屏幕区域；空值表示原句没有可验证的位置要求。
    region: str = ""
    # 后缀明确指定的控件角色；空值表示原句没有类型要求。
    role: str = ""

    def matches_node(
        self, node: dict, bounds: tuple[int, int, int, int], size: tuple[int, int]
    ) -> bool:
        """用原生节点属性与当前截图几何验证角色及位置，缺少所需证据时返回 False。

        node 是当前无障碍树里的候选节点，bounds 为像素坐标 (左、上、右、下)，
        size 为同帧的 (宽、高)。顶部与底部使用外侧三分之一，角落还需位于对应的半屏。
        整个矩形都必须在请求区域内。可点击性、遮挡、包名及唯一性继续由定位器统一检查。
        """
        # 安卓标准控件在语义树中暴露完整类名。入口须可点，编辑框及可勾选项不能当作入口。
        class_name = node.get("class") or node.get("className")
        if self.role == "entry":
            if (
                node.get("clickable") not in (True, "true")
                or node.get("checkable") in (True, "true")
                or node.get("editable") in (True, "true")
                or class_name in _ROLE_CLASSES["input"] | _ROLE_CLASSES["switch"]
            ):
                return False
        elif self.role and class_name not in _ROLE_CLASSES[self.role]:
            return False
        if not self.region:
            return True

        # 独立检查垂直与水平方向，取屏幕坐标原点为左上角，边界线包含在目标区域内。
        left, top, right, bottom = bounds
        width, height = size
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            return False
        if self.region.startswith("top") and bottom * 3 > height:
            return False
        if self.region.startswith("bottom") and top * 3 < height * 2:
            return False
        if self.region.endswith("left") and right * 2 > width:
            return False
        return not (self.region.endswith("right") and left * 2 < width)


def parse_query(raw: str) -> TargetQuery:
    """从 raw 提取用于第二次资源查找的名称；调用方先尝试原查询的完整匹配。

    支持一次动作前缀、位置前缀、角色后缀和成对引号；保留未识别文字。引号内始终
    是完整名称，例如「搜索按钮」的“按钮”属于名称。解析不访问工程文件或设备。
    """
    # 每种修饰最多去掉一次，避免反复剥词改变一个控件名的业务含义。
    name = _ACTION.sub("", raw.strip(), count=1).strip()
    region = ""
    role = ""
    # 开头明确标注屏幕区域时，将其保留为运行时条件。
    position = _REGION_PREFIX.match(name)
    if position:
        region = _REGIONS[position[0].strip().removesuffix("的").casefold().replace(" ", "-")]
        name = name[position.end() :].strip()
    # 完整的引号封装不会命中这个后缀；例如「通知开关」自身仍然是资源名。
    suffix = _ROLE_SUFFIX.search(name)
    if suffix and suffix.start() > 0:
        role = _ROLES[suffix[0].strip().casefold()]
        name = name[: suffix.start()].strip()
    # 引号成对且只环绕一个词组时，资源查找可直接使用引号内的完整含义。
    closing = _QUOTES.get(name[:1])
    if closing and len(name) > 1 and name.endswith(closing) and closing not in name[1:-1]:
        name = name[1:-1].strip()
    return TargetQuery(name, region, role)
