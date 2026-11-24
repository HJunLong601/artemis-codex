"""管理用户为 Android App 登记的本机源码目录，仅写 ARTEMIS 的绑定配置。"""

from collections import Counter
import os
from pathlib import Path
import tempfile
import threading
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from artemis.config.paths import get_app_dir

# 普通任务子进程传递的源码组 UUID。目录只在任务启动后从本机配置读取。
TASK_SOURCE_GROUP_ENV = "ARTEMIS_TASK_SOURCE_GROUP_ID"
# 本机队列允许未手选源码组的 Android worker 按前台包名选择唯一绑定。
TASK_AUTO_SOURCE_GROUPS_ENV = "ARTEMIS_TASK_AUTO_SOURCE_GROUPS"
# 沿用后台模型设置的 2 MiB 配置读写上限，避免一次加载过大的本机配置。
_MAX_BYTES = 2 * 1024 * 1024
# 将当前后台进程内的绑定更新串行化，单组变更保留其余记录。
_WRITE_LOCK = threading.Lock()
# 本机目录路径，裁掉表单首尾空白，内部空格保留。长度上限以字符计。
DirectoryPath = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)
]


class BindingError(Exception):
    """可展示的绑定错误，携带 HTTP 状态码，消息不包含系统路径错误详情。"""

    def __init__(self, status: int, message: str) -> None:
        """保存调用方可返回的 HTTP 状态码和提示，不写文件。"""
        super().__init__(message)
        # 对应本机配置接口使用的 4xx 或 5xx 状态码。
        self.status = status


