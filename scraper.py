#!/usr/bin/env python3
"""
Doraemon Wiki Episode Scraper v2.0
Automatically scrapes episode data from the 2005 Remake series.
Handles multi-page season structure - extracts season links then visits each.
Designed for GitHub Actions integration.
"""

import os
import sys
import json
import time
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urljoin

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
    """Main scraper class for Doraemon wiki episode data with multi-page support."""
    
    def __init__(self, config_path: str = 'config.yaml'):
        self.config = self._load_config(config_path)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; DoraemonScraper/2.0; +https://github.com/yourusername/doraemon-scraper)',
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
                'season_link': 'a[href*="season"], a[href*="/Season "]',
                'episode_table': 'table.wikitable, table.episode-list',
                'episode_row': 'tr',
                'episode_number': 'td:first-child, th:first-child',
                'season_episode': 'td:nth-child(2), th:nth-child(2)',
                'title': 'td:nth-child(3) a, td:nth-child(3), th:nth-child(3) a, th:nth-child(3)'
            },
            'delay_between_requests': 2.0,
            'max_seasons': 50
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
                time.sleep(2 ** attempt)
        
        return None
    
    def extract_season_links(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        """Extract all season page links from the main page."""
        selectors = self.config.get('selectors', {})
        season_links = []
        
        # Find all links that point to season pages
        link_selectors = selectors.get('season_link', 'a[href*="season"]')
        links = soup.select(link_selectors)
        
        for link in links:
            href = link.get('href', '')
            if not href:
                continue
            
            # Build full URL
            full_url = urljoin(base_url, href)
            
            # Avoid duplicates
            if full_url not in season_links:
                season_links.append(full_url)
                logger.info(f"  Found season link: {full_url}")
        
        logger.info(f"Found {len(season_links)} season pages")
        return season_links
    
    def extract_episodes_from_season_page(self, soup: BeautifulSoup, season_url: str, 
                                           season_name: str) -> List[Dict[str, Any]]:
        """Extract all episodes from a single season page."""
        episodes = []
        selectors = self.config.get('selectors', {})
        
        # Find episode rows
        table_rows = soup.select(selectors.get('episode_row', 'tr'))
        valid_tables = soup.select(selectors.get('episode_table', 'table.wikitable'))
        
        # Get all row IDs that belong to valid tables
        valid_row_ids = set()
        for table in valid_tables:
            for row in table.find_all('tr'):
                valid_row_ids.add(id(row))
        
        for row in table_rows:
            if id(row) not in valid_row_ids:
                continue
            
            cells = row.find_all(['td', 'th'])
            
            # Skip rows with fewer than 2 cells
            if len(cells) < 2:
                continue
            
            # Skip header rows (colspan)
            if any(cell.get('colspan') for cell in cells):
                continue
            
            # Skip very short rows (spacers/dividers)
            total_text = sum(len(cell.get_text(strip=True)) for cell in cells)
            if total_text < 5:
                continue
            
            episode = {
                'season': season_name,
                'source_season_url': season_url,
                'scraped_at': datetime.now().isoformat()
            }
            
            # Extract episode number
            num_cell = cells[0]
            if num_cell:
                raw_text = self._clean_text(num_cell.get_text())
                # Normalize numbers (handle "1", "#1", "Ep. 1", etc.)
                episode['episode_number'] = self._normalize_episode_number(raw_text)
            else:
                episode['episode_number'] = None
            
            # Extract season/episode reference (if exists)
            season_ref_cell = cells[1] if len(cells) > 1 else None
            if season_ref_cell:
                episode['season_episode'] = self._clean_text(season_ref_cell.get_text())
            else:
                episode['season_episode'] = None
            
            # Extract title (check for link)
            title_cell = cells[2] if len(cells) > 2 else None
            if title_cell:
                link = title_cell.find('a')
                if link:
                    episode['title'] = self._clean_text(link.get_text())
                    href = link.get('href', '')
                    if href and not href.startswith('#'):
                        episode['title_url'] = urljoin(season_url, href)
                    else:
                        episode['title_url'] = None
                else:
                    episode['title'] = self._clean_text(title_cell.get_text())
                    episode['title_url'] = None
            else:
                episode['title'] = None
                episode['title_url'] = None
            
            episodes.append(episode)
        
        logger.info(f"  Extracted {len(episodes)} episodes from {season_name}")
        return episodes
    
    def _normalize_episode_number(self, text: Optional[str]) -> Optional[str]:
        """Normalize episode numbers to consistent format."""
        if not text:
            return None
        
        # Remove common prefixes
        patterns = [r'#(\d+)', r'ep\.?\s*(\d+)', r'episode\s*(\d+)', r'^(\d+)']
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        # If no pattern matched, return original cleaned text
        return text.strip() if text.strip() else None
    
    def _clean_text(self, text: Optional[str]) -> Optional[str]:
        """Clean and normalize extracted text."""
        if text is None:
            return None
        
        text = str(text)
        text = ' '.join(text.split())  # Collapse whitespace
        text = re.sub(r'\[\d+\]', '', text)  # Remove citation markers [1], [2]
        text = text.replace('[', '').replace(']', '')
        
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
            # Sort by season and episode number for consistency
            df = df.sort_values(by=['season', 'episode_number'])
            df.to_csv(filepath, index=False, encoding='utf-8-sig')
            logger.info(f"✅ Saved {len(data)} episodes to {filepath}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to save CSV: {e}")
            return False
    
    def _create_summary(self, all_episodes: List[Dict[str, Any]], 
                        seasons_scraped: int, 
                        start_time: float):
        """Create a summary report."""
        end_time = time.time()
        
        # Calculate statistics
        seasons = list(set(e.get('season') for e in all_episodes if e.get('season')))
        
        summary = {
            'total_episodes': len(all_episodes),
            'total_seasons_scraped': seasons_scraped,
            'season_names': sorted(seasons),
            'episodes_per_season': {
                season: sum(1 for e in all_episodes if e.get('season') == season)
                for season in seasons
            },
            'episodes_with_titles': sum(1 for e in all_episodes if e.get('title')),
            'episodes_with_numbers': sum(1 for e in all_episodes if e.get('episode_number')),
            'episodes_with_urls': sum(1 for e in all_episodes if e.get('title_url')),
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
        
        # Print formatted summary
        print(f"\n{'='*50}")
        print(f"📊 SCRAPER SUMMARY")
        print(f"{'='*50}")
        print(f"   Total Seasons Scraped: {seasons_scraped}")
        print(f"   Total Episodes: {summary['total_episodes']}")
        print(f"   Seasons: {', '.join(summary['season_names'])}")
        print(f"   Episodes Per Season: {summary['episodes_per_season']}")
        print(f"   Duration: {summary['duration_seconds']}s")
        print(f"{'='*50}\n")
    
    def run(self) -> bool:
        """Execute the complete scraping pipeline."""
        logger.info("=" * 60)
        logger.info("🚀 Starting Doraemon Multi-Page Episode Scraper v2.0")
        logger.info(f"📍 Target: {self.config['target_url']}")
        logger.info("=" * 60)
        
        start_time = time.time()
        all_episodes = []
        seasons_scraped = 0
        
        # Step 1: Fetch main page
        main_soup = self.fetch_page(self.config['target_url'])
        
        if not main_soup:
            logger.error("❌ Failed to fetch main page. Exiting.")
            return False
        
        logger.info("✓ Main page fetched successfully")
        
        # Step 2: Extract season links from main page
        season_links = self.extract_season_links(main_soup, self.config['target_url'])
        
        if not season_links:
            logger.error("❌ No season links found - check selectors in config.yaml")
            return False
        
        # Step 3: Visit each season page and extract episodes
        max_seasons = self.config.get('max_seasons', 50)
        
        for i, season_url in enumerate(season_links[:max_seasons]):
            if i > 0:
                delay = self.config.get('delay_between_requests', 2.0)
                logger.info(f"Waiting {delay}s before next request...")
                time.sleep(delay)
            
            logger.info(f"[{i+1}/{min(max_seasons, len(season_links))}] Processing season page")
            
            season_soup = self.fetch_page(season_url)
            
            if not season_soup:
                logger.warning(f"  ⚠️ Failed to fetch {season_url}")
                continue
            
            # Extract season name from URL or page title
            season_name = self._extract_season_name(season_url)
            
            # Extract episodes from this season page
            episodes = self.extract_episodes_from_season_page(season_soup, season_url, season_name)
            all_episodes.extend(episodes)
            seasons_scraped += 1
        
        # Step 4: Validate collected data
        if not all_episodes:
            logger.error("❌ No episodes extracted across all pages - verify selectors")
            return False
        
        valid_count = sum(1 for e in all_episodes if e.get('title'))
        validation_rate = valid_count / len(all_episodes) * 100
        logger.info(f"📋 Validation: {valid_count}/{len(all_episodes)} episodes ({validation_rate:.1f}%) have titles")
        
        # Step 5: Save data
        json_saved = self.save_to_json(all_episodes, self.config['output_json'])
        csv_saved = self.save_to_csv(all_episodes, self.config['output_csv'])
        
        # Step 6: Create summary
        self._create_summary(all_episodes, seasons_scraped, start_time)
        
        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"✅ Scraping completed in {elapsed:.1f}s")
        logger.info(f"   Total Seasons: {seasons_scraped}")
        logger.info(f"   Total Episodes: {len(all_episodes)}")
        logger.info("=" * 60)
        
        return json_saved and csv_saved
    
    def _extract_season_name(self, url: str) -> str:
        """Extract season name from URL or generate one."""
        # Try to extract from URL (e.g., /Season%2011 -> Season 11)
        season_match = re.search(r'Season[\s%]*(\d+)', url, re.IGNORECASE)
        if season_match:
            return f"Season {season_match.group(1)}"
        return f"Unknown_Season_{len(url)}"


if __name__ == '__main__':
    scraper = DoraemonScraper()
    success = scraper.run()
    sys.exit(0 if success else 1)
