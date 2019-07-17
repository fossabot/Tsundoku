import asyncio
import typing

import feedparser
from fuzzywuzzy import process
from quart.ctx import AppContext

from config import get_config_value
from feeds.exceptions import InvalidPollerInterval


class Poller:
    def __init__(self, app_context: AppContext):
        self.app = app_context.app
        self.loop = asyncio.get_running_loop()

        self.current_parser = None  # keeps track of the current RSS feed parser.

        interval = get_config_value("Tsundoku", "polling_interval")
        try:
            self.interval = int(interval)
        except ValueError:
            raise InvalidPollerInterval(f"'{interval}' is an invalid polling interval.")


    async def start(self) -> None:
        """
        Iterates through every installed RSS parser
        and will check for new items to download.

        The program will poll every n seconds, as specified
        in the configuration file.
        """
        while True:
            for parser in self.app.rss_parsers:
                self.current_parser = parser
                feed = await self.get_feed_from_parser()
                await self.check_feed(feed)

            await asyncio.sleep(self.interval)


    async def check_feed(self, feed: dict) -> None:
        """
        Iterates through the list of items in an
        RSS feed and will individually check each
        item.

        Parameters
        ----------
        feed: dict
            The RSS feed.
        """
        for item in feed["items"]:
            await self.check_item(item)


    async def check_item_for_match(self, show_name: str, episode: int) -> typing.Optional[str]:
        """
        Takes a show name from RSS and an episode from RSS and
        checks if the object should be downloaded.

        An item should be downloaded only if it is in the
        `shows` table and the episode is not already in the
        `show_entry` table.

        Parameters
        ----------
        show_name: str
            The name of the show from RSS.
        episode: int
            The episode of the entry from RSS.

        Returns
        -------
        typing.Optional[str]
            The name of the matched show.
        """
        async with self.app.db_pool.acquire() as con:
            desired_shows = await con.fetch("""
                SELECT search_title FROM shows;
            """)
        show_list = [show["search_title"] for show in desired_shows]
        
        if not show_list:
            return

        match = process.extractOne(show_name, show_list)

        if match[1] >= 90:
            return match[0]


    async def check_item(self, item: dict) -> None:
        """
        Checks an item to see if it is from a 
        desired show entry, and will then begin
        downloading the item if so.

        Parameters
        ----------
        item: dict
            The item to check for matches.
        """
        if hasattr(self.current_parser, "ignore_logic"):
            if self.current_parser.ignore_logic(item) == False:
                return

        if hasattr(self.current_parser, "get_file_name"):
            torrent_name = self.current_parser.get_file_name(item)
        else:
            torrent_name = item["title"]

        show_name = self.current_parser.get_show_name(torrent_name)
        show_episode = self.current_parser.get_episode_number(torrent_name)

        match = await self.check_item_for_match(show_name, show_episode)

        if match is None:
            return

        if hasattr(self.current_parser, "get_link_location"):
            torrent_url = await self.get_torrent_link(item)
        else:
            torrent_url = item["link"]

        print(show_name, show_episode)


    async def get_feed_from_parser(self) -> dict:
        """
        Returns the RSS feed dict from the
        current parser.

        Returns
        -------
        dict
            The parsed RSS feed.
        """
        return await self.loop.run_in_executor(None, feedparser.parse, self.current_parser.url)


    async def get_torrent_link(self, item: dict) -> str:
        """
        Returns a magnet URL for a specified item.
        This is found by using the parser's `get_link_location`
        method and is assisted by a torrent file to magnet
        decoder.

        Parameters
        ----------
        item: dict
            The item to find the magnet URL for.

        Returns
        -------
        str
            The found magnet URL.
        """
        deluge = self.app.deluge
        torrent_location = self.current_parser.get_link_location(item)

        return await deluge.get_magnet(torrent_location)