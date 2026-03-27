import aiohttp


async def send_ifttt_event(event, key, header, body, url, logger=None):
    payload = {"value1": header, "value2": body, "value3": url}
    endpoint = f"https://maker.ifttt.com/trigger/{event}/with/key/{key}"
    async with aiohttp.ClientSession() as session:
        async with session.post(endpoint, data=payload) as resp:
            if logger is not None:
                logger.info(f"[{url}] {header}{body}\nIFTTT status: {resp.status}")
            return resp.status
