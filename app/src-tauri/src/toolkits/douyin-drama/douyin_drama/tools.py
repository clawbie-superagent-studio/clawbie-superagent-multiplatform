"""
Douyin Short Drama tools.
Browse, play, and download short drama episodes (短剧).
Each function does one thing and returns JSON for LLM orchestration.
"""
import time
import json
import re
import subprocess
import os

from . import chrome_bridge as cb

DOUYIN_SERIES_URL = "https://www.douyin.com/series"
DOUYIN_VIDEO_URL = "https://www.douyin.com/video/{video_id}"


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


# ============================================================
# Browse dramas
# ============================================================

def list_dramas(category="推荐"):
    """List trending short dramas on the series page.

    Args:
        category: filter category (推荐/热榜/爱情/剧情/逆袭/玄幻/古装/悬疑/喜剧 etc.)

    Returns: {"count": 20, "dramas": [
        {"index": 0, "title": "爸，我带您来办业务了", "genre": "其他", "episodes": "55集", "views": "9.9亿"},
        ...
    ]}
    """
    cb.activate_chrome()
    time.sleep(0.5)

    cb.navigate(DOUYIN_SERIES_URL)
    time.sleep(5)

    # Click category tab if not default
    if category != "推荐":
        cb.run_js("""
(function(){
    var all = document.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
        if (all[i].textContent.trim() === '""" + category + """' && all[i].children.length === 0) {
            all[i].click();
            return;
        }
    }
})()
""")
        time.sleep(3)

    # Parse drama cards from page text
    raw = cb.run_js("document.body.innerText")
    dramas = _parse_drama_list(raw)

    return _json({"count": len(dramas), "dramas": dramas})


def open_drama(index=0):
    """Open a drama from the series list page (cliclick on cover image).

    Prerequisite: on the series page (via list_dramas).

    Args:
        index: drama index in the list (0-based)

    Returns: {"ok": true, "title": "...", "url": "..."} or {"ok": false, "error": "..."}
    """
    cb.activate_chrome()
    time.sleep(0.3)

    # Find the Nth cover image
    idx = str(index)
    coords = cb.run_js("""
(function(){
    var imgs = document.querySelectorAll('img');
    var covers = [];
    for (var i = 0; i < imgs.length; i++) {
        var rect = imgs[i].getBoundingClientRect();
        if (rect.width > 100 && rect.height > 150 && rect.y > 50) {
            covers.push(imgs[i]);
        }
    }
    if (""" + idx + """ >= covers.length) return 'OUT_OF_RANGE:' + covers.length;
    var img = covers[""" + idx + """];
    img.scrollIntoView({block:'center'});
    return 'SCROLL';
})()
""")
    if coords and coords.startswith("OUT_OF_RANGE"):
        return _json({"ok": False, "error": coords})

    time.sleep(2)

    # Re-get coordinates after scroll
    coords = cb.run_js("""
(function(){
    var imgs = document.querySelectorAll('img');
    var covers = [];
    for (var i = 0; i < imgs.length; i++) {
        var rect = imgs[i].getBoundingClientRect();
        if (rect.width > 100 && rect.height > 150 && rect.y > 50 && rect.y < 1200) {
            covers.push(imgs[i]);
        }
    }
    if (""" + idx + """ >= covers.length) return 'OUT_OF_RANGE';
    var img = covers[""" + idx + """];
    var rect = img.getBoundingClientRect();
    return Math.round(rect.x + rect.width/2) + '|' + Math.round(rect.y + rect.height/2);
})()
""")
    if not coords or "|" not in coords:
        return _json({"ok": False, "error": "Cannot locate cover: " + coords})

    vx, vy = [int(x) for x in coords.split("|")]
    cb.real_click(vx, vy)
    time.sleep(5)

    title = cb.run_js("document.title")
    url = cb.run_js("document.URL")

    return _json({"ok": True, "title": title, "url": url})


