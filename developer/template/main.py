"""
Minimal game template used by Developer Client uploads.
Replace the logic with your own gameplay. Entry point must match manifest.json.
"""

import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", default="Player")
    parser.add_argument("--server", default="")
    args = parser.parse_args()
    print(f"Hello {args.player}! 這裡可以放入你的遊戲邏輯。伺服器位址: {args.server}")


if __name__ == "__main__":
    main()
