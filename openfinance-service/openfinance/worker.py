import os
import signal
import time
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .runtime import resources
from .store import Connection, Event, run_one


def main():
    _,sf,provider=resources()
    sandbox_only=os.environ.get('SANDBOX_ONLY','true').lower()!='false'
    stop=False
    def halt(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,halt)
    signal.signal(signal.SIGINT,halt)
    last_poll=0
    while not stop:
        try:
            # Single worker deployment; the lease also recovers interrupted jobs.
            if time.time()-last_poll>300:
                last_poll=time.time()
                with sf() as s:
                    ids=list(s.scalars(select(Connection.id).where(Connection.active==True)))
                for item_id in ids:
                    try:
                        with sf.begin() as s:
                            s.add(Event(id=f'poll:{item_id}:{int(time.time()//21600)}',item_id=item_id,
                                        event='item/updated',payload={},available_at=time.time()))
                    except IntegrityError: pass
            if not run_one(sf,provider,sandbox_only): time.sleep(2)
        except Exception as exc:
            print(f'worker error: {type(exc).__name__}',flush=True)
            time.sleep(5)


if __name__=='__main__': main()
