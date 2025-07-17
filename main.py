import asyncio
from admin_bot import run_adminbot

async def main():
    await run_adminbot()

if __name__ == "__main__":
    asyncio.run(main())
