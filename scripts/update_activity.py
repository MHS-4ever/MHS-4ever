import datetime as dt
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ["GH_STATS_TOKEN"]
USERNAME = os.environ["GH_USERNAME"]
README = "README.md"
CHART = "images/code-activity.svg"
DAYS = 30
API = "https://api.github.com"

HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "daily-code-activity-dashboard",
}

# Palette shared with the other images in this profile.
SURFACE = "#0d1117"
CARD = "#111827"
BORDER = "#1f2937"
AXIS = "#334155"
TEXT = "#e2e8f0"
MUTED = "#94a3b8"
CYAN = "#38bdf8"
TEAL = "#0e7490"
INDIGO = "#818cf8"
VIOLET = "#a78bfa"
VIOLET_DEEP = "#6d28d9"
FONT = "Segoe UI, Helvetica, Arial, sans-serif"


def get_json(url):
    request = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in (404, 409):  # missing or empty repository
                return []
            if error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0":
                reset = int(error.headers.get("X-RateLimit-Reset", "0"))
                time.sleep(max(1, min(reset - int(time.time()) + 1, 900)))
            elif error.code in (403, 429, 500, 502, 503, 504):
                time.sleep(2 ** attempt * 2)
            else:
                raise
        except urllib.error.URLError:
            time.sleep(2 ** attempt * 2)
    raise RuntimeError("GitHub API request failed after retries")


def paginate(url):
    sep = "&" if "?" in url else "?"
    page = 1
    while True:
        items = get_json(f"{url}{sep}per_page=100&page={page}")
        yield from items
        if len(items) < 100:
            return
        page += 1