# ============================================================
# Episode management
# ============================================================

def get_drama_info():
    """Get info about the currently playing drama episode.

    Returns: {"title": "爸，我带您来办业务了", "episode": 1, "total_episodes": 55,
              "likes": "8.1万", "comments": "5068", "video_id": "7605192997013130530"}
    """
    url = cb.run_js("document.URL")
    video_id = ""
    m = re.search(r'/video/(\d+)', url)
    if m:
        video_id = m.group(1)

    text = cb.run_js("document.body.innerText.substring(0, 500)")

    # Extract episode number and title
    episode = 0
    title = ""
    ep_match = re.search(r'第(\d+)集\s*[|｜]\s*(.+?)(?:\s*#|$)', text, re.MULTILINE)
    if ep_match:
        episode = int(ep_match.group(1))
        title = ep_match.group(2).strip()

    # Total episodes from page title or text
    total = 0
    total_match = re.search(r'(\d+)集', cb.run_js("document.title") + " " + text)
    if total_match:
        total = int(total_match.group(1))

    # Stats: likes, comments, favorites, shares
    stats = _get_video_stats(text)

    return _json({
        "title": title,
        "episode": episode,
        "total_episodes": total,
        "video_id": video_id,
        **stats,
    })


def get_episodes():
    """Get the episode list of the current drama.

    Extracts episode video IDs from the page's related video recommendations.

    Returns: {"episodes": [{"episode": 1, "video_id": "760519...", "current": true}, ...]}
    """
    # The drama video page shows related episodes in the recommendation area
    # Each episode is a link with /video/{id} format
    raw = cb.run_js("""
(function(){
    var links = document.querySelectorAll('a[href*="/video/"]');
    var r = [];
    var seen = {};
    for (var i = 0; i < links.length; i++) {
        var href = links[i].href;
        var m = href.match(/\\/video\\/(\\d+)/);
        if (m && !seen[m[1]]) {
            seen[m[1]] = 1;
            var text = links[i].innerText.trim().substring(0, 60).replace(/\\n/g, ' ');
            r.push(m[1] + '||' + text);
        }
    }
    return r.join('\\n');
})()
""")
    current_url = cb.run_js("document.URL")
    current_id = ""
    m = re.search(r'/video/(\d+)', current_url)
    if m:
        current_id = m.group(1)

    episodes = []
    if raw:
        for i, line in enumerate(raw.strip().split("\n")):
            if "||" not in line:
                continue
            vid, text = line.split("||", 1)
            ep_num = i + 1
            ep_match = re.search(r'第(\d+)集', text)
            if ep_match:
                ep_num = int(ep_match.group(1))
            episodes.append({
                "episode": ep_num,
                "video_id": vid,
                "title": text[:50],
                "current": vid == current_id,
            })

    return _json({"episodes": episodes})


def play_episode(episode):
    """Navigate to a specific episode number.

    Finds the episode link on the page and navigates to it.

    Args:
        episode: episode number (e.g. 2)

    Returns: {"ok": true, "video_id": "..."} or {"ok": false, "error": "..."}
    """
    ep_str = str(episode)

    # Find the link for this episode
    video_id = cb.run_js("""
(function(){
    var links = document.querySelectorAll('a[href*="/video/"]');
    for (var i = 0; i < links.length; i++) {
        var text = links[i].innerText.trim();
        if (text.indexOf('第""" + ep_str + """集') > -1) {
            var m = links[i].href.match(/\\/video\\/(\\d+)/);
            return m ? m[1] : '';
        }
    }
    return '';
})()
""")
    if not video_id:
        return _json({"ok": False, "error": f"Episode {episode} not found on page"})

    url = DOUYIN_VIDEO_URL.format(video_id=video_id)
    cb.navigate(url)
    time.sleep(5)

    return _json({"ok": True, "video_id": video_id})


# ============================================================
# Download
# ============================================================

