"""Crawler for yearendlists.com to collect critics' album lists."""

import asyncio
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

BASE_URL = "https://www.yearendlists.com"
START_URL = "https://www.yearendlists.com/category/2025-albums"
URL_PREFIX = "https://www.yearendlists.com/2025"


@dataclass
class Album:
    rank: int
    artist: str
    title: str
    artist_url: str | None = None
    album_url: str | None = None


@dataclass
class CriticList:
    url: str
    title: str
    critic: str
    albums: list[Album]


class YearEndListsCrawler:
    def __init__(self, delay: float = 0.5):
        self.delay = delay
        self.visited: set[str] = set()
        self.list_urls: set[str] = set()
        self.lists: list[CriticList] = []

    async def fetch(self, client: httpx.AsyncClient, url: str) -> str | None:
        """Fetch a URL with rate limiting."""
        if url in self.visited:
            return None
        self.visited.add(url)

        try:
            await asyncio.sleep(self.delay)
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            return response.text
        except httpx.HTTPError as e:
            console.print(f"[red]Error fetching {url}: {e}[/red]")
            return None

    def extract_list_urls(self, html: str, base_url: str) -> tuple[set[str], str | None]:
        """Extract list URLs and next page URL from a category page."""
        soup = BeautifulSoup(html, "lxml")
        list_urls = set()
        next_page = None

        # Find all links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full_url = urljoin(base_url, href)

            # Check if it's a 2025 list URL (but not a category/page URL)
            if full_url.startswith(URL_PREFIX) and "/category/" not in full_url and "/page/" not in full_url:
                # Skip album and artist detail pages
                if "/albums/" not in full_url and "/artists/" not in full_url:
                    # Only include music/album lists, exclude film/TV/book/podcast/poetry
                    url_lower = full_url.lower()
                    is_music = any(kw in url_lower for kw in ["album", "song", "music", "record"])
                    is_other = any(kw in url_lower for kw in ["film", "movie", "tv-show", "book", "podcast", "television", "poetry", "reads"])
                    if is_music or not is_other:
                        list_urls.add(full_url)

            # Check for next page
            if "Next" in a.get_text() or "›" in a.get_text():
                if "/page/" in href:
                    next_page = urljoin(base_url, href)

        return list_urls, next_page

    def parse_list_page(self, html: str, url: str) -> CriticList | None:
        """Parse a critic's list page and extract album data."""
        soup = BeautifulSoup(html, "lxml")

        # Extract title and critic from the page
        title_elem = soup.find("h1") or soup.find("h2")
        if not title_elem:
            return None

        full_title = title_elem.get_text(strip=True)

        # Try to extract critic name from the title
        # Common patterns: "Critic Name: The Top 50 Albums of 2025" or "The Top 50 Albums of 2025 by Critic Name"
        critic = ""
        title = full_title

        # Pattern 1: "Critic Name: ..." (colon separator)
        if ": " in full_title:
            parts = full_title.split(": ", 1)
            # Check if first part looks like a critic name (not "The Top 50")
            if not any(word in parts[0].lower() for word in ["top", "best", "album", "favorite"]):
                critic = parts[0].strip()
                title = parts[1].strip()

        # Pattern 2: Fallback to URL parsing
        if not critic:
            url_path = urlparse(url).path
            # URL pattern: /2025/critic-name-top-50-albums-of-2025
            if url_path.startswith("/2025/"):
                slug = url_path[6:]  # Remove /2025/
                parts = slug.split("-")
                # Find where list descriptor words start
                # Note: "the" at the START is often part of the name (The Atlantic), but mid-phrase means list title
                stop_words = {"top", "best", "favorite", "favourite", "albums", "records", "10", "15", "20", "25", "50", "100"}
                for i, part in enumerate(parts):
                    lower_part = part.lower()
                    # "the" is only a stop word if not at position 0
                    if lower_part in stop_words or (lower_part == "the" and i > 0):
                        if i > 0:
                            critic = " ".join(parts[:i]).title()
                        break
                if not critic and parts:
                    critic = parts[0].title()

        albums = []
        rank = 0

        # Find all lists (ol or ul) which contain the albums
        # Some sites use ol (ordered), others use ul (unordered)
        for list_elem in soup.find_all(["ol", "ul"]):
            for li in list_elem.find_all("li", recursive=False):
                links = li.find_all("a", href=True)

                album_title = ""
                artist = ""
                album_url = None
                artist_url = None
                has_album_or_artist_link = False

                for link in links:
                    href = link["href"]
                    text = link.get_text(strip=True)

                    if "/albums/" in href:
                        album_title = text
                        album_url = urljoin(BASE_URL, href)
                        has_album_or_artist_link = True
                    elif "/artists/" in href:
                        artist = text
                        artist_url = urljoin(BASE_URL, href)
                        has_album_or_artist_link = True

                # Only include entries that have proper album/artist links
                # This filters out navigation menus and other non-album lists
                if has_album_or_artist_link and (album_title or artist):
                    rank += 1
                    albums.append(Album(
                        rank=rank,
                        artist=artist,
                        title=album_title,
                        artist_url=artist_url,
                        album_url=album_url,
                    ))

        if not albums:
            return None

        return CriticList(
            url=url,
            title=title,
            critic=critic,
            albums=albums,
        )

    async def crawl_category_pages(self, client: httpx.AsyncClient) -> None:
        """Crawl all category pages to find list URLs."""
        url = START_URL
        page_num = 1

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Discovering lists...", total=None)

            while url:
                progress.update(task, description=f"Scanning page {page_num}...")
                html = await self.fetch(client, url)
                if not html:
                    break

                new_urls, next_page = self.extract_list_urls(html, url)
                self.list_urls.update(new_urls)
                progress.update(task, description=f"Found {len(self.list_urls)} lists...")

                url = next_page
                page_num += 1

    async def crawl_lists(self, client: httpx.AsyncClient) -> None:
        """Crawl all discovered list pages."""
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Fetching lists...", total=len(self.list_urls))

            for i, url in enumerate(sorted(self.list_urls)):
                progress.update(task, description=f"Fetching list {i+1}/{len(self.list_urls)}...")
                html = await self.fetch(client, url)
                if html:
                    critic_list = self.parse_list_page(html, url)
                    if critic_list and critic_list.albums:
                        self.lists.append(critic_list)
                        progress.update(task, advance=1)

    async def crawl(self) -> list[CriticList]:
        """Run the full crawl."""
        async with httpx.AsyncClient(
            headers={"User-Agent": "music-2025-crawler/1.0 (educational project)"},
            timeout=30.0,
        ) as client:
            console.print("[cyan]Phase 1: Discovering list URLs...[/cyan]")
            await self.crawl_category_pages(client)
            console.print(f"[green]Found {len(self.list_urls)} lists to crawl[/green]")

            console.print("\n[cyan]Phase 2: Fetching and parsing lists...[/cyan]")
            await self.crawl_lists(client)
            console.print(f"[green]Successfully parsed {len(self.lists)} lists[/green]")

        return self.lists

    def save(self, output_path: Path) -> None:
        """Save crawled data to JSON."""
        data = [
            {
                "url": lst.url,
                "title": lst.title,
                "critic": lst.critic,
                "albums": [asdict(a) for a in lst.albums],
            }
            for lst in self.lists
        ]
        output_path.write_text(json.dumps(data, indent=2))
        console.print(f"[green]Saved {len(self.lists)} lists to {output_path}[/green]")


async def run_crawler(output: Path, delay: float = 0.5) -> list[CriticList]:
    """Main entry point for the crawler."""
    crawler = YearEndListsCrawler(delay=delay)
    lists = await crawler.crawl()
    crawler.save(output)
    return lists
