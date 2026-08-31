
from bs4 import BeautifulSoup
from datetime import datetime

def scrape_fighter_profile(fighter_url, page):
    page.goto(fighter_url, wait_until="networkidle", timeout=60000)
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    def get_stat(label):
        tags = soup.select("li.b-list__box-list-item")
        for tag in tags:
            text = tag.get_text(strip=True)
            if text.startswith(label):
                return text.replace(label, "").strip()
        return None

    def parse_height(h):
        if not h or h == "--":
            return None
        try:
            parts = h.replace('"', '').split("'")
            feet = int(parts[0].strip())
            inches = int(parts[1].strip())
            return feet * 12 + inches
        except (ValueError, IndexError, AttributeError):
            return None

    def parse_reach(r):
        if not r or r == "--":
            return None
        try:
            return float(r.replace('"', '').strip())
        except (ValueError, IndexError, AttributeError):
            return None

    def parse_dob(d):
        if not d or d == "--":
            return None
        try:
            return datetime.strptime(d, "%b %d, %Y").date()
        except (ValueError, IndexError, AttributeError):
            return None

    height_raw = get_stat("Height:")
    reach_raw = get_stat("Reach:")
    stance_raw = get_stat("STANCE:")
    dob_raw = get_stat("DOB:")

    return {
        "height": parse_height(height_raw),
        "reach": parse_reach(reach_raw),
        "stance": stance_raw,
        "dob": parse_dob(dob_raw)
    }


if __name__ == "__main__":
    test_url = "http://www.ufcstats.com/fighter-details/c03520b5c88ed6b4"
    profile = scrape_fighter_profile(test_url)
    print(profile)