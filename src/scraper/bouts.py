
from bs4 import BeautifulSoup

# Longest / most specific first: "Heavyweight" is a substring of
# "Light Heavyweight", and "Flyweight" of "Women's Flyweight".
WEIGHT_CLASSES = [
    "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight",
    "Women's Featherweight", "Women's Lightweight",
    "Light Heavyweight", "Super Heavyweight",
    "Strawweight", "Flyweight", "Bantamweight", "Featherweight",
    "Lightweight", "Welterweight", "Middleweight", "Heavyweight",
    "Catch Weight", "Open Weight",
]


def parse_weight_class(title_text):
    """Match the title against known divisions; stripping words out of it left
    debris like "4 Tournament". Anything unrecognised is an open-weight bout."""
    if not title_text:
        return None
    lowered = title_text.lower()
    for weight_class in WEIGHT_CLASSES:
        if weight_class.lower() in lowered:
            return weight_class
    return "Open Weight"


TEST_EVENT_URL = "http://www.ufcstats.com/event-details/f354c50b8d63d9b3"

def scrape_bout_urls(event_url, page):
    page.goto(event_url, wait_until="networkidle", timeout=60000)
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("tr.b-fight-details__table-row")

    bout_urls = []
    for row in rows:
        link = row.get("data-link")
        if link:
            bout_urls.append(link)

    return bout_urls
def scrape_event_results(event_url, page):
    """Read every bout outcome off one event page. The listing carries a
    win/draw/nc flag and both fighter URLs, so one request settles a card."""
    page.goto(event_url, wait_until="networkidle", timeout=60000)
    soup = BeautifulSoup(page.content(), "html.parser")

    results = []
    for row in soup.select("tr.b-fight-details__table-row"):
        bout_url = row.get("data-link")
        flag = row.select_one("i.b-flag__text")
        fighter_links = [
            a["href"] for a in row.select("a.b-link")
            if "fighter-details" in a.get("href", "")
        ]

        if not bout_url or not flag or len(fighter_links) < 2:
            continue

        results.append({
            "bout_url": bout_url,
            "fighter_a_url": fighter_links[0],
            "fighter_b_url": fighter_links[1],
            "outcome": flag.get_text(strip=True).lower(),
        })

    return results


def scrape_upcoming_card(event_url, page):
    """Matchups on a scheduled card: fight URL, both fighters and the division."""
    page.goto(event_url, wait_until="networkidle", timeout=60000)
    soup = BeautifulSoup(page.content(), "html.parser")

    card = []
    for row in soup.select("tr.b-fight-details__table-row"):
        bout_url = row.get("data-link")
        fighters = [a for a in row.select("a") if "fighter-details" in (a.get("href") or "")]
        cells = row.select("td")
        if not bout_url or len(fighters) < 2 or len(cells) < 7:
            continue
        card.append({
            "bout_url": bout_url,
            "fighter_a_url": fighters[0]["href"], "fighter_a_name": fighters[0].get_text(strip=True),
            "fighter_b_url": fighters[1]["href"], "fighter_b_name": fighters[1].get_text(strip=True),
            "weight_class": parse_weight_class(cells[6].get_text(" ", strip=True)),
        })
    return card