def download_episode(save_path=None):
    """Download the currently playing episode to a local file.

    Extracts video URL from browser network entries or video.currentSrc,
    then downloads via curl.

    Args:
        save_path: file path (default: /tmp/drama_ep{N}.mp4)

    Returns: {"ok": true, "path": "...", "size_mb": 15.1} or {"ok": false, "error": "..."}
    """
    # Auto-generate path from episode info
    if not save_path:
        text = cb.run_js("document.body.innerText.substring(0, 200)")
        ep_match = re.search(r'第(\d+)集', text)
        ep_num = ep_match.group(1) if ep_match else "0"
        save_path = f"/tmp/drama_ep{ep_num}.mp4"

    # Find video CDN URL: performance entries first, then video.currentSrc
    video_url = cb.run_js("""
(function(){
    var entries = performance.getEntriesByType('resource');
    for (var i = 0; i < entries.length; i++) {
        var url = entries[i].name;
        if (url.indexOf('douyinvod') > -1 || url.indexOf('bytevod') > -1) {
            return url;
        }
    }
    var v = document.querySelector('video');
    if (v && v.currentSrc && v.currentSrc.indexOf('douyinvod') > -1) {
        return v.currentSrc;
    }
    return '';
})()
""")
    if not video_url:
        return _json({"ok": False, "error": "No video CDN URL found. Is the episode playing?"})

    save_path = os.path.expanduser(save_path)
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

    result = subprocess.run([
        "curl", "-L", "-o", save_path,
        "-H", "Referer: https://www.douyin.com/",
        video_url
    ], capture_output=True, text=True, timeout=300)

    if not os.path.exists(save_path):
        return _json({"ok": False, "error": "Download failed: file not created"})

    size = os.path.getsize(save_path)
    if size < 1024:
        return _json({"ok": False, "error": f"Download failed: file too small ({size} bytes)"})

    return _json({"ok": True, "path": save_path, "size_mb": round(size / 1024 / 1024, 1)})


def wait():
    """Random 3-5 second delay to mimic human interaction.

    Returns: {"waited": 4.2}
    """
    import random
    seconds = round(random.uniform(3, 5), 1)
    time.sleep(seconds)
    return _json({"waited": seconds})


# ============================================================
# Internal helpers
# ============================================================

def _parse_drama_list(text):
    """Parse drama list from page text."""
    dramas = []
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    i = 0
    while i < len(lines):
        # Pattern: views (亿/万) → title → genre·episodes
        views_match = re.match(r'^([\d.]+亿|[\d.]+万)$', lines[i])
        if views_match and i + 2 < len(lines):
            views = views_match.group(1)
            title = lines[i + 1]
            genre_ep = lines[i + 2]

            genre = ""
            episodes = ""
            gm = re.match(r'^(.+?)·(\d+集)$', genre_ep)
            if gm:
                genre = gm.group(1)
                episodes = gm.group(2)

            if title and len(title) < 30:
                dramas.append({
                    "index": len(dramas),
                    "title": title,
                    "genre": genre,
                    "episodes": episodes,
                    "views": views,
                })
            i += 3
            continue
        i += 1

    return dramas


def _get_video_stats(text):
    """Extract likes/comments/favorites/shares from page text."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    stats = {"likes": "", "comments": "", "favorites": "", "shares": ""}

    # Find 4 consecutive stat numbers after episode title
    for i, line in enumerate(lines):
        if re.match(r'^第\d+集', line):
            nums = []
            for j in range(i + 1, min(i + 10, len(lines))):
                if re.match(r'^[\d.]+万?$', lines[j]):
                    nums.append(lines[j])
                if len(nums) == 4:
                    break
            if len(nums) >= 4:
                stats["likes"] = nums[0]
                stats["comments"] = nums[1]
                stats["favorites"] = nums[2]
                stats["shares"] = nums[3]
            break

    return stats
