import asyncio
import aiohttp
import websockets
import json
import re

from pokemonai.smogon.battle import BattleHandler

SMOGON_WEBSOCKET_URI = 'ws://sim.smogon.com:8000/showdown/websocket'
SMOGON_ACTION_URL = 'http://play.pokemonshowdown.com/action.php'
SMOGON_USERNAME = 'gdelta'
SMOGON_PASSWORD = 'gebiet'

SMOGON_TEAM = '|togekiss|choicescarf|1|airslash,ancientpower,trick,roost|Timid|,,,252,4,252||,0,,,,|||]|mimikyu|mentalherb||trickroom,destinybond,painsplit,shadowsneak||||,,,,,0||1|]|torkoal|firiumz|1|fireblast,|Modest|4,,,252,252,||,0,,,,|||'

MAX_PARALLEL_GAMES = 1

# Pool of open battles
pool = set()

"""
Continuously searches for battles and runs MAX_PARALLEL_GAMES battles at a time
"""
async def run():
    async with websockets.connect(SMOGON_WEBSOCKET_URI) as websocket:
        await _connect(websocket)

        while True:
            while len(pool) < MAX_PARALLEL_GAMES:
                # Preload connection for battle so that it receives messages
                conn = await _open_connection()

                # Start Smogon battle search
                await websocket.send('|/utm %s' % SMOGON_TEAM)
                await websocket.send('|/search gen71v1')

                # Wait for battle initialization
                msg = ''
                while not '|init|battle' in msg:
                    msg = await websocket.recv()

                # Room id is in the first line of the message
                # of the form >roomid
                m = re.match('>(.+?)\n', msg)
                roomid = m.group(1)

                # Start battle handler with preloaded connection
                bh = BattleHandler(roomid, SMOGON_USERNAME)
                pool.add(asyncio.ensure_future(_battle(conn, bh)))

            # Wait for battle handlers to complete
            done, _ = await asyncio.wait(pool, return_when=asyncio.FIRST_COMPLETED)

            # Remove done from pool
            for d in done:
                pool.remove(d)


"""
Handles initial handshaking/auth with Smogon
"""
async def _connect(websocket):
    # Initial communication
    await websocket.send('|/cmd rooms')
    await websocket.send('|/autojoin')

    # Wait for 'challstr' response needed for authentication
    challstr = ''
    while not challstr.startswith('|challstr|'):
        challstr = await websocket.recv()
    challstr = challstr.replace('|challstr|', '')

    # Authenticate with Smogon
    assertion_token = await _authenticate(SMOGON_USERNAME, SMOGON_PASSWORD, challstr)
    await websocket.send('|/trn %s,0,%s' % (SMOGON_USERNAME, assertion_token))

    # Wait for verification that we are logged in
    resp = ''
    while not resp.startswith('|updateuser|%s' % SMOGON_USERNAME):
        resp = await websocket.recv()


"""
Authenticates with Smogon server
by taking a username and challstr
to get an 'assertion' token

Returns: assertion token
"""
async def _authenticate(username, password, challstr):
    async with aiohttp.ClientSession() as session:
        res = await session.post(SMOGON_ACTION_URL, data={
            'act': 'login',
            'name': username,
            'pass': password,
            'challstr': challstr
            })
        async with res:
            body = await res.text()

            # First character is ']' followed by JSON object
            return json.loads(body[1:])['assertion']


"""
Opens new websocket and connects to Smogon
NOTE: Does not handle closing of websocket
"""
async def _open_connection():
    websocket = await websockets.connect(SMOGON_WEBSOCKET_URI)
    await _connect(websocket)
    return websocket


"""
Communicates with Smogon about battle
NOTE: Closes given websocket on completion
"""
async def _battle(websocket, battlehandler):
    try:
        # Start the battle timer
        await websocket.send('%s|/timer on' % battlehandler.roomid)

        while True:
            msg = await websocket.recv()
            if msg.startswith('>' + battlehandler.roomid):
                #print(msg)
                response, done = battlehandler.parse(msg)

                if response:
                    await websocket.send('%s|%s' % (battlehandler.roomid, response))

                if done:
                    break
    finally:
        await websocket.close()

