import re

import asknews


def test_version_is_semantic() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", asknews.__version__)
