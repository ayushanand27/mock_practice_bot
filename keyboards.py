"""Reply and inline keyboards for the study bot."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from config import CATEGORIES, CATEGORY_TOPICS, TEST_TYPES

MAIN_MENU_LABELS = (
    "📚 Study",
    "Notes",
    "Reminders",
    "Help",
)


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["📚 Study", "Upload"],
            ["📊 Progress", "📝 Review"],
            ["Notes", "Reminders"],
            ["Help"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def categories_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"cat:{cid}")]
        for cid, (label, _) in CATEGORIES.items()
    ]
    return InlineKeyboardMarkup(rows)


def topics_keyboard(category_id: str) -> InlineKeyboardMarkup:
    topics = CATEGORY_TOPICS.get(category_id, [])
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for slug, label in topics:
        row.append(InlineKeyboardButton(label, callback_data=f"top:{category_id}:{slug}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [InlineKeyboardButton("📚 All topics", callback_data=f"top:{category_id}:all")]
    )
    rows.append([InlineKeyboardButton("← Categories", callback_data="study:home")])
    return InlineKeyboardMarkup(rows)


def mode_keyboard(category_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📖 Learn", callback_data=f"mode:learn:{category_id}"),
                InlineKeyboardButton("📝 Test", callback_data=f"mode:test:{category_id}"),
            ],
            [
                InlineKeyboardButton(
                    "🎯 Practice mistakes", callback_data=f"mode:mistakes:{category_id}"
                ),
            ],
            [
                InlineKeyboardButton("📊 Progress", callback_data="study:stats"),
                InlineKeyboardButton("📝 Review list", callback_data="study:review"),
            ],
            [
                InlineKeyboardButton("Change topic", callback_data=f"cat:{category_id}"),
                InlineKeyboardButton("← Categories", callback_data="study:home"),
            ],
        ]
    )


def difficulty_keyboard(category_id: str, test_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Easy", callback_data=f"diff:easy:{test_type}:{category_id}"),
                InlineKeyboardButton("Medium", callback_data=f"diff:medium:{test_type}:{category_id}"),
                InlineKeyboardButton("Hard", callback_data=f"diff:hard:{test_type}:{category_id}"),
            ],
            [InlineKeyboardButton("← Back", callback_data=f"mode:test:{category_id}")],
        ]
    )


def test_types_keyboard(category_id: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"ttype:{tid}:{category_id}")]
        for label, tid in TEST_TYPES
    ]
    rows.append([InlineKeyboardButton("← Back", callback_data=f"cat:{category_id}")])
    return InlineKeyboardMarkup(rows)


def learn_controls_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔊 Hear answer", callback_data="learn:voice"),
                InlineKeyboardButton("Switch to Test", callback_data="study:switch_test"),
            ],
            [
                InlineKeyboardButton("Change topic", callback_data="study:retopic"),
                InlineKeyboardButton("📊 Progress", callback_data="study:stats"),
            ],
            [InlineKeyboardButton("← Categories", callback_data="study:home")],
        ]
    )


def test_controls_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Next question", callback_data="test:next"),
                InlineKeyboardButton("Change type", callback_data="test:change_type"),
            ],
            [
                InlineKeyboardButton("End + report", callback_data="test:end"),
                InlineKeyboardButton("🎯 Mistakes only", callback_data="study:mistakes_now"),
            ],
            [InlineKeyboardButton("← Categories", callback_data="study:home")],
        ]
    )


def upload_category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"upload_cat:{cid}")]
        for cid, (label, _) in CATEGORIES.items()
    ]
    rows.append([InlineKeyboardButton("Cancel", callback_data="upload:cancel")])
    return InlineKeyboardMarkup(rows)


def interview_topics_keyboard() -> InlineKeyboardMarkup:
    topics = (
        ("Python", "topic:Python"),
        ("System Design", "topic:System Design"),
        ("Behavioral", "topic:Behavioral"),
        ("SQL / Databases", "topic:SQL"),
        ("JavaScript", "topic:JavaScript"),
    )
    rows = [[InlineKeyboardButton(label, callback_data=cb)] for label, cb in topics]
    rows.append([InlineKeyboardButton("Cancel", callback_data="interview:cancel")])
    return InlineKeyboardMarkup(rows)


def interview_controls_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Next question", callback_data="interview:next"),
                InlineKeyboardButton("End practice", callback_data="interview:end"),
            ]
        ]
    )


def cancel_reminder_keyboard(job_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Cancel this reminder", callback_data=f"remind_cancel:{job_name}")]]
    )


def reminders_list_keyboard(job_names: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"Cancel #{i + 1}", callback_data=f"remind_cancel:{name}")]
        for i, name in enumerate(job_names)
    ]
    if not rows:
        rows = [[InlineKeyboardButton("No pending reminders", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)
