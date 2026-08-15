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
import requests
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from bs4 import BeautifulSoup
import pandas as pd
from urllib.parse import urljoin, urlparse

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
            'User-Agent': 'Mozilla/5.0 (compatible; DoraemonScraper/1.0; +https://github.com/yourusername/doraemon-scraper)'
        })
        self.all_episodes: List[Dict[str, Any]] = []
        
    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from YAML file."""
        try:
            with open(config_path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning(f"Config file {config_path} not found. Using defaults.")
            return self._get_default_config()
    
    def _get_default_config(self) -> Dict:
        """Default configuration if file missing."""
        return {
            'target_url': 'https://tsuki.page/wiki/doraemon/2005-present',
            'output_json': 'data/doraemon_episodes.json',
            'output_csv': 'data/doraemon_episodes.csv',
            'selectors': {
                'episode_table': 'table.wikitable, table.episode-list',
                'episode_row': 'tr',
                'episode_number': 'td:first-child, th:first-child',
                'season_episode': 'td:nth-child(2), th:nth-child(2)',
                'title': 'td:nth-child(3) a, td:nth-child(3)'
            },
            'follow_links': True,
            'delay_between_requests': 1.5,
            'max_pages': 50
        }
    
    def fetch_page(self, url: str) -> Optional[BeautifulSoup]:
        """Fetch and parse a webpage."""
        try:
            logger.info(f"Fetching: {url}")
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return BeautifulSoup(response.content, 'lxml')
        except requests.RequestException as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None
    
    def extract_episode_data(self, soup: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
        """Extract episode information from parsed HTML."""
        episodes = []
        selectors = self.config.get('selectors', {})
        
        # Find all table rows that look like episode entries
        table_rows = soup.select(selectors.get('episode_row', 'tr'))
        
        for row in table_rows:
            cells = row.find_all(['td', 'th'])
            
            if len(cells) >= 2:  # Minimum required columns
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
                        episode['title_url'] = urljoin(base_url, link.get('href', ''))
                    else:
                        episode['title'] = self._clean_text(title_cell.get_text())
                        episode['title_url'] = None
                else:
                    episode['title'] = None
                    episode['title_url'] = None
                
                episodes.append(episode)
        
        return episodes
    
    def follow_episode_links(self, episodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Follow links on episode pages for additional details."""
        if not self.config.get('follow_links', False):
            return episodes
        
        logger.info("Following episode links for additional details...")
        
        for i, episode in enumerate(episodes):
            if i > 0 and i % 10 == 0:
                time.sleep(self.config.get('delay_between_requests', 1.5))
            
            title_url = episode.get('title_url')
            if title_url:
                page_soup = self.fetch_page(title_url)
                if page_soup:
                    # Additional extraction from individual episode pages
                    # Customize based on actual page structure
                    additional = {}
                    
                    # Example: air date
                    date_element = page_soup.find(class_='infobox-date') or page_soup.find(string='Air date:')
                    if date_element:
                        additional['air_date'] = self._clean_text(str(date_element))
                    
                    # Example: director
                    dir_element = page_soup.find(string='Director:')
                    if dir_element:
                        additional['director'] = self._clean_text(dir_element.parent.next_sibling) if hasattr(dir_element.parent, 'next_sibling') else None
                    
                    episode.update(additional)
        
        return episodes
    
    def _clean_text(self, text: Optional[str]) -> Optional[str]:
        """Clean and normalize extracted text."""
        if text is None:
            return None
        return ' '.join(text.split()).strip()
    
    def save_to_json(self, data: List[Dict[str, Any]], filepath: str):
        """Save scraped data to JSON file."""
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(data)} episodes to {filepath}")
    
    def save_to_csv(self, data: List[Dict[str, Any]], filepath: str):
        """Save scraped data to CSV file."""
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(data)
        df.to_csv(filepath, index=False, encoding='utf-8-sig')
        logger.info(f"Saved {len(data)} episodes to {filepath}")
    
    def run(self):
        """Execute the complete scraping pipeline."""
        logger.info("=" * 60)
        logger.info("Starting Doraemon Episode Scraper")
        logger.info("=" * 60)
        
        # Fetch main page
        main_soup = self.fetch_page(self.config['target_url'])
        
        if not main_soup:
            logger.error("Failed to fetch main page. Exiting.")
            return
        
        # Extract episodes from main page
        episodes = self.extract_episode_data(main_soup, self.config['target_url'])
        logger.info(f"Found {len(episodes)} episodes on main page")
        
        # Follow links for additional details if configured
        if self.config.get('follow_links', False):
            episodes = self.follow_episode_links(episodes)
        
        # Save data
        if self.config.get('output_json'):
            self.save_to_json(episodes, self.config['output_json'])
        
        if self.config.get('output_csv'):
            self.save_to_csv(episodes, self.config['output_csv'])
        
        # Create summary report
        self._create_summary(episodes)
        
        logger.info("Scraping completed successfully!")
        logger.info("=" * 60)
    
    def _create_summary(self, episodes: List[Dict[str, Any]]):
        """Create a summary report of scraped data."""
        summary = {
            'total_episodes': len(episodes),
            'episodes_with_titles': sum(1 for e in episodes if e.get('title')),
            'episodes_with_urls': sum(1 for e in episodes if e.get('title_url')),
            'unique_seasons': len(set(e.get('season_episode') for e in episodes if e.get('season_episode'))),
            'output_files': [
                self.config.get('output_json'),
                self.config.get('output_csv')
            ],
            'scrape_timestamp': datetime.now().isoformat()
        }
        
        summary_path = 'data/scrape_summary.json'
        Path(summary_path).parent.mkdir(parents=True, exist_ok=True)
        
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Summary saved to {summary_path}")
        print(f"\n📊 Summary:")
        print(f"   Total Episodes: {summary['total_episodes']}")
        print(f"   With Titles: {summary['episodes_with_titles']}")
        print(f"   Unique Seasons: {summary['unique_seasons']}")


if __name__ == '__main__':
    scraper = DoraemonScraper()
    scraper.run()
