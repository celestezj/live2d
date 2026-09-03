"""mock_control.py — interactive TCP control client for desktop_pet.py.

Connect to a running pet's --control-port and send emotion / say / mouth
commands. Use it to test the AI-pipeline control channel without a real AI.

Usage:
  python desktop_pet.py --control-port 5000          # 先起宠物
  python mock_control.py --port 5000                 # 再开这个控制端

Type an emotion name (平和/开心/兴奋/...) to switch; or a command:
  say <文本>         显示头顶说话框 (e.g. say 你好呀，我是桌宠)
  say null / say     隐藏说话框 (say 空即隐藏)
  emotion null       表情复位回 平和
  mouth <0..1>       force the mouth open (e.g. mouth 0.7)
  mouth null         release the mouth back to idle/lipsync
  demo              auto-cycle a few emotions
  help / quit
"""
import argparse
import json
import socket
import sys
import time

from desktop_pet import EMOTIONS          # reuse the same emotion table

DEMO_SEQ = ["平和", "开心", "兴奋", "惊喜", "温柔", "关切", "好奇", "期待", "无奈", "失望", "沮丧", "难过", "担心", "不满", "生气", "愤怒"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args()

    try:
        s = socket.create_connection((args.host, args.port), timeout=5)
    except OSError as exc:
        print(f"cannot connect to {args.host}:{args.port} — is the pet running "
              f"with --control-port {args.port}? ({exc})")
        sys.exit(1)

    print(f"connected to {args.host}:{args.port}. type an emotion to send it.")
    print(f"emotions: {'、'.join(EMOTIONS)}")
    print("commands: say <文本> | say(null 隐藏) | emotion null(复位平和) | "
          "mouth <0..1> | mouth null | demo | help | quit")

    def send(payload):
        try:
            s.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        except OSError as exc:
            print(f"send failed — is the pet still running? ({exc})")
            sys.exit(1)

    try:
        while True:
            try:
                line = input("pet> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            low = line.lower()
            if low in ("quit", "exit", "q"):
                break
            if low == "help":
                print(f"  emotions: {'、'.join(EMOTIONS)}")
                print("  say <文本>      show speech bubble (e.g. 'say 你好呀，我是桌宠')")
                print("  say null / say  hide the speech bubble")
                print("  emotion null    reset to the default 平和")
                print("  mouth <0..1>    force mouth open (e.g. 'mouth 0.7')")
                print("  mouth null      release the mouth")
                print("  demo            auto-cycle a few emotions")
                continue
            if low == "demo":
                for name in DEMO_SEQ:
                    send({"emotion": name})
                    print(f"  -> 情绪 {name}")
                    time.sleep(3)
                continue
            if low.startswith("say"):
                _, _, val = line.partition(" ")
                val = val.strip()
                text = None if not val or val.lower() in ("null", "none") else val
                send({"say": text})
                print("  -> 说话框 " + (f"显示 {val!r}" if text else "隐藏"))
                continue
            if low == "emotion null" or low == "emotion none":
                send({"emotion": None})
                print("  -> 情绪 复位平和")
                continue
            if low.startswith("mouth"):
                _, _, val = line.partition(" ")
                val = val.strip().lower()
                payload = {"mouth": None if val in ("null", "none", "") else float(val)}
                send(payload)
                print(f"  -> mouth {payload['mouth']}")
                continue
            if line in EMOTIONS:
                send({"emotion": line})
                print(f"  -> 情绪 {line}")
                continue
            print(f"unknown: {line!r} (emotion name, or "
                  "'say <文本>'/'say null'/'emotion null'/'mouth <0..1>'/'mouth null')")
    finally:
        s.close()
        print("bye")


if __name__ == "__main__":
    main()
