#!/usr/bin/env python3
"""Standalone GUI picker script for Windows to guarantee clean topmost dialog popups with UTF-8 paths."""
import ctypes
import os
import sys

def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    mode = sys.argv[1] if len(sys.argv) > 1 else "folder"
    initial_dir = sys.argv[2] if len(sys.argv) > 2 and os.path.isdir(sys.argv[2]) else os.path.expanduser("~/Downloads")

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.geometry("1x1+200+200")
        root.attributes("-alpha", 0.0)
        root.attributes("-topmost", True)
        root.deiconify()
        root.lift()
        root.focus_force()
        root.update()

        if mode == "folder":
            path = filedialog.askdirectory(
                parent=root,
                initialdir=initial_dir,
                title="Chọn thư mục lưu video MP4",
                mustexist=True,
            )
        else:
            path = filedialog.asksaveasfilename(
                parent=root,
                initialdir=initial_dir,
                title="Chọn nơi lưu video MP4",
                defaultextension=".mp4",
                initialfile="minimax_h3_video.mp4",
                filetypes=[("MP4 Video", "*.mp4"), ("All Files", "*.*")],
            )
        root.destroy()
        if path:
            print(os.path.normpath(path))
            return
    except Exception:
        pass
    print("")

if __name__ == "__main__":
    main()
