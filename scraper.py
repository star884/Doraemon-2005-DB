import os
import re
import json
import logging
import requests
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Configure Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TsukiScraper")

TARGET_URL = "https://tsuki.page/wiki/doraemon"
OUTPUT_DIR = "output"
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

class AdvancedWikiScraper:
    def __init__(self, target_url: str, output_dir: str):
        self.target_url = target_url
        self.output_dir = output_dir
        self.domain = f"{urlparse(target_url).scheme}://{urlparse(target_url).netloc}"
        self.intercepted_json = []

    def fetch_page(self) -> str:
        """Fetches page content with Playwright while intercepting XHR/Fetch JSON calls."""
        logger.info(f"Initiating dynamic fetch for {self.target_url}...")
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=USER_AGENTS[0],
                viewport={"width": 1920, "height": 1080}
            )

            # Block heavy assets to speed up extraction
            page = context.new_page()
            page.route("**/*.{png,jpg,jpeg,svg,css,woff,woff2}", lambda route: route.abort())

            # Intercept raw JSON responses from background API requests
            def handle_response(response):
                if "json" in response.headers.get("content-type", "").lower():
                    try:
                        self.intercepted_json.append({
                            "url": response.url,
                            "data": response.json()
                        })
                    except Exception:
                        pass

            page.on("response", handle_response)

            try:
                page.goto(self.target_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2000)  # Wait for full SPA hydration
                content = page.content()
            except PlaywrightTimeoutError:
                logger.warning("Network idle timeout reached. Capturing current DOM state...")
                content = page.content()
            finally:
                browser.close()
                
            return content

    def clean_text(self, text: str) -> str:
        """Sanitizes raw strings."""
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def extract_structured_data(self, html: str) -> dict:
        """Parses raw HTML DOM, embedded state, and structured metadata."""
        soup = BeautifulSoup(html, "html.parser")

        data = {
            "page_info": {
                "title": "",
                "canonical_url": self.target_url,
                "domain": self.domain
            },
            "meta_tags": {},
            "embedded_state_data": self.intercepted_json,
            "infobox": {},
            "sections": [],
            "tables": [],
            "lists": [],
            "media_assets": [],
            "internal_links": [],
            "external_links": []
        }

        # 1. Page Title & Head Meta Extraction
        title_tag = soup.find("title") or soup.find("h1")
        data["page_info"]["title"] = self.clean_text(title_tag.get_text()) if title_tag else "Doraemon"

        for meta in soup.find_all("meta"):
            name = meta.get("name") or meta.get("property")
            content = meta.get("content")
            if name and content:
                data["meta_tags"][name] = self.clean_text(content)

        # 2. Extract Hydrated State Scripts (JSON-LD / Nuxt / Next)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data["embedded_state_data"].append(json.loads(script.string))
            except Exception:
                pass

        # Decompose elements that shouldn't be in main body content
        for unwanted in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
            unwanted.decompose()

        # 3. Infobox / Key-Value Extraction
        infobox_candidates = soup.find_all(class_=re.compile(r"infobox|sidebar|summary|key-info", re.I))
        for box in infobox_candidates:
            for row in box.find_all(["tr", "div", "li"]):
                text = self.clean_text(row.get_text())
                if ":" in text:
                    key, _, val = text.partition(":")
                    if len(key) < 50 and val:
                        data["infobox"][self.clean_text(key)] = self.clean_text(val)

        # 4. Content Sections & Hierarchy Parsing
        current_section = {"heading": "Overview", "level": "h1", "paragraphs": []}
        
        for element in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "table"]):
            tag_name = element.name

            if tag_name.startswith("h"):
                if current_section["paragraphs"]:
                    data["sections"].append(current_section)
                current_section = {
                    "heading": self.clean_text(element.get_text()),
                    "level": tag_name,
                    "paragraphs": []
                }
            
            elif tag_name == "p":
                text = self.clean_text(element.get_text())
                if text:
                    current_section["paragraphs"].append(text)

            elif tag_name in ["ul", "ol"]:
                items = [self.clean_text(li.get_text()) for li in element.find_all("li") if self.clean_text(li.get_text())]
                if items:
                    data["lists"].append({
                        "section": current_section["heading"],
                        "items": items
                    })

            elif tag_name == "table":
                table_data = []
                headers = [self.clean_text(th.get_text()) for th in element.find_all("th")]
                for tr in element.find_all("tr"):
                    row = [self.clean_text(td.get_text()) for td in tr.find_all("td")]
                    if row:
                        table_data.append(row)
                if table_data:
                    data["tables"].append({
                        "section": current_section["heading"],
                        "headers": headers,
                        "rows": table_data
                    })

        if current_section["paragraphs"]:
            data["sections"].append(current_section)

        # 5. Media Assets Extraction
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if src:
                full_url = urljoin(self.domain, src)
                data["media_assets"].append({
                    "src": full_url,
                    "alt": self.clean_text(img.get("alt", "")),
                    "title": self.clean_text(img.get("title", ""))
                })

        # 6. Hyperlinks Extraction & Classification
        seen_links = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href")
            full_link = urljoin(self.domain, href)
            anchor_text = self.clean_text(a.get_text())

            if full_link not in seen_links and not href.startswith("#"):
                seen_links.add(full_link)
                link_obj = {"text": anchor_text, "url": full_link}
                if self.domain in full_link:
                    data["internal_links"].append(link_obj)
                else:
                    data["external_links"].append(link_obj)

        return data

    def export_data(self, data: dict):
        """Generates comprehensive JSON output alongside structured multi-file relational CSVs."""
        os.makedirs(self.output_dir, exist_ok=True)

        # 1. Export Complete Consolidated JSON
        json_path = os.path.join(self.output_dir, "doraemon.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"JSON export completed: {json_path}")

        # 2. Export Main Flat CSV (Overview Data)
        main_csv_path = os.path.join(self.output_dir, "doraemon.csv")
        flattened_main = []

        # Add Metadata & Infobox to main CSV
        for k, v in data["meta_tags"].items():
            flattened_main.append({"Category": "Meta", "Key/Heading": k, "Value": v})
        for k, v in data["infobox"].items():
            flattened_main.append({"Category": "Infobox", "Key/Heading": k, "Value": v})
        
        # Add Sections to main CSV
        for sec in data["sections"]:
            flattened_main.append({
                "Category": f"Section ({sec['level']})",
                "Key/Heading": sec["heading"],
                "Value": " ".join(sec["paragraphs"])
            })

        pd.DataFrame(flattened_main).to_csv(main_csv_path, index=False, encoding="utf-8-sig")
        logger.info(f"Main CSV export completed: {main_csv_path}")

        # 3. Export Relational Datasets (Sections, Media, and Links)
        if data["sections"]:
            sec_df = pd.DataFrame([
                {"heading": s["heading"], "level": s["level"], "content": "\n".join(s["paragraphs"])} 
                for s in data["sections"]
            ])
            sec_df.to_csv(os.path.join(self.output_dir, "sections.csv"), index=False, encoding="utf-8-sig")

        if data["media_assets"]:
            pd.DataFrame(data["media_assets"]).to_csv(
                os.path.join(self.output_dir, "media_assets.csv"), index=False, encoding="utf-8-sig"
            )

        if data["internal_links"] or data["external_links"]:
            all_links = (
                [{"type": "internal", **l} for l in data["internal_links"]] + 
                [{"type": "external", **l} for l in data["external_links"]]
            )
            pd.DataFrame(all_links).to_csv(
                os.path.join(self.output_dir, "links.csv"), index=False, encoding="utf-8-sig"
            )

        logger.info("All datasets generated successfully.")

    def run(self):
        html = self.fetch_page()
        structured_data = self.extract_structured_data(html)
        self.export_data(structured_data)

if __name__ == "__main__":
    scraper = AdvancedWikiScraper(target_url=TARGET_URL, output_dir=OUTPUT_DIR)
    scraper.run()
