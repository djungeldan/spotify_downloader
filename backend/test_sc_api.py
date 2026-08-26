import asyncio, httpx, re

SC_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Origin': 'https://soundcloud.com',
    'Referer': 'https://soundcloud.com/',
}

async def test():
    client_id = '6QNse33jZWUMFNeFn5QzGfBErFktk7Sa'
    async with httpx.AsyncClient(headers=SC_HEADERS, timeout=15, follow_redirects=True) as c:
        # Step 1: Resolve profile
        r = await c.get(f'https://api-v2.soundcloud.com/resolve?url=https://soundcloud.com/explorersoftheinternet&client_id={client_id}&app_version=1710001000')
        print('Resolve status:', r.status_code)
        data = r.json()
        print('Kind:', data.get('kind'), 'ID:', data.get('id'))
        if data.get('id'):
            user_id = data['id']
            r2 = await c.get(f'https://api-v2.soundcloud.com/users/{user_id}/tracks?client_id={client_id}&limit=5')
            print('Tracks status:', r2.status_code)
            print('Headers used:', dict(c.headers))
            if r2.status_code == 200:
                t = r2.json()
                print('First track:', t['collection'][0]['permalink_url'] if t['collection'] else 'empty')
            else:
                print('Response:', r2.text[:300])

asyncio.run(test())
