import json
import re
from dataclasses import asdict, dataclass
from random import randint
from typing import Any

from zerver.lib.message import SendMessageRequest
from zerver.models import Message, SubMessage


@dataclass
class PollData:
    question: str
    options: list[str]


@dataclass
class TodoTaskData:
    task: str
    desc: str


@dataclass
class TodoData:
    task_list_title: str
    tasks: list[TodoTaskData]


MIN_ROLL_RANGE = 2
MAX_ROLL_RANGE = 1000
MIN_ROLL_COUNT = 1
MAX_ROLL_COUNT = 20


@dataclass
class RollData:
    range: int
    count: int
    # Clamps values to acceptable ranges
    def __post_init__(self) -> None:
        self.range = min(max(self.range, MIN_ROLL_RANGE), MAX_ROLL_RANGE)
        self.count = min(max(self.count, MIN_ROLL_COUNT), MAX_ROLL_COUNT)


@dataclass
class NewRoll:
    values: list[int]
    total: int


def get_widget_data(content: str) -> tuple[str | None, Any]:
    valid_widget_types = ["poll", "todo", "roll"]
    tokens = re.split(r"\s+|\n+", content)

    # tokens[0] will always exist
    if tokens[0].startswith("/"):
        widget_type = tokens[0].removeprefix("/")
        if widget_type in valid_widget_types:
            remaining_content = content.replace(tokens[0], "", 1)
            extra_data = get_extra_data_from_widget_type(remaining_content, widget_type)
            return widget_type, asdict(extra_data)

    return None, None


def parse_poll_extra_data(content: str) -> PollData:
    # This is used to extract the question from the poll command.
    # The command '/poll question' will pre-set the question in the poll
    lines = content.splitlines()
    question = ""
    options = []
    if lines and lines[0]:
        question = lines.pop(0).strip()
    for line in lines:
        # If someone is using the list syntax, we remove it
        # before adding an option.
        option = re.sub(r"(\s*[-*]?\s*)", "", line.strip(), count=1)
        if len(option) > 0:
            options.append(option)
    return PollData(question=question, options=options)


def parse_todo_extra_data(content: str) -> TodoData:
    # This is used to extract the task list title from the todo command.
    # The command '/todo Title' will pre-set the task list title
    lines = content.splitlines()
    task_list_title = ""
    if lines and lines[0]:
        task_list_title = lines.pop(0).strip()
    tasks = []
    for line in lines:
        # If someone is using the list syntax, we remove it
        # before adding a task.
        task_data = re.sub(r"(\s*[-*]?\s*)", "", line.strip(), count=1)
        if len(task_data) > 0:
            # a task and its description (optional) are separated
            # by the (first) `: ` substring
            task_data_array = task_data.split(": ", 1)
            tasks.append(
                TodoTaskData(
                    task=task_data_array[0].strip(),
                    desc=task_data_array[1].strip() if len(task_data_array) > 1 else "",
                )
            )
    return TodoData(task_list_title=task_list_title, tasks=tasks)


def parse_roll_extra_data(content: str) -> RollData:
    # Used to extract count and range form command. 
    # Will default values if an option isnt present.
    line = content.strip()
    # Two ways of matching first is: /roll "1d6" or  "1 6" == count: 1, range: 6.
    # Second is: /roll "6" == count: 1, range: 6.
    match = re.search(r"\A(\d{0,3})\s{0,3}[dD\s]\s{0,3}(\d{1,4})|\A(\d{0,4})", line)
    # default values
    count = MIN_ROLL_COUNT
    range = MIN_ROLL_RANGE
    if match:
        if match[1]:
            count = int(match[1])
        if match[2]:
            range = int(match[2])
        if match[3]:
            range = int(match[3])
    return RollData(range=range, count=count)


def get_extra_data_from_widget_type(
    content: str, widget_type: str | None
) -> PollData | TodoData | RollData:
    if widget_type == "poll":
        return parse_poll_extra_data(content)
    elif widget_type == "roll":
        return parse_roll_extra_data(content)
    else:
        return parse_todo_extra_data(content)


def roll_dice_widget(message_id: int) -> NewRoll:
    # Rolls the dice on specific config
    extra_data = get_widget_extra_data(message_id=message_id)
    values = []
    total = 0
    for _ in range(extra_data["count"]):
        val = randint(1, extra_data["range"])
        values.append(val)
        total += val

    return NewRoll(values=values, total=total)


def get_new_roll_content(message_id: int) -> str:
    roll_content = dict(
        **asdict(roll_dice_widget(message_id=message_id)),
        type="new_roll",
    )
    return json.dumps(roll_content)


def get_total_rolls(message_id: int) -> int:
    return (
        SubMessage.objects.filter(message_id=message_id, msg_type="widget").only("content").count()
    )


def do_widget_post_save_actions(send_request: SendMessageRequest) -> None:
    """
    This code works with the web app; mobile and other
    clients should also start supporting this soon.
    """
    message_content = send_request.message.content
    sender_id = send_request.message.sender_id
    message_id = send_request.message.id

    widget_type = None
    extra_data = None

    widget_type, extra_data = get_widget_data(message_content)
    widget_content = send_request.widget_content
    if widget_content is not None:
        # Note that we validate this data in check_message,
        # so we can trust it here.
        widget_type = widget_content["widget_type"]
        extra_data = widget_content["extra_data"]

    if widget_type:
        content = dict(
            widget_type=widget_type,
            extra_data=extra_data,
        )
        submessage = SubMessage(
            sender_id=sender_id,
            message_id=message_id,
            msg_type="widget",
            content=json.dumps(content),
        )
        submessage.save()
        # Send a roll with the config
        if widget_type == "roll":
            roll_submessage = SubMessage(
                sender_id=sender_id,
                message_id=message_id,
                msg_type="widget",
                content=get_new_roll_content(message_id=message_id),
            )
            roll_submessage.save()
        send_request.submessages = SubMessage.get_raw_db_rows([message_id])


def get_widget_type(*, message_id: int) -> str | None:
    submessage = (
        SubMessage.objects.filter(
            message_id=message_id,
            msg_type="widget",
        )
        .only("content")
        .first()
    )

    if submessage is None:
        return None

    try:
        data = json.loads(submessage.content)
    except Exception:
        return None

    try:
        return data["widget_type"]
    except Exception:
        return None


def get_widget_extra_data(*, message_id: int) -> None | dict:
    submessage = (
        SubMessage.objects.filter(
            message_id=message_id,
            msg_type="widget",
        )
        .only("content")
        .first()
    )

    if submessage is None:
        return None

    try:
        data = json.loads(submessage.content)
    except Exception:
        return None

    try:
        return data["extra_data"]
    except Exception:
        return None


def is_widget_message(message: Message) -> bool:
    # Right now all messages that are widgetized use submessage, and vice versa.
    return message.submessage_set.exists()
