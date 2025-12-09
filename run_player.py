import os
import subprocess
import sys


def main():
    env = os.environ.copy()
    server = env.get("GAME_SERVER_URL", "http://127.0.0.1:5000")
    print(f"啟動玩家端，伺服器：{server}")
    cmd = [sys.executable, os.path.join("player", "client.py")]
    try:
        subprocess.call(cmd, env=env)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
