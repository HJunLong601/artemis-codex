import pytest

from artemis.runtime.cache_exploration.policy import elements, permit, unsafe_window


def frame(label="首页", extra="", klass="android.widget.Button"):
    return f'''<hierarchy><node package="org.example.app" class="android.widget.FrameLayout"
      bounds="[0,0][400,800]"><node package="org.example.app" resource-id="org.example.app:id/bottom_navigation"
      text="{label}" clickable="true" class="{klass}" bounds="[20,600][100,700]" {extra}/></node></hierarchy>'''


def test_safe_navigation_is_grounded_in_current_element():
    nodes = elements(frame(), "org.example.app", (400, 800))
    assert permit({"action": "navigate", "element_ref": 1}, nodes, (400, 800), False)
    assert not permit({"action": "navigate", "element_ref": 999}, nodes, (400, 800), False)
    assert not permit(
        {"action": "navigate", "element_ref": 1, "coords": [1, 2]}, nodes, (400, 800), False
    )


@pytest.mark.parametrize(
    "label",
    ["删除", "发送", "购买", "分享", "开始录音", "修改账号", "提交", "立即更新", "退出登录"],
)
def test_business_operations_rejected_even_inside_navigation(label):
    assert not permit(
        {"action": "navigate", "element_ref": 1},
        elements(frame(label), "org.example.app", (400, 800)),
        (400, 800),
        False,
    )


def test_unknown_button_switch_and_back_at_root_fail_closed():
    for xml in [
        frame("确定"),
        frame("首页", 'checkable="true"'),
        frame("首页", klass="android.widget.Switch"),
    ]:
        assert not permit(
            {"action": "navigate", "element_ref": 1},
            elements(xml, "org.example.app", (400, 800)),
            (400, 800),
            False,
        )
    assert not permit({"action": "back"}, [], (400, 800), False)
    assert not permit({"action": "input_text", "text": "hello"}, [], (400, 800), False)


def test_sensitive_dialog_pauses_and_foreign_package_not_a_candidate():
    assert unsafe_window(
        '<hierarchy><node class="android.app.Dialog" text="确认支付"/></hierarchy>'
    )
    assert not elements(
        frame().replace("org.example.app", "org.other.app"), "org.example.app", (400, 800)
    )


def test_navigation_container_does_not_authorize_unknown_or_disabled_controls():
    assert not elements(frame("Unknown action"), "org.example.app", (400, 800))
    assert not elements(frame("Home", 'enabled="false"'), "org.example.app", (400, 800))


def test_destructive_confirmation_dialog_is_not_treated_as_normal_navigation():
    xml = '<hierarchy><node resource-id="android:id/alertTitle" text="Delete item?"/><node resource-id="android:id/button1" text="Delete" clickable="true"/></hierarchy>'
    assert unsafe_window(xml)


@pytest.mark.parametrize(
    "kind",
    ["android.view.View", "android.widget.ScrollView", "androidx.recyclerview.widget.RecyclerView"],
)
def test_full_height_accessibility_scroll_container_is_supported_but_not_initial_pull_down(kind):
    xml = f'<hierarchy><node package="org.example.app" class="{kind}" scrollable="true" bounds="[0,0][400,800]"/></hierarchy>'
    nodes = elements(xml, "org.example.app", (400, 800))
    assert len(nodes) == 1
    assert permit(
        {"action": "scroll", "element_ref": 1, "direction": "up"}, nodes, (400, 800), False
    )
    assert not permit(
        {"action": "scroll", "element_ref": 1, "direction": "down"}, nodes, (400, 800), False
    )
    assert permit(
        {"action": "scroll", "element_ref": 1, "direction": "down"},
        nodes,
        (400, 800),
        False,
        allow_down=True,
    )
