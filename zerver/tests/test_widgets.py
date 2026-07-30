import re
from typing import TYPE_CHECKING, Any

import orjson
from django.core.exceptions import ValidationError

from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.validator import MAX_IDX, check_widget_content
from zerver.lib.widget import (
    MAX_ROLL_COUNT,
    MAX_ROLL_RANGE,
    MIN_ROLL_COUNT,
    MIN_ROLL_RANGE,
    get_widget_data,
    get_widget_type,
)
from zerver.models import SubMessage, UserProfile

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


class WidgetContentTestCase(ZulipTestCase):
    def test_validation(self) -> None:
        def assert_error(obj: object, msg: str) -> None:
            with self.assertRaisesRegex(ValidationError, re.escape(msg)):
                check_widget_content(obj)

        assert_error(5, "widget_content is not a dict")

        assert_error({}, "widget_type is not in widget_content")

        assert_error(dict(widget_type="whatever"), "extra_data is not in widget_content")

        assert_error(dict(widget_type="zform", extra_data=4), "extra_data is not a dict")

        assert_error(dict(widget_type="bogus", extra_data={}), "unknown widget type: bogus")

        extra_data: dict[str, Any] = {}
        obj = dict(widget_type="zform", extra_data=extra_data)

        assert_error(obj, "zform is missing type field")

        extra_data["type"] = "bogus"
        assert_error(obj, "unknown zform type: bogus")

        extra_data["type"] = "choices"
        assert_error(obj, "heading key is missing from extra_data")

        extra_data["heading"] = "whatever"
        assert_error(obj, "choices key is missing from extra_data")

        extra_data["choices"] = 99
        assert_error(obj, 'extra_data["choices"] is not a list')

        extra_data["choices"] = [99]
        assert_error(obj, 'extra_data["choices"][0] is not a dict')

        extra_data["choices"] = [
            dict(long_name="foo", reply="bar"),
        ]
        assert_error(obj, 'short_name key is missing from extra_data["choices"][0]')

        extra_data["choices"] = [
            dict(short_name="a", long_name="foo", reply="bar"),
        ]

        check_widget_content(obj)

    def test_message_error_handling(self) -> None:
        sender = self.example_user("cordelia")
        stream_name = "Verona"

        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
            content="whatever",
        )

        payload["widget_content"] = "{{{{{{"  # unparsable
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_error_contains(result, "Widgets: API programmer sent invalid JSON")

        bogus_data = dict(color="red", foo="bar", x=2)
        payload["widget_content"] = orjson.dumps(bogus_data).decode()
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_error_contains(result, "Widgets: widget_type is not in widget_content")

    def test_get_widget_data_for_non_widget_messages(self) -> None:
        # This is a pretty important test, despite testing the
        # "negative" case.  We never want widgets to interfere
        # with normal messages.

        test_messages = [
            "",
            "     ",
            "this is an ordinary message",
            "/bogus_command",
            "/me shrugs",
            "use /poll",
        ]

        for message in test_messages:
            self.assertEqual(get_widget_data(content=message), (None, None))

        # Add positive checks for context
        self.assertEqual(
            get_widget_data(content="/todo"), ("todo", {"task_list_title": "", "tasks": []})
        )
        self.assertEqual(
            get_widget_data(content="/todo Title"),
            ("todo", {"task_list_title": "Title", "tasks": []}),
        )
        # Test tokenization on newline character
        self.assertEqual(
            get_widget_data(content="/todo\nTask"),
            ("todo", {"task_list_title": "", "tasks": [{"task": "Task", "desc": ""}]}),
        )

    def test_explicit_widget_content(self) -> None:
        # Users can send widget_content directly on messages
        # using the `widget_content` field.

        sender = self.example_user("cordelia")
        stream_name = "Verona"
        content = "does-not-matter"
        zform_data = dict(
            type="choices",
            heading="Options:",
            choices=[],
        )

        widget_content = dict(
            widget_type="zform",
            extra_data=zform_data,
        )

        check_widget_content(widget_content)

        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
            content=content,
            widget_content=orjson.dumps(widget_content).decode(),
        )
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_success(result)

        message = self.get_last_message()
        self.assertEqual(message.content, content)

        expected_submessage_content = dict(
            widget_type="zform",
            extra_data=zform_data,
        )

        submessage = SubMessage.objects.get(message_id=message.id)
        self.assertEqual(submessage.msg_type, "widget")
        self.assertEqual(orjson.loads(submessage.content), expected_submessage_content)

    def test_todo(self) -> None:
        # This also helps us get test coverage that could apply
        # to future widgets.

        sender = self.example_user("cordelia")
        stream_name = "Verona"
        content = "/todo"

        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
            content=content,
        )
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_success(result)

        message = self.get_last_message()
        self.assertEqual(message.content, content)

        expected_submessage_content = dict(
            widget_type="todo",
            extra_data={"task_list_title": "", "tasks": []},
        )

        submessage = SubMessage.objects.get(message_id=message.id)
        self.assertEqual(submessage.msg_type, "widget")
        self.assertEqual(orjson.loads(submessage.content), expected_submessage_content)

        content = "/todo Example Task List Title"
        payload["content"] = content
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_success(result)

        message = self.get_last_message()
        self.assertEqual(message.content, content)

        expected_submessage_content = dict(
            widget_type="todo",
            extra_data={"task_list_title": "Example Task List Title", "tasks": []},
        )

        submessage = SubMessage.objects.get(message_id=message.id)
        self.assertEqual(submessage.msg_type, "widget")
        self.assertEqual(orjson.loads(submessage.content), expected_submessage_content)

        # We test for both trailing and leading spaces, along with blank lines
        # for the tasks.
        content = "/todo Example Task List Title\n\n    task without description\ntask: with description    \n\n - task as list : also with description"
        payload["content"] = content
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_success(result)

        message = self.get_last_message()
        self.assertEqual(message.content, content)

        expected_submessage_content = dict(
            widget_type="todo",
            extra_data=dict(
                task_list_title="Example Task List Title",
                tasks=[
                    dict(
                        task="task without description",
                        desc="",
                    ),
                    dict(
                        task="task",
                        desc="with description",
                    ),
                    dict(
                        task="task as list",
                        desc="also with description",
                    ),
                ],
            ),
        )

        submessage = SubMessage.objects.get(message_id=message.id)
        self.assertEqual(submessage.msg_type, "widget")
        self.assertEqual(orjson.loads(submessage.content), expected_submessage_content)

    def test_roll_values_accurate_to_object(self) -> None:
        sender = self.example_user("cordelia")
        stream_name = "Verona"

        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
        )

        def send_roll_and_get_values(content: str) -> dict[str, Any]:
            payload["content"] = content
            result = self.api_post(sender, "/api/v1/messages", payload)
            self.assert_json_success(result)

            message = self.get_last_message()
            self.assertEqual(message.content, content)

            roll_submessage = (
                SubMessage.objects.filter(message_id=message.id, msg_type="widget")
                .order_by("id")
                .last()
            )
            assert roll_submessage is not None
            return orjson.loads(roll_submessage.content)
        
        def assert_valid_roll_values(
                roll_content: dict[str, Any], *, expected_count: int, expected_range: int
        ) -> None:
            # Checks that a "new_roll" submessage's dice values are well
            # within thier valid range and add up to the correct total
            self.assertEqual(roll_content["type"], "new_roll")
            values = roll_content["values"]
            self.assert_length(values, expected_count)
            for value in values:
                self.assertGreaterEqual(value, 1)
                self.assertLessEqual(value, expected_range)
            self.assertEqual(roll_content["total"], sum(values))

        # Create and test rolls 
        roll_content = send_roll_and_get_values("/roll 2d6")
        assert_valid_roll_values(roll_content, expected_count=2, expected_range=6)

        roll_content = send_roll_and_get_values("/roll 9999")
        assert_valid_roll_values(
            roll_content, expected_count=1, expected_range=MAX_ROLL_RANGE
        )

        roll_content = send_roll_and_get_values("/roll 50d6")
        assert_valid_roll_values(
            roll_content, expected_count=MAX_ROLL_COUNT, expected_range=6
        )

    def test_roll_reroll_via_submessage(self) -> None:

        author = self.example_user("cordelia")
        other_user = self.example_user("hamlet")
        stream_name = "Verona"
        content = "/roll 2d6"

        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
            content=content,
        )
        result = self.api_post(author, "/api/v1/messages", payload)
        self.assert_json_success(result)
        message = self.get_last_message()

        # Sending "/roll 2d6" should create 2 submessages: the
        # widget config and the initial dice roll.
        self.assert_length(
            SubMessage.objects.filter(message_id=message.id, msg_type="widget"), 2
        )
        # Appends a roll
        def click_roll(sender: UserProfile) -> "TestHttpResponse":
            payload = dict(
                message_id=message.id,
                msg_type="widget",
                content=orjson.dumps(dict(type="new_roll")).decode(),
            )
            return self.api_post(sender, "/api/v1/submessage", payload)

        # A user other than the message author can roll
        result = click_roll(other_user)
        self.assert_json_success(result)

        submessages = list(
            SubMessage.objects.filter(message_id=message.id, msg_type="widget").order_by("id")
        )
        self.assert_length(submessages, 3)

    def test_poll_command_extra_data(self) -> None:
        sender = self.example_user("cordelia")
        stream_name = "Verona"
        # We test for both trailing and leading spaces, along with blank lines
        # for the poll options.
        content = "/poll What is your favorite color?\n\nRed\nGreen  \n\n   Blue\n - Yellow"

        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
            content=content,
        )
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_success(result)

        message = self.get_last_message()
        self.assertEqual(message.content, content)

        expected_submessage_content = dict(
            widget_type="poll",
            extra_data=dict(
                options=["Red", "Green", "Blue", "Yellow"],
                question="What is your favorite color?",
            ),
        )

        submessage = SubMessage.objects.get(message_id=message.id)
        self.assertEqual(submessage.msg_type, "widget")
        self.assertEqual(orjson.loads(submessage.content), expected_submessage_content)

        # Now don't supply a question.

        content = "/poll"
        payload["content"] = content
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_success(result)

        expected_submessage_content = dict(
            widget_type="poll",
            extra_data=dict(
                options=[],
                question="",
            ),
        )

        message = self.get_last_message()
        self.assertEqual(message.content, content)
        submessage = SubMessage.objects.get(message_id=message.id)
        self.assertEqual(submessage.msg_type, "widget")
        self.assertEqual(orjson.loads(submessage.content), expected_submessage_content)

    def test_todo_command_extra_data(self) -> None:
        sender = self.example_user("cordelia")
        stream_name = "Verona"
        # We test for leading spaces.
        content = "/todo   School Work"

        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
            content=content,
        )
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_success(result)

        message = self.get_last_message()
        self.assertEqual(message.content, content)

        expected_submessage_content = dict(
            widget_type="todo",
            extra_data=dict(task_list_title="School Work", tasks=[]),
        )

        submessage = SubMessage.objects.get(message_id=message.id)
        self.assertEqual(submessage.msg_type, "widget")
        self.assertEqual(orjson.loads(submessage.content), expected_submessage_content)

        # Now don't supply a task list title.

        content = "/todo"
        payload["content"] = content
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_success(result)

        expected_submessage_content = dict(
            widget_type="todo",
            extra_data=dict(task_list_title="", tasks=[]),
        )

        message = self.get_last_message()
        self.assertEqual(message.content, content)
        submessage = SubMessage.objects.get(message_id=message.id)
        self.assertEqual(submessage.msg_type, "widget")
        self.assertEqual(orjson.loads(submessage.content), expected_submessage_content)
        # Now supply both task list title and tasks.

        content = "/todo School Work\nchemistry homework: assignment 2\nstudy for english test: pages 56-67"
        payload["content"] = content
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_success(result)

        expected_submessage_content = dict(
            widget_type="todo",
            extra_data=dict(
                task_list_title="School Work",
                tasks=[
                    dict(
                        task="chemistry homework",
                        desc="assignment 2",
                    ),
                    dict(
                        task="study for english test",
                        desc="pages 56-67",
                    ),
                ],
            ),
        )

        message = self.get_last_message()
        self.assertEqual(message.content, content)
        submessage = SubMessage.objects.get(message_id=message.id)
        self.assertEqual(submessage.msg_type, "widget")
        self.assertEqual(orjson.loads(submessage.content), expected_submessage_content)

    def test_roll_command_extra_data(self) -> None:
        # Test fixture user/stream; not relevant to roll parsing itself.
        sender = self.example_user("cordelia")
        stream_name = "Verona"

        # Base payload for a stream message; "content" gets overwritten
        # per-case inside assert_extra_data below.
        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
        )

        def assert_extra_data(content: str, *, expected_count: int, expected_range: int) -> None:
            payload["content"] = content
            result = self.api_post(sender, "/api/v1/messages", payload)
            self.assert_json_success(result)

            message = self.get_last_message()

            self.assertEqual(message.content, content)

            config_submessage = (
                SubMessage.objects.filter(message_id=message.id, msg_type="widget")
                .order_by("id")
                .first()
            )
            assert config_submessage is not None  
            self.assertEqual(
                orjson.loads(config_submessage.content),
                dict(
                    widget_type="roll",
                    extra_data=dict(count=expected_count, range=expected_range),
                ),
            )
        # Checks config to see if within expected range
        # No arguments: defaults
        assert_extra_data("/roll", expected_count=MIN_ROLL_COUNT, expected_range=MIN_ROLL_RANGE)

        # A bare number sets the range (number of sides), not the count
        assert_extra_data("/roll 6", expected_count=MIN_ROLL_COUNT, expected_range=6)

        # "CdR" dice notation sets both count and range
        assert_extra_data("/roll 2d20", expected_count=2, expected_range=20)

        # The count is optional in dice notation
        assert_extra_data("/roll d6", expected_count=MIN_ROLL_COUNT, expected_range=6)

        # A plain space also works as the d separator
        assert_extra_data("/roll 3 6", expected_count=3, expected_range=6)

        # Range and count are clamped to their allowed bounds
        assert_extra_data("/roll 9999", expected_count=1, expected_range=MAX_ROLL_RANGE)
        assert_extra_data("/roll 50d6", expected_count=MAX_ROLL_COUNT, expected_range=6)

    def test_poll_permissions(self) -> None:
        cordelia = self.example_user("cordelia")
        hamlet = self.example_user("hamlet")
        stream_name = "Verona"
        content = "/poll Preference?\n\nyes\nno"

        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
            content=content,
        )
        result = self.api_post(cordelia, "/api/v1/messages", payload)
        self.assert_json_success(result)

        message = self.get_last_message()

        def post(sender: UserProfile, data: dict[str, object]) -> "TestHttpResponse":
            payload = dict(
                message_id=message.id, msg_type="widget", content=orjson.dumps(data).decode()
            )
            return self.api_post(sender, "/api/v1/submessage", payload)

        result = post(cordelia, dict(type="question", question="Tabs or spaces?"))
        self.assert_json_success(result)

        result = post(hamlet, dict(type="question", question="Tabs or spaces?"))
        self.assert_json_error(result, "You can't edit a question unless you are the author.")

    def test_todo_permissions(self) -> None:
        cordelia = self.example_user("cordelia")
        hamlet = self.example_user("hamlet")
        stream_name = "Verona"
        content = "/todo School Work"

        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
            content=content,
        )
        result = self.api_post(cordelia, "/api/v1/messages", payload)
        self.assert_json_success(result)

        message = self.get_last_message()

        def post(sender: UserProfile, data: dict[str, object]) -> "TestHttpResponse":
            payload = dict(
                message_id=message.id, msg_type="widget", content=orjson.dumps(data).decode()
            )
            return self.api_post(sender, "/api/v1/submessage", payload)

        result = post(cordelia, dict(type="new_task_list_title", title="School Work"))
        self.assert_json_success(result)

        result = post(hamlet, dict(type="new_task_list_title", title="School Work"))
        self.assert_json_error(
            result, "You can't edit the task list title unless you are the author."
        )

    def test_poll_type_validation(self) -> None:
        sender = self.example_user("cordelia")
        stream_name = "Verona"
        content = "/poll Preference?\n\nyes\nno"

        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
            content=content,
        )
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_success(result)

        message = self.get_last_message()

        def post_submessage(content: str) -> "TestHttpResponse":
            payload = dict(
                message_id=message.id,
                msg_type="widget",
                content=content,
            )
            return self.api_post(sender, "/api/v1/submessage", payload)

        def assert_error(content: str, error: str) -> None:
            result = post_submessage(content)
            self.assert_json_error_contains(result, error)

        assert_error("bogus", "Invalid json for submessage")
        assert_error('""', "not a dict")
        assert_error("[]", "not a dict")

        assert_error('{"type": "bogus"}', "Unknown type for poll data: bogus")
        assert_error('{"type": "vote"}', "key is missing")
        assert_error('{"type": "vote", "key": "1,1,", "vote": 99}', "Invalid poll data")

        assert_error('{"type": "question"}', "key is missing")
        assert_error('{"type": "question", "question": 7}', "not a string")

        assert_error('{"type": "new_option"}', "key is missing")
        assert_error('{"type": "new_option", "idx": 7, "option": 999}', "not a string")
        assert_error('{"type": "new_option", "idx": -1, "option": "pizza"}', "too small")
        assert_error('{"type": "new_option", "idx": 1001, "option": "pizza"}', "too large")
        assert_error('{"type": "new_option", "idx": "bogus", "option": "maybe"}', "not an int")

        def assert_success(data: dict[str, object]) -> None:
            content = orjson.dumps(data).decode()
            result = post_submessage(content)
            self.assert_json_success(result)

        # Note that we only validate for types. The server code may, for,
        # example, allow a vote for a non-existing option, and we rely
        # on the clients to ignore those.

        assert_success(dict(type="vote", key="1,1", vote=1))
        assert_success(dict(type="new_option", idx=7, option="maybe"))
        assert_success(dict(type="question", question="what's for dinner?"))

    def test_todo_type_validation(self) -> None:
        sender = self.example_user("cordelia")
        stream_name = "Verona"
        content = "/todo"

        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
            content=content,
        )
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_success(result)

        message = self.get_last_message()

        def post_submessage(content: str) -> "TestHttpResponse":
            payload = dict(
                message_id=message.id,
                msg_type="widget",
                content=content,
            )
            return self.api_post(sender, "/api/v1/submessage", payload)

        def assert_error(content: str, error: str) -> None:
            result = post_submessage(content)
            self.assert_json_error_contains(result, error)

        assert_error('{"type": "bogus"}', "Unknown type for todo data: bogus")

        assert_error('{"type": "new_task"}', "key is missing")
        assert_error(
            '{"type": "new_task", "key": 7, "task": 7, "desc": "", "completed": false}',
            'data["task"] is not a string',
        )
        assert_error(
            '{"type": "new_task", "key": -1, "task": "eat", "desc": "", "completed": false}',
            'data["key"] is too small',
        )
        assert_error(
            '{"type": "new_task", "key": 1001, "task": "eat", "desc": "", "completed": false}',
            'data["key"] is too large',
        )

        assert_error('{"type": "strike"}', "key is missing")
        assert_error('{"type": "strike", "key": 999}', 'data["key"] is not a string')

        def assert_success(data: dict[str, object]) -> None:
            content = orjson.dumps(data).decode()
            result = post_submessage(content)
            self.assert_json_success(result)

        assert_success(dict(type="new_task", key=7, task="eat", desc="", completed=False))
        assert_success(dict(type="strike", key="5,9"))

    def test_new_roll_type_validation(self) -> None:
        cordelia = self.example_user("cordelia")
        hamlet = self.example_user("hamlet")
        stream_name = "Verona"
        content = "/roll 2d6"

        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
            content=content,
        )
        result = self.api_post(cordelia, "/api/v1/messages", payload)
        self.assert_json_success(result)

        message = self.get_last_message()

        def post_submessage(sender: UserProfile, content: str) -> "TestHttpResponse":
            payload = dict(
                message_id=message.id,
                msg_type="widget",
                content=content,
            )
            return self.api_post(sender, "/api/v1/submessage", payload)

        def assert_error(content: str, error: str) -> None:
            result = post_submessage(cordelia, content)
            self.assert_json_error_contains(result, error)

        assert_error('{"type": "bogus"}', "Unknown type for roll data: bogus")
        
        # Dice values/total are computed server-side. a client can't
        # submit its own roll result
        assert_error(
            '{"type": "new_roll", "values": [6, 6], "total": 12}',
            "Unexpected arguments",
        )

        # Anyone can click Roll regardless of who sent the message
        result = post_submessage(hamlet, '{"type": "new_roll"}')
        self.assert_json_success(result)

    def test_get_widget_type(self) -> None:
        sender = self.example_user("cordelia")
        stream_name = "Verona"
        # We test for both trailing and leading spaces, along with blank lines
        # for the poll options.
        content = "/poll Preference?\n\nyes\nno"

        payload = dict(
            type="stream",
            to=orjson.dumps(stream_name).decode(),
            topic="whatever",
            content=content,
        )
        result = self.api_post(sender, "/api/v1/messages", payload)
        self.assert_json_success(result)

        message = self.get_last_message()

        [submessage] = SubMessage.objects.filter(message_id=message.id)

        self.assertEqual(get_widget_type(message_id=message.id), "poll")

        submessage.content = "bogus non-json"
        submessage.save()
        self.assertEqual(get_widget_type(message_id=message.id), None)

        submessage.content = '{"bogus": 1}'
        submessage.save()
        self.assertEqual(get_widget_type(message_id=message.id), None)

        submessage.content = '{"widget_type": "todo"}'
        submessage.save()
        self.assertEqual(get_widget_type(message_id=message.id), "todo")
