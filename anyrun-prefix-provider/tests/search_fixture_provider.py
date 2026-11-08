#!/usr/bin/python3
"""Delayed two-plugin IPC fixture for search UI integration tests."""
import heapq
import json
import os
import select
import socket
import sys
import time

plugins = [{"name": "Applications", "icon": "applications-other"}, {"name": "Shell", "icon": "utilities-terminal"}]

def item(title):
    return {"title": title, "description": None, "use_pango": False, "icon": None, "id": 1}

with socket.socket(socket.AF_UNIX) as connection:
    connection.connect(sys.argv[-1])
    def send(value):
        connection.sendall(json.dumps(value).encode() + b"\n")
    send({"Ready": {"info": plugins}})
    scheduled = []
    serial = 0
    buffer = b""
    submenu = False
    while True:
        now = time.monotonic()
        while scheduled and scheduled[0][0] <= now:
            _, _, response = heapq.heappop(scheduled)
            send(response)
        timeout = max(0, scheduled[0][0] - now) if scheduled else 1
        if not select.select([connection], [], [], timeout)[0]:
            continue
        chunk = connection.recv(65536)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            request = json.loads(line)
            with open(os.environ["ANYRUN_SEARCH_LOG"], "a") as log:
                log.write(json.dumps(request) + "\n")
            if request == "Quit":
                sys.exit(0)
            if request == "History":
                send({"History": {"entries": [{"plugin": plugins[0], "selection": item("History seed"), "query": "seed"}]}})
            elif isinstance(request, dict) and "Query" in request:
                text = request["Query"]["text"]
                for index, plugin in enumerate(plugins):
                    title = "Open" if text == "menu" and submenu and index == 0 else "Stable" if text.startswith("stable") else ("A:" if index == 0 else "B:") + text
                    matches = [item(title)] if text and (index == 0 or text == "menu") else []
                    serial += 1
                    heapq.heappush(scheduled, (time.monotonic() + index * 0.25, serial,
                        {"Matches": {"query": text, "plugin": plugin, "matches": matches}}))
            elif isinstance(request, dict) and "Handle" in request:
                selected = request["Handle"]["selection"]["title"]
                if selected == "A:menu":
                    submenu = True
                    result = {"Refresh": True}
                elif selected == "Open":
                    submenu = False
                    result = {"Refresh": False}
                else:
                    result = "Close"
                send({"Handled": {"plugin": request["Handle"]["plugin"], "result": result}})
