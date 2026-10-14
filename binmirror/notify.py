"""Notifica di sistema a fine sincronizzazione (macOS: Centro notifiche)."""
import subprocess
import sys


def _q(text):
    return '"%s"' % str(text).replace("\\", "\\\\").replace('"', '\\"')


def notify(title, message):
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e", "display notification %s with title %s"
                            % (_q(message), _q(title))], timeout=5, capture_output=True)
        elif sys.platform.startswith("linux"):
            subprocess.run(["notify-send", title, message], timeout=5, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass
