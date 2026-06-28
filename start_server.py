"""
简易HTTP服务器 - 用于本地预览Fund-Radar看板
用法：python start_server.py
访问：http://localhost:8000
"""

import http.server
import socketserver
import webbrowser
import os

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        print(f"[访问] {args[0]}")

if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"=" * 50)
        print(f"  Fund-Radar 本地服务器已启动")
        print(f"  访问地址: http://localhost:{PORT}")
        print(f"  按 Ctrl+C 停止服务器")
        print(f"=" * 50)

        # 自动打开浏览器
        webbrowser.open(f"http://localhost:{PORT}/milestone3_ui_template.html")

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n服务器已停止")
