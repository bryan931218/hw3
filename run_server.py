import os
import subprocess
import sys


def main():
    env = os.environ.copy()
    port = env.get("PORT", "5000")
    print(f"啟動後端伺服器 (PORT={port})...")
    cmd = [sys.executable, "-m", "server.server"]
    subprocess.call(cmd, env=env)


if __name__ == "__main__":
    main()
