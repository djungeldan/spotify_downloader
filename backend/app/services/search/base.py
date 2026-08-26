from abc import ABC, abstractmethod
from typing import List, Dict, Any, Callable

class SearchProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    async def search(self, query: str, on_result: Callable[[Dict[str, Any]], None] = None) -> List[Dict[str, Any]]:
        """
        Search for a query.
        Returns a list of standardized result dictionaries:
        [{
            'source': 'soulseek',
            'filename': 'Artist - Title.mp3',
            'size': 1024,
            'duration': 120,
            'extension': 'mp3',
            'bitrate': 320,
            'download_link': '...', # Token or Magnet
            'metadata': {} # Original result object
        }]
        """
        pass
        
    @abstractmethod
    async def download(self, result: Dict[str, Any], output_path: str) -> bool:
        """
        Initiate a download for the given result.
        """
        pass