def window(today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    start = today - dt.timedelta(days=DAYS - 1)
    return [start + dt.timedelta(days=i) for i in range(DAYS)]


def collect(days):
    """Per-day commits and line changes, bucketed by commit author date (UTC)."""
    stats = {day: {"commits": 0, "added": 0, "deleted": 0} for day in days}
    since = dt.datetime.combine(days[0], dt.time.min, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = urllib.parse.urlencode({"author": USERNAME, "since": since})

    seen = set()
    repos = paginate(f"{API}/user/repos?affiliation=owner&visibility=all&sort=pushed")
    for repo in repos:
        if repo["owner"]["login"].lower() != USERNAME.lower() or repo.get("archived"):
            continue
        if repo.get("pushed_at") and repo["pushed_at"] < since:
            continue  # nothing pushed inside the window
        name = repo["full_name"]
        for item in paginate(f"{API}/repos/{name}/commits?{query}"):
            sha = item["sha"]
            if sha in seen or len(item.get("parents", [])) > 1:
                continue  # forks share SHAs; merge commits double-count
            seen.add(sha)
            detail = get_json(f"{API}/repos/{name}/commits/{sha}")
            if not detail:
                continue
            stamp = detail["commit"]["author"]["date"].replace("Z", "+00:00")
            day = dt.datetime.fromisoformat(stamp).astimezone(dt.timezone.utc).date()
            if day not in stats:
                continue
            totals = detail.get("stats") or {}
            stats[day]["commits"] += 1
            stats[day]["added"] += totals.get("additions", 0)
            stats[day]["deleted"] += totals.get("deletions", 0)
    return stats


def fmt(n):
    return f"{n:,}"


def fmt_card(n):
    return f"{n / 1_000_000:.1f}M" if abs(n) >= 1_000_000 else fmt(n)


def fmt_axis(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:g}M"
    if n >= 1000:
        return f"{n / 1000:g}k"
    return str(n)


def signed(n):
    if n == 0:
        return "0"
    return f"+{fmt_card(n)}" if n > 0 else f"−{fmt_card(-n)}"


def short_date(day):
    return f"{day.strftime('%b')} {day.day}"


def summarize(stats):
    days = list(stats)
    added = sum(d["added"] for d in stats.values())
    deleted = sum(d["deleted"] for d in stats.values())
    commits = sum(d["commits"] for d in stats.values())
    active = sum(1 for d in stats.values() if d["commits"])
    peak_add = max(days, key=lambda k: stats[k]["added"])
    peak_del = max(days, key=lambda k: stats[k]["deleted"])
    return {
        "added": added,
        "deleted": deleted,
        "net": added - deleted,
        "commits": commits,
        "active": active,
        "peak_add": peak_add if stats[peak_add]["added"] else None,
        "peak_del": peak_del if stats[peak_del]["deleted"] else None,
    }


def nice_ticks(peak):
    """Up to three round values on a square-root scale, largest first."""
    candidates = sorted(m * 10 ** k for k in range(8) for m in (1, 2, 5) if m * 10 ** k <= peak)
    ticks, last = [], 1.0
    for value in reversed(candidates):
        pos = math.sqrt(value / peak)
        if pos >= 0.18 and last - pos >= 0.2:
            ticks.append(value)
            last = pos
        if len(ticks) == 3:
            break
    return ticks


def bar_path(x, w, base, length, up):
    r = min(3.0, length / 2, w / 2)
    tip = base - length if up else base + length
    s = -1 if up else 1
    return (
        f"M{x:.1f} {base:.1f}V{tip - s * r:.1f}A{r:.1f} {r:.1f} 0 0 {1 if up else 0} {x + r:.1f} {tip:.1f}"
        f"H{x + w - r:.1f}A{r:.1f} {r:.1f} 0 0 {1 if up else 0} {x + w:.1f} {tip - s * r:.1f}V{base:.1f}Z"
    )


def build_chart(stats):
    s = summarize(stats)
    days = list(stats)
    W, H, PAD = 900, 556, 28
    inner = W - 2 * PAD

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'font-family="{FONT}" role="img" aria-labelledby="t d">',
        f'<title id="t">Daily code activity, {short_date(days[0])} to {short_date(days[-1])}</title>',
        f'<desc id="d">{fmt(s["added"])} lines added, {fmt(s["deleted"])} deleted, {fmt(s["commits"])} commits '
        f'on {s["active"]} of {DAYS} days.</desc>',
        "<defs>",
        f'<linearGradient id="brand" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{CYAN}"/>'
        f'<stop offset="1" stop-color="{VIOLET}"/></linearGradient>',
        f'<linearGradient id="add" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="{TEAL}"/>'
        f'<stop offset="1" stop-color="{CYAN}"/></linearGradient>',
        f'<linearGradient id="del" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{VIOLET_DEEP}"/>'
        f'<stop offset="1" stop-color="{VIOLET}"/></linearGradient>',
        f'<radialGradient id="glow" cx="1" cy="0" r="0.9"><stop offset="0" stop-color="{CYAN}" stop-opacity=".09"/>'
        f'<stop offset="1" stop-color="{CYAN}" stop-opacity="0"/></radialGradient>',
        "</defs>",
        f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="14" fill="{SURFACE}" stroke="{BORDER}"/>',
        f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="14" fill="url(#glow)"/>',
    ]

    # Header
    out += [
        f'<rect x="{PAD}" y="26" width="4" height="46" rx="2" fill="url(#brand)"/>',
        f'<text x="{PAD + 16}" y="46" font-size="23" font-weight="700" fill="{TEXT}">Daily code activity</text>',
        f'<text x="{PAD + 16}" y="68" font-size="14" fill="{MUTED}">Last {DAYS} days · '
        f'{short_date(days[0])} – {short_date(days[-1])}, {days[-1].year} · UTC</text>',
        f'<rect x="{W - PAD - 150}" y="32" width="150" height="30" rx="15" fill="{CYAN}" fill-opacity=".1" '
        f'stroke="{CYAN}" stroke-opacity=".4"/>',
        f'<text x="{W - PAD - 75}" y="52" font-size="13" font-weight="600" fill="{TEXT}" text-anchor="middle" '
        f'letter-spacing=".5">Public + private</text>',
    ]

    # Stat cards
    def peak_note(day):
        return f"peak day {short_date(day)}" if day else "no activity"

    per_day = f"{s['commits'] / s['active']:.1f} per active day" if s["active"] else "no activity"
    cards = [
        ("LINES ADDED", signed(s["added"]), peak_note(s["peak_add"]), CYAN),
        ("LINES DELETED", signed(-s["deleted"]), peak_note(s["peak_del"]), VIOLET),
        ("NET CHANGE", signed(s["net"]), "added − deleted", "url(#brand)"),
        ("COMMITS", fmt_card(s["commits"]), per_day, INDIGO),
        ("ACTIVE DAYS", str(s["active"]), f"of {DAYS} days", INDIGO),
    ]
    gap, top, ch = 12, 100, 92
    cw = (inner - gap * (len(cards) - 1)) / len(cards)
    for i, (label, value, note, accent) in enumerate(cards):
        x = PAD + i * (cw + gap)
        size = 28 if len(value) <= 8 else 22
        out += [
            f'<rect x="{x:.1f}" y="{top}" width="{cw:.1f}" height="{ch}" rx="10" fill="{CARD}" stroke="{BORDER}"/>',
            f'<rect x="{x:.1f}" y="{top + 18}" width="4" height="{ch - 36}" rx="2" fill="{accent}"/>',
            f'<text x="{x + 20:.1f}" y="{top + 27}" font-size="12" font-weight="600" fill="{MUTED}" '
            f'letter-spacing="1">{label}</text>',
            f'<text x="{x + 20:.1f}" y="{top + 61}" font-size="{size}" font-weight="700" fill="{TEXT}">{value}</text>',
            f'<text x="{x + 20:.1f}" y="{top + 81}" font-size="13" fill="{MUTED}">{note}</text>',
        ]

    # Chart heading and legend
    out.append(f'<text x="{PAD}" y="232" font-size="16" font-weight="600" fill="{TEXT}">Lines changed per day</text>')
    lx = W - PAD
    for label, color in (("Lines deleted", VIOLET), ("Lines added", CYAN)):
        w = len(label) * 6.6
        out += [
            f'<text x="{lx:.1f}" y="232" font-size="13" fill="{MUTED}" text-anchor="end">{label}</text>',
            f'<rect x="{lx - w - 17:.1f}" y="223" width="10" height="10" rx="3" fill="{color}"/>',
        ]
        lx -= w + 17 + 22

    # Plot area: additions above the baseline, deletions below, one shared square-root scale
    x0, x1 = 88, W - PAD
    mid, half = 362, 108
    slot = (x1 - x0) / len(days)
    bw = slot * 0.62
    peak = max([d[k] for d in stats.values() for k in ("added", "deleted")] + [0]) or 100
    ticks = nice_ticks(peak)

    for t in ticks:
        off = math.sqrt(t / peak) * half
        for y, sign in ((mid - off, "+"), (mid + off, "−")):
            out += [
                f'<line x1="{x0}" x2="{x1}" y1="{y:.1f}" y2="{y:.1f}" stroke="{BORDER}" stroke-dasharray="2 4"/>',
                f'<text x="{x0 - 12}" y="{y + 4.5:.1f}" font-size="12.5" fill="{MUTED}" text-anchor="end">'
                f'{sign}{fmt_axis(t)}</text>',
            ]
    out.append(f'<line x1="{x0}" x2="{x1}" y1="{mid}" y2="{mid}" stroke="{AXIS}" stroke-width="1.2"/>')
    out.append(f'<text x="{x0 - 12}" y="{mid + 4.5}" font-size="12.5" fill="{MUTED}" text-anchor="end">0</text>')

    for i, day in enumerate(days):
        d = stats[day]
        x = x0 + i * slot + (slot - bw) / 2
        cx = x + bw / 2
        for key, up, fill in (("added", True, "add"), ("deleted", False, "del")):
            if d[key]:
                length = max(2.0, math.sqrt(d[key] / peak) * half)
                out.append(f'<path d="{bar_path(x, bw, mid, length, up)}" fill="url(#{fill})"/>')
        if not d["commits"]:
            out.append(f'<circle cx="{cx:.1f}" cy="{mid}" r="2" fill="{AXIS}"/>')
        if i % 5 == 0 or i == len(days) - 1:
            last = i == len(days) - 1
            weight = ' font-weight="600"' if last else ""
            out.append(
                f'<text x="{cx:.1f}" y="{mid + half + 30}" font-size="13" text-anchor="middle" '
                f'fill="{TEXT if last else MUTED}"{weight}>{short_date(day)}</text>'
            )

    if not s["commits"]:
        out.append(
            f'<text x="{(x0 + x1) / 2:.0f}" y="{mid - 18}" font-size="15" fill="{MUTED}" text-anchor="middle">'
            f'No commits in this period</text>'
        )

    out += [
        f'<line x1="{PAD}" x2="{W - PAD}" y1="522" y2="522" stroke="{BORDER}"/>',
        f'<text x="{PAD}" y="543" font-size="12" fill="{MUTED}">Commits counted by author date (UTC) · '
        f'bar height uses a square-root scale so small days stay visible</text>',
        "</svg>",
    ]
    return "\n".join(out)


def build_section(stats):
    s = summarize(stats)
    alt = (
        f"Daily code activity over the last {DAYS} days: {fmt(s['added'])} lines added, "
        f"{fmt(s['deleted'])} deleted, {fmt(s['commits'])} commits on {s['active']} active days"
    )
    return f"""## 📊 Daily Code Activity

<p align="center">
<img src="{CHART}" width="100%" alt="{alt}" />
</p>"""


def update_readme(section):
    with open(README, encoding="utf-8") as f:
        content = f.read()
    pattern = re.compile(r"(<!-- CODE_ACTIVITY_START -->).*?(<!-- CODE_ACTIVITY_END -->)", re.S)
    if not pattern.search(content):
        raise SystemExit("CODE_ACTIVITY markers not found in README.md")
    content = pattern.sub(lambda m: f"{m.group(1)}\n{section}\n\n{m.group(2)}", content)
    with open(README, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    stats = collect(window())
    os.makedirs(os.path.dirname(CHART), exist_ok=True)
    with open(CHART, "w", encoding="utf-8") as f:
        f.write(build_chart(stats))
    update_readme(build_section(stats))


if __name__ == "__main__":
    main()
