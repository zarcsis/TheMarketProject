"""Button menus for the Telegram remote: a bare command asks for what it needs.

/session asks which bot, then start or stop; /close lists the open positions; /strategies
toggles the presets. Each answer travels in the callback data of the next buttons
("m:session:demo"), so nothing is kept between taps. Every answer is checked against the
step's options before a command is built, and the command then goes through
`remote.parse` and the usual second tap for dangerous commands.
"""

from dataclasses import dataclass


class MenuError(ValueError):
    pass


@dataclass(frozen=True)
class Step:
    question: str
    options: tuple[tuple[str, str], ...]  # (label, value)


ENV = Step("Which bot?", (("Live", "live"), ("Demo", "demo")))
POSITIONS = "positions"  # a step built from the bot's open positions

STEPS: dict[str, tuple[Step | str, ...]] = {
    "status": (ENV,),
    "events": (ENV,),
    "check": (ENV,),
    "pause": (ENV,),
    "resume": (ENV,),
    "restart": (ENV,),
    "flatten": (ENV,),
    "report": (
        ENV,
        Step("Which period?", (("24 hours", "24"), ("3 days", "72"), ("7 days", "168"))),
    ),
    "session": (ENV, Step("Start or stop the session?", (("Start", "start"), ("Stop", "stop")))),
    "auto": (ENV, Step("Let the strategies trade?", (("On", "on"), ("Off", "off")))),
    "on-stop": (
        ENV,
        Step(
            "When the session ends, open positions are:",
            (("Closed (flatten)", "flatten"), ("Kept under their stops", "keep")),
        ),
    ),
    "risk-scale": (
        ENV,
        Step(
            "Risk scale? Below 0.3 entries are rejected (Binance's 5 USDT minimum); "
            "40% stops need about 0.6.",
            (("0.3", "0.3"), ("0.5", "0.5"), ("0.6", "0.6"), ("1", "1")),
        ),
    ),
    "close": (ENV, POSITIONS),
    "strategies": (
        ENV,
        Step("Show or choose the strategies?", (("Show", "show"), ("Choose", "choose"))),
    ),
    "transfer": (
        Step("Which way?", (("Spot → futures", "to-futures"), ("Futures → spot", "to-spot"))),
        Step("How many USDT?", (("10", "10"), ("25", "25"), ("50", "50"), ("100", "100"))),
    ),
}


def _resolve(step: Step | str, choices: list[str], positions: dict[str, list[str]]) -> Step:
    if step == POSITIONS:
        env = choices[0]
        symbols = positions.get(env, [])
        return Step(f"Close which {env} position?", tuple((s, s) for s in symbols))
    assert isinstance(step, Step)
    return step


def next_step(name: str, choices: list[str], positions: dict[str, list[str]]) -> Step | None:
    """The next question of a command's menu, or None when every step is answered.

    Raises MenuError for an unknown command or an answer that is not one of the options.
    """
    if name not in STEPS:
        raise MenuError(f"no menu for {name}")
    steps = STEPS[name]
    if len(choices) > len(steps):
        raise MenuError("too many answers")
    for index, answer in enumerate(choices):
        step = _resolve(steps[index], choices[:index], positions)
        if answer not in {value for _, value in step.options}:
            raise MenuError(f"'{answer}' is not an option")
    if len(choices) == len(steps):
        return None
    return _resolve(steps[len(choices)], choices, positions)


def command(name: str, choices: list[str]) -> str:
    """The `mb` command of a completed menu."""
    if name == "transfer":
        direction, amount = choices
        return f"mb transfer {amount} {direction}"
    env, *rest = choices
    prefix = "mb --env demo " if env == "demo" else "mb "
    if name == "report":
        return f"{prefix}report --hours {rest[0]}"
    if name == "strategies":
        return f"{prefix}strategies show"
    return prefix + " ".join([name, *rest])


def encode(name: str, choices: list[str]) -> str:
    data = ":".join(["m", name, *choices])
    if len(data.encode()) > 64:  # Telegram's limit for callback data
        raise MenuError("callback data too long")
    return data


def decode(data: str) -> tuple[str, list[str]]:
    _, name, *choices = data.split(":")
    return name, choices


# The strategy chooser: a bit per preset, in the order of `presets.names()`.


def mask_of(configured: str, presets: list[str]) -> int:
    chosen = {name.strip() for name in configured.split(",") if name.strip()}
    return sum(1 << index for index, name in enumerate(presets) if name in chosen)


def chosen(mask: int, presets: list[str]) -> list[str]:
    return [name for index, name in enumerate(presets) if mask >> index & 1]


def strategy_buttons(env: str, mask: int, presets: list[str]) -> list[list[tuple[str, str]]]:
    """One toggle per preset, then Save; rows of (label, callback data)."""
    rows = [
        [(f"{'☑' if mask >> index & 1 else '☐'} {name}", f"s:{env}:{mask:x}:{index}")]
        for index, name in enumerate(presets)
    ]
    rows.append([("Save", f"s:{env}:{mask:x}:ok")])
    return rows


def strategy_answer(data: str, presets: list[str]) -> tuple[str, int, bool]:
    """(env, new mask, save) from a chooser button. Raises MenuError on bad data."""
    try:
        _, env, mask_hex, action = data.split(":")
        mask = int(mask_hex, 16)
    except ValueError:
        raise MenuError("bad strategy button") from None
    if env not in ("live", "demo") or mask >> len(presets):
        raise MenuError("bad strategy button")
    if action == "ok":
        return env, mask, True
    if not action.isdigit() or int(action) >= len(presets):
        raise MenuError("bad strategy button")
    return env, mask ^ (1 << int(action)), False
