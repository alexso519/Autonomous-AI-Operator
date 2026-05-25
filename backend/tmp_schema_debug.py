import asyncio
import aiosqlite
from app.database import database as dbmod

async def main():
    conn = await aiosqlite.connect(':memory:')
    conn.row_factory = aiosqlite.Row
    await dbmod._init_tables(conn)
    cursor = await conn.execute('PRAGMA table_info(executions)')
    rows = await cursor.fetchall()
    for row in rows:
        print(tuple(row))
    await conn.close()

asyncio.run(main())
