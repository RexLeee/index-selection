"""Process 03 after 14 days if every host is held to a per-host rate: hosts that cannot finish, URLs left.

Usage: left14.py <sizes file> <rate> [<rate> ...]
"""
import sys
from pathlib import Path

sizes = [int(x) for x in Path(sys.argv[1]).read_text().split()]
total = sum(sizes)
for rate in map(float, sys.argv[2:]):
    cap = rate * 14 * 86400
    left = sum(max(0, s - cap) for s in sizes)
    print(f"rate {rate:.3f}: {cap:,.0f} docs per host in 14 d, {sum(s > cap for s in sizes)} hosts cannot finish,"
          f" {left:,.0f} URLs left = {left / total:.2%}; 241,920 needs {241920 / rate / 86400:.1f} d")
