#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""t0_banner_grab.py — T0 端口复核兜底脚本

port-check 自 D-3 起已原生读 banner(非 TLS 端口 recv 问候语, 静默端口报
open_no_banner), 常规复核直接用工具; 本脚本仅在工具不可用/需自定义限速时兜底。

ROE: 单线程, 间隔>=1s, connect+recv 即止, 无爆破无 payload 投递。
用法:
  python [SKILL_DIR]/scripts/t0_banner_grab.py --targets-file targets.json --out out.json
targets.json 格式: [{"target": "203.0.113.5:3306", "label": "MySQL-示例"}, ...]
缺省 --out 时仅打印 stdout JSON(逐行 + 不落盘)。
"""
import argparse
import json
import socket
import time
from pathlib import Path


def grab(entry: dict, timeout: float) -> dict:
    host, _, port = entry["target"].rpartition(":")
    result = {"target": entry["target"], "label": entry.get("label"), "open": False,
              "banner": None, "latency_ms": None, "error": None}
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    started = time.time()
    try:
        sock.connect((host, int(port)))
        result["open"] = True
        result["latency_ms"] = round((time.time() - started) * 1000)
        sock.settimeout(4)
        try:
            data = sock.recv(256)
            if data:
                text = data.decode("utf-8", errors="replace")
                result["banner"] = text[:160].replace("\r", "\\r").replace("\n", "\\n")
        except socket.timeout:
            result["banner"] = None  # 端口开放但不主动发 banner
    except socket.timeout:
        result["error"] = "filtered_or_no_response"
    except ConnectionRefusedError:
        result["error"] = "refused"
    except OSError as exc:
        result["error"] = f"os_error:{exc}"
    finally:
        sock.close()
    return result


def main():
    parser = argparse.ArgumentParser(description="T0 banner 抓取(兜底;常规走 port-check 原生 banner)")
    parser.add_argument("--targets-file", required=True,
                        help='JSON: [{"target":"ip:port","label":"..."}, ...]')
    parser.add_argument("--out", default=None, help="结果 JSON 落盘路径(缺省仅 stdout)")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--interval", type=float, default=1.2, help="ROE 限速间隔秒")
    args = parser.parse_args()
    targets = json.loads(Path(args.targets_file).read_text(encoding="utf-8"))
    results = []
    for entry in targets:
        result = grab(entry, args.timeout)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False))
        time.sleep(args.interval)
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        print(f"saved: {args.out}")


if __name__ == "__main__":
    main()
