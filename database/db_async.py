"""
database/db_async.py - Async DB Wrapper untuk Bot Jual Gmail
Thin async wrapper — semua DB call dijalankan di thread pool executor
agar tidak blokir asyncio event loop.
"""
import asyncio
from functools import partial
from concurrent.futures import ThreadPoolExecutor
from database import db
import atexit

# Thread pool khusus DB - 32 workers cukup
_executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="db-worker-gmail")
atexit.register(_executor.shutdown, wait=False)


def _run(func, *args, **kwargs):
    """Jalankan fungsi DB sinkron di thread pool, return coroutine."""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(_executor, partial(func, *args, **kwargs))


class AsyncDBProxy:
    """Async wrapper untuk semua fungsi db.py secara dinamis."""
    def __getattr__(self, name):
        attr = getattr(db, name)
        
        # Jika bukan callable, kembalikan langsung
        if not callable(attr):
            return attr
            
        # Untuk get_session, set_session, clear_session, get_connection tetap sinkron
        if name in ("get_session", "set_session", "clear_session", "get_connection", "init_connection_pool"):
            return attr
            
        # Wrap fungsi lainnya ke dalam thread executor
        async def async_wrapper(*args, **kwargs):
            return await _run(attr, *args, **kwargs)
            
        return async_wrapper


# Singleton instance
adb = AsyncDBProxy()
