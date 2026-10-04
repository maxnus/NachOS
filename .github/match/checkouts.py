"""Clone the repositories the bot's config places next to it, with the token in a header for each command only.

Run by .github/workflows/match.yml with CHECKOUTS, the config's list as JSON, and TOKEN in the environment.
"""

import base64
import json
import os
import subprocess


def main() -> None:
    auth = base64.b64encode(f"x-access-token:{os.environ['TOKEN']}".encode()).decode()
    for checkout in json.loads(os.environ["CHECKOUTS"]):
        args = ["git", "-c", f"http.extraHeader=Authorization: Basic {auth}", "clone", "--depth", "1"]
        if checkout.get("ref"):
            args += ["--branch", checkout["ref"]]
        subprocess.run([*args, f"https://github.com/{checkout['repository']}", checkout["path"]], check=True)


if __name__ == "__main__":
    main()
