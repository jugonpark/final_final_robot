"""Local console launcher: reuse a healthy control UI, never kill its owner."""
import argparse
import json
import socket
import sys
import webbrowser
from http.client import HTTPException
from urllib.request import ProxyHandler,build_opener


def port_state(port):
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as probe:
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        try:
            probe.bind(('127.0.0.1',port))
            return 'free'
        except OSError:
            pass
    # Ignore proxy settings for loopback. A preview or another local service
    # must not be mistaken for the browser-only robot control application.
    try:
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/api/state',timeout=1.5) as reply:
            state=json.loads(reply.read(2*1024*1024))
        if state.get('runtime_kind')=='browser_control' and state.get('control_available') is True:
            return 'control'
    except (OSError,ValueError,AttributeError,HTTPException):
        pass
    return 'busy'


def select_port(preferred=8766,*,explicit=False,reuse=True,probe=port_state):
    if not 1<=preferred<=65535:raise ValueError('Web port must be 1..65535.')
    first_free=None
    ports=[preferred] if explicit else range(preferred,min(preferred+20,65536))
    for port in ports:
        state=probe(port)
        if state=='control' and reuse:return port,True
        if state=='free' and first_free is None:first_free=port
    if first_free is not None:return first_free,False
    raise ValueError('Requested local port is occupied. Use --web-port with a free port; no existing server was stopped.')


def main(argv=None):
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--web-port',type=int)
    options,forwarded=parser.parse_known_args(argv)
    try:
        port,reused=select_port(8766 if options.web_port is None else options.web_port,explicit=options.web_port is not None,reuse=not forwarded)
    except ValueError as exc:
        print(str(exc),file=sys.stderr);return 1
    url=f'http://127.0.0.1:{port}/'
    if reused:
        print(f'Already running: {url} (existing server retained)',flush=True)
        if webbrowser.open(url):return 0
        print(f'Open this address in your browser: {url}',flush=True);return 1
    print(f'Starting D.I.G.: {url}',flush=True)
    from python_controller.main import main as run
    return run(['--ui','web','--web-control','--web-port',str(port),'--agent-enabled','--open-web',*forwarded])


if __name__=='__main__':raise SystemExit(main())
