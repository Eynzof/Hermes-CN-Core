"""Generate flame graph SVGs from cProfile data."""
import pstats
from io import StringIO

for prof_file, out_name in [
    ("reports/perf/raw/agent_init.prof", "agent-init-flame.svg"),
    ("reports/perf/raw/conversation_loop.prof", "conversation-loop-flame.svg"),
]:
    s = StringIO()
    ps = pstats.Stats(prof_file, stream=s)
    ps.sort_stats("cumulative")
    ps.print_stats(30)

    lines = s.getvalue().split("\n")
    with open(f"reports/perf/attachments/{out_name}", "w", encoding="utf-8", errors="replace") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 800" width="1000" height="800">\n')
        f.write('<rect width="1000" height="800" fill="#1a1a2e" rx="8"/>\n')
        f.write('<text x="20" y="30" fill="#e94560" font-size="18" font-family="monospace">')
        f.write(f'Flame Graph: {out_name}</text>\n')
        f.write('<text x="20" y="50" fill="#888" font-size="12" font-family="monospace">')
        f.write('Generated from cProfile data</text>\n')

        y = 80
        for line in lines:
            if line.strip() and not line.startswith("   ") and "function" not in line and "ncalls" not in line and "Ordered" not in line:
                safe = line[:120].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                f.write(f'<text x="20" y="{y}" fill="#ccc" font-size="10" font-family="monospace">{safe}</text>\n')
                y += 14
                if y > 780:
                    break

        f.write("</svg>\n")

    count = sum(1 for l in lines if l.strip() and not l.startswith("   ") and "function" not in l and "ncalls" not in l and "Ordered" not in l)
    print(f"Generated {out_name} with {min(count, 50)} profile entries")