"""从所绑工程提取静态线索，用当前设备的原生控件树核实候选。

```text
resolver.py       组织同一帧的批量查询，合并各技术栈线索
  ├─ android/     Views XML 控件，以及 Views 和 Compose 共用的资源解析
  ├─ compose/     Kotlin 中的 Compose ID 和文案声明
  ├─ flutter/     Dart 控件声明、ARB 与 JSON 翻译
  ├─ react_native/  JSX 属性和 Pressable 包装器
  └─ common/      查询约束、候选数据类型和当前帧核验
```
"""
