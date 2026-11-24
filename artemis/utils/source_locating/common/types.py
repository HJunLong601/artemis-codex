"""各源码读取器共享的控件证据，只描述工程内可追溯的静态关联。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceHint:
    """同一声明或同一翻译键的候选特征。运行时必须到当前帧核验才可使用。"""

    # 同一控件可能显示的文字或无障碍描述；空元组表示仅有标识符。
    labels: tuple[str, ...] = ()
    # 控件声明的标识符。空串表示缺少 ID 证据，不能从文案键猜测。
    resource_id: str = ""
    # True 表示 Android View 资源名；其他框架的语义标识按原始字符串匹配。
    android_id: bool = False
    # 工程内的资源键或声明位置，仅用于本地排查，不作为设备坐标证据。
    origin: str = ""
    # 可从同一控件声明直接确认的动作；"tap" 表示绑定了点击回调，空串只提供名称或 ID。
    action: str = ""
