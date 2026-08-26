import asyncio
import logging
from aioslsk.client import SoulSeekClient
from aioslsk.settings import Settings, CredentialsSettings

logging.basicConfig(level=logging.INFO)

async def main():
    settings = Settings(
        credentials=CredentialsSettings(
            username="scrantanamo",
            password="testforbanana123"
        )
    )

    async with SoulSeekClient(settings) as client:
        await client.login()
        print("Logged in, searching for 'skrillex'...")
        try:
            results = await client.searches.search('skrillex', timeout=5)
            print("Search finished. Result type:", type(results))
            if results:
                print(dir(results[0]))
                print("First result:", results[0])
            else:
                print("No results within 5 seconds.")
        except Exception as e:
            print("Search error:", e)

if __name__ == '__main__':
    asyncio.run(main())
