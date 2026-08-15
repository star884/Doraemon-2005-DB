#!/usr/bin/env python3
"""
Doraemon Wiki Episode Scraper
Automatically scrapes, organizes, and stores episode data in JSON/CSV.
Designed for GitHub Actions integration.
"""

import os
import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup, NavigableString
import pandas as pd

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('scraper.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class DoraemonScraper:
    """Main scraper class for Doraemon wiki episode data."""
    
    def __init__(self, config_path: str = 'config.yaml'):
        self.config = self._load_config(config_path)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; DoraemonScraper/1.0; +https://github.com/yourusername/doraemon-scraper)',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5'
        })
        
    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from YAML file."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning(f"Config file {config_path} not found. Using defaults.")
            return self._get_default_config()
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML config: {e}")
            return self._get_default_config()
    
    def _get_default_config(self) -> Dict:
        """Default configuration if file missing."""
        return {
            'target_url': 'https://tsuki.page/wiki/doraemon/2005-present',
            'output_json': 'data/doraemon_episodes.json',
            'output_csv': 'data/doraemon_episodes.csv',
            'selectors': {
                'episode_table': 'table.wikitable, table.episode-list, .episode-table',
                'episode_row': 'tr:not(.mw-empty-elt)',
                'episode_number': 'td:first-child, th:first-child',
                'season_episode': 'td:nth-child(2), th:nth-child(2)',
                'title': 'td:nth-child(3) a, td:nth-child(3), th:nth-child(3) a, th:nth-child(3)'
            },
            'follow_links': True,
            'delay_between_requests': 2.0,
            'max_pages': 50
        }
    
    def fetch_page(self, url: str, retries: int = 3) -> Optional[BeautifulSoup]:
        """Fetch and parse a webpage with retry logic."""
        for attempt in range(retries):
            try:
                logger.info(f"Fetching: {url} (attempt {attempt + 1}/{retries})")
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                return BeautifulSoup(response.content, 'lxml')
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout fetching {url}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to fetch {url}: {e}")
            
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
        
        return None
    
    def extract_episode_data(self, soup: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
        """Extract episode information from parsed HTML."""
        episodes = []
        selectors = self.config.get('selectors', {})
        
        # Find all table rows that look like episode entries (exclude headers/navigation)
        table_rows = soup.select(selectors.get('episode_row', 'tr'))
        valid_tables = soup.select(selectors.get('episode_table', 'table.wikitable'))
        
        # Filter to only rows within valid episode tables
        valid_row_ids = set()
        for table in valid_tables:
            for idx, row in enumerate(table.select('tr')):
                valid_row_ids.add(id(row))
        
        processed_count = 0
        for row in table_rows:
            # Skip rows not in episode tables
            if id(row) not in valid_row_ids:
                continue
            
            cells = row.find_all(['td', 'th'])
            
            # Skip rows with fewer than 2 cells (likely headers or navigation)
            if len(cells) < 2:
                continue
            
            # Skip rows that look like section headers (usually have colspan)
            if any(cell.get('colspan') for cell in cells):
                continue
            
            # Skip rows with very few characters (likely spacers/dividers)
            total_text_length = sum(len(cell.get_text(strip=True)) for cell in cells)
            if total_text_length < 10:
                continue
            
            episode = {
                'scraped_at': datetime.now().isoformat(),
                'source_url': base_url
            }
            
            # Extract episode number
            num_cell = cells[0]
            if num_cell:
                episode['episode_number'] = self._clean_text(num_cell.get_text())
            else:
                episode['episode_number'] = None
            
            # Extract season/episode reference
            season_cell = cells[1] if len(cells) > 1 else None
            if season_cell:
                episode['season_episode'] = self._clean_text(season_cell.get_text())
            else:
                episode['season_episode'] = None
            
            # Extract title (check for link first)
            title_cell = cells[2] if len(cells) > 2 else None
            if title_cell:
                link = title_cell.find('a')
                if link:
                    episode['title'] = self._clean_text(link.get_text())
                    href = link.get('href', '')
                    if href and not href.startswith('#'):
                        episode['title_url'] = urljoin(base_url, href)
                    else:
                        episode['title_url'] = None
                else:
                    episode['title'] = self._clean_text(title_cell.get_text())
                    episode['title_url'] = None
            else:
                episode['title'] = None
                episode['title_url'] = None
            
            episodes.append(episode)
            processed_count += 1
        
        logger.info(f"Processed {processed_count} rows, extracted {len(episodes)} valid episodes")
        return episodes
    
    def follow_episode_links(self, episodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Follow links on episode pages for additional details."""
        if not self.config.get('follow_links', False):
            return episodes
        
        logger.info(f"Following episode links for additional details... (limit: {self.config.get('max_pages', 50)})")
        
        max_pages = min(self.config.get('max_pages', 50), len(episodes))
        
        for i, episode in enumerate(episodes[:max_pages]):
            # Delay on every iteration, not just every 10th
            if i > 0:
                delay = self.config.get('delay_between_requests', 2.0)
                time.sleep(delay)
            
            title_url = episode.get('title_url')
            if not title_url:
                continue
            
            page_soup = self.fetch_page(title_url)
            if not page_soup:
                continue
            
            # Extract additional info from individual episode pages
            additional = {}
            
            # Try multiple common patterns for air date
            air_date_patterns = [
                ('Air date:', self._find_label_value),
                ('Release date:', self._find_label_value),
                ('aired:', self._find_label_case_insensitive),
            ]
            
            for label, finder_func in air_date_patterns:
                result = finder_func(page_soup, label)
                if result:
                    additional['air_date'] = result
                    break
            
            # Try multiple patterns for director
            dir_patterns = [
                ('Director:', self._find_label_value),
                ('Directed by:', self._find_label_value),
            ]
            
            for label, finder_func in dir_patterns:
                result = finder_func(page_soup, label)
                if result:
                    additional['director'] = result
                    break
            
            # Try to find synopsis/plot
            synopsis_elem = page_soup.find('div', {'class': ['synopsis', 'plot', 'summary']})
            if synopsis_elem:
                additional['synopsis'] = self._clean_text(synopsis_elem.get_text())
            
            episode.update(additional)
            
            # Log progress periodically
            if (i + 1) % 10 == 0 or (i + 1) == max_pages:
                logger.info(f"Progress: {i + 1}/{max_pages} episodes processed")
        
        return episodes
    
    def _find_label_value(self, soup: BeautifulSoup, label: str) -> Optional[str]:
        """Find value next to a label in infobox-style layout."""
        # Try finding the label text then the sibling element
        for text_elem in soup.find_all(string=lambda text: text and label.lower() in text.lower()):
            parent = text_elem.parent
            if parent:
                # Look for the next element containing the actual value
                for sibling in parent.next_siblings:
                    if isinstance(sibling, NavigableString):
                        cleaned = self._clean_text(str(sibling).replace(label, '').strip())
                        if cleaned:
                            return cleaned
                    elif hasattr(sibling, 'get_text'):
                        cleaned = self._clean_text(sibling.get_text().replace(label, '').strip())
                        if cleaned:
                            return cleaned
        return None
    
    def _find_label_case_insensitive(self, soup: BeautifulSoup, label: str) -> Optional[str]:
        """Case-insensitive search for label and extract value."""
        for text_elem in soup.find_all(string=lambda text: text and label.lower() in str(text).lower()):
            parent = text_elem.parent
            if parent:
                next_elem = parent.find_next()
                if next_elem:
                    return self._clean_text(next_elem.get_text().strip())
        return None
    
    def _clean_text(self, text: Optional[str]) -> Optional[str]:
        """Clean and normalize extracted text."""
        if text is None:
            return None
        
        # Convert to string if needed
        text = str(text)
        
        # Remove extra whitespace
        text = ' '.join(text.split())
        
        # Remove common wiki artifacts
        text = text.replace('[', '').replace(']', '')
        text = re.sub(r'\[\d+\]', '', text)  # Remove citation numbers like [1][2]
        
        return text.strip() if text.strip() else None
    
    def save_to_json(self, data: List[Dict[str, Any]], filepath: str) -> bool:
        """Save scraped data to JSON file."""
        try:
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"✅ Saved {len(data)} episodes to {filepath}")
            return True
        except IOError as e:
            logger.error(f"❌ Failed to save JSON: {e}")
            return False
    
    def save_to_csv(self, data: List[Dict[str, Any]], filepath: str) -> bool:
        """Save scraped data to CSV file."""
        try:
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            
            if not data:
                logger.warning("⚠️ No data to save to CSV")
                return True
            
            df = pd.DataFrame(data)
            df.to_csv(filepath, index=False, encoding='utf-8-sig')
            logger.info(f"✅ Saved {len(data)} episodes to {filepath}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to save CSV: {e}")
            return False
    
    def validate_data(self, episodes: List[Dict[str, Any]]) -> bool:
        """Validate scraped data before saving."""
        if not episodes:
            logger.warning("⚠️ No episodes were scraped - check selectors")
            return False
        
        # Check for required fields
        valid_count = sum(1 for e in episodes if e.get('episode_number') and e.get('title'))
        validation_rate = valid_count / len(episodes) * 100
        
        logger.info(f"📋 Validation: {valid_count}/{len(episodes)} episodes ({validation_rate:.1f}%) have required fields")
        
        return validation_rate > 50  # At least 50% should have core data
    
    def run(self) -> bool:
        """Execute the complete scraping pipeline."""
        logger.info("=" * 60)
        logger.info("🚀 Starting Doraemon Episode Scraper")
        logger.info(f"📍 Target: {self.config['target_url']}")
        logger.info("=" * 60)
        
        start_time = time.time()
        
        # Fetch main page
        main_soup = self.fetch_page(self.config['target_url'])
        
        if not main_soup:
            logger.error("❌ Failed to fetch main page. Exiting.")
            return False
        
        # Verify we got actual content
        if len(main_soup.body.decode_contents()) < 1000:
            logger.error("❌ Page content too small - possibly blocked or wrong URL")
            return False
        
        # Extract episodes from main page
        episodes = self.extract_episode_data(main_soup, self.config['target_url'])
        logger.info(f"📊 Found {len(episodes)} potential episodes on main page")
        
        if not episodes:
            logger.error("❌ No episodes extracted - verify CSS selectors in config.yaml")
            return False
        
        # Validate data
        if not self.validate_data(episodes):
            logger.warning("⚠️ Data quality low - proceeding anyway")
        
        # Follow links for additional details if configured
        if self.config.get('follow_links', False):
            episodes = self.follow_episode_links(episodes)
        
        # Save data
        json_saved = self.save_to_json(episodes, self.config['output_json'])
        csv_saved = self.save_to_csv(episodes, self.config['output_csv'])
        
        # Create summary report
        self._create_summary(episodes, start_time)
        
        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"✅ Scraping completed in {elapsed:.1f}s")
        logger.info(f"   JSON saved: {json_saved}")
        logger.info(f"   CSV saved: {csv_saved}")
        logger.info("=" * 60)
        
        return json_saved and csv_saved
    
    def _create_summary(self, episodes: List[Dict[str, Any]], start_time: float):
        """Create a summary report of scraped data."""
        end_time = time.time()
        
        summary = {
            'total_episodes': len(episodes),
            'episodes_with_titles': sum(1 for e in episodes if e.get('title')),
            'episodes_with_numbers': sum(1 for e in episodes if e.get('episode_number')),
            'episodes_with_urls': sum(1 for e in episodes if e.get('title_url')),
            'episodes_with_air_dates': sum(1 for e in episodes if e.get('air_date')),
            'episodes_with_directors': sum(1 for e in episodes if e.get('director')),
            'unique_seasons': len(set(e.get('season_episode') for e in episodes if e.get('season_episode'))),
            'output_files': [
                self.config.get('output_json'),
                self.config.get('output_csv')
            ],
            'scrape_timestamp': datetime.now().isoformat(),
            'duration_seconds': round(end_time - start_time, 2),
            'target_url': self.config.get('target_url')
        }
        
        summary_path = 'data/scrape_summary.json'
        Path(summary_path).parent.mkdir(parents=True, exist_ok=True)
        
        try:
            with open(summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            logger.info(f"📄 Summary saved to {summary_path}")
        except IOError as e:
            logger.error(f"Failed to save summary: {e}")
        
        print(f"\n{'='*40}")
        print(f"📊 SCRAPING SUMMARY")
        print(f"{'='*40}")
        print(f"   Total Episodes: {summary['total_episodes']}")
        print(f"   With Titles: {summary['episodes_with_titles']}")
        print(f"   With Episode Numbers: {summary['episodes_with_numbers']}")
        print(f"   With Air Dates: {summary['episodes_with_air_dates']}")
        print(f"   Unique Seasons: {summary['unique_seasons']}")
        print(f"   Duration: {summary['duration_seconds']}s")
        print(f"{'='*40}\n")


# Add missing import at top of file
import re


if __name__ == '__main__':
    scraper = DoraemonScraper()
    success = scraper.run()
    sys.exit(0 if success else 1)
