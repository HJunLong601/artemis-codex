# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from artemis.utils.notes import update_note_content


def test_update_task_plan_preserves_surrounding_tasks(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    plan = notes / "task_plan.md"
    task_plan = """- [x] Initial task
- [/] Active task
- [ ] Future task"""
    plan.write_text(task_plan, encoding="utf-8")

    warning = update_note_content(
        tmp_path,
        "task_plan",
        "- [/] Active task",
        "- [/] Active task\n    - [ ] New subgoal",
    )
    assert warning is None
    assert plan.read_text(encoding="utf-8") == (
        "- [x] Initial task\n- [/] Active task\n    - [ ] New subgoal\n- [ ] Future task"
    )
