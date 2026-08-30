# -*- coding: utf-8 -*-
"""第六题（2）：点击按钮打开摄像头，点击按钮抓拍画面"""
import cv2
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
from datetime import datetime


class CameraApp:
    def __init__(self, root):
        self.root = root
        self.root.title('摄像头抓拍程序')
        self.cap = None
        self.running = False

        self.label = tk.Label(root)          # 视频预览区
        self.label.pack(padx=10, pady=10)

        ttk.Button(root, text='打开摄像头', command=self.open).pack(side=tk.LEFT, padx=10)
        ttk.Button(root, text='抓拍画面', command=self.capture).pack(side=tk.LEFT, padx=10)
        ttk.Button(root, text='关闭摄像头', command=self.close).pack(side=tk.LEFT, padx=10)

    def open(self):
        self.cap = cv2.VideoCapture(0)       # 打开默认摄像头
        if not self.cap.isOpened():
            self.label.config(text='无法打开摄像头')
            return
        self.running = True
        self.update_frame()

    def update_frame(self):
        """每 30ms 刷新一帧，实现实时预览"""
        if not self.running:
            return
        ok, frame = self.cap.read()
        if ok:
            img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)   # BGR → RGB
            img = Image.fromarray(img)
            photo = ImageTk.PhotoImage(img)
            self.label.config(image=photo)
            self.label.image = photo                       # 防止被回收
        self.root.after(30, self.update_frame)

    def capture(self):
        """抓拍当前画面并另存为 jpg"""
        if self.cap is None:
            return
        ok, frame = self.cap.read()
        if ok:
            name = f'capture_{datetime.now():%Y%m%d_%H%M%S}.jpg'
            cv2.imwrite(name, frame)
            print(f'已保存：{name}')

    def close(self):
        self.running = False
        if self.cap:
            self.cap.release()


if __name__ == '__main__':
    root = tk.Tk()
    CameraApp(root)
    root.mainloop()
