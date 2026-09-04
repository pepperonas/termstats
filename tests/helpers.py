"""Shared test helpers.

Lives beside conftest.py so pytest's default import mode puts it on sys.path.
"""

from rich.console import Console
from rich.text import Text


def plain(renderable, width=80, height=40):
    """Render something the way a terminal would, then strip the styling.

    Assertions go against what a human would SEE. The renderables carry rich markup,
    embedded ANSI from plotext and nested layouts; matching on their repr would pin the
    implementation rather than the output.

    ⚠️ A bare Text is printed with its own no_wrap/overflow passed explicitly, because
    Console.print pushes loose Text objects through Text.join, which builds a fresh Text
    and DISCARDS those two attributes. Inside a Panel or Layout - which is the only way
    the dashboard ever renders them - __rich_console__ is called directly and honours
    them. Printing a bare meter without this would show it wrapping when it does not.
    """
    console = Console(width=width, height=height, no_color=True, legacy_windows=False)
    kwargs = {}
    if isinstance(renderable, Text):
        kwargs = {"no_wrap": renderable.no_wrap, "overflow": renderable.overflow}
    with console.capture() as cap:
        console.print(renderable, **kwargs)
    return cap.get()
