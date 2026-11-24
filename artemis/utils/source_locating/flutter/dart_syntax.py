"""Flutter 控件和资源加载器共用的只读 Dart 词法工具，限定于可静态确认的语法。

```text
已解码的 Dart 源码
  → 将可识别的插值隔离成动态标记
  ├─ 存在无法确认边界的插值、嵌套注释或词法错误 → 放弃文件的词法证据
  └─ 跳过注释，按原文偏移拼回字符串 token
       ├─ arguments 按顶层逗号拆出构造参数，保留子表达式
       └─ string_literal 仅从完整的简单字符串取字面值
```

结果保留源码顺序，供调用方确认字段、组件和资源键的关联。
"""

from collections.abc import Iterator
import re

from pygments.lexers import DartLexer
from pygments.token import Comment, Error, Name, String


# 只接受不含转义或插值的字符串常量；两边引号必须配对。
_LITERAL = re.compile(r"""(?:'([^'\\$\r\n]*)'|"([^"\\$\r\n]*)")""")
# 将常见的无嵌套 Dart 插值替换为动态标记。引号必须成对，复杂嵌套和转义交回正常定位。
_SIMPLE_INTERPOLATION = re.compile(r"""\$\{(?:[^{}"'\\]|'[^'{}\\]*'|"[^"{}\\]*")*\}""")


def string_literal(token: str) -> str:
    """返回单个简单 Dart 字符串 token 的原值；非静态表达式或空标签返回空串。"""
    match = _LITERAL.fullmatch(token)
    return (match.group(1) or match.group(2) or "") if match else ""


def tokenize(source: str) -> list[str]:
    """用 Dart 词法器分出代码、字符串与注释。遇到无法可靠分词的语法就放弃此文件。"""
    # 此词法器不跟踪嵌套块注释深度；宁可跳过带嵌套的整个文件，也不把注释识别为声明。
    if re.search(r"/\*(?:(?!\*/|/\*).)*/\*", source, re.S):
        return []
    # 不在插值表达式中查 Widget。保留动态标记令此字符串不能被当作固定标签；
    # 对含嵌套大括号等无法简单确定边界的插值，保守跳过文件。
    source = _SIMPLE_INTERPOLATION.sub("<dynamic-$>", source)
    if "${" in source:
        return []
    # 字符串词法项可能被拆成多个片段，按原文件相邻偏移拼回一个 token。
    tokens: list[str] = []
    string_end = -1
    for offset, kind, value in DartLexer().get_tokens_unprocessed(source):
        if kind in Error:
            return []
        if kind in Comment or value.isspace() and kind not in String:
            continue
        if kind in String:
            if offset == string_end:
                tokens[-1] += value
            else:
                tokens.append(value)
            string_end = offset + len(value)
        elif kind in Name.Label and value.endswith(":"):
            # DartLexer 把没有空格的命名参数名和冒号合为一个 token。
            tokens.extend((value[:-1], ":"))
        else:
            tokens.append(value)
    return tokens


def arguments(tokens: list[str], start: int) -> Iterator[list[str]]:
    """从开括号后的 start 下标按顶层逗号拆参数；嵌套 Widget 的参数只属于子控件。

    tokens 来自 tokenize，start 是第一个参数所在下标。逐项返回 token 切片，
    读到调用的闭括号即结束，调用方仍需核对返回片段是否符合自己支持的语法。
    """
    # depth 以括号层数计，遇到本构造函数的闭括号即结束。
    depth = 0
    for end in range(start, len(tokens)):
        token = tokens[end]
        if token in ("(", "[", "{"):
            depth += 1
        elif token in (")", "]", "}"):
            if depth == 0:
                yield tokens[start:end]
                return
            depth -= 1
        elif token == "," and depth == 0:
            yield tokens[start:end]
            start = end + 1