class SourceGroupInput(BaseModel):
    """用户指定同一 App 构建所用的工程根目录与附加目录。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Android 安装包名可从设备选择或直接输入，与同包名的其他源码组分别保存。
    package: str = Field(max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+$")
    # 控制台展示的源码组名称，1 至 100 个字符。
    name: str = Field(min_length=1, max_length=100)
    # 该构建的主工程目录，保存时解析为宿主机上的绝对路径。
    project_dir: DirectoryPath
    # 该构建的其他目录，默认空列表，最多 32 项。
    additional_dirs: list[DirectoryPath] = Field(default_factory=list, max_length=32)


class SourceGroup(SourceGroupInput):
    """已保存的源码组，编辑名称或目录时沿用原组 ID。"""

    # 后台创建的 UUID，用于手动选择源码组。
    id: UUID


# 自定义的应用展示名称，首尾空格不保留，空字符串表示清除已保存的名称。
AppName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]


class SourceGroupRequest(SourceGroupInput):
    """编辑源码组时可同时修改该包名的展示名称，兼容只发送组字段的旧客户端。"""

    # 属于包名而不是源码组；省略或传 null 时保留已有展示名称。
    app_name: AppName | None = None


class SourceGroups(BaseModel):
    """本机绑定配置，一个包名可对应多组，初始为空。"""

    model_config = ConfigDict(extra="forbid")

    # 已保存记录，按创建顺序展示。
    groups: list[SourceGroup] = Field(default_factory=list)
    # 以 Android 包名为键保存可选的应用展示名称，旧配置缺少该字段时视为空。
    app_names: dict[str, AppName] = Field(default_factory=dict)


def _path() -> Path:
    """取得 ARTEMIS 用户数据目录中的绑定文件路径，不打开 App 工程。"""
    return get_app_dir() / "app-projects.json"


def _read(path: Path) -> SourceGroups:
    """从 path 读取一次完整快照。首次使用返回空记录，损坏或不可读时抛出 503。"""
    try:
        # 原子替换使路径始终指向完整的新文件或旧文件。
        with path.open("rb") as handle:
            payload = handle.read(_MAX_BYTES + 1)
    except FileNotFoundError:
        return SourceGroups()
    except OSError:
        raise BindingError(503, "无法读取本机 App 工程绑定。") from None
    if len(payload) > _MAX_BYTES:
        raise BindingError(503, "本机 App 工程绑定配置超过 2 MiB。")
    try:
        # 校验已有记录，避免编辑操作覆盖无法识别的配置。
        snapshot = SourceGroups.model_validate_json(payload)
    except ValidationError:
        raise BindingError(503, "本机 App 工程绑定配置无效。") from None
    if len({group.id for group in snapshot.groups}) != len(snapshot.groups):
        raise BindingError(503, "本机 App 工程绑定包含重复的源码组 ID。")
    return snapshot


def _write(path: Path, snapshot: SourceGroups) -> None:
    """在持有写锁时将 snapshot 原子保存到 path，失败抛出 503 并清理临时文件。"""
    # 用 UTF-8 保留中文名称和路径，落盘上限与读取上限一致。
    payload = snapshot.model_dump_json(indent=2).encode("utf-8")
    if len(payload) > _MAX_BYTES:
        raise BindingError(422, "本机 App 工程绑定配置超过 2 MiB。")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 临时文件放在目标目录，确保替换不会跨文件系统。
        fd, temporary = tempfile.mkstemp(prefix=".app-projects-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    except OSError:
        raise BindingError(503, "无法保存本机 App 工程绑定。") from None


def _normalize(input_group: SourceGroupInput) -> dict[str, str | list[str]]:
    """校验 input_group 指定的目录，返回可读、去重后的绝对目录字段。

    保留附加目录的输入顺序。目录无效或重复时抛出 422，无文件写入。
    """
    # 列表首项是主工程，余下条目原序对应附加目录。
    directories: list[Path] = []
    # 文件系统设备号和 inode 可发现大小写别名等指向同一物理目录的路径。
    seen: set[tuple[int, int]] = set()
    for value in [input_group.project_dir, *input_group.additional_dirs]:
        try:
            # 允许本机目录常用的 ~ 写法，拒绝基于后台工作目录解析相对路径。
            entered = Path(value).expanduser()
            if not entered.is_absolute():
                raise BindingError(422, "请输入本机目录的绝对路径。")
            canonical = entered.resolve(strict=True)
            if not canonical.is_dir() or not os.access(canonical, os.R_OK | os.X_OK):
                raise BindingError(422, "请输入后台可读取的本机目录。")
            # 在支持 inode 的文件系统上，同时校验物理目录唯一性。
            info = canonical.stat()
        except (OSError, RuntimeError, ValueError):
            raise BindingError(422, "输入的本机目录不存在或无法读取。") from None
        identity = (info.st_dev, info.st_ino)
        if canonical in directories or (info.st_ino and identity in seen):
            raise BindingError(422, "同一源码组内的目录不能重复。")
        if info.st_ino:
            seen.add(identity)
        directories.append(canonical)
    return {
        "project_dir": str(directories[0]),
        "additional_dirs": [str(directory) for directory in directories[1:]],
    }


def _position(snapshot: SourceGroups, group_id: str) -> int:
    """找出 group_id 在 snapshot 内的位置，未知 ID 抛出 404。"""
    for index, group in enumerate(snapshot.groups):
        if str(group.id) == group_id:
            return index
    raise BindingError(404, "未找到所选源码组。")


def list_groups() -> SourceGroups:
    """列出本机所有绑定，保留需修复路径的记录供用户编辑。"""
    return _read(_path())


def get_automatic_groups() -> dict[str, SourceGroup]:
    """按 Android 包名返回可自动使用的组。一个包名有多组时必须由用户选定。"""
    # 在任务开始时读一次绑定快照，不猜测候选分支；实际读取源码时仍检查目录。
    groups = list_groups().groups
    counts = Counter(group.package for group in groups)
    return {group.package: group for group in groups if counts[group.package] == 1}


def get_group(group_id: str) -> SourceGroup:
    """返回 group_id 对应的验证配置。目录失效或被重定向时抛出 409。"""
    snapshot = list_groups()
    group = snapshot.groups[_position(snapshot, group_id)]
    try:
        # 验证前重新检查，避免使用保存后被移动或被符号链接替换的目录。
        paths = _normalize(group)
    except BindingError:
        raise BindingError(409, "源码组的目录已失效，请重新选择。") from None
    if (
        paths["project_dir"] != group.project_dir
        or paths["additional_dirs"] != group.additional_dirs
    ):
        raise BindingError(409, "源码组的目录已变化，请重新选择。")
    return group


def create_group(input_group: SourceGroupInput, *, app_name: AppName | None = None) -> SourceGroup:
    """校验 input_group 的目录，并原子保存新 UUID 和可选的包名展示名称。

    app_name 为 None 时保留名称，空字符串清除名称。目录或保存失败时抛出 BindingError。
    """
    group = SourceGroup(id=uuid4(), **(input_group.model_dump(exclude={"app_name"}) | _normalize(input_group)))
    with _WRITE_LOCK:
        path = _path()
        snapshot = _read(path)
        snapshot.groups.append(group)
        _set_app_name(snapshot, group.package, app_name)
        _write(path, snapshot)
    return group


def update_group(
    group_id: str, input_group: SourceGroupInput, *, app_name: AppName | None = None
) -> SourceGroup:
    """在写锁内替换 group_id 对应的组，保留 UUID 和顺序，并更新目标包名的展示名称。

    app_name 为 None 时保留名称，空字符串清除名称。无效目录、未知组或写入失败时抛出 BindingError。
    """
    paths = _normalize(input_group)
    with _WRITE_LOCK:
        path = _path()
        snapshot = _read(path)
        index = _position(snapshot, group_id)
        # 旧组提供原包名和 UUID；旧客户端可能使用此接口修改包名。
        previous = snapshot.groups[index]
        group = SourceGroup(id=previous.id, **(input_group.model_dump(exclude={"app_name"}) | paths))
        snapshot.groups[index] = group
        _drop_unbound_name(snapshot, previous.package)
        _set_app_name(snapshot, group.package, app_name)
        _write(path, snapshot)
    return group


def delete_group(group_id: str) -> None:
    """从绑定配置移除 group_id，未知 ID 抛出 404。工程文件没有写入或删除。"""
    with _WRITE_LOCK:
        path = _path()
        snapshot = _read(path)
        # 删除前记住所属包名，以便在最后一组移除时清理展示名称。
        removed = snapshot.groups.pop(_position(snapshot, group_id))
        _drop_unbound_name(snapshot, removed.package)
        _write(path, snapshot)


def _set_app_name(snapshot: SourceGroups, package: str, app_name: AppName | None) -> None:
    """在写锁内更新快照里的包名展示名称。None 保持原值，空字符串清除，不单独落盘。"""
    if app_name is None:
        return
    if app_name:
        snapshot.app_names[package] = app_name
    else:
        snapshot.app_names.pop(package, None)


def _drop_unbound_name(snapshot: SourceGroups, package: str) -> None:
    """在写锁内清理失去最后一组源码的包名展示名称，不单独落盘。"""
    if not any(group.package == package for group in snapshot.groups):
        snapshot.app_names.pop(package, None)
