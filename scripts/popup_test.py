"""弹窗干扰测试 — 09:02 开始，连弹10次，每次10秒"""
import time
import tkinter as tk
import threading
from datetime import datetime

TARGET_HOUR = 9
TARGET_MIN = 2
POPUP_COUNT = 10
GAP_SECONDS = 1    # 弹窗之间几乎无间隔
DURATION = 10      # 每个停留 10 秒


def show_popup(count: int):
    root = tk.Tk()
    root.title(f"弹窗 #{count}")
    root.attributes("-topmost", True)
    root.geometry("300x100+750+350")
    root.configure(bg="#fff3cd")
    tk.Label(
        root,
        text=f"干扰弹窗 #{count}/{POPUP_COUNT}\n{DURATION}秒后关闭",
        font=("Microsoft YaHei", 13),
        bg="#fff3cd",
    ).pack(expand=True)
    root.after(int(DURATION * 1000), root.destroy)
    root.mainloop()


def main():
    now = datetime.now()
    target = now.replace(hour=TARGET_HOUR, minute=TARGET_MIN, second=0, microsecond=0)
    wait = (target - now).total_seconds()
    if wait > 0:
        print(f"等待 {wait:.0f}s ...")
        time.sleep(wait)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 连续弹窗 {POPUP_COUNT} 次！")
    for i in range(1, POPUP_COUNT + 1):
        t = threading.Thread(target=show_popup, args=(i,), daemon=True)
        t.start()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 弹窗 #{i}")
        time.sleep(GAP_SECONDS)
    print("全部弹完！")


if __name__ == "__main__":
    main()
