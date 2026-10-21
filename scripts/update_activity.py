import datetime as dt
import json
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
    raise RuntimeError(f"Failed to fetch {url}")


def paginate(url):
    sep = "&" if "?" in url else "?"
    page = 1
    while True:
        items = get_json(f"{url}{sep}per_page=100&page={page}")
        yield from items
        if len(items) < 100:
            return
        page += 1


def collect():
    today = dt.datetime.now(dt.timezone.utc).date()
    start = today - dt.timedelta(days=DAYS - 1)
    since = dt.datetime.combine(start, dt.time.min, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stats = {
        start + dt.timedelta(days=i): {"commits": 0, "added": 0, "deleted": 0}
        for i in range(DAYS)
    }

    repos = paginate(f"{API}/user/repos?affiliation=owner&visibility=all&sort=pushed")
    seen = set()
    for repo in repos:
        if repo["owner"]["login"].lower() != USERNAME.lower() or repo.get("archived"):
            continue
        if repo.get("pushed_at") and repo["pushed_at"] < since:
            continue
        name = repo["full_name"]
        query = urllib.parse.urlencode({"author": USERNAME, "since": since})
        for item in paginate(f"{API}/repos/{name}/commits?{query}"):
            sha = item["sha"]
            if sha in seen or len(item.get("parents", [])) > 1:
                continue  # forks share SHAs; merge commits double-count
            seen.add(sha)
            detail = get_json(f"{API}/repos/{name}/commits/{sha}")
            day = dt.datetime.fromisoformat(
                detail["commit"]["author"]["date"].replace("Z", "+00:00")
            ).astimezone(dt.timezone.utc).date()
            if day not in stats:
                continue
            total = detail.get("stats", {})
            stats[day]["commits"] += 1
            stats[day]["added"] += total.get("additions", 0)
            stats[day]["deleted"] += total.get("deletions", 0)
    return stats


def fmt(n):
    return f"{n:,}"


def build_chart(stats):
    width, height = 900, 300
    left, right, top, bottom = 56, 16, 36, 44
    plot_w, plot_h = width - left - right, height - top - bottom
    mid = top + plot_h / 2
    peak = max([1] + [max(d["added"], d["deleted"]) for d in stats.values()])
    slot = plot_w / len(stats)
    bar = slot * 0.7

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Lines added and deleted per day over the last {DAYS} days">',
        "<style>"
        ".bg{fill:#0d1117}.t{fill:#8b949e;font:11px -apple-system,Segoe UI,sans-serif}"
        ".h{fill:#e6edf3;font:600 14px -apple-system,Segoe UI,sans-serif}"
        ".g{stroke:#30363d;stroke-width:1}.a{fill:#3fb950}.d{fill:#f85149}"
        "</style>",
        f'<rect class="bg" width="{width}" height="{height}" rx="8"/>',
        f'<text class="h" x="{left}" y="22">Lines added / deleted per day</text>',
        f'<line class="g" x1="{left}" x2="{width - right}" y1="{mid}" y2="{mid}"/>',
        f'<text class="t" x="{left - 8}" y="{top + 4}" text-anchor="end">+{fmt(peak)}</text>',
        f'<text class="t" x="{left - 8}" y="{mid + 4}" text-anchor="end">0</text>',
        f'<text class="t" x="{left - 8}" y="{top + plot_h + 4}" text-anchor="end">-{fmt(peak)}</text>',
    ]
    for i, (day, d) in enumerate(stats.items()):
        x = left + i * slot + (slot - bar) / 2
        up = d["added"] / peak * plot_h / 2
        down = d["deleted"] / peak * plot_h / 2
        label = f'{day.isoformat()}: +{d["added"]} / -{d["deleted"]} ({d["commits"]} commits)'
        if up:
            parts.append(f'<rect class="a" x="{x:.1f}" y="{mid - up:.1f}" width="{bar:.1f}" height="{up:.1f}"><title>{label}</title></rect>')
        if down:
            parts.append(f'<rect class="d" x="{x:.1f}" y="{mid:.1f}" width="{bar:.1f}" height="{down:.1f}"><title>{label}</title></rect>')
        if i % 5 == 0 or i == len(stats) - 1:
            parts.append(f'<text class="t" x="{x + bar / 2:.1f}" y="{height - 22}" text-anchor="middle">{day.strftime("%b %d")}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def build_section(stats, generated):
    commits = sum(d["commits"] for d in stats.values())
    added = sum(d["added"] for d in stats.values())
    deleted = sum(d["deleted"] for d in stats.values())
    active = sum(1 for d in stats.values() if d["commits"])

    cards = [
        ("Commits", fmt(commits)),
        ("Lines added", f"+{fmt(added)}"),
        ("Lines deleted", f"-{fmt(deleted)}"),
        ("Active days", f"{active}/{DAYS}"),
    ]
    cells = "\n".join(
        f'    <td align="center"><b>{value}</b><br/><sub>{label}</sub></td>'
        for label, value in cards
    )
    rows = "\n".join(
        f"    <tr><td>{day.isoformat()}</td><td align=\"right\">{d['commits']}</td>"
        f"<td align=\"right\">+{fmt(d['added'])}</td><td align=\"right\">-{fmt(d['deleted'])}</td></tr>"
        for day, d in reversed(list(stats.items()))
    )
    return f"""## 📊 Daily Code Activity

<p align="center">
  <sub>Automatically updated daily · Last {DAYS} days · Public + private repositories · Updated {generated} UTC</sub>
</p>

<table align="center">
  <tr>
{cells}
  </tr>
</table>

<p align="center">
  <img src="{CHART}" alt="Lines added and deleted per day" width="100%" />
</p>

<details>
<summary>Daily breakdown</summary>

<table align="center">
  <thead>
    <tr><th>Date (UTC)</th><th>Commits</th><th>Added</th><th>Deleted</th></tr>
  </thead>
  <tbody>
{rows}
  </tbody>
</table>

</details>"""


def main():
    stats = collect()
    os.makedirs(os.path.dirname(CHART), exist_ok=True)
    with open(CHART, "w", encoding="utf-8") as f:
        f.write(build_chart(stats))

    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    section = build_section(stats, generated)
    with open(README, encoding="utf-8") as f:
        content = f.read()
    pattern = re.compile(r"(<!-- CODE_ACTIVITY_START -->).*?(<!-- CODE_ACTIVITY_END -->)", re.S)
    if not pattern.search(content):
        raise SystemExit("CODE_ACTIVITY markers not found in README.md")
    content = pattern.sub(lambda m: f"{m.group(1)}\n{section}\n\n{m.group(2)}", content)
    with open(README, "w", encoding="utf-8") as f:
        f.write(content)


if __name__ == "__main__":
    main()