def scrape_bout_details(bout_url, page):
    page.goto(bout_url, wait_until="networkidle", timeout=60000)
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    # Fighter names and URLs
    fighter_tags = soup.select("a.b-link.b-fight-details__person-link")
    fighter_a_name = fighter_tags[0].get_text(strip=True)
    fighter_a_url = fighter_tags[0]["href"]
    fighter_b_name = fighter_tags[1].get_text(strip=True)
    fighter_b_url = fighter_tags[1]["href"]

    # The status tag reads W/L, but D for a draw and NC for a no contest, so
    # only an explicit "W" names a winner.
    result_tags = soup.select("i.b-fight-details__person-status")
    statuses = [t.get_text(strip=True) for t in result_tags]

    if statuses and statuses[0] == "W":
        winner = fighter_a_name
    elif len(statuses) > 1 and statuses[1] == "W":
        winner = fighter_b_name
    else:
        winner = None

    if winner is not None:
        outcome = "win"
    elif "NC" in statuses:
        outcome = "nc"
    else:
        outcome = "draw"

    details_section = soup.select_one("div.b-fight-details__content")
    # Method
    method_container = details_section.select_one("i.b-fight-details__text-item_first")
    method = method_container.select_one("i:not([class])").get_text(strip=True)

    # Round and time
    detail_items = details_section.select("i.b-fight-details__text-item")
    round_ = detail_items[0].get_text(strip=True).replace("Round:", "").strip()
    time = detail_items[1].get_text(strip=True).replace("Time:", "").strip()

    # Title fight detection
    bout_type = soup.select_one("i.b-fight-details__fight-title")
    is_title_fight = False
    if bout_type:
        bout_text = bout_type.get_text(strip=True).lower()
        is_title_fight = "title" in bout_text

    # Method detail
    method_parts = method.split(" - ")
    method_type = method_parts[0].strip()
    method_detail = method_parts[1].strip() if len(method_parts) > 1 else None

    # Weight class
    weight_class = parse_weight_class(bout_type.get_text(strip=True)) if bout_type else None

    return {
        "fighter_a_name": fighter_a_name,
        "fighter_a_url": fighter_a_url,
        "fighter_b_name": fighter_b_name,
        "fighter_b_url": fighter_b_url,
        "winner": winner,
        "outcome": outcome,
        "method": method_type,
        "method_detail": method_detail,
        "round": round_,
        "time": time,
        "weight_class": weight_class,
        "is_title_fight": is_title_fight,
        "is_defence": False,
        "soup": soup
    }


def scrape_bout_stats(soup, fighter_a_url, fighter_b_url):
    sections = soup.select("section.b-fight-details__section")

    if len(sections) < 2:
        return None

    totals_table = sections[1].select_one("table")
    if not totals_table:
        return None

    rows = totals_table.select("tr.b-fight-details__table-row")
    data_row = None
    for row in rows:
        cols = row.select("td.b-fight-details__table-col")
        if cols:
            data_row = cols
            break

    if not data_row:
        return None

    def parse_of(text):
        text = text.strip()
        if "of" in text:
            parts = text.split("of")
            return int(parts[0].strip()), int(parts[1].strip())
        return 0, 0

    def parse_control_time(text):
        text = text.strip()
        if ":" in text:
            parts = text.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        return 0

    def get_col(col, idx):
        ps = col.select("p")
        return ps[idx].get_text(strip=True) if len(ps) > idx else ""

    stats = []
    for i, fighter_url in enumerate([fighter_a_url, fighter_b_url]):
        kd = int(get_col(data_row[1], i)) if get_col(data_row[1], i).isdigit() else 0
        sig_landed, sig_attempted = parse_of(get_col(data_row[2], i))
        total_landed, total_attempted = parse_of(get_col(data_row[4], i))
        td_landed, td_attempted = parse_of(get_col(data_row[5], i))
        sub_attempts = int(get_col(data_row[7], i)) if get_col(data_row[7], i).isdigit() else 0
        control = parse_control_time(get_col(data_row[9], i))

        stats.append({
            "fighter_url": fighter_url,
            "knockdowns": kd,
            "sig_strikes_landed": sig_landed,
            "sig_strikes_attempted": sig_attempted,
            "total_strikes_landed": total_landed,
            "total_strikes_attempted": total_attempted,
            "takedowns_landed": td_landed,
            "takedowns_attempted": td_attempted,
            "submission_attempts": sub_attempts,
            "control_time_seconds": control
        })

    return stats



if __name__ == "__main__":
    from playwright.sync_api import sync_playwright

    test_urls = [
        "http://www.ufcstats.com/fight-details/4a0db214d9721d6e",  # Merab vs Yan 2
        "http://www.ufcstats.com/fight-details/91bb64656e13a1d4",  # Edgar vs Maynard 2 (draw)
        "http://www.ufcstats.com/fight-details/65b4d4be2d04bb34",  # Vera vs Silva (no contest)
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for url in test_urls:
            details = scrape_bout_details(url, page)
            stats = scrape_bout_stats(
                details["soup"],
                details["fighter_a_url"],
                details["fighter_b_url"]
            )
            printable = {k: v for k, v in details.items() if k != "soup"}
            print(printable)
            print(stats)
            print()
        browser.close()
