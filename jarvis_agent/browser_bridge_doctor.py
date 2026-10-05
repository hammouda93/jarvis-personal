"""Read-only health check for the normal-profile Chrome Native Messaging bridge."""
from __future__ import annotations

import argparse
import json
import sys

from .browser_core import BrowserCore, NativeBrowserTransport


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()

    core = BrowserCore(
        NativeBrowserTransport(
            args.config,
            timeout_s=max(0.5, min(args.timeout, 5.0)),
        )
    )
    try:
        tabs = core.list_tabs()
        active = core.get_active_tab()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "hint": (
                        "Vérifiez que l'extension Personal AI Browser Bridge est "
                        "chargée dans le profil Chrome normal et cliquez son icône "
                        "pour reconnecter le Native Messaging host."
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 2

    print(
        json.dumps(
            {
                "ok": True,
                "tabs": tabs,
                "active_tab": active,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
