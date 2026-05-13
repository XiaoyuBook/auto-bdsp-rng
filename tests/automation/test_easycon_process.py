from __future__ import annotations

import subprocess

from auto_bdsp_rng.automation.easycon.process import no_window_subprocess_kwargs


def test_no_window_subprocess_kwargs_hides_windows_console():
    kwargs = no_window_subprocess_kwargs()

    assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
